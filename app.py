import json
import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests
from flask import Flask, jsonify, request


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("dntrade-iban")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# ----------------------------
# DNTrade
# ----------------------------

DNTRADE_API_URL = os.environ.get(
    "DNTRADE_API_URL",
    "https://api.dntrade.com.ua",
).rstrip("/")

DNTRADE_TOKEN = os.environ.get(
    "DNTRADE_TOKEN",
    "",
).strip()


# ----------------------------
# IBAN Oplata
# ----------------------------

IBAN_OPLATA_API_URL = os.environ.get(
    "IBAN_OPLATA_API_URL",
    "https://api.ibanoplata.com",
).rstrip("/")

IBAN_ENDPOINT = os.environ.get(
    "IBAN_ENDPOINT",
    "/v2/iban-invoice",
).strip()

IBAN_TOKEN = os.environ.get(
    "IBAN_TOKEN",
    "",
).strip()

IBAN_ORGANIZATION_NAME = os.environ.get(
    "IBAN_ORGANIZATION_NAME",
    "",
)

IBAN_IDENTIFICATION_CODE = os.environ.get(
    "IBAN_IDENTIFICATION_CODE",
    "",
)

IBAN_ACCOUNT = os.environ.get(
    "IBAN_ACCOUNT",
    "",
)


# ----------------------------
# Order statuses
# ----------------------------

STATUS_FULL_PREPAY = int(
    os.environ.get(
        "STATUS_PREPAY_FULL",
        "15",
    )
)

STATUS_PARTIAL_PREPAY = int(
    os.environ.get(
        "STATUS_PREPAY_PARTIAL",
        "16",
    )
)

STATUS_WAITING_PAYMENT = int(
    os.environ.get(
        "STATUS_WAITING_PAYMENT",
        "1",
    )
)


# ----------------------------
# Partial prepayment
# ----------------------------

try:
    PREPAYMENT_PARTIAL_AMOUNT = Decimal(
        os.environ.get(
            "PREPAYMENT_PARTIAL_AMOUNT",
            "200",
        )
    )
except InvalidOperation:
    PREPAYMENT_PARTIAL_AMOUNT = Decimal("200")


# ----------------------------
# HTTP
# ----------------------------

CONNECT_TIMEOUT = int(
    os.environ.get(
        "CONNECT_TIMEOUT",
        "10",
    )
)

READ_TIMEOUT = int(
    os.environ.get(
        "READ_TIMEOUT",
        "30",
    )
)

REQUEST_TIMEOUT = (
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
)


# ----------------------------
# Optional cron protection
# ----------------------------

CRON_SECRET = os.environ.get(
    "CRON_SECRET",
    "",
).strip()


# ============================================================
# HEADERS
# ============================================================

