"""Sync Imweb product reviews to a dedicated Notion database.

This module does not import, execute, or configure the sales report.
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from review_state import ReviewState

KST = ZoneInfo("Asia/Seoul")
REVIEW_DATABASE_ID = "e7caefbd-53c1-428a-bf90-c8ef399d77d2"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 설정이 없습니다: {name}")
    return value


def imweb_api_code(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("code")
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value if isinstance(value, int) else None


def raise_for_imweb_api_error(payload: Any) -> None:
    code = imweb_api_code(payload)
    if code is not None and code not in {0, 200}:
        raise RuntimeError(f"아임웹 API 오류: code={code} msg={payload.get('msg')}")


class ImwebClient:
    def __init__(self) -> None:
        self.base_url = "https://api.imweb.me"
        self.api_key = required_env("IMWEB_API_KEY")
        self.secret_key = required_env("IMWEB_SECRET_KEY")
        self.shop_code = os.getenv("IMWEB_SHOP_CODE", "").strip()
        self.timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
        self.access_token: str | None = None

    def authenticate(self) -> None:
        body = {"key": self.api_key, "secret": self.secret_key}
        if self.shop_code:
            body["shop_code"] = self.shop_code
        response = requests.post(f"{self.base_url}/v2/auth", json=body, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        raise_for_imweb_api_error(payload)
        data = payload.get("data") or {}
        token = payload.get("access_token") or payload.get("token")
        if isinstance(data, dict):
            token = token or data.get("access_token") or data.get("token")
        if not token and isinstance(payload.get("msg"), dict):
            token = payload["msg"].get("access_token")
        if not token:
            raise RuntimeError("아임웹 인증 응답에 접근 토큰이 없습니다.")
        self.access_token = str(token)

    def headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("아임웹 인증이 먼저 필요합니다.")
        return {"access-token": self.access_token, "Content-Type": "application/json"}


class NotionClient:
    def __init__(self, database_id: str) -> None:
        self.database_id = database_id
        self.token = required_env("REVIEW_NOTION_TOKEN")
        self.timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
        for attempt in range(6):
            try:
                response = requests.request(
                    method, f"https://api.notion.com/v1{path}", headers=headers,
                    timeout=self.timeout, **kwargs,
                )
                if response.status_code == 429 and attempt < 5:
                    time.sleep(float(response.headers.get("Retry-After", "5")))
                    continue
                response.raise_for_status()
                return response.json() if response.content else {}
            except (requests.Timeout, requests.ConnectionError) as exc:
                # A create/append may already have succeeded. The durable pending
                # record is reconciled on the next run instead of creating twice.
                if (method == "POST" and path == "/pages") or (method == "PATCH" and path.endswith("/children")):
                    raise
                if attempt == 5:
                    raise
                logging.warning("노션 응답 지연, 재시도 %s/5: %s", attempt + 1, exc)
                time.sleep(2 ** attempt)
        raise RuntimeError("노션 요청 재시도 횟수를 넘었습니다.")

    def get_database(self) -> dict[str, Any]:
        return self._request("GET", f"/databases/{self.database_id}")


def items_from_response(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = data.get("list", data.get("reviews", data.get("items")))
    if data is None:
        raise ValueError("아임웹 구매평 응답에 목록이 없습니다.")
    if not isinstance(data, list):
        raise ValueError("아임웹 구매평 응답의 목록 형식을 알 수 없습니다.")
    return [item for item in data if isinstance(item, dict)]


def review_key(review: dict[str, Any]) -> str:
    review_no = review.get("idx", review.get("review_no", review.get("reviewNo")))
    if review_no is None or str(review_no).strip() == "":
        raise ValueError("아임웹 구매평에 리뷰 번호가 없습니다.")
    return str(review_no)


def review_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp, KST).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def chunks(value: str, size: int = 2000) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": value[i:i + size]}} for i in range(0, len(value), size)]


class ReviewHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            src = dict(attrs).get("src") or ""
            parsed = urlparse(src)
            if parsed.scheme in {"http", "https"} and parsed.netloc and len(src) <= 2000:
                if src not in self.images:
                    self.images.append(src)
        elif tag in {"br", "p", "div", "li"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"}:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def review_content(body: str) -> tuple[str, list[str]]:
    if not re.search(r"<\s*(?:img|p|div|br|span|a|ul|li)\b", body, re.IGNORECASE):
        return body, []
    parser = ReviewHtmlParser()
    parser.feed(body)
    text = "".join(parser.text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), parser.images


def review_properties(review: dict[str, Any], product_name: str, synced_at: str) -> dict[str, Any]:
    key = review_key(review)
    body, images = review_content(str(review.get("body") or ""))
    if len(body) > 200_000:
        raise ValueError(f"구매평 #{key} 내용이 노션 텍스트 속성 한도를 넘습니다.")
    if len(images) > 100:
        raise ValueError(f"구매평 #{key} 사진이 노션 파일 속성 한도를 넘습니다.")
    low_rated = review.get("rating") is not None and float(review["rating"]) <= 3
    title = f"{product_name or '상품 ' + str(review.get('prod_no') or '')} · 구매평"
    if low_rated:
        title = f"🔴 {title}"
    source = {"imweb": "아임웹", "npay": "네이버페이"}.get(str(review.get("type") or "").lower())
    title_parts = chunks(title[:2000])
    if low_rated:
        for part in title_parts:
            part["annotations"] = {"color": "red"}
    properties: dict[str, Any] = {
        "리뷰": {"title": title_parts},
        "상품 옵션": {"rich_text": chunks(str(review.get("prod_option") or ""))},
        "작성자": {"rich_text": chunks(str(review.get("nick") or ""))},
        "사진": {"files": [
            {"name": f"리뷰 사진 {i}.jpg", "type": "external", "external": {"url": url}}
            for i, url in enumerate(images, 1)
        ]},
        "포토 리뷰": {"checkbox": as_bool(review.get("is_photo"))},
        "동기화 시각": {"date": {"start": synced_at}},
    }
    if source:
        properties["출처"] = {"select": {"name": source}}
    if review.get("rating") is not None:
        properties["평점"] = {"number": float(review["rating"])}
    written_at = review_date(review.get("wtime"))
    if written_at:
        properties["작성일"] = {"date": {"start": written_at}}
    return properties


def imweb_get(client: ImwebClient, path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{client.base_url}{path}"
    attempts = int(os.getenv("IMWEB_TOO_MANY_REQUEST_RETRIES", "3")) + 1
    for attempt in range(attempts):
        response = requests.get(url, headers=client.headers(), params=params, timeout=client.timeout)
        response.raise_for_status()
        payload = response.json()
        if imweb_api_code(payload) != -7:
            raise_for_imweb_api_error(payload)
            return payload
        if attempt < attempts - 1:
            time.sleep(float(os.getenv("IMWEB_TOO_MANY_REQUEST_SLEEP_SECONDS", "10")))
    raise RuntimeError("아임웹 요청 한도를 넘었습니다.")


def fetch_reviews(client: ImwebClient) -> list[dict[str, Any]]:
    page_size = min(int(os.getenv("IMWEB_REVIEW_PAGE_SIZE", "100")), 100)
    max_pages = int(os.getenv("IMWEB_REVIEW_MAX_PAGES", "500"))
    reviews: dict[str, dict[str, Any]] = {}
    last_signature: tuple[str, str, int] | None = None
    for page in range(1, max_pages + 1):
        payload = imweb_get(client, "/v2/shop/reviews", {"offset": page, "limit": page_size})
        items = items_from_response(payload)
        if not items:
            break
        signature = (review_key(items[0]), review_key(items[-1]), len(items))
        if signature == last_signature:
            raise RuntimeError("아임웹이 같은 구매평 페이지를 반복했습니다. 페이지 설정을 확인하세요.")
        last_signature = signature
        for review in items:
            reviews[review_key(review)] = review
        page_data = payload.get("data") if isinstance(payload, dict) else None
        total_pages = page_data.get("total_page") if isinstance(page_data, dict) else None
        logging.info("구매평 조회: %s페이지, 누적 %s건", page, len(reviews))
        if len(items) < page_size or (total_pages and page >= int(total_pages)):
            break
        if page == max_pages:
            raise RuntimeError("구매평 페이지 제한에 도달했습니다. 일부 데이터가 누락되지 않도록 동기화를 중단합니다.")
        time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "0.35")))
    return list(reviews.values())


def all_pages(notion: NotionClient) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion._request("POST", f"/databases/{notion.database_id}/query", json=body)
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
        if not cursor:
            raise RuntimeError("노션 페이지 목록의 다음 커서가 없습니다.")
    return pages


def plain_text(parts: list[dict[str, Any]]) -> str:
    return "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in parts)


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def body_blocks(body: str, images: list[str]) -> list[dict[str, Any]]:
    if len(images) > 98:
        raise ValueError("구매평 사진이 한 페이지 생성 한도를 넘습니다.")
    return [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": chunks("리뷰 내용")}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": chunks(body)}},
    ] + [{"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}} for url in images]


def get_children(notion: NotionClient, page_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        response = notion._request("GET", f"/blocks/{page_id}/children", params=params)
        result.extend(response.get("results", []))
        if not response.get("has_more"):
            return result
        cursor = response.get("next_cursor")
        if not cursor:
            raise RuntimeError("구매평 본문 조회 커서가 없습니다.")


def ensure_body(notion: NotionClient, page_id: str, body: str, images: list[str], *, preserve_existing: bool = False) -> str:
    blocks = get_children(notion, page_id)
    headings = [i for i, block in enumerate(blocks) if block.get("type") == "heading_2"
                and plain_text(block["heading_2"].get("rich_text", [])) == "리뷰 내용"]
    if len(headings) > 1:
        raise RuntimeError(f"리뷰 내용 영역이 중복되어 있습니다: {page_id}")
    if headings:
        offset = headings[0] + 1
        if offset >= len(blocks) or blocks[offset].get("type") != "paragraph":
            raise RuntimeError(f"리뷰 내용의 본문 블록을 확인할 수 없습니다: {page_id}")
        paragraph = blocks[offset]
        current = plain_text(paragraph["paragraph"].get("rich_text", []))
        if current != body:
            if preserve_existing:
                raise RuntimeError(f"기존 본문과 내용 열이 다릅니다. 원문을 보존하고 중단합니다: {page_id}")
            result = notion._request("PATCH", f"/blocks/{paragraph['id']}", json={"paragraph": {"rich_text": chunks(body)}})
            if plain_text(result["paragraph"].get("rich_text", [])) != body:
                raise RuntimeError("리뷰 본문 쓰기 검증 실패")
        text_id = paragraph["id"]
        present = {b.get("image", {}).get("external", {}).get("url") for b in blocks if b.get("type") == "image"}
        additions = [b for b in body_blocks(body, images)[2:] if b["image"]["external"]["url"] not in present]
        if additions:
            notion._request("PATCH", f"/blocks/{page_id}/children", json={"children": additions})
    else:
        response = notion._request("PATCH", f"/blocks/{page_id}/children", json={"children": body_blocks(body, images)})
        results = response.get("results", [])
        if len(results) < 2 or plain_text(results[1].get("paragraph", {}).get("rich_text", [])) != body:
            raise RuntimeError("리뷰 본문 추가 검증 실패")
        text_id = results[1]["id"]
    return text_id


def property_fingerprint(properties: dict[str, Any]) -> str:
    values = comparable_properties({k: v for k, v in properties.items() if k != "동기화 시각"})
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def existing_pages(notion: NotionClient, state: ReviewState) -> dict[str, dict[str, Any]]:
    pages = all_pages(notion)
    by_id = {p["id"].replace("-", ""): p for p in pages}
    records = state.data["reviews"]
    if pages and not records:
        raise RuntimeError("구매평 동기화 기록이 없습니다. 본문 이전 작업을 먼저 완료하세요.")
    mapped = {r["page_id"].replace("-", "") for r in records.values()}
    for key, pending in list(state.data["pending"].items()):
        candidates = [p for p in pages if p["id"].replace("-", "") not in mapped
                      and property_fingerprint(p["properties"]) == pending["fingerprint"]]
        if len(candidates) != 1:
            raise RuntimeError(f"응답이 끊긴 구매평 {key}의 생성 여부를 확정할 수 없습니다. 중복 방지를 위해 중단합니다.")
        page = candidates[0]
        # A create request contains both properties and body atomically.
        records[key] = {"page_id": page["id"], "body_hash": pending["body_hash"], "images": pending["images"]}
        mapped.add(page["id"].replace("-", ""))
        del state.data["pending"][key]
        state.save()
    return {key: by_id[r["page_id"].replace("-", "")] for key, r in records.items()
            if r["page_id"].replace("-", "") in by_id}


def comparable_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Compare writable review fields without their varying Notion metadata."""
    result: dict[str, Any] = {}
    for name, value in properties.items():
        kind = next((candidate for candidate in ("title", "rich_text", "date", "select", "checkbox", "number", "files") if candidate in value), None)
        if kind is None:
            result[name] = None
            continue
        current = value[kind]
        if kind == "title":
            result[name] = (
                "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in current),
                tuple(part.get("annotations", {}).get("color", "default") for part in current),
            )
        elif kind == "rich_text":
            result[name] = "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in current)
        elif kind == "date":
            result[name] = (current or {}).get("start")
        elif kind == "select":
            result[name] = (current or {}).get("name")
        elif kind == "files":
            result[name] = tuple(file.get("external", {}).get("url") for file in current)
        else:
            result[name] = current
    return result


