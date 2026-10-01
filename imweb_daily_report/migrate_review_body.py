"""Copy review text into page bodies, verify, then remove four legacy columns."""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from review_sync import (NotionClient, REVIEW_DATABASE_ID, all_pages, body_hash,
                         ensure_body, plain_text, review_content)
from review_state import ReviewState, encode_state

REMOVED = {"내용", "리뷰 번호", "비밀글", "숨김"}
BACKUP = Path("review-migration-backup.json")


class MigrationClient(NotionClient):
    def __init__(self, database_id):
        super().__init__(database_id)
        self.lock = threading.Lock()
        self.next_request = 0.0

    def _request(self, *args, **kwargs):
        # Respect Notion's integration-wide average request limit.
        with self.lock:
            time.sleep(max(0.0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + 0.4
        return super()._request(*args, **kwargs)


def review_number(page):
    return plain_text(page.get("properties", {}).get("리뷰 번호", {}).get("rich_text", []))


def source_body(page):
    return plain_text(page.get("properties", {}).get("내용", {}).get("rich_text", []))


def prepare(notion, state):
    database = notion.get_database()
    pages = all_pages(notion)
    if not (REMOVED & set(database.get("properties", {}))):
        payload = {"already_complete": True, "count": len(pages)}
        BACKUP.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        Path("review-migration-backup.enc").write_text(encode_state(payload), encoding="utf-8")
        state.save()
        logging.info("이미 이전 완료: %s건", len(pages))
        return
    if not {"내용", "리뷰 번호"} <= set(database["properties"]):
        raise RuntimeError("원본 내용/리뷰 번호 열이 없어 안전하게 이전할 수 없습니다.")
    keys = [review_number(p) for p in pages]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise RuntimeError("리뷰 번호가 없거나 중복된 페이지가 있습니다.")
    payload = {"database_id": notion.database_id, "database": database, "pages": pages}
    BACKUP.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    Path("review-migration-backup.enc").write_text(encode_state(payload), encoding="utf-8")
    for key, page in zip(keys, pages):
        record = state.data["reviews"].setdefault(key, {})
        if record.get("page_id") and record["page_id"].replace("-", "") != page["id"].replace("-", ""):
            raise RuntimeError("동기화 기록의 페이지 ID가 일치하지 않습니다.")
        record["page_id"] = page["id"]
    state.save()
    logging.info("백업 및 리뷰 번호 내부 기록 저장: %s건", len(pages))


def apply(notion, state):
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    if backup.get("already_complete"):
        return
    if backup["database_id"] != notion.database_id:
        raise RuntimeError("백업 데이터베이스가 일치하지 않습니다.")
    pages = backup["pages"]
    pages.sort(key=lambda p: (p["properties"].get("작성일", {}).get("date") or {}).get("start", ""), reverse=True)
    failures = []
    finished = 0

    def migrate(page):
        key = review_number(page)
        body, images = review_content(source_body(page))
        record = state.data["reviews"][key]
        digest = body_hash(body)
        if record.get("body_migrated") and record.get("body_hash") == digest and record.get("images") == images:
            return key, record
        text_id = ensure_body(notion, page["id"], body, images, preserve_existing=True)
        return key, {"page_id": page["id"], "body_hash": digest, "images": images,
                     "text_block_id": text_id, "body_migrated": True}

    # Different pages are independent; all requests share one rate limiter.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(migrate, page): review_number(page) for page in pages}
        for future in as_completed(futures):
            try:
                key, record = future.result()
                state.data["reviews"][key] = record
                finished += 1
            except Exception as exc:
                failures.append({"review_id": futures[future], "error": str(exc)})
                logging.error("본문 이전 실패 #%s: %s", futures[future], exc)
            if (finished + len(failures)) % 100 == 0:
                state.save()
                logging.info("본문 이전 검증: %s/%s건, 실패 %s건", finished, len(pages), len(failures))
    state.save()
    if failures:
        Path("review-migration-errors.json").write_text(json.dumps(failures, ensure_ascii=False), encoding="utf-8")
        raise RuntimeError(f"{len(failures)}건을 확인하지 못해 원본 열을 유지합니다.")

    # Stop if any source changed or a new row arrived while copying.
    fresh = all_pages(notion)
    before = {p["id"]: (review_number(p), source_body(p)) for p in pages}
    after = {p["id"]: (review_number(p), source_body(p)) for p in fresh}
    if before != after:
        raise RuntimeError("이전 중 원본 데이터가 바뀌었습니다. 원본 열을 유지하고 재실행을 기다립니다.")
    state.data["migration_verified"] = True
    state.save()
    logging.info("본문 검증 완료: %s건. 기록 파일 저장 후 열을 삭제합니다.", len(pages))


def finalize(notion, state):
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    if backup.get("already_complete"):
        return
    pages = backup["pages"]
    if not state.data.get("migration_verified") or any(not state.data['reviews'].get(review_number(p), {}).get('body_migrated') for p in pages):
        raise RuntimeError("본문 이전 검증이 완료되지 않았습니다.")
    before = {p["id"]: (review_number(p), source_body(p)) for p in pages}
    after = {p["id"]: (review_number(p), source_body(p)) for p in all_pages(notion)}
    if before != after:
        raise RuntimeError("기록 저장 중 원본이 바뀌어 열 삭제를 중단합니다.")
    remaining_columns = REMOVED & set(notion.get_database().get("properties", {}))
    if remaining_columns:
        notion._request("PATCH", f"/databases/{notion.database_id}", json={"properties": {name: None for name in remaining_columns}})
    if REMOVED & set(notion.get_database().get("properties", {})):
        raise RuntimeError("열 삭제 최종 검증 실패")
    report = {"verified_reviews": len(pages), "removed_columns": sorted(REMOVED), "status": "complete"}
    Path("review-migration-result.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    logging.info("완료: %s건 본문 보존 및 4개 열 삭제 검증", len(pages))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "apply", "finalize"])
    args = parser.parse_args()
    database_id = os.getenv("NOTION_REVIEW_DATABASE_ID", REVIEW_DATABASE_ID)
    if database_id != REVIEW_DATABASE_ID:
        raise RuntimeError("이 작업은 지정된 구매평 데이터베이스에서만 실행할 수 있습니다.")
    client = MigrationClient(database_id)
    state = ReviewState(database_id)
    {"prepare": prepare, "apply": apply, "finalize": finalize}[args.mode](client, state)

