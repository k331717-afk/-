from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
PROBE_FILE = LOG_DIR / "probe_imweb_api.json"
KST = ZoneInfo("Asia/Seoul")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else int(value)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}")
        sys.exit(1)
    return value


def deep_get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def yesterday_range_kst() -> tuple[datetime, datetime]:
    target = datetime.now(KST).date() - timedelta(days=1)
    start = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=KST)
    end = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=KST)
    return start, end


def build_imweb_auth_headers(access_token: str) -> dict[str, str]:
    header_name = os.getenv("IMWEB_ACCESS_TOKEN_HEADER", "access-token").strip() or "access-token"
    auth_scheme = os.getenv("IMWEB_ACCESS_TOKEN_SCHEME", "").strip()
    if header_name.lower() == "authorization":
        value = f"{auth_scheme} {access_token}".strip() if auth_scheme else access_token
    else:
        value = access_token
    return {header_name: value, "Content-Type": "application/json"}


def authenticate(base_url: str, timeout: int) -> str:
    payload = {
        "key": required_env("IMWEB_API_KEY"),
        "secret": required_env("IMWEB_SECRET_KEY"),
    }
    shop_code = os.getenv("IMWEB_SHOP_CODE", "").strip()
    if shop_code:
        payload["shop_code"] = shop_code

    url = f"{base_url}{os.getenv('IMWEB_AUTH_ENDPOINT', '/v2/auth')}"
    response = requests.post(url, json=payload, timeout=timeout)
    if not response.ok:
        print(f"auth status_code: {response.status_code}")
        print(f"auth response.text: {response.text}")
        sys.exit(1)

    data = response.json()
    token = (
        deep_get(data, "access_token")
        or deep_get(data, "token")
        or deep_get(data, "data.access_token")
        or deep_get(data, "data.token")
        or deep_get(data, "msg.access_token")
    )
    if not token:
        print("Access token not found:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
        sys.exit(1)
    return str(token)


def extract_orders(data: Any) -> list[dict[str, Any]]:
    candidates = [
        deep_get(data, "data.list"),
        deep_get(data, "data.orders"),
        deep_get(data, "data.items"),
        deep_get(data, "list"),
        deep_get(data, "orders"),
        deep_get(data, "items"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def find_product_like_paths(data: Any, prefix: str = "") -> list[str]:
    product_tokens = (
        "prod",
        "product",
        "goods",
        "item",
        "option",
        "quantity",
        "qty",
        "ea",
        "price",
    )
    paths: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(token in str(key).lower() for token in product_tokens):
                paths.append(path)
            paths.extend(find_product_like_paths(value, path))
    elif isinstance(data, list):
        for index, value in enumerate(data[:2]):
            paths.extend(find_product_like_paths(value, f"{prefix}[{index}]"))
    return paths[:40]


def first_order(data: Any) -> dict[str, Any]:
    orders = extract_orders(data)
    if not orders:
        return {}
    return orders[0]


def first_order_code(data: Any) -> str:
    order = first_order(data)
    return str(order.get("order_code") or order.get("order_no") or "")


def call_orders(base_url: str, headers: dict[str, str], params: dict[str, Any], timeout: int) -> dict[str, Any]:
    url = f"{base_url}{os.getenv('IMWEB_ORDERS_ENDPOINT', '/v2/shop/orders')}"
    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    try:
        data = response.json()
    except ValueError:
        data = {"raw_text": response.text}
    return {
        "status_code": response.status_code,
        "url": response.url,
        "response": data,
        "first_order_code": first_order_code(data),
        "current_page": deep_get(data, "data.pagenation.current_page"),
        "total_page": deep_get(data, "data.pagenation.total_page"),
        "list_count": len(extract_orders(data)),
    }


def probe_page_params(base_url: str, headers: dict[str, str], timeout: int) -> list[dict[str, Any]]:
    start, end = yesterday_range_kst()
    limit_name = os.getenv("IMWEB_PARAM_LIMIT", "limit")
    date_params = {
        os.getenv("IMWEB_PARAM_START_DATE", "start_date"): int(start.timestamp()),
        os.getenv("IMWEB_PARAM_END_DATE", "end_date"): int(end.timestamp()),
        limit_name: env_int("IMWEB_PAGE_SIZE", 100),
    }
    candidates = ["page", "page_no", "page_num", "current_page", "p", "offset"]
    results: list[dict[str, Any]] = []

    print("[페이지 파라미터 진단]")
    for page_param in candidates:
        params1 = {**date_params, page_param: 1}
        params2 = {**date_params, page_param: 2}
        result1 = call_orders(base_url, headers, params1, timeout)
        time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5")))
        result2 = call_orders(base_url, headers, params2, timeout)

        changed = result1["first_order_code"] != result2["first_order_code"]
        current_page_changed = result2["current_page"] not in (None, result1["current_page"])
        status = "가능성 높음" if changed or current_page_changed else "변화 없음"
        print(
            f"- {page_param}: {status} "
            f"(page1={result1['first_order_code']}, page2={result2['first_order_code']}, "
            f"current_page={result1['current_page']}->{result2['current_page']})"
        )
        results.append(
            {
                "page_param": page_param,
                "status": status,
                "page1": result1,
                "page2": result2,
            }
        )
        time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5")))

    return results


def probe_detail_endpoints(base_url: str, headers: dict[str, str], timeout: int, sample_order: dict[str, Any]) -> list[dict[str, Any]]:
    if not sample_order:
        return []

    order_code = str(sample_order.get("order_code") or "")
    order_no = str(sample_order.get("order_no") or "")
    channel_order_no = str(sample_order.get("channel_order_no") or "")
    values = {
        "order_code": order_code,
        "order_no": order_no,
        "channel_order_no": channel_order_no,
    }

    templates = [
        "/v2/shop/orders/{order_code}",
        "/v2/shop/orders/{order_no}",
        "/v2/shop/orders/{channel_order_no}",
        "/v2/shop/orders/{order_no}?include=items",
        "/v2/shop/orders/{order_no}?include=products",
        "/v2/shop/orders/{order_no}?include=order_products",
        "/v2/shop/orders/{order_no}?include=prod_orders",
        "/v2/shop/orders/{order_no}?with_items=Y",
        "/v2/shop/orders/{order_no}?with_products=Y",
        "/v2/shop/orders/{order_no}?items=Y",
        "/v2/shop/orders/{order_no}?products=Y",
        "/v2/shop/order/{order_code}",
        "/v2/shop/order/{order_no}",
        "/v2/shop/orders/detail/{order_code}",
        "/v2/shop/orders/detail/{order_no}",
        "/v2/shop/order/detail/{order_code}",
        "/v2/shop/order/detail/{order_no}",
        "/v2/shop/orders/{order_no}/items",
        "/v2/shop/orders/{order_no}/products",
        "/v2/shop/orders/{order_no}/goods",
        "/v2/shop/orders/{order_no}/order-products",
        "/v2/shop/orders/{order_no}/order_products",
        "/v2/shop/orders/{order_no}/prod-orders",
        "/v2/shop/orders/{order_no}/prod_orders",
        "/v2/shop/order-products/{order_no}",
        "/v2/shop/order_products/{order_no}",
        "/v2/shop/prod-orders/{order_no}",
        "/v2/shop/prod_orders/{order_no}",
        "/v2/shop/order-products?order_code={order_code}",
        "/v2/shop/order-products?order_no={order_no}",
        "/v2/shop/order_products?order_code={order_code}",
        "/v2/shop/order_products?order_no={order_no}",
        "/v2/shop/prod-orders?order_code={order_code}",
        "/v2/shop/prod-orders?order_no={order_no}",
        "/v2/shop/prod_orders?order_code={order_code}",
        "/v2/shop/prod_orders?order_no={order_no}",
        "/v2/shop/orders?order_code={order_code}",
        "/v2/shop/orders?order_no={order_no}",
        "/v2/shop/orders?idx={order_code}",
        "/v2/shop/orders?idx={order_no}",
    ]
    results: list[dict[str, Any]] = []

    print()
    print("[주문 상세 엔드포인트 후보 진단]")
    for template in templates:
        if "{channel_order_no}" in template and not channel_order_no:
            continue
        url = f"{base_url}{template.format(**values)}"
        response = requests.get(url, headers=headers, timeout=timeout)
        text = response.text
        try:
            data: Any = response.json()
        except ValueError:
            data = {"raw_text": text[:1000]}
        code = data.get("code") if isinstance(data, dict) else None
        msg = data.get("msg") if isinstance(data, dict) else None
        product_like_paths = find_product_like_paths(data)
        product_hint = f" product_keys={len(product_like_paths)}" if product_like_paths else ""
        print(f"- {template}: status={response.status_code} code={code} msg={msg}{product_hint}")
        results.append(
            {
                "template": template,
                "status_code": response.status_code,
                "product_like_paths": product_like_paths,
                "response": data,
            }
        )
        time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5")))

    return results


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    base_url = os.getenv("IMWEB_BASE_URL", "https://api.imweb.me").rstrip("/")
    timeout = env_int("REQUEST_TIMEOUT_SECONDS", 30)
    access_token = authenticate(base_url, timeout)
    headers = build_imweb_auth_headers(access_token)

    page_results = probe_page_params(base_url, headers, timeout)
    sample_order: dict[str, Any] = {}
    for result in page_results:
        sample_response = result["page1"].get("response")
        sample_order = first_order(sample_response)
        if sample_order:
            break

    detail_results = probe_detail_endpoints(base_url, headers, timeout, sample_order)
    PROBE_FILE.write_text(
        json.dumps({"page_results": page_results, "detail_results": detail_results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"진단 전체 응답 저장: {PROBE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
