"""Diagnose one failed text round trip without logging customer text."""
import hashlib
import json
import unicodedata
from review_sync import (ImwebClient, NotionClient, REVIEW_DATABASE_ID, imweb_get,
                        items_from_response, review_key, review_content,
                        get_children, plain_text, chunks, body_hash)
from review_state import ReviewState

def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()

def details(expected, actual):
    controls = lambda s: "".join(c for c in s if unicodedata.category(c).startswith("C"))
    return {
        "expected_length": len(expected), "actual_length": len(actual),
        "expected_hash": digest(expected), "actual_hash": digest(actual),
        "same_after_strip": expected.strip() == actual.strip(),
        "same_after_crlf": expected.replace("\r\n", "\n").replace("\r", "\n") == actual,
        "same_after_tab_to_space": expected.replace("\t", " ") == actual,
        "same_after_remove_nul": expected.replace("\x00", "") == actual,
        "same_after_nfc": unicodedata.normalize("NFC", expected) == actual,
        "same_without_controls": "".join(c for c in expected if not unicodedata.category(c).startswith("C")) == "".join(c for c in actual if not unicodedata.category(c).startswith("C")),
        "expected_controls": sorted(set(ord(c) for c in controls(expected))),
        "actual_controls": sorted(set(ord(c) for c in controls(actual))),
    }

state = ReviewState(REVIEW_DATABASE_ID)
notion = NotionClient(REVIEW_DATABASE_ID)
imweb = ImwebClient()
imweb.authenticate()
checked = 0
for offset in range(81, 89):
    reviews = items_from_response(imweb_get(imweb, "/v2/shop/reviews", {"offset":offset, "limit":100}))
    for review in reviews:
        key = review_key(review)
        record = state.data["reviews"].get(key)
        if not record:
            continue
        body, _ = review_content(str(review.get("body") or ""))
        if record.get("body_hash") == body_hash(body):
            continue
        blocks = get_children(notion, record["page_id"])
        for i, block in enumerate(blocks[:-1]):
            if block.get("type") != "heading_2" or plain_text(block["heading_2"].get("rich_text", [])) != "리뷰 내용":
                continue
            paragraph = blocks[i+1]
            if paragraph.get("type") != "paragraph":
                continue
            checked += 1
            current = plain_text(paragraph["paragraph"].get("rich_text", []))
            if current == body:
                continue
            result = notion._request("PATCH", "/blocks/" + paragraph["id"], json={"paragraph":{"rich_text":chunks(body)}})
            actual = plain_text(result["paragraph"].get("rich_text", []))
            if actual != body:
                readback = notion._request("GET", "/blocks/" + paragraph["id"])
                print(json.dumps({"review_id": key, "response": details(body, actual), "text_content_matches": "".join(p.get("text", {}).get("content", "") for p in result["paragraph"].get("rich_text", [])) == body,
                                  "readback": details(body, plain_text(readback["paragraph"].get("rich_text", [])))}, ensure_ascii=True))
                raise SystemExit(1)
print(json.dumps({"checked_changed_bodies":checked, "roundtrip_failures":0}))
