"""Move file-property photos into review bodies, retaining the property schema."""
from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from migrate_review_body import MigrationClient
from review_state import ReviewState, encode_state
from review_sync import REVIEW_DATABASE_ID, all_pages, get_children, plain_text

BACKUP = Path("review-photo-backup.json")


def property_images(page):
    result = []
    for item in page.get("properties", {}).get("사진", {}).get("files", []):
        url = item.get("external", {}).get("url", "")
        parsed = urlparse(url)
        # Expiring Notion file URLs must not be copied as permanent external images.
        if item.get("type") != "external" or parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise RuntimeError("영구 이미지 주소가 아닌 파일이 있어 사진 속성을 보존합니다.")
        if url not in result:
            result.append(url)
    return result


def image_urls(blocks):
    return {b.get("image", {}).get("external", {}).get("url") for b in blocks if b.get("type") == "image"}


def ensure_photos(notion, page_id, urls):
    blocks = get_children(notion, page_id)
    sections = [i for i, b in enumerate(blocks) if b.get("type") == "heading_2"
                and plain_text(b["heading_2"].get("rich_text", [])) == "리뷰 내용"]
    if len(sections) != 1 or sections[0] + 1 >= len(blocks) or blocks[sections[0] + 1].get("type") != "paragraph":
        raise RuntimeError("리뷰 내용 본문을 확인할 수 없어 사진 속성을 보존합니다.")
    present = image_urls(blocks)
    missing = [u for u in urls if u not in present]
    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        response = notion._request("PATCH", f"/blocks/{page_id}/children", json={"children": [
            {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": u}}}
            for u in batch
        ]})
        present.update(image_urls(response.get("results", [])))
    if not set(urls) <= present:
        raise RuntimeError("본문 이미지 저장 검증 실패")
    return len(missing)


def prepare(notion, state):
    schema = notion.get_database().get("properties", {})
    pages = all_pages(notion)
    if "사진" not in schema:
        payload = {"already_complete": True, "count": len(pages)}
    else:
        photo_pages = []
        for page in pages:
            urls = property_images(page)
            if urls:
                photo_pages.append({"page_id": page["id"], "urls": urls,
                    "written_at": (page["properties"].get("작성일", {}).get("date") or {}).get("start", "")})
        photo_pages.sort(key=lambda p: p["written_at"], reverse=True)
        known = {r["page_id"] for r in state.data["reviews"].values()}
        if any(p["page_id"] not in known for p in photo_pages):
            raise RuntimeError("동기화 기록에 없는 리뷰가 있어 이전을 중단합니다.")
        payload = {"database_id": notion.database_id, "pages": photo_pages, "count": len(pages)}
    BACKUP.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    Path("review-photo-backup.enc").write_text(encode_state(payload), encoding="utf-8")
    state.save()
    logging.info("사진 이전 대상: %s개 리뷰, 총 %s개 이미지", len(payload.get("pages", [])),
                 sum(len(p["urls"]) for p in payload.get("pages", [])))


def apply(notion, state):
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    if backup.get("already_complete"):
        return
    records = {r["page_id"]: r for r in state.data["reviews"].values()}
    pages = backup["pages"]
    failed = []
    finished = added = 0

    def migrate(page):
        record = records[page["page_id"]]
        if record.get("photo_property_verified") == page["urls"]:
            return page, 0
        count = ensure_photos(notion, page["page_id"], page["urls"])
        return page, count

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(migrate, page): page["page_id"] for page in pages}
        for future in as_completed(futures):
            try:
                page, count = future.result()
                record = records[page["page_id"]]
                record["preserved_images"] = list(dict.fromkeys(record.get("preserved_images", []) + page["urls"]))
                record["images"] = list(dict.fromkeys(record.get("images", []) + page["urls"]))
                record["photo_property_verified"] = page["urls"]
                finished += 1
                added += count
            except Exception as exc:
                failed.append(futures[future])
                logging.error("사진 이전 실패 페이지 %s: %s", futures[future], type(exc).__name__)
            if (finished + len(failed)) % 100 == 0:
                state.save()
                logging.info("사진 본문 이전 검증: %s/%s개 리뷰, 실패 %s건", finished, len(pages), len(failed))
    state.save()
    if failed:
        raise RuntimeError(f"{len(failed)}개 리뷰의 사진을 확인하지 못해 사진 속성을 유지합니다.")
    state.data["photo_migration_verified"] = True
    state.save()
    logging.info("사진 본문 이전 검증 완료: %s개 리뷰, 신규 이미지 %s개", finished, added)


def finalize(notion, state):
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    if backup.get("already_complete"):
        return
    if backup["database_id"] != notion.database_id:
        raise RuntimeError("사진 백업 대상 불일치")
    records = {r["page_id"]: r for r in state.data["reviews"].values()}
    if not state.data.get("photo_migration_verified") or any(records[p["page_id"]].get("photo_property_verified") != p["urls"] for p in backup["pages"]):
        raise RuntimeError("사진 이전 검증이 완료되지 않았습니다.")
    before = {p["page_id"]: p["urls"] for p in backup["pages"]}
    after = {}
    for p in all_pages(notion):
        urls = property_images(p)
        if urls:
            after[p["id"]] = urls
    if before != after:
        raise RuntimeError("이전 중 사진 원본이 바뀌어 사진 속성 삭제를 중단합니다.")
    # Keep the database property itself. Empty a cell only after its image URLs
    # have been preserved in both the page body and the encrypted state artifact.
    def clear_photo_cell(page):
        response = notion._request("PATCH", f"/pages/{page['page_id']}", json={"properties": {"사진": {"files": []}}})
        if response.get("properties", {}).get("사진", {}).get("files") != []:
            raise RuntimeError("사진 첨부값 비우기 검증 실패")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(clear_photo_cell, p) for p in backup["pages"]]
        for done, future in enumerate(as_completed(futures), 1):
            future.result()
            if done % 100 == 0:
                logging.info("이전 완료된 사진 첨부값 정리: %s/%s개 리뷰", done, len(before))
    count = sum(len(p["urls"]) for p in backup["pages"])
    Path("review-photo-migration-result.json").write_text(json.dumps({"status": "complete", "reviews": len(before), "photos": count}), encoding="utf-8")
    logging.info("완료: %s개 리뷰의 이미지 %s개 본문 보존 및 사진 첨부값 정리", len(before), count)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "apply", "finalize"])
    args = parser.parse_args()
    database_id = os.getenv("NOTION_REVIEW_DATABASE_ID", REVIEW_DATABASE_ID)
    if database_id != REVIEW_DATABASE_ID:
        raise RuntimeError("지정된 구매평 데이터베이스에서만 실행할 수 있습니다.")
    client = MigrationClient(database_id)
    state = ReviewState(database_id)
    {"prepare": prepare, "apply": apply, "finalize": finalize}[args.mode](client, state)
