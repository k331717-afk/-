"""Read-only public media transport probe; never print review text or URLs."""
import requests
import re
from review_media import STOREFRONT, parse_review_images
r=requests.post(STOREFRONT+"/ajax/review_detail_view.cm",
                data={"idx":"129418004","review_page":"1","only_photo":"Y"}, timeout=30)
print("PUBLIC_STATUS",r.status_code,"CONTENT_TYPE",r.headers.get("Content-Type"),"SERVER",r.headers.get("Server"))
if r.status_code == 403:
    print("PUBLIC_DENIAL", re.sub(r"<[^>]*>", " ", r.text).strip()[:500])
try:
    payload=r.json()
    print("PUBLIC_KEYS",sorted(payload.keys()),"SUCCESS",payload.get("msg")=="SUCCESS","IDENTITY",str(payload.get("idx"))=="129418004",
          "HTML_LENGTH",len(str(payload.get("html") or "")))
    print("PUBLIC_IMAGES",len(parse_review_images(payload,"129418004")))
except Exception as exc:
    print("PUBLIC_PARSE_ERROR",type(exc).__name__)
