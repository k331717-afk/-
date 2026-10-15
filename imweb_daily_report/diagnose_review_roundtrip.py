"""Verify the known failed review without logging customer text."""
from review_sync import (ImwebClient, NotionClient, REVIEW_DATABASE_ID, imweb_get,
                        items_from_response, review_key, review_content,
                        get_children, plain_text, ensure_body)
from review_state import ReviewState
state = ReviewState(REVIEW_DATABASE_ID)
notion = NotionClient(REVIEW_DATABASE_ID)
imweb = ImwebClient()
imweb.authenticate()
target = "115337291"
for offset in range(81, 89):
    reviews = items_from_response(imweb_get(imweb, "/v2/shop/reviews", {"offset":offset, "limit":100}))
    for review in reviews:
        if review_key(review) != target:
            continue
        body, _ = review_content(str(review.get("body") or ""))
        page_id = state.data["reviews"][target]["page_id"]
        ensure_body(notion, page_id, body, [])
        blocks = get_children(notion, page_id)
        for i, block in enumerate(blocks[:-1]):
            if block.get("type") == "heading_2" and plain_text(block["heading_2"].get("rich_text", [])) == "리뷰 내용":
                assert plain_text(blocks[i+1]["paragraph"].get("rich_text", [])) == body
                print("Verified: known failed review body matches Notion readback; U+200B normalized.")
                raise SystemExit(0)
        raise RuntimeError("Review body heading not found")
raise RuntimeError("Known failed review not found in the expected source range")
