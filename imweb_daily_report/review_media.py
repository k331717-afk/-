"""Read attached images from the store's public review detail view.

Imweb v2 returns is_photo but omits native review attachments. This reader uses
the same read-only endpoint as the public storefront, without API credentials
or a logged-in browser session. Product placeholders and avatars are excluded.
"""
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests

STOREFRONT = "https://www.concretebread.com"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}


class ReviewImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.images = []
        self.has_review_body = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        active = bool(self.stack and self.stack[-1][1])
        active = active or bool(classes & {"_review_img", "_review_body"})
        if "_review_body" in classes:
            self.has_review_body = True
        if tag == "img" and active:
            url = attrs.get("src") or ""
            parsed = urlparse(url)
            if parsed.scheme in {"https", "http"} and parsed.netloc and len(url) <= 2000:
                if url not in self.images:
                    self.images.append(url)
        if tag not in VOID_TAGS:
            self.stack.append((tag, active))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def parse_review_images(payload, review_id):
    if not isinstance(payload, dict) or payload.get("msg") != "SUCCESS":
        raise RuntimeError("공개 구매평의 첨부사진 조회에 실패했습니다.")
    if str(payload.get("idx")) != str(review_id):
        raise RuntimeError("사진 조회 결과의 구매평 번호가 다릅니다.")
    parser = ReviewImageParser()
    parser.feed(str(payload.get("html") or ""))
    if not parser.has_review_body:
        raise RuntimeError("공개 구매평의 본문 영역을 확인할 수 없습니다.")
    return parser.images


def fetch_review_images(review_id):
    # This POST reads a review modal; it does not create or update a review.
    response = requests.post(
        STOREFRONT + "/ajax/review_detail_view.cm",
        data={"idx": str(review_id), "review_page": "1", "only_photo": "Y"},
        timeout=30,
    )
    response.raise_for_status()
    return parse_review_images(response.json(), review_id)
