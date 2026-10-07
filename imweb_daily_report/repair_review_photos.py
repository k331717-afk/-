"""Repair explicitly selected reviews without altering their text/properties."""
import argparse
import logging
from review_sync import (REVIEW_DATABASE_ID, ImwebClient, NotionClient,
    imweb_get, review_content, collect_missing_images, ensure_review_images, get_children)
from review_state import ReviewState


def repair(review_ids):
    state = ReviewState(REVIEW_DATABASE_ID)
    state.save()
    notion = NotionClient(REVIEW_DATABASE_ID)
    imweb = ImwebClient()
    imweb.authenticate()
    added = 0
    for key in review_ids:
        record = state.data["reviews"].get(key)
        if not record:
            raise RuntimeError("대상 구매평의 기존 노션 페이지를 확인할 수 없습니다.")
        review = imweb_get(imweb, f"/v2/shop/reviews/{key}").get("data") or {}
        if str(review.get("idx")) != key:
            raise RuntimeError("복구 대상 구매평 번호가 다릅니다.")
        _, embedded = review_content(str(review.get("body") or ""))
        # Include photos already restored through Notion, so retries reconcile
        # the identity index without duplicating or depending on the storefront.
        blocks = get_children(notion, record["page_id"])
        restored = [b.get("image", {}).get("external", {}).get("url")
                    for b in blocks if b.get("type") == "image"]
        restored = [url for url in restored if url]
        images = list(dict.fromkeys(embedded + record.get("preserved_images", []) + record.get("images", []) + restored))
        images, pending = collect_missing_images(review, images)
        if pending or not images:
            raise RuntimeError(f"구매평 #{key} 사진을 확인할 수 없습니다.")
        count = ensure_review_images(notion, record["page_id"], images)
        record.update(images=images, photo_pending=False)
        state.save()
        added += count
        logging.info("사진 복구 검증: 구매평 #%s, 사진 %s장, 신규 추가 %s장", key, len(images), count)
    logging.info("사진 복구 완료: %s개 리뷰, 추가 사진 %s장", len(review_ids), added)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("review_ids", nargs="+")
    repair(parser.parse_args().review_ids)
