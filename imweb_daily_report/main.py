from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "report.log"
SAMPLE_RESPONSE_FILE = LOG_DIR / "sample_imweb_response.json"
KST = ZoneInfo("Asia/Seoul")
IMWEB_SUCCESS_CODES = {0, 200}


@dataclass(frozen=True)
class OrderItem:
    order_id: str
    product_name: str
    option_name: str
    quantity: int
    amount: int


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"환경변수 {name} 값이 필요합니다. .env 파일을 확인하세요.")
    return value


def yesterday_range_kst() -> tuple[datetime, datetime, str]:
    today = datetime.now(KST).date()
    target = today - timedelta(days=1)
    start = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=KST)
    end = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=KST)
    return start, end, target.isoformat()


def deep_get(data: dict[str, Any], paths: list[str], default: Any = None) -> Any:
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
    if text == "":
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def order_timestamp(order: dict[str, Any]) -> int:
    return to_int(deep_get(order, ["order_time", "payment.payment_time", "complete_time"], 0))


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def split_csv_env(name: str, default: str) -> set[str]:
    raw = os.getenv(name, default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def raise_for_imweb_api_error(data: Any) -> None:
    if not isinstance(data, dict) or "code" not in data:
        return
    code = data.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        code = int(code)
    if isinstance(code, int) and code not in IMWEB_SUCCESS_CODES:
        raise RuntimeError(f"아임웹 API 오류: code={data.get('code')} msg={data.get('msg')}")


def imweb_api_code(data: Any) -> int | None:
    if not isinstance(data, dict) or "code" not in data:
        return None
    code = data.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        return int(code)
    return code if isinstance(code, int) else None


def build_imweb_auth_headers(access_token: str) -> dict[str, str]:
    header_name = os.getenv("IMWEB_ACCESS_TOKEN_HEADER", "access-token").strip() or "access-token"
    auth_scheme = os.getenv("IMWEB_ACCESS_TOKEN_SCHEME", "").strip()
    if header_name.lower() == "authorization":
        value = f"{auth_scheme} {access_token}".strip() if auth_scheme else access_token
    else:
        value = access_token
    return {header_name: value, "Content-Type": "application/json"}


def is_order_in_range(order: dict[str, Any], start: datetime, end: datetime) -> bool:
    timestamp = order_timestamp(order)
    if not timestamp:
        return False
    ordered_at = datetime.fromtimestamp(timestamp, KST)
    return start <= ordered_at <= end


def page_signature(orders: list[dict[str, Any]]) -> tuple[str, str, int] | None:
    if not orders:
        return None
    first = str(deep_get(orders[0], ["order_code", "order_no", "id"], ""))
    last = str(deep_get(orders[-1], ["order_code", "order_no", "id"], ""))
    return first, last, len(orders)


class ImwebClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("IMWEB_BASE_URL", "https://api.imweb.me").rstrip("/")
        self.api_key = get_required_env("IMWEB_API_KEY")
        self.secret_key = get_required_env("IMWEB_SECRET_KEY")
        self.shop_code = os.getenv("IMWEB_SHOP_CODE", "").strip()
        self.auth_endpoint = os.getenv("IMWEB_AUTH_ENDPOINT", "/v2/auth")
        self.orders_endpoint = os.getenv("IMWEB_ORDERS_ENDPOINT", "/v2/shop/orders")
        self.prod_orders_endpoint_template = os.getenv(
            "IMWEB_PROD_ORDERS_ENDPOINT_TEMPLATE",
            "/v2/shop/orders/{order_no}/prod-orders",
        )
        self.timeout = env_int("REQUEST_TIMEOUT_SECONDS", 30)
        self.page_size = env_int("IMWEB_PAGE_SIZE", 100)
        self.max_pages = env_int("IMWEB_MAX_PAGES", 100)
        self.access_token: str | None = None

    def authenticate(self) -> None:
        url = f"{self.base_url}{self.auth_endpoint}"
        payload = {
            "key": self.api_key,
            "secret": self.secret_key,
        }
        if self.shop_code:
            payload["shop_code"] = self.shop_code

        logging.info("아임웹 API 인증 요청: %s", url)
        response = requests.post(url, json=payload, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logging.exception("아임웹 인증 실패: status=%s body=%s", response.status_code, response.text[:1000])
            raise

        data = response.json()
        raise_for_imweb_api_error(data)
        token = deep_get(data, ["access_token", "token", "data.access_token", "data.token", "msg.access_token"])
        if not token:
            raise RuntimeError(f"아임웹 인증 응답에서 access token을 찾지 못했습니다: {data}")
        self.access_token = str(token)

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("아임웹 인증이 먼저 필요합니다.")
        return build_imweb_auth_headers(self.access_token)

    def fetch_orders(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        all_orders: list[dict[str, Any]] = []
        seen_page_signatures: set[tuple[str, str, int]] = set()
        date_format = os.getenv("IMWEB_DATE_FORMAT", "timestamp").lower()
        start_value: Any = int(start.timestamp()) if date_format == "timestamp" else start.isoformat()
        end_value: Any = int(end.timestamp()) if date_format == "timestamp" else end.isoformat()

        for page in range(1, self.max_pages + 1):
            params = {
                os.getenv("IMWEB_PARAM_START_DATE", "start_date"): start_value,
                os.getenv("IMWEB_PARAM_END_DATE", "end_date"): end_value,
                os.getenv("IMWEB_PARAM_PAGE", "offset"): page,
                os.getenv("IMWEB_PARAM_LIMIT", "limit"): self.page_size,
            }
            url = f"{self.base_url}{self.orders_endpoint}"
            data: Any = None
            for attempt in range(1, env_int("IMWEB_TOO_MANY_REQUEST_RETRIES", 3) + 2):
                logging.info("아임웹 주문 조회: page=%s attempt=%s", page, attempt)
                response = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
                try:
                    response.raise_for_status()
                except requests.HTTPError:
                    logging.exception("아임웹 주문 조회 실패: status=%s body=%s", response.status_code, response.text[:1000])
                    raise

                data = response.json()
                if imweb_api_code(data) != -7:
                    break
                sleep_seconds = float(os.getenv("IMWEB_TOO_MANY_REQUEST_SLEEP_SECONDS", "10"))
                logging.warning("아임웹 TOO MANY REQUEST 응답. %.1f초 대기 후 재시도합니다.", sleep_seconds)
                time.sleep(sleep_seconds)

            raise_for_imweb_api_error(data)
            if env_bool("SAVE_SAMPLE_IMWEB_RESPONSE", False) and page == 1:
                SAMPLE_RESPONSE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logging.info("아임웹 샘플 응답 저장: %s", SAMPLE_RESPONSE_FILE)

            orders = self._extract_orders(data)
            signature = page_signature(orders)
            if signature and signature in seen_page_signatures:
                logging.warning("중복 페이지 감지: page=%s. 같은 주문 목록이 반복되어 조회를 중단합니다.", page)
                break
            if signature:
                seen_page_signatures.add(signature)
            all_orders.extend(orders)
            timestamps = [order_timestamp(order) for order in orders if order_timestamp(order)]
            if timestamps and max(timestamps) < int(start.timestamp()):
                logging.info("기준일보다 오래된 주문 페이지에 도달해 조회를 중단합니다: page=%s", page)
                break
            if len(orders) < self.page_size:
                break
            time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5")))

        filtered_orders = [order for order in all_orders if is_order_in_range(order, start, end)]
        if len(filtered_orders) != len(all_orders):
            logging.warning("기준일 로컬 필터 적용: 원본 %s건 -> 기준일 주문 %s건", len(all_orders), len(filtered_orders))
        logging.info("아임웹 주문 조회 완료: %s건", len(filtered_orders))
        return filtered_orders

    def fetch_prod_orders(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        order_no = str(deep_get(order, ["order_no"], "")).strip()
        if not order_no:
            return []

        endpoint = self.prod_orders_endpoint_template.format(order_no=order_no)
        url = f"{self.base_url}{endpoint}"
        data: Any = None
        for attempt in range(1, env_int("IMWEB_TOO_MANY_REQUEST_RETRIES", 3) + 2):
            logging.info("아임웹 주문상품 조회: order_no=%s attempt=%s", order_no, attempt)
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
            try:
                response.raise_for_status()
            except requests.HTTPError:
                logging.exception("아임웹 주문상품 조회 실패: status=%s body=%s", response.status_code, response.text[:1000])
                raise

            data = response.json()
            if imweb_api_code(data) != -7:
                break
            sleep_seconds = float(os.getenv("IMWEB_TOO_MANY_REQUEST_SLEEP_SECONDS", "10"))
            logging.warning("아임웹 TOO MANY REQUEST 응답. %.1f초 대기 후 재시도합니다.", sleep_seconds)
            time.sleep(sleep_seconds)

        raise_for_imweb_api_error(data)
        prod_orders = deep_get(data, ["data"], [])
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

    def fetch_order_items(self, orders: list[dict[str, Any]]) -> list[OrderItem]:
        all_items: list[OrderItem] = []
        sleep_seconds = float(os.getenv("REQUEST_SLEEP_SECONDS", "1.5"))
        for index, order in enumerate(orders, start=1):
            logging.info("주문상품 집계 준비: %s/%s", index, len(orders))
            prod_orders = self.fetch_prod_orders(order)
            all_items.extend(extract_items_from_prod_orders(str(deep_get(order, ["order_no"], "")), prod_orders))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        return all_items

    @staticmethod
    def _extract_orders(data: Any) -> list[dict[str, Any]]:
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


def extract_items_from_orders(orders: list[dict[str, Any]]) -> list[OrderItem]:
    excluded_statuses = split_csv_env(
        "EXCLUDED_ORDER_STATUSES",
        "cancel,canceled,cancelled,refund,refunded,return,returned,입금대기,취소,환불,반품",
    )
    items: list[OrderItem] = []

    for order in orders:
        order_status = normalize_status(
            deep_get(order, ["status", "order_status", "payment_status", "pay_status", "order.status", "payment.status", "delivery.status"])
        )
        if order_status in excluded_statuses:
            continue

        order_id = str(deep_get(order, ["order_no", "order_id", "id", "uid"], ""))
        product_rows = deep_get(
            order,
            ["items", "products", "order_items", "order_products", "prod_list", "product_list"],
            [],
        )
        if isinstance(product_rows, dict):
            product_rows = list(product_rows.values())
        if not isinstance(product_rows, list):
            product_rows = []

        for row in product_rows:
            if not isinstance(row, dict):
                continue
            item_status = normalize_status(deep_get(row, ["status", "item_status", "order_status"]))
            if item_status in excluded_statuses:
                continue

            product_name = str(
                deep_get(row, ["product_name", "prod_name", "name", "goods_name", "product.name"], "상품명 미확인")
            ).strip()
            option_name = str(
                deep_get(row, ["option_name", "option", "option_text", "options", "option.title"], "옵션 없음")
            ).strip()
            if option_name in {"", "None", "null"}:
                option_name = "옵션 없음"

            quantity = to_int(deep_get(row, ["quantity", "qty", "count", "order_count", "ea"], 1), 1)
            amount = to_int(
                deep_get(
                    row,
                    [
                        "total_price",
                        "payment_price",
                        "price_total",
                        "sale_price",
                        "amount",
                        "product_price",
                        "price",
                        "payment.payment_amount",
                        "payment.total_price",
                    ],
                    0,
                )
            )
            unit_price = to_int(deep_get(row, ["unit_price", "price", "product_price"], 0))
            if amount == 0 and unit_price:
                amount = unit_price * quantity

            items.append(
                OrderItem(
                    order_id=order_id,
                    product_name=product_name,
                    option_name=option_name,
                    quantity=quantity,
                    amount=amount,
                )
            )

    return items


def item_payment_amount(item: dict[str, Any]) -> int:
    payment = deep_get(item, ["payment"], {})
    gross = to_int(deep_get(item, ["payment.price", "price", "total_price"], 0))
    discount = sum(
        to_int(deep_get(payment, [key], 0))
        for key in ["price_sale", "coupon", "membership_discount"]
    )
    return max(gross - discount, 0)


def option_name_from_item(item: dict[str, Any]) -> str:
    options = deep_get(item, ["options"], [])
    names: list[str] = []
    if isinstance(options, list):
        for group in options:
            details = group if isinstance(group, list) else [group]
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                value_names = detail.get("value_name_list")
                if isinstance(value_names, list):
                    names.extend(str(value).strip() for value in value_names if str(value).strip())
                elif detail.get("value_name"):
                    names.append(str(detail["value_name"]).strip())
    return " / ".join(names) if names else "옵션 없음"


def extract_items_from_prod_orders(order_id: str, prod_orders: list[dict[str, Any]]) -> list[OrderItem]:
    excluded_statuses = split_csv_env(
        "EXCLUDED_ORDER_STATUSES",
        "cancel,canceled,cancelled,refund,refunded,return,returned,pay_wait,payment_wait,deposit_wait,입금대기,취소,환불,반품",
    )
    items: list[OrderItem] = []
    for prod_order in prod_orders:
        status = normalize_status(deep_get(prod_order, ["status"], ""))
        if status in excluded_statuses:
            continue

        prod_items = deep_get(prod_order, ["items"], [])
        if not isinstance(prod_items, list):
            continue
        for item in prod_items:
            if not isinstance(item, dict):
                continue
            product_name = str(deep_get(item, ["prod_name", "product_name", "name"], "상품명 미확인")).strip()
            quantity = to_int(deep_get(item, ["payment.count", "count", "quantity", "qty"], 1), 1)
            amount = item_payment_amount(item)
            items.append(
                OrderItem(
                    order_id=order_id,
                    product_name=product_name,
                    option_name=option_name_from_item(item),
                    quantity=quantity,
                    amount=amount,
                )
            )
    return items


def aggregate_report(items: list[OrderItem], target_date: str) -> dict[str, Any]:
    by_product: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"amount": 0, "quantity": 0, "order_ids": set(), "options": defaultdict(lambda: {"amount": 0, "quantity": 0})}
    )

    for item in items:
        product = by_product[item.product_name]
        product["amount"] += item.amount
        product["quantity"] += item.quantity
        if item.order_id:
            product["order_ids"].add(item.order_id)
        product["options"][item.option_name]["amount"] += item.amount
        product["options"][item.option_name]["quantity"] += item.quantity

    ranking = sorted(
        by_product.items(),
        key=lambda pair: (pair[1]["quantity"], pair[1]["amount"]),
        reverse=True,
    )[:10]
    top_products = []
    for product_name, stats in ranking:
        options = sorted(
            stats["options"].items(),
            key=lambda pair: (pair[1]["quantity"], pair[1]["amount"]),
            reverse=True,
        )
        top_products.append(
            {
                "product_name": product_name,
                "amount": stats["amount"],
                "quantity": stats["quantity"],
                "options": [
                    {"option_name": option_name, "amount": option_stats["amount"], "quantity": option_stats["quantity"]}
                    for option_name, option_stats in options
                ],
            }
        )

    return {
        "target_date": target_date,
        "total_order_count": len({item.order_id for item in items if item.order_id}),
        "total_item_count": sum(item.quantity for item in items),
        "total_amount": sum(item.amount for item in items),
        "top_products": top_products,
    }


def won(value: int) -> str:
    return f"{value:,}원"


def build_message(report: dict[str, Any]) -> str:
    lines = [
        "[전일 판매 리포트]",
        f"기준일: {report['target_date']}",
        "",
        f"총 구매 건수: {report['total_order_count']:,}건",
        f"총 구매 금액: {won(report['total_amount'])}",
        "",
        "상품별 판매량 TOP 10",
    ]

    if not report["top_products"]:
        lines.append("")
        lines.append("집계 대상 주문이 없습니다.")
        return "\n".join(lines)

    for idx, product in enumerate(report["top_products"], start=1):
        lines.extend(
            [
                "",
                f"{idx}. {product['product_name']}",
                f"- 판매 수량: {product['quantity']:,}개",
                f"- 구매 금액: {won(product['amount'])}",
                "- 옵션별 판매 순위:",
            ]
        )
        for option in product["options"]:
            lines.append(f"  · {option['option_name']}: {option['quantity']:,}개 / {won(option['amount'])}")

    return "\n".join(lines)


def split_message(message: str, max_chars: int) -> list[str]:
    if len(message) <= max_chars:
        return [message]

    chunks: list[str] = []
    current = ""
    for line in message.splitlines():
        candidate = line if current == "" else f"{current}\n{line}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= max_chars:
            current = line
        else:
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            current = ""
    if current:
        chunks.append(current)
    return chunks


class KakaoClient:
    TOKEN_URL = "https://kauth.kakao.com/oauth/token"
    MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

    def __init__(self) -> None:
        self.rest_api_key = get_required_env("KAKAO_REST_API_KEY")
        self.refresh_token = get_required_env("KAKAO_REFRESH_TOKEN")
        self.timeout = env_int("REQUEST_TIMEOUT_SECONDS", 30)

    def refresh_access_token(self) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
        if client_secret:
            data["client_secret"] = client_secret

        logging.info("카카오 access_token 갱신 요청")
        response = requests.post(self.TOKEN_URL, data=data, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            print(f"response.status_code: {response.status_code}")
            print(f"response.text: {response.text}")
            logging.exception("카카오 토큰 갱신 실패: status=%s body=%s", response.status_code, response.text[:1000])
            raise

        token = response.json().get("access_token")
        if not token:
            raise RuntimeError(f"카카오 토큰 응답에서 access_token을 찾지 못했습니다: {response.text[:1000]}")
        return str(token)

    def send_to_me(self, message: str) -> None:
        access_token = self.refresh_access_token()
        max_chars = env_int("KAKAO_MESSAGE_MAX_CHARS", 900)
        chunks = split_message(message, max_chars)

        for idx, chunk in enumerate(chunks, start=1):
            text = chunk if len(chunks) == 1 else f"{chunk}\n\n({idx}/{len(chunks)})"
            template_object = {
                "object_type": "text",
                "text": text,
                "link": {
                    "web_url": os.getenv("KAKAO_MESSAGE_LINK_URL", "https://developers.kakao.com"),
                    "mobile_web_url": os.getenv("KAKAO_MESSAGE_LINK_URL", "https://developers.kakao.com"),
                },
                "button_title": os.getenv("KAKAO_MESSAGE_BUTTON_TITLE", "확인"),
            }
            headers = {"Authorization": f"Bearer {access_token}"}
            payload = {"template_object": json.dumps(template_object, ensure_ascii=False)}
            logging.info("카카오톡 나에게 보내기: %s/%s", idx, len(chunks))
            response = requests.post(self.MEMO_URL, headers=headers, data=payload, timeout=self.timeout)
            try:
                response.raise_for_status()
            except requests.HTTPError:
                logging.exception("카카오톡 발송 실패: status=%s body=%s", response.status_code, response.text[:1000])
                raise
            time.sleep(float(os.getenv("REQUEST_SLEEP_SECONDS", "0.2")))


class NotionClient:
    API_BASE_URL = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"

    def __init__(self) -> None:
        self.token = get_required_env("NOTION_TOKEN")
        self.database_id = get_required_env("NOTION_DATABASE_ID")
        self.timeout = env_int("REQUEST_TIMEOUT_SECONDS", 30)
        self.database: dict[str, Any] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": self.NOTION_VERSION,
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.API_BASE_URL}{path}"
        response = requests.request(method, url, headers=self._headers(), timeout=self.timeout, **kwargs)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logging.exception("노션 API 실패: status=%s body=%s", response.status_code, response.text[:1000])
            raise
        if not response.text:
            return {}
        return response.json()

    def get_database(self) -> dict[str, Any]:
        if self.database is None:
            self.database = self._request("GET", f"/databases/{self.database_id}")
        return self.database

    def property_name_by_type(self, prop_type: str, preferred_names: list[str]) -> str | None:
        properties = self.get_database().get("properties", {})
        for name in preferred_names:
            prop = properties.get(name)
            if isinstance(prop, dict) and prop.get("type") == prop_type:
                return name
        for name, prop in properties.items():
            if isinstance(prop, dict) and prop.get("type") == prop_type:
                return str(name)
        return None

    def title_property_name(self) -> str:
        configured = os.getenv("NOTION_PROP_TITLE", "").strip()
        found = self.property_name_by_type("title", [configured, "이름", "Name", "제목", "Title"])
        if not found:
            raise RuntimeError("노션 데이터베이스에서 title 속성을 찾지 못했습니다.")
        return found

    def date_property_name(self) -> str | None:
        configured = os.getenv("NOTION_PROP_DATE", "").strip()
        return self.property_name_by_type("date", [configured, "기준일", "Date", "날짜"])

    def number_property_name(self, env_name: str, preferred_names: list[str]) -> str | None:
        configured = os.getenv(env_name, "").strip()
        return self.property_name_by_type("number", [configured, *preferred_names])

    def query_page(self, target_date: str, title: str) -> str | None:
        date_prop = self.date_property_name()
        title_prop = self.title_property_name()
        if date_prop:
            filter_object: dict[str, Any] = {"property": date_prop, "date": {"equals": target_date}}
        else:
            filter_object = {"property": title_prop, "title": {"equals": title}}

        result = self._request(
            "POST",
            f"/databases/{self.database_id}/query",
            json={"filter": filter_object, "page_size": 1},
        )
        results = result.get("results", [])
        if results:
            return str(results[0].get("id"))
        return None

    def report_properties(self, report: dict[str, Any], title: str) -> dict[str, Any]:
        properties: dict[str, Any] = {
            self.title_property_name(): {"title": [{"text": {"content": title}}]},
        }

        date_prop = self.date_property_name()
        if date_prop:
            properties[date_prop] = {"date": {"start": report["target_date"]}}

        number_mappings = [
            ("NOTION_PROP_TOTAL_ORDERS", ["총 구매 건수", "주문 수", "Orders"], report["total_order_count"]),
            ("NOTION_PROP_TOTAL_QUANTITY", ["총 판매 수량", "판매 수량", "Quantity"], report["total_item_count"]),
            ("NOTION_PROP_TOTAL_AMOUNT", ["총 상품 구매 금액", "총 구매 금액", "Amount"], report["total_amount"]),
        ]
        for env_name, preferred_names, value in number_mappings:
            prop_name = self.number_property_name(env_name, preferred_names)
            if prop_name:
                properties[prop_name] = {"number": value}

        return properties

    def build_blocks(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        top_product = report["top_products"][0] if report["top_products"] else None
        top_option = top_product["options"][0] if top_product and top_product["options"] else None

        blocks: list[dict[str, Any]] = [
            self.callout_block(
                "오늘의 판매 요약\n"
                f"기준일: {report['target_date']}\n"
                f"총 구매 건수: {report['total_order_count']:,}건\n"
                f"총 판매 수량: {report['total_item_count']:,}개\n"
                f"총 상품 구매 금액: {won(report['total_amount'])}"
                + (
                    f"\n판매량 1위: {top_product['product_name']} {top_product['quantity']:,}개"
                    if top_product
                    else ""
                )
                + (
                    f"\n1위 상품 인기 옵션: {top_option['option_name']} {top_option['quantity']:,}개"
                    if top_option
                    else ""
                )
            )
        ]

        if not report["top_products"]:
            blocks.append(self.paragraph_block("집계 대상 주문이 없습니다."))
            return blocks

        blocks.append(self.heading_block("상품 TOP 10 요약", 2))
        blocks.append(self.summary_table_block(report["top_products"]))
        blocks.append(self.heading_block("옵션별 상세 판매 순위", 2))

        for idx, product in enumerate(report["top_products"], start=1):
            blocks.append(self.heading_block(f"{idx}. {product['product_name']}", 3))
            blocks.append(self.paragraph_block(f"판매 수량: {product['quantity']:,}개\n구매 금액: {won(product['amount'])}"))
            blocks.append(self.paragraph_block("옵션별 판매 순위"))
            for option in product["options"]:
                blocks.append(
                    self.bullet_block(
                        f"{option['option_name']}: {option['quantity']:,}개 / {won(option['amount'])}"
                    )
                )
        return blocks

    @staticmethod
    def rich_text(text: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": {"content": text[:2000]}}]

    def paragraph_block(self, text: str) -> dict[str, Any]:
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": self.rich_text(text)}}

    def callout_block(self, text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": self.rich_text(text),
                "icon": {"type": "emoji", "emoji": "📌"},
            },
        }

    def bullet_block(self, text: str) -> dict[str, Any]:
        return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": self.rich_text(text)}}

    def heading_block(self, text: str, level: int) -> dict[str, Any]:
        block_type = f"heading_{level}"
        return {"object": "block", "type": block_type, block_type: {"rich_text": self.rich_text(text)}}

    def table_cell(self, text: str) -> list[dict[str, Any]]:
        return self.rich_text(text)

    def table_row_block(self, cells: list[str]) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [self.table_cell(cell) for cell in cells]},
        }

    def summary_table_block(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        rows = [
            self.table_row_block(["순위", "상품명", "판매 수량", "구매 금액", "1위 옵션"]),
        ]
        for idx, product in enumerate(products, start=1):
            top_option = product["options"][0] if product["options"] else None
            top_option_text = (
                f"{top_option['option_name']} ({top_option['quantity']:,}개)"
                if top_option
                else "-"
            )
            rows.append(
                self.table_row_block(
                    [
                        str(idx),
                        str(product["product_name"]),
                        f"{product['quantity']:,}개",
                        won(product["amount"]),
                        top_option_text,
                    ]
                )
            )
        return {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": 5,
                "has_column_header": True,
                "has_row_header": False,
                "children": rows,
            },
        }

    def clear_children(self, page_id: str) -> None:
        cursor: str | None = None
        while True:
            path = f"/blocks/{page_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            result = self._request("GET", path)
            for block in result.get("results", []):
                block_id = block.get("id")
                if block_id:
                    self._request("DELETE", f"/blocks/{block_id}")
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")

    def append_children(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        for start in range(0, len(blocks), 100):
            self._request("PATCH", f"/blocks/{page_id}/children", json={"children": blocks[start : start + 100]})

    def upsert_report(self, report: dict[str, Any]) -> str:
        title = f"{report['target_date']} 전일 판매 리포트"
        properties = self.report_properties(report, title)
        blocks = self.build_blocks(report)
        page_id = self.query_page(report["target_date"], title)

        if page_id:
            logging.info("노션 리포트 페이지 업데이트: %s", page_id)
            self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})
            self.clear_children(page_id)
            self.append_children(page_id, blocks)
            return page_id

        logging.info("노션 리포트 페이지 생성")
        result = self._request(
            "POST",
            "/pages",
            json={
                "parent": {"database_id": self.database_id},
                "properties": properties,
                "children": blocks[:100],
            },
        )
        page_id = str(result.get("id"))
        if len(blocks) > 100:
            self.append_children(page_id, blocks[100:])
        return page_id


