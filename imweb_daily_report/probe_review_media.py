"""Read-only check of the store's AJAX request format. No review data is logged."""
import requests
from review_media import STOREFRONT, parse_review_images
r = requests.post(
    STOREFRONT + "/ajax/review_detail_view.cm",
    data={"idx": "129418004", "review_page": "1", "only_photo": "Y"},
    headers={"Origin": STOREFRONT, "Referer": STOREFRONT + "/",
             "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"},
    timeout=30,
)
print("PUBLIC_STATUS", r.status_code)
r.raise_for_status()
images = parse_review_images(r.json(), "129418004")
print("VERIFIED_REVIEW_IMAGE_COUNT", len(images))
if not images:
    raise RuntimeError("No review attachment returned")
