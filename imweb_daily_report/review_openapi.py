"""Read native review attachments through Imweb's supported OAuth API.

Kept separate from the sales API client. Caller supplies a current access token.
Unrecognized attachment formats fail explicitly until verified with live data.
"""
import json
from urllib.parse import urlsplit

import requests

OPENAPI = "https://openapi.imweb.me"


class PhotoAuthenticationError(RuntimeError):
    pass


def parse_attachment_urls(value):
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        if value.strip().startswith(("[", "{")):
            return parse_attachment_urls(json.loads(value))
        values = [value.strip()]
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        values = value
    else:
        raise ValueError("공식 API의 첨부사진 형식을 확인해야 합니다.")
    result = []
    for value in values:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username
                or parsed.password or len(value) > 2000):
            raise ValueError("공식 API가 유효한 HTTPS 사진 주소를 반환하지 않았습니다.")
        if value not in result:
            result.append(value)
    return result


def parse_review_attachments(payload, review_id, site_code):
    if not isinstance(payload, dict) or payload.get("statusCode") != 200:
        raise RuntimeError("공식 구매평 조회가 정상 응답을 반환하지 않았습니다.")
    review = payload.get("data")
    if not isinstance(review, dict):
        raise ValueError("공식 API의 구매평 응답 형식을 확인해야 합니다.")
    if str(review.get("reviewNo")) != str(review_id):
        raise ValueError("사진 조회 결과의 구매평 번호가 다릅니다.")
    if review.get("siteCode") != site_code:
        raise ValueError("사진 조회 결과의 사이트가 다릅니다.")
    return parse_attachment_urls(review.get("img"))


def fetch_review_attachments(review_id, access_token, site_code):
    if not access_token:
        raise RuntimeError("구매평 사진 조회용 공식 API 인증이 필요합니다.")
    if not str(review_id).isdigit() or not site_code:
        raise ValueError("구매평 번호와 사이트 코드를 확인해야 합니다.")
    response = requests.get(
        OPENAPI + "/community/review/" + str(review_id),
        headers={"Authorization": "Bearer " + access_token}, timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        # Do not include response bodies, customer data, or access tokens in logs.
        if response.status_code in {401, 403}:
            raise PhotoAuthenticationError("공식 사진 API 인증/권한을 확인해야 합니다: HTTP " + str(response.status_code))
        raise RuntimeError("공식 구매평 사진 조회 실패: HTTP " + str(response.status_code))
    return parse_review_attachments(response.json(), review_id, site_code)
