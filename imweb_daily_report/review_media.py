"""Read attached review images through the supported Imweb OAuth API.

The legacy HTML parser remains for compatibility with existing migration tests.
Live photo requests use only the official community/review endpoint.
"""
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests
import os
from review_openapi import fetch_review_attachments, PhotoAuthenticationError

_photo_auth_rejected = False

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


def photo_api_ready():
    return bool(not _photo_auth_rejected and os.getenv("IMWEB_REVIEW_ACCESS_TOKEN", "").strip()
                and os.getenv("IMWEB_REVIEW_SITE_CODE", "").strip())


def fetch_review_images(review_id):
    """Use the supported API; never call the blocked storefront AJAX URL."""
    global _photo_auth_rejected
    try:
        return fetch_review_attachments(
            review_id, os.getenv("IMWEB_REVIEW_ACCESS_TOKEN", "").strip(),
            os.getenv("IMWEB_REVIEW_SITE_CODE", "").strip())
    except PhotoAuthenticationError:
        _photo_auth_rejected = True
        raise