def sync() -> tuple[int, int, int]:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    database_id = os.getenv("NOTION_REVIEW_DATABASE_ID", REVIEW_DATABASE_ID)
    notion = NotionClient(database_id)
    schema = notion.get_database().get("properties", {})
    required = {"리뷰", "작성일", "평점", "상품 옵션", "작성자", "사진", "출처", "포토 리뷰", "동기화 시각"}
    missing = required - set(schema)
    if missing:
        raise RuntimeError(f"노션 구매평 DB에 필요한 속성이 없습니다: {', '.join(sorted(missing))}")
    if {"내용", "리뷰 번호", "비밀글", "숨김"} & set(schema):
        raise RuntimeError("구매평 본문 이전 작업이 아직 완료되지 않았습니다.")
    state = ReviewState(database_id)

    imweb = ImwebClient()
    imweb.authenticate()
    reviews = fetch_reviews(imweb)
    # Apply the visible warning color first, including on existing reviews.
    reviews.sort(key=lambda review: 0 if review.get("rating") is not None and float(review["rating"]) <= 3 else 1)
    known = existing_pages(notion, state)
    product_names: dict[str, str] = {}
    created = updated = 0
    rejected: list[str] = []
    synced_at = datetime.now(KST).isoformat()
    for index, review in enumerate(reviews, 1):
        key = review_key(review)
        old = known.get(key)
        prod_no = str(review.get("prod_no") or "")
        if prod_no and prod_no not in product_names and old:
            old_title = "".join(
                part.get("plain_text", part.get("text", {}).get("content", ""))
                for part in old.get("properties", {}).get("리뷰", {}).get("title", [])
            )
            if old_title.endswith(" · 구매평"):
                product_names[prod_no] = old_title.removeprefix("🔴 ")[:-len(" · 구매평")]
        if prod_no and prod_no not in product_names:
            try:
                data = imweb_get(imweb, f"/v2/shop/products/{prod_no}")
                product = data.get("data") or {}
                product_names[prod_no] = str(product.get("name") or "") if isinstance(product, dict) else ""
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                logging.warning("상품 %s 이름 조회 실패: %s", prod_no, exc)
                product_names[prod_no] = ""
            time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "0.35")))
        props = review_properties(review, product_names.get(prod_no, ""), synced_at)
        body, images = review_content(str(review.get("body") or ""))
        digest = body_hash(body)
        if old:
            record = state.data["reviews"][key]
            desired = comparable_properties({k: v for k, v in props.items() if k != "동기화 시각"})
            current = comparable_properties({k: old.get("properties", {}).get(k, {}) for k in desired})
            body_changed = record.get("body_hash") != digest or record.get("images") != images
            if desired == current and not body_changed:
                continue
            if body_changed:
                record["text_block_id"] = ensure_body(notion, old["id"], body, images)
                record.update(body_hash=digest, images=images)
            if desired != current:
                notion._request("PATCH", f"/pages/{old['id']}", json={"properties": props})
            state.save()
            updated += 1
        else:
            if key in state.data["reviews"]:
                # Respect pages the owner has archived or deleted.
                continue
            state.data["pending"][key] = {"fingerprint": property_fingerprint(props), "body_hash": digest, "images": images}
            state.save()
            try:
                page = notion._request("POST", "/pages", json={"parent": {"database_id": database_id}, "properties": props, "children": body_blocks(body, images)})
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 400:
                    raise
                logging.error("구매평 #%s 노션 입력 거절: %s", key, exc.response.text[:1000])
                del state.data["pending"][key]
                state.save()
                rejected.append(key)
                continue
            state.data["reviews"][key] = {"page_id": page["id"], "body_hash": digest, "images": images}
            del state.data["pending"][key]
            state.save()
            known[key] = page
            created += 1
        if index % 25 == 0:
            logging.info("노션 구매평 처리: %s/%s건", index, len(reviews))
        time.sleep(float(os.getenv("NOTION_WRITE_SLEEP_SECONDS", "0.35")))
    if rejected:
        raise RuntimeError(f"노션이 거절한 구매평 {len(rejected)}건: {', '.join(rejected[:20])}")
    return len(reviews), created, updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    total, created, updated = sync()
    logging.info("완료: 아임웹 %s건, 노션 신규 %s건, 수정 %s건", total, created, updated)