def get_dntrade_headers() -> dict:
    """
    Заголовки DNTrade.

    DNTrade використовує ApiKey.
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if DNTRADE_TOKEN:
        headers["ApiKey"] = DNTRADE_TOKEN

    return headers


def get_iban_headers() -> dict:
    """
    Заголовки IBAN Oplata.

    Зберігаємо кілька варіантів авторизації,
    які використовувалися вашим робочим кодом.
    """

    token = IBAN_TOKEN

    return {
        "X-API-KEY": token,
        "ApiKey": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ============================================================
# GENERAL HTTP HELPERS
# ============================================================

def safe_response_text(
    response: requests.Response,
    max_length: int = 10000,
) -> str:
    """
    Безпечне отримання response body для логів.
    """

    try:
        text = response.text
    except Exception:
        return "<unable to read response body>"

    if len(text) > max_length:
        return text[:max_length] + " ... [TRUNCATED]"

    return text


def log_http_response(
    prefix: str,
    response: requests.Response,
) -> None:
    """
    Детальне логування HTTP-відповіді.
    """

    logger.info(
        "%s HTTP %s",
        prefix,
        response.status_code,
    )

    logger.info(
        "%s Response body: %s",
        prefix,
        safe_response_text(response),
    )


# ============================================================
# MONEY
# ============================================================

def parse_money(value: Any) -> Decimal:
    """
    Перетворення значення в Decimal.

    Не використовуємо float для грошей.
    """

    if value is None:
        raise ValueError(
            "Money value is None"
        )

    try:
        amount = Decimal(
            str(value).replace(",", ".").strip()
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"Invalid money value: {value!r}"
        ) from exc

    return amount.quantize(
        Decimal("0.01")
    )


# ============================================================
# IBAN OPLATA
# ============================================================

def create_iban_payment_link(
    order_number: Any,
    amount: Decimal,
    description: str,
) -> Optional[str]:
    """
    Створює рахунок IBAN.

    Повертає payment URL або None.
    """

    url = (
        f"{IBAN_OPLATA_API_URL}"
        f"{IBAN_ENDPOINT}"
    )

    amount = amount.quantize(
        Decimal("0.01")
    )

    payload = {
        "organizationName": IBAN_ORGANIZATION_NAME,
        "identificationCode": IBAN_IDENTIFICATION_CODE,
        "iban": IBAN_ACCOUNT,

        # Передаємо число, а не float,
        # щоб не отримати 199.999999...
        "amount": float(amount),

        "paymentPurpose": description,

        "notes": (
            f"Замовлення №{order_number}"
        ),

        "clientNotes": (
            f"Оплата замовлення №{order_number}"
        ),

        "expirationHours": 24,
    }

    logger.info(
        "[IBAN] POST %s",
        url,
    )

    logger.info(
        "[IBAN] Order=%s amount=%s",
        order_number,
        amount,
    )

    logger.info(
        "[IBAN] Payload=%s",
        json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ),
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_iban_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        log_http_response(
            "[IBAN]",
            response,
        )

    except requests.Timeout:
        logger.exception(
            "[IBAN] Timeout для order=%s",
            order_number,
        )
        return None

    except requests.RequestException:
        logger.exception(
            "[IBAN] HTTP exception для order=%s",
            order_number,
        )
        return None

    except Exception:
        logger.exception(
            "[IBAN] Unknown exception для order=%s",
            order_number,
        )
        return None

    if response.status_code not in (
        200,
        201,
    ):
        logger.error(
            "[IBAN] API error: HTTP %s | %s",
            response.status_code,
            safe_response_text(response),
        )
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error(
            "[IBAN] API повернув не JSON: %s",
            safe_response_text(response),
        )
        return None

    logger.info(
        "[IBAN] Parsed response=%s",
        json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        ),
    )

    if not isinstance(data, dict):
        logger.error(
            "[IBAN] Unexpected response type: %s",
            type(data).__name__,
        )
        return None

    payment_link = (
        data.get("ibanInvoiceUrl")
        or data.get("url")
        or data.get("paymentUrl")
        or data.get("payment_url")
    )

    if not payment_link:
        logger.error(
            "[IBAN] Рахунок створено, але URL "
            "не знайдений у відповіді."
        )

        logger.error(
            "[IBAN] Response=%s",
            data,
        )

        return None

    logger.info(
        "[IBAN] SUCCESS order=%s URL=%s",
        order_number,
        payment_link,
    )

    return str(payment_link)


# ============================================================
# DNTRADE - SET STATUS
# ============================================================

def change_dntrade_order_status(
    external_id: str,
    new_status_id: int,
) -> bool:
    """
    Зміна статусу через /orders/setstatus.

    external_id — GUID замовлення.

    У вашій поточній інтеграції саме цей запит уже працює,
    тому залишаємо формат id/external_id + status_id/status.
    """

    url = (
        f"{DNTRADE_API_URL}"
        f"/orders/setstatus"
    )

    if not external_id:
        logger.error(
            "[DNTrade Status] external_id порожній"
        )
        return False

    payload = {
        "id": external_id,
        "external_id": external_id,
        "status_id": new_status_id,
        "status": new_status_id,
    }

    logger.info(
        "[DNTrade Status] POST %s",
        url,
    )

    logger.info(
        "[DNTrade Status] external_id=%s new_status=%s",
        external_id,
        new_status_id,
    )

    logger.info(
        "[DNTrade Status] Payload=%s",
        json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        log_http_response(
            "[DNTrade Status]",
            response,
        )

    except requests.Timeout:
        logger.exception(
            "[DNTrade Status] Timeout"
        )
        return False

    except requests.RequestException:
        logger.exception(
            "[DNTrade Status] HTTP exception"
        )
        return False

    except Exception:
        logger.exception(
            "[DNTrade Status] Unknown exception"
        )
        return False

    if response.status_code in (
        200,
        201,
    ):
        logger.info(
            "[DNTrade Status] SUCCESS "
            "external_id=%s status=%s",
            external_id,
            new_status_id,
        )

        return True

    logger.error(
        "[DNTrade Status] ERROR "
        "HTTP=%s body=%s",
        response.status_code,
        safe_response_text(response),
    )

    return False


# ============================================================
# DNTRADE - UPDATE NOTE
# ============================================================

def update_dntrade_order_note(
    order_data: dict,
    payment_link: str,
) -> bool:
    """
    Записує payment URL у поле note через /orders/upload.

    ВАЖЛИВО:
    - external_id = GUID
    - number = номер замовлення
    - products = products із DNTrade
    - order_status тут НЕ змінюємо

    Статус змінюється окремим /orders/setstatus
    тільки після успішного update note.
    """

    url = (
        f"{DNTRADE_API_URL}"
        f"/orders/upload"
    )

    external_id = (
        order_data.get("external_id")
    )

    number = (
        order_data.get("number")
    )

    products = (
        order_data.get("products")
        or []
    )

    if not external_id:
        logger.error(
            "[DNTrade Note] "
            "external_id відсутній. "
            "Оновлення неможливе."
        )
        return False

    if not payment_link:
        logger.error(
            "[DNTrade Note] "
            "payment_link порожній."
        )
        return False

    # --------------------------------------------------------
    # Формуємо об'єкт замовлення.
    #
    # НЕ додаємо order_status.
    # Статус змінюється окремим endpoint.
    # --------------------------------------------------------

    updated_order = {
        "external_id": external_id,
        "number": number,
        "note": payment_link,
        "products": products,
    }

    payload = {
        "orders": [
            updated_order
        ]
    }

    logger.info(
        "[DNTrade Note] POST %s",
        url,
    )

    logger.info(
        "[DNTrade Note] "
        "order=%s external_id=%s",
        number,
        external_id,
    )

    logger.info(
        "[DNTrade Note] payment_link=%s",
        payment_link,
    )

    logger.info(
        "[DNTrade Note] products_count=%s",
        len(products),
    )

    logger.info(
        "[DNTrade Note] Payload=%s",
        json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ),
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        log_http_response(
            "[DNTrade Note]",
            response,
        )

    except requests.Timeout:
        logger.exception(
            "[DNTrade Note] Timeout "
            "order=%s",
            number,
        )
        return False

    except requests.RequestException:
        logger.exception(
            "[DNTrade Note] HTTP exception "
            "order=%s",
            number,
        )
        return False

    except Exception:
        logger.exception(
            "[DNTrade Note] Unknown exception "
            "order=%s",
            number,
        )
        return False

    if response.status_code not in (
        200,
        201,
    ):
        logger.error(
            "[DNTrade Note] FAILED "
            "order=%s external_id=%s "
            "HTTP=%s",
            number,
            external_id,
            response.status_code,
        )

        logger.error(
            "[DNTrade Note] API response=%s",
            safe_response_text(response),
        )

        return False

    # --------------------------------------------------------
    # Пробуємо розібрати відповідь
    # --------------------------------------------------------

    try:
        response_data = response.json()

        logger.info(
            "[DNTrade Note] Parsed response=%s",
            json.dumps(
                response_data,
                ensure_ascii=False,
                default=str,
            ),
        )

    except ValueError:
        logger.info(
            "[DNTrade Note] "
            "API response is not JSON."
        )

    logger.info(
        "[DNTrade Note] SUCCESS "
        "order=%s external_id=%s",
        number,
        external_id,
    )

    return True


# ============================================================
# DNTRADE - GET ORDERS
# ============================================================

def get_dntrade_orders() -> list:
    """
    GET /orders/list
    """

    url = (
        f"{DNTRADE_API_URL}"
        f"/orders/list"
    )

    params = {
        "limit": 50,
        "page": 1,
    }

    logger.info(
        "[DNTrade Orders] GET %s",
        url,
    )

    logger.info(
        "[DNTrade Orders] Params=%s",
        params,
    )

    try:
        response = requests.get(
            url,
            headers=get_dntrade_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        log_http_response(
            "[DNTrade Orders]",
            response,
        )

    except requests.Timeout:
        logger.exception(
            "[DNTrade Orders] Timeout"
        )
        raise RuntimeError(
            "DNTrade /orders/list timeout"
        )

    except requests.RequestException as exc:
        logger.exception(
            "[DNTrade Orders] HTTP exception"
        )
        raise RuntimeError(
            f"DNTrade /orders/list HTTP error: {exc}"
        ) from exc

    if response.status_code != 200:
        raise RuntimeError(
            "DNTrade /orders/list returned "
            f"HTTP {response.status_code}: "
            f"{safe_response_text(response)}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "DNTrade /orders/list returned invalid JSON"
        ) from exc

    logger.info(
        "[DNTrade Orders] Response parsed."
    )

    # --------------------------------------------------------
    # Визначаємо масив orders.
    # --------------------------------------------------------

    if isinstance(data, list):
        orders = data

    elif isinstance(data, dict):

        if isinstance(
            data.get("data"),
            list,
        ):
            orders = data["data"]

        elif isinstance(
            data.get("orders"),
            list,
        ):
            orders = data["orders"]

        elif isinstance(
            data.get("items"),
            list,
        ):
            orders = data["items"]

        elif isinstance(
            data.get("result"),
            list,
        ):
            orders = data["result"]

        else:
            logger.error(
                "[DNTrade Orders] "
                "Не знайдено масив orders/data/items."
            )

            logger.error(
                "[DNTrade Orders] data=%s",
                data,
            )

            raise RuntimeError(
                "Unexpected DNTrade /orders/list format"
            )

    else:
        raise RuntimeError(
            "Unexpected DNTrade /orders/list response type"
        )

    logger.info(
        "[DNTrade Orders] "
        "Отримано замовлень: %s",
        len(orders),
    )

    return orders


# ============================================================
# ORDER PROCESSING
# ============================================================

def process_single_order(
    order: dict,
) -> dict:
    """
    Обробка одного замовлення.

    Послідовність:

    1. Перевірити статус.
    2. Створити IBAN.
    3. Записати URL у note.
    4. Змінити status -> 1.
    """

    external_id = (
        order.get("external_id")
    )

    number = (
        order.get("number")
    )

    raw_status = (
        order.get("order_status")
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "[PROCESS] order=%s "
        "external_id=%s "
        "status=%s",
        number,
        external_id,
        raw_status,
    )

    # --------------------------------------------------------
    # external_id
    # --------------------------------------------------------

    if not external_id:
        logger.error(
            "[PROCESS] order=%s "
            "пропущено: немає external_id",
            number,
        )

        return {
            "number": number,
            "external_id": None,
            "success": False,
            "error": "missing external_id",
        }

    # --------------------------------------------------------
    # number
    # --------------------------------------------------------

    if number is None:
        logger.error(
            "[PROCESS] external_id=%s "
            "пропущено: немає number",
            external_id,
        )

        return {
            "number": None,
            "external_id": external_id,
            "success": False,
            "error": "missing number",
        }

    # --------------------------------------------------------
    # status
    # --------------------------------------------------------

    try:
        order_status = int(
            raw_status
        )
    except (
        TypeError,
        ValueError,
    ):
        logger.error(
            "[PROCESS] order=%s "
            "invalid order_status=%r",
            number,
            raw_status,
        )

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": "invalid order_status",
        }

    # --------------------------------------------------------
    # status 15 / 16
    # --------------------------------------------------------

    if order_status not in (
        STATUS_FULL_PREPAY,
        STATUS_PARTIAL_PREPAY,
    ):
        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "skipped": True,
            "reason": (
                f"status {order_status} "
                "is not payable"
            ),
        }

    # --------------------------------------------------------
    # total_price
    # --------------------------------------------------------

    try:
        total_price = parse_money(
            order.get("total_price")
        )

    except ValueError as exc:
        logger.error(
            "[PROCESS] order=%s "
            "invalid total_price: %s",
            number,
            exc,
        )

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": str(exc),
        }

    if total_price <= 0:
        logger.error(
            "[PROCESS] order=%s "
            "total_price=%s <= 0",
            number,
            total_price,
        )

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": "total_price must be > 0",
        }

    # --------------------------------------------------------
    # payment amount
    # --------------------------------------------------------

    if order_status == STATUS_FULL_PREPAY:

        payment_amount = total_price

        description = (
            f"Повна оплата "
            f"замовлення №{number}"
        )

    else:

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата "
            f"за замовлення №{number}"
        )

    payment_amount = payment_amount.quantize(
        Decimal("0.01")
    )

    logger.info(
        "[PROCESS] order=%s "
        "total_price=%s "
        "payment_amount=%s",
        number,
        total_price,
        payment_amount,
    )

    # ========================================================
    # 1. CREATE IBAN INVOICE
    # ========================================================

    payment_link = create_iban_payment_link(
        order_number=number,
        amount=payment_amount,
        description=description,
    )

    if not payment_link:

        logger.error(
            "[PROCESS] order=%s "
            "IBAN payment link не створено",
            number,
        )

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": "IBAN invoice creation failed",
        }

    # ========================================================
    # 2. UPDATE NOTE
    # ========================================================

    logger.info(
        "[PROCESS] order=%s "
        "Записуємо payment URL у note...",
        number,
    )

    note_ok = update_dntrade_order_note(
        order_data=order,
        payment_link=payment_link,
    )

    if not note_ok:

        logger.error(
            "[PROCESS] order=%s "
            "NOTE UPDATE FAILED.",
            number,
        )

        # Дуже важливо:
        # статус НЕ змінюємо.
        #
        # Інакше замовлення стане "Очікує оплату",
        # але в ньому не буде payment URL.

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": (
                "DNTrade note update failed"
            ),
            "payment_link": payment_link,
            "status_changed_to_1": False,
            "note_updated": False,
        }

    # ========================================================
    # 3. CHANGE STATUS -> 1
    # ========================================================

    logger.info(
        "[PROCESS] order=%s "
        "NOTE SUCCESS. "
        "Changing status -> %s...",
        number,
        STATUS_WAITING_PAYMENT,
    )

    status_ok = change_dntrade_order_status(
        external_id=external_id,
        new_status_id=STATUS_WAITING_PAYMENT,
    )

    if not status_ok:

        logger.error(
            "[PROCESS] order=%s "
            "STATUS UPDATE FAILED.",
            number,
        )

        return {
            "number": number,
            "external_id": external_id,
            "success": False,
            "error": (
                "DNTrade status update failed"
            ),
            "payment_link": payment_link,
            "status_changed_to_1": False,
            "note_updated": True,
        }

    # ========================================================
    # SUCCESS
    # ========================================================

    logger.info(
        "[PROCESS] SUCCESS "
        "order=%s external_id=%s "
        "amount=%s status=%s -> %s",
        number,
        external_id,
        payment_amount,
        order_status,
        STATUS_WAITING_PAYMENT,
    )

    return {
        "number": number,
        "external_id": external_id,
        "success": True,
        "order_status_before": order_status,
        "order_status_after": STATUS_WAITING_PAYMENT,
        "amount": str(payment_amount),
        "payment_link": payment_link,
        "note_updated": True,
        "status_changed_to_1": True,
    }


# ============================================================
# CRON PROCESS
# ============================================================

@app.route(
    "/cron/process",
    methods=["GET", "POST"],
)
def process_dntrade_orders():
    """
    Основний endpoint для Render Cron.
    """

    # --------------------------------------------------------
    # Optional authorization
    # --------------------------------------------------------

    if CRON_SECRET:

        supplied_secret = (
            request.headers.get(
                "X-Cron-Secret",
                "",
            )
        )

        if supplied_secret != CRON_SECRET:

            logger.warning(
                "[CRON] Unauthorized request"
            )

            return jsonify({
                "status": "error",
                "error": "Unauthorized",
            }), 401

    logger.info(
        "=================================================="
    )

    logger.info(
        "[CRON] START processing"
    )

    try:

        orders = get_dntrade_orders()

        result = {
            "status": "success",
            "total_orders": len(orders),
            "processed_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "orders": [],
        }

        # ----------------------------------------------------
        # Process each order
        # ----------------------------------------------------

        for order in orders:

            if not isinstance(
                order,
                dict,
            ):

                logger.error(
                    "[CRON] Order is not object: %r",
                    order,
                )

                result[
                    "error_count"
                ] += 1

                continue

            item_result = process_single_order(
                order
            )

            result[
                "orders"
            ].append(item_result)

            if item_result.get(
                "skipped"
            ):
                result[
                    "skipped_count"
                ] += 1

            elif item_result.get(
                "success"
            ):
                result[
                    "processed_count"
                ] += 1

            else:
                result[
                    "error_count"
                ] += 1

        logger.info(
            "[CRON] FINISH "
            "total=%s processed=%s "
            "skipped=%s errors=%s",
            result["total_orders"],
            result["processed_count"],
            result["skipped_count"],
            result["error_count"],
        )

        return jsonify(result), 200

    except Exception as exc:

        logger.exception(
            "[CRON] FATAL ERROR"
        )

        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/",
    methods=["GET", "HEAD"],
)
def index():

    return jsonify({
        "status": "DNTrade processor is active",
        "service": "DNTrade -> IBAN Oplata",
    }), 200


@app.route(
    "/health",
    methods=["GET", "HEAD"],
)
def health():

    return jsonify({
        "status": "ok",
    }), 200


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000",
        )
    )

    logger.info(
        "Starting Flask on port %s",
        port,
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
