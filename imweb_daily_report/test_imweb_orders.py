from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
SAMPLE_FILE = LOG_DIR / "sample_imweb_orders.json"
DETAIL_SAMPLE_FILE = LOG_DIR / "sample_imweb_order_detail.json"
PROD_ORDERS_SAMPLE_FILE = LOG_DIR / "sample_imweb_prod_orders.json"
KST = ZoneInfo("Asia/Seoul")
IMWEB_SUCCESS_CODES = {0, 200}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}")
        sys.exit(1)
    return value


def deep_get(data: dict[str, Any], paths: str | list[str], default: Any = None) -> Any:
    if isinstance(paths, str):
        paths = [paths]
    for path in paths:
        current: Any = data
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found:
            return current
    return default


def to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").replace("원", "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def yesterday_range_kst() -> tuple[datetime, datetime, str]:
    target = datetime.now(KST).date() - timedelta(days=1)
    start = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=KST)
    end = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=KST)
    return start, end, target.isoformat()


def extract_orders(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    candidates = [
        "data.list",
        "data.orders",
        "data.items",
        "list",
        "orders",
        "items",
        "msg.list",
        "msg.orders",
    ]
    extracted = deep_get(data, candidates, [])
    if isinstance(extracted, list):
        return [item for item in extracted if isinstance(item, dict)]
    return []


def extract_products(order: dict[str, Any]) -> list[dict[str, Any]]:
    product_rows = deep_get(
        order,
        ["items", "products", "order_items", "order_products", "prod_list", "product_list"],
        [],
    )
    if isinstance(product_rows, dict):
        return [item for item in product_rows.values() if isinstance(item, dict)]
    if isinstance(product_rows, list):
        return [item for item in product_rows if isinstance(item, dict)]
    return []


def extract_prod_orders(data: Any) -> list[dict[str, Any]]:
    prod_orders = deep_get(data, "data", [])
    if isinstance(prod_orders, list):
        return [item for item in prod_orders if isinstance(item, dict)]
    if isinstance(prod_orders, dict):
        flattened: list[dict[str, Any]] = []
        for value in prod_orders.values():
            if isinstance(value, dict):
                flattened.extend(item for item in value.values() if isinstance(item, dict))
            elif isinstance(value, list):
                flattened.extend(item for item in value if isinstance(item, dict))
        return flattened
    return []


def order_status(order: dict[str, Any]) -> str:
    value = deep_get(
        order,
        [
            "status",
            "order_status",
            "payment_status",
            "pay_status",
            "order.status",
            "payment.status",
            "delivery.status",
        ],
    )
    return str(value).strip() if value not in (None, "") else "상태 필드 없음"


def order_timestamp(order: dict[str, Any]) -> int:
    return to_int(deep_get(order, ["order_time", "payment.payment_time", "complete_time"], 0))


def is_order_in_range(order: dict[str, Any], start: datetime, end: datetime) -> bool:
    timestamp = order_timestamp(order)
    if not timestamp:
        return False
    ordered_at = datetime.fromtimestamp(timestamp, KST)
    return start <= ordered_at <= end


def order_amount(order: dict[str, Any]) -> int:
    direct_amount = to_int(
        deep_get(
            order,
            [
                "payment_price",
                "total_price",
                "order_price",
                "amount",
                "pay_amount",
                "price_total",
                "total_amount",
                "payment.payment_amount",
                "payment.total_price",
                "data.payment_price",
            ],
            0,
        )
    )
    if direct_amount:
        return direct_amount

    total = 0
    for product in extract_products(order):
        amount = to_int(
            deep_get(
                product,
                ["total_price", "payment_price", "price_total", "sale_price", "amount", "product_price", "price"],
                0,
            )
        )
        qty = to_int(deep_get(product, ["quantity", "qty", "count", "order_count", "ea"], 1), 1)
        unit_price = to_int(deep_get(product, ["unit_price", "price", "product_price"], 0))
        total += amount if amount else unit_price * qty
    return total


def print_response_failure(response: requests.Response) -> None:
    print(f"status_code: {response.status_code}")
    print(f"response.text: {response.text}")


def imweb_api_code(data: Any) -> int | None:
    if not isinstance(data, dict) or "code" not in data:
        return None
    code = data.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        return int(code)
    return code if isinstance(code, int) else None


def fail_if_imweb_api_error(data: Any) -> None:
    if not isinstance(data, dict) or "code" not in data:
        return

    code = data.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        code = int(code)
    if isinstance(code, int) and code not in IMWEB_SUCCESS_CODES:
        print("아임웹 API 오류 응답:")
        print(f"code: {data.get('code')}")
        print(f"msg: {data.get('msg')}")
        print("response.text:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        sys.exit(1)


def build_imweb_auth_headers(access_token: str) -> dict[str, str]:
    header_name = os.getenv("IMWEB_ACCESS_TOKEN_HEADER", "access-token").strip() or "access-token"
    auth_scheme = os.getenv("IMWEB_ACCESS_TOKEN_SCHEME", "").strip()
    if header_name.lower() == "authorization":
        value = f"{auth_scheme} {access_token}".strip() if auth_scheme else access_token
    else:
        value = access_token
    return {header_name: value, "Content-Type": "application/json"}


def page_signature(orders: list[dict[str, Any]]) -> tuple[str, str, int] | None:
    if not orders:
        return None
    first = str(deep_get(orders[0], ["order_code", "order_no", "id"], ""))
    last = str(deep_get(orders[-1], ["order_code", "order_no", "id"], ""))
    return first, last, len(orders)


def authenticate(base_url: str, timeout: int) -> str:
    endpoint = os.getenv("IMWEB_AUTH_ENDPOINT", "/v2/auth")
    payload = {
        "key": required_env("IMWEB_API_KEY"),
        "secret": required_env("IMWEB_SECRET_KEY"),
    }
    shop_code = os.getenv("IMWEB_SHOP_CODE", "").strip()
    if shop_code:
        payload["shop_code"] = shop_code

    url = f"{base_url}{endpoint}"
    print(f"[auth] POST {url}")
    response = requests.post(url, json=payload, timeout=timeout)
    if not response.ok:
        print_response_failure(response)
        sys.exit(1)

    data = response.json()
    fail_if_imweb_api_error(data)
    token = deep_get(data, ["access_token", "token", "data.access_token", "data.token", "msg.access_token"])
    if not token:
        print("Access token not found in response:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
        sys.exit(1)
    return str(token)


def fetch_orders(base_url: str, access_token: str, start: datetime, end: datetime, timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    endpoint = os.getenv("IMWEB_ORDERS_ENDPOINT", "/v2/shop/orders")
    page_size = env_int("IMWEB_PAGE_SIZE", 100)
    max_pages = env_int("IMWEB_MAX_PAGES", 100)
    date_format = os.getenv("IMWEB_DATE_FORMAT", "timestamp").lower()
    start_value: Any = int(start.timestamp()) if date_format == "timestamp" else start.isoformat()
    end_value: Any = int(end.timestamp()) if date_format == "timestamp" else end.isoformat()
    headers = build_imweb_auth_headers(access_token)
    request_sleep = float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5"))
    too_many_retries = env_int("IMWEB_TOO_MANY_REQUEST_RETRIES", 3)
    too_many_sleep = float(os.getenv("IMWEB_TOO_MANY_REQUEST_SLEEP_SECONDS", "10"))

    orders: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    seen_page_signatures: set[tuple[str, str, int]] = set()

    for page in range(1, max_pages + 1):
        params = {
            os.getenv("IMWEB_PARAM_START_DATE", "start_date"): start_value,
            os.getenv("IMWEB_PARAM_END_DATE", "end_date"): end_value,
            os.getenv("IMWEB_PARAM_PAGE", "offset"): page,
            os.getenv("IMWEB_PARAM_LIMIT", "limit"): page_size,
        }
        url = f"{base_url}{endpoint}"
        data: Any = None
        for attempt in range(1, too_many_retries + 2):
            print(f"[orders] GET {url} page={page} attempt={attempt}")
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
            if not response.ok:
                print_response_failure(response)
                sys.exit(1)

            data = response.json()
            if imweb_api_code(data) != -7:
                break
            print(f"TOO MANY REQUEST 응답. {too_many_sleep:g}초 대기 후 재시도합니다.")
            if attempt <= too_many_retries:
                import time

                time.sleep(too_many_sleep)

        page_orders = extract_orders(data)
        raw_pages.append({"page": page, "params": params, "response": data})
        fail_if_imweb_api_error(data)

        signature = page_signature(page_orders)
        if signature and signature in seen_page_signatures:
            print(f"중복 페이지 감지: page={page}. 같은 주문 목록이 반복되어 조회를 중단합니다.")
            print("IMWEB_PARAM_PAGE 값이 실제 아임웹 페이지 파라미터명과 맞는지 확인하세요.")
            break
        if signature:
            seen_page_signatures.add(signature)

        orders.extend(page_orders)
        timestamps = [order_timestamp(order) for order in page_orders if order_timestamp(order)]
        if timestamps and max(timestamps) < int(start.timestamp()):
            print(f"기준일보다 오래된 주문 페이지에 도달해 조회를 중단합니다: page={page}")
            break
        if len(page_orders) < page_size:
            break
        if request_sleep > 0:
            import time

            time.sleep(request_sleep)

    return orders, raw_pages


def fetch_order_detail(base_url: str, access_token: str, order: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    template = os.getenv("IMWEB_ORDER_DETAIL_ENDPOINT_TEMPLATE", "").strip()
    if not template:
        return None

    order_code = str(deep_get(order, ["order_code", "order_no", "id"], "")).strip()
    if not order_code:
        print("주문 상세 조회용 order_code를 찾지 못했습니다.")
        return None

    endpoint = template.format(order_code=order_code, order_no=order_code)
    url = f"{base_url}{endpoint}"
    print(f"[order-detail] GET {url}")
    response = requests.get(url, headers=build_imweb_auth_headers(access_token), timeout=timeout)
    if not response.ok:
        print_response_failure(response)
        return None
    data = response.json()
    DETAIL_SAMPLE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    fail_if_imweb_api_error(data)
    return data


def fetch_prod_orders(base_url: str, access_token: str, order: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    order_no = str(deep_get(order, "order_no", "")).strip()
    if not order_no:
        return []

    template = os.getenv("IMWEB_PROD_ORDERS_ENDPOINT_TEMPLATE", "/v2/shop/orders/{order_no}/prod-orders")
    url = f"{base_url}{template.format(order_no=order_no)}"
    print(f"[prod-orders] GET {url}")
    response = requests.get(url, headers=build_imweb_auth_headers(access_token), timeout=timeout)
    if not response.ok:
        print_response_failure(response)
        return []
    data = response.json()
    PROD_ORDERS_SAMPLE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    fail_if_imweb_api_error(data)
    return extract_prod_orders(data)


def prod_order_item_amount(item: dict[str, Any]) -> int:
    gross = to_int(deep_get(item, "payment.price", 0))
    discount = sum(to_int(deep_get(item, f"payment.{key}", 0)) for key in ["price_sale", "coupon", "membership_discount"])
    return max(gross - discount, 0)


def prod_order_option_name(item: dict[str, Any]) -> str:
    options = deep_get(item, "options", [])
    names: list[str] = []
    if isinstance(options, list):
        for group in options:
            details = group if isinstance(group, list) else [group]
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                values = detail.get("value_name_list")
                if isinstance(values, list):
                    names.extend(str(value).strip() for value in values if str(value).strip())
                elif detail.get("value_name"):
                    names.append(str(detail["value_name"]).strip())
    return " / ".join(names) if names else "옵션 없음"


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    base_url = os.getenv("IMWEB_BASE_URL", "https://api.imweb.me").rstrip("/")
    timeout = env_int("REQUEST_TIMEOUT_SECONDS", 30)
    start, end, target_date = yesterday_range_kst()

    print("[아임웹 전일 주문 조회 테스트]")
    print(f"기준일: {target_date}")
    print(f"조회범위: {start.isoformat()} ~ {end.isoformat()}")
    print()

    access_token = authenticate(base_url, timeout)
    print("access_token 발급 성공")
    print()

    orders, raw_pages = fetch_orders(base_url, access_token, start, end, timeout)

    SAMPLE_FILE.write_text(
        json.dumps({"target_date": target_date, "pages": raw_pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    filtered_orders = [order for order in orders if is_order_in_range(order, start, end)]
    status_counts = Counter(order_status(order) for order in filtered_orders)
    total_amount = sum(order_amount(order) for order in filtered_orders)

    if orders:
        timestamps = [order_timestamp(order) for order in orders if order_timestamp(order)]
        if timestamps:
            first_dt = datetime.fromtimestamp(min(timestamps), KST)
            last_dt = datetime.fromtimestamp(max(timestamps), KST)
            print(f"원본 주문 시간 범위: {first_dt.isoformat()} ~ {last_dt.isoformat()}")
        if len(filtered_orders) != len(orders):
            print(f"기준일 로컬 필터 적용: 원본 {len(orders):,}건 -> 기준일 주문 {len(filtered_orders):,}건")
            print("아임웹 날짜 파라미터가 무시되는 경우를 대비해 order_time 기준으로 한 번 더 필터링했습니다.")

    print()
    print(f"조회된 주문 수: {len(filtered_orders):,}건")
    print("주문 상태별 건수:")
    if status_counts:
        for status, count in status_counts.most_common():
            print(f"- {status}: {count:,}건")
    else:
        print("- 조회된 주문 없음")
    print(f"총 결제금액 합계: {total_amount:,}원")
    print()
    print(f"전체 응답 저장: {SAMPLE_FILE}")
    print()

    print("주문 원본 응답 일부:")
    sample_order = filtered_orders[0] if filtered_orders else (orders[0] if orders else None)
    if sample_order:
        print(json.dumps(sample_order, ensure_ascii=False, indent=2)[:3000])
        products = extract_products(sample_order)
        print()
        print("주문상품 원본 응답 일부:")
        if products:
            print(json.dumps(products[0], ensure_ascii=False, indent=2)[:3000])
        else:
            print("첫 주문에서 주문상품 배열을 찾지 못했습니다.")
            detail = fetch_order_detail(base_url, access_token, sample_order, timeout)
            if detail is not None:
                print(f"주문 상세 응답 저장: {DETAIL_SAMPLE_FILE}")
                print(json.dumps(detail, ensure_ascii=False, indent=2)[:3000])

            prod_orders = fetch_prod_orders(base_url, access_token, sample_order, timeout)
            print()
            print("주문상품 API 응답 일부:")
            if prod_orders:
                first_prod_order = prod_orders[0]
                print(json.dumps(first_prod_order, ensure_ascii=False, indent=2)[:3000])
                first_items = first_prod_order.get("items", [])
                if isinstance(first_items, list) and first_items:
                    first_item = first_items[0]
                    print()
                    print("주문상품 집계 테스트:")
                    print(f"- 상품명: {first_item.get('prod_name')}")
                    print(f"- 옵션명: {prod_order_option_name(first_item)}")
                    print(f"- 수량: {to_int(deep_get(first_item, 'payment.count', 1), 1):,}개")
                    print(f"- 금액: {prod_order_item_amount(first_item):,}원")
                print(f"주문상품 응답 저장: {PROD_ORDERS_SAMPLE_FILE}")
            else:
                print("주문상품 API에서도 상품 배열을 찾지 못했습니다.")
    else:
        print("조회된 주문이 없습니다.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
