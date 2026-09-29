"""Sync Imweb product reviews to a dedicated Notion database.

This module does not import, execute, or configure the sales report.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

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
        review_number = ""
        if method == "POST" and path == "/pages":
            parts = kwargs.get("json", {}).get("properties", {}).get("리뷰 번호", {}).get("rich_text", [])
            review_number = "".join(part.get("text", {}).get("content", "") for part in parts)
        for attempt in range(3):
            try:
                response = requests.request(
                    method, f"https://api.notion.com/v1{path}", headers=headers,
                    timeout=self.timeout, **kwargs,
                )
                response.raise_for_status()
                return response.json() if response.content else {}
            except (requests.Timeout, requests.ConnectionError) as exc:
                if review_number:
                    # A timed-out create may have succeeded; check before retrying.
                    try:
                        result = requests.post(
                            f"https://api.notion.com/v1/databases/{self.database_id}/query",
                            headers=headers, timeout=self.timeout,
                            json={"filter": {"property": "리뷰 번호", "rich_text": {"equals": review_number}}},
                        )
                        result.raise_for_status()
                        matches = result.json().get("results", [])
                        if matches:
                            return matches[0]
                    except (requests.Timeout, requests.ConnectionError):
                        # Avoid a duplicate create when the lookup also times out.
                        raise
                if attempt == 2:
                    raise
                logging.warning("노션 응답 지연, 재시도 %s/2: %s", attempt + 1, exc)
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


def review_properties(review: dict[str, Any], product_name: str, synced_at: str) -> dict[str, Any]:
    key = review_key(review)
    body = str(review.get("body") or "")
    if len(body) > 200_000:
        raise ValueError(f"구매평 #{key} 내용이 노션 텍스트 속성 한도를 넘습니다.")
    title = f"{product_name or '상품 ' + str(review.get('prod_no') or '')} · 구매평"
    source = {"imweb": "아임웹", "npay": "네이버페이"}.get(str(review.get("type") or "").lower())
    title_parts = chunks(title[:2000])
    if review.get("rating") is not None and float(review["rating"]) <= 3:
        for part in title_parts:
            part["annotations"] = {"color": "red"}
    properties: dict[str, Any] = {
        "리뷰": {"title": title_parts},
        "리뷰 번호": {"rich_text": chunks(key)},
        "상품 옵션": {"rich_text": chunks(str(review.get("prod_option") or ""))},
        "작성자": {"rich_text": chunks(str(review.get("nick") or ""))},
        "내용": {"rich_text": chunks(body)},
        "포토 리뷰": {"checkbox": as_bool(review.get("is_photo"))},
        "비밀글": {"checkbox": as_bool(review.get("is_secret"))},
        "숨김": {"checkbox": as_bool(review.get("is_hide"))},
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


def existing_pages(notion: NotionClient) -> dict[str, dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion._request("POST", f"/databases/{notion.database_id}/query", json=body)
        for page in result.get("results", []):
            rich_text = (page.get("properties", {}).get("리뷰 번호", {}).get("rich_text") or [])
            key = "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in rich_text)
            if key:
                pages[key] = page
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
        if not cursor:
            raise RuntimeError("노션 페이지 목록의 다음 커서가 없습니다.")
    return pages


def comparable_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Compare writable review fields without their varying Notion metadata."""
    result: dict[str, Any] = {}
    for name, value in properties.items():
        kind = next((candidate for candidate in ("title", "rich_text", "date", "select", "checkbox", "number") if candidate in value), None)
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
        else:
            result[name] = current
    return result


def sync() -> tuple[int, int, int]:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    database_id = os.getenv("NOTION_REVIEW_DATABASE_ID", REVIEW_DATABASE_ID)
    notion = NotionClient(database_id)
    required = {"리뷰", "리뷰 번호", "작성일", "평점", "상품 옵션", "작성자", "내용", "출처", "포토 리뷰", "비밀글", "숨김", "동기화 시각"}
    missing = required - set(notion.get_database().get("properties", {}))
    if missing:
        raise RuntimeError(f"노션 구매평 DB에 필요한 속성이 없습니다: {', '.join(sorted(missing))}")

    imweb = ImwebClient()
    imweb.authenticate()
    reviews = fetch_reviews(imweb)
    known = existing_pages(notion)
    product_names: dict[str, str] = {}
    created = updated = 0
    synced_at = datetime.now(KST).isoformat()
    for index, review in enumerate(reviews, 1):
        prod_no = str(review.get("prod_no") or "")
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
        key = review_key(review)
        old = known.get(key)
        if old:
            desired = comparable_properties({k: v for k, v in props.items() if k != "동기화 시각"})
            current = comparable_properties({k: old.get("properties", {}).get(k, {}) for k in desired})
            if desired == current:
                continue
            notion._request("PATCH", f"/pages/{old['id']}", json={"properties": props})
            updated += 1
        else:
            notion._request("POST", "/pages", json={"parent": {"database_id": database_id}, "properties": props})
            created += 1
        if index % 25 == 0:
            logging.info("노션 구매평 처리: %s/%s건", index, len(reviews))
        time.sleep(float(os.getenv("NOTION_WRITE_SLEEP_SECONDS", "0.35")))
    return len(reviews), created, updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    total, created, updated = sync()
    logging.info("완료: 아임웹 %s건, 노션 신규 %s건, 수정 %s건", total, created, updated)
