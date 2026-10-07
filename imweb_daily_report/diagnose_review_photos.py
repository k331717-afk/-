"""Read-only review diagnostics. Never print customer content or media URLs."""
import json
import logging
from urllib.parse import urlparse
from review_sync import *
from review_state import ReviewState

def shape(value, depth=0):
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth+1) for k,v in value.items()}
    if isinstance(value, list):
        return {"type":"list", "count":len(value), "items":[shape(v,depth+1) for v in value[:2]]}
    if isinstance(value, str):
        return {"type":"str","length":len(value),"img_tags":len(re.findall(r"<img\\b", value, re.I)),
                "escaped_img":len(re.findall(r"&lt;img\\b",value,re.I)),
                "url_host":urlparse(value).hostname if value.startswith(("https://","http://")) else None}
    return type(value).__name__

logging.basicConfig(level=logging.INFO, format="%(message)s")
state=ReviewState(REVIEW_DATABASE_ID)
client=ImwebClient()
client.authenticate()
notion=NotionClient(REVIEW_DATABASE_ID)
targets={"3ef9f355db8581e39520dca6808cd8f8","3ef9f355db8581ad9b8ccb88e9dd0ccf","3ef9f355db85818d86a5d1a931639c00"}
for key, record in state.data["reviews"].items():
    if record["page_id"].replace("-","") in targets:
        detail=imweb_get(client, f"/v2/shop/reviews/{key}")
        print("TARGET_DETAIL",key,json.dumps(shape(detail),ensure_ascii=False))
reviews=fetch_reviews(client)
reviews.sort(key=lambda r: 0 if r.get("rating") is not None and float(r["rating"])<=3 else 1)
for r in reviews:
    key=review_key(r)
    record=state.data["reviews"].get(key)
    if not record:
        continue
    text,images=review_content(str(r.get("body") or ""))
    if record["page_id"].replace("-","") in targets:
        print("TARGET_LIST",key,json.dumps(shape(r),ensure_ascii=False))
    if r.get("rating") is not None and float(r["rating"])<=3 and record.get("body_hash")!=body_hash(text):
        blocks=get_children(notion,record["page_id"])
        for i,b in enumerate(blocks):
            if b.get("type")=="heading_2" and plain_text(b["heading_2"].get("rich_text",[]))=="리뷰 내용":
                if i+1<len(blocks) and blocks[i+1].get("type")=="paragraph":
                    current=plain_text(blocks[i+1]["paragraph"].get("rich_text",[]))
                    diffs=[{"index":j,"source":ord(a),"notion":ord(b)} for j,(a,b) in enumerate(zip(text,current)) if a!=b][:4]
                    print("BODY_MISMATCH",json.dumps({"key":key,"length_source":len(text),"length_notion":len(current),
                        "source_cr":text.count("\r"),"notion_cr":current.count("\r"),"source_lf":text.count("\n"),"notion_lf":current.count("\n"),
                        "crlf_equal":text.replace("\r\n","\n").replace("\r","\n")==current,
                        "strip_equal":text.strip()==current.strip(),"diffs":diffs,
                        "detail_shape":shape(imweb_get(client,f"/v2/shop/reviews/{key}"))},ensure_ascii=False))
        break
print("READ_ONLY_DIAGNOSTIC_COMPLETE")