def run() -> int:
    setup_logging()
    load_dotenv(BASE_DIR / ".env")

    try:
        start, end, target_date = yesterday_range_kst()
        logging.info("리포트 시작: 기준일=%s 조회범위=%s ~ %s", target_date, start.isoformat(), end.isoformat())

        imweb = ImwebClient()
        imweb.authenticate()
        orders = imweb.fetch_orders(start, end)
        items = imweb.fetch_order_items(orders)
        if not items:
            logging.warning("주문상품 API에서 상품을 찾지 못해 주문 목록 응답에서 상품 추출을 시도합니다.")
            items = extract_items_from_orders(orders)
        report = aggregate_report(items, target_date)
        message = build_message(report)

        print()
        print(message)
        print()

        if env_bool("SEND_NOTION", False):
            page_id = NotionClient().upsert_report(report)
            logging.info("노션 저장 완료: page_id=%s", page_id)
        else:
            logging.info("SEND_NOTION=false 이므로 노션 저장을 건너뜁니다.")

        if not env_bool("SEND_KAKAO", False):
            logging.info("SEND_KAKAO=false 이므로 카카오톡 관련 코드를 실행하지 않습니다.")
        elif env_bool("DRY_RUN", True):
            logging.info("DRY_RUN=true 이므로 카카오톡 발송을 건너뜁니다.")
        else:
            KakaoClient().send_to_me(message)
            logging.info("카카오톡 발송 완료")

        logging.info("리포트 완료")
        return 0
    except Exception as exc:
        logging.exception("리포트 실행 실패: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
