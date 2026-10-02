import logging
import os
from typing import Optional

import requests
from flask import Flask, jsonify


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

app = Flask(__name__)


# ============================================================
# DNTRADE
# ============================================================

DNTRADE_API_URL = os.environ.get(
    "DNTRADE_API_URL",
    "https://api.dntrade.com.ua",
).rstrip("/")

DNTRADE_TOKEN = os.environ.get(
    "DNTRADE_TOKEN",
    "",
).strip()

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ============================================================
# IBAN OPLATA
# ============================================================

IBAN_OPLATA_API_URL = os.environ.get(
    "IBAN_OPLATA_API_URL",
    "https://api.ibanoplata.com",
).rstrip("/")

IBAN_ENDPOINT = os.environ.get(
    "IBAN_ENDPOINT",
    "/v2/iban-invoice",
)

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


# ============================================================
# STATUSES
# ============================================================

STATUS_FULL_PREPAY = int(
    os.environ.get("STATUS_PREPAY_FULL", "15")
)

STATUS_PARTIAL_PREPAY = int(
    os.environ.get("STATUS_PREPAY_PARTIAL", "16")
)

STATUS_WAITING_PAYMENT = int(
    os.environ.get("STATUS_WAITING_PAYMENT", "1")
)

PREPAYMENT_PARTIAL_AMOUNT = float(
    os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", "200")
)


REQUEST_TIMEOUT = int(
    os.environ.get("REQUEST_TIMEOUT", "15")
)

ORDERS_PAGE_SIZE = 50


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", ".").strip()

        return float(value)

    except (TypeError, ValueError):
        return default


def get_iban_headers():
    token = (IBAN_TOKEN or "").strip()

    return {
        "X-API-KEY": token,
        "ApiKey": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def extract_orders(response_data):
    if isinstance(response_data, dict):

        if isinstance(response_data.get("orders"), list):
            return response_data["orders"]

        if isinstance(response_data.get("data"), list):
            return response_data["data"]

        if isinstance(response_data.get("data"), dict):

            if isinstance(
                response_data["data"].get("orders"),
                list,
            ):
                return response_data["data"]["orders"]

    if isinstance(response_data, list):
        return response_data

    return []


# ============================================================
# IBAN
# ============================================================

def create_iban_payment_link(
    order_number,
    amount: float,
    description: str,
) -> Optional[str]:

    url = f"{IBAN_OPLATA_API_URL}{IBAN_ENDPOINT}"

    payload = {
        "organizationName": IBAN_ORGANIZATION_NAME,
        "identificationCode": IBAN_IDENTIFICATION_CODE,
        "iban": IBAN_ACCOUNT,
        "amount": round(amount, 2),
        "paymentPurpose": description,
        "notes": f"Замовлення №{order_number}",
        "clientNotes": f"Оплата замовлення №{order_number}",
        "expirationHours": 24,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            headers=get_iban_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[IBAN] HTTP %s | %s",
            response.status_code,
            response.text,
        )

        if response.status_code not in (200, 201):
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        if not isinstance(data, dict):
            return None

        return (
            data.get("ibanInvoiceUrl")
            or data.get("url")
            or data.get("paymentUrl")
            or data.get("payment_url")
            or data.get("invoiceUrl")
            or data.get("invoice_url")
        )

    except Exception:
        logging.exception("[IBAN] Помилка")
        return None


# ============================================================
# DNTRADE UPLOAD
# ============================================================

def update_dntrade_order_note(
    order_data: dict,
    payment_link: str,
):
    """
    Діагностика POST /orders/upload.

    Поки НЕ змінюємо структуру payload навмання.
    Надсилаємо правильний за Swagger мінімальний варіант:

    {
        "id": "...",
        "personal_info": {
            "comment": "..."
        }
    }

    Функція повертає повну відповідь DNTrade,
    щоб побачити точну причину помилки.
    """

    order_id = order_data.get("external_id")

    if not order_id:
        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error": "Відсутній external_id",
            "payload": None,
        }

    url = f"{DNTRADE_API_URL}/orders/upload"

    payload = {
        "id": order_id,
        "personal_info": {
            "comment": payment_link,
        },
    }

    try:

        logging.info(
            "[DNTrade Upload] POST %s",
            url,
        )

        logging.info(
            "[DNTrade Upload] Payload: %s",
            payload,
        )

        response = requests.post(
            url,
            json=payload,
            headers=HEADERS_DNTRADE,
            timeout=REQUEST_TIMEOUT,
        )

        response_text = response.text

        logging.info(
            "[DNTrade Upload] HTTP %s | %s",
            response.status_code,
            response_text,
        )

        try:
            response_json = response.json()
        except ValueError:
            response_json = None

        success = response.status_code in (200, 201)

        return {
            "success": success,
            "http_status": response.status_code,
            "response": response_json
            if response_json is not None
            else response_text,
            "error": None if success else response_text,
            "payload": payload,
        }

    except requests.RequestException as exc:

        logging.exception(
            "[DNTrade Upload] HTTP exception"
        )

        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error": str(exc),
            "payload": payload,
        }

    except Exception as exc:

        logging.exception(
            "[DNTrade Upload] Exception"
        )

        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error": str(exc),
            "payload": payload,
        }


# ============================================================
# DNTRADE STATUS
# ============================================================

def change_dntrade_order_status(
    order_id: str,
    new_status_id: int,
):
    """
    POST /orders/setstatus

    Swagger:
    {
        "id": "...",
        "status": 1
    }
    """

    url = f"{DNTRADE_API_URL}/orders/setstatus"

    payload = {
        "id": order_id,
        "status": new_status_id,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            headers=HEADERS_DNTRADE,
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Status] HTTP %s | %s",
            response.status_code,
            response.text,
        )

        try:
            response_json = response.json()
        except ValueError:
            response_json = response.text

        return {
            "success": response.status_code in (200, 201),
            "http_status": response.status_code,
            "response": response_json,
        }

    except Exception as exc:

        logging.exception(
            "[DNTrade Status] Помилка"
        )

        return {
            "success": False,
            "http_status": None,
            "response": str(exc),
        }


# ============================================================
# GET ORDERS
# ============================================================

def get_dntrade_orders():

    all_orders = []
    offset = 0

    while True:

        params = {
            "limit": ORDERS_PAGE_SIZE,
            "offset": offset,
        }

        url = f"{DNTRADE_API_URL}/orders/list"

        try:

            response = requests.get(
                url,
                headers=HEADERS_DNTRADE,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            logging.info(
                "[DNTrade Orders] HTTP %s | offset=%s",
                response.status_code,
                offset,
            )

            if response.status_code != 200:

                return None, {
                    "status_code": response.status_code,
                    "error": response.text,
                    "offset": offset,
                }

            data = response.json()

            orders = extract_orders(data)

            all_orders.extend(orders)

            logging.info(
                "[DNTrade Orders] offset=%s отримано=%s",
                offset,
                len(orders),
            )

            if len(orders) < ORDERS_PAGE_SIZE:
                break

            offset += ORDERS_PAGE_SIZE

        except Exception as exc:

            logging.exception(
                "[DNTrade Orders] Помилка"
            )

            return None, {
                "status_code": 500,
                "error": str(exc),
                "offset": offset,
            }

    return all_orders, None


# ============================================================
# STATUS LIST
# ============================================================

def get_dntrade_status_list():

    url = f"{DNTRADE_API_URL}/orders/statuslist"

    try:

        response = requests.get(
            url,
            headers=HEADERS_DNTRADE,
            timeout=REQUEST_TIMEOUT,
        )

        try:
            data = response.json()
        except ValueError:
            data = None

        statuses = []

        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                statuses = data["data"]

        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "statuses": statuses,
            "error": None
            if response.status_code == 200
            else response.text,
        }

    except Exception as exc:

        return {
            "success": False,
            "status_code": 500,
            "statuses": [],
            "error": str(exc),
        }


# ============================================================
# PROCESS ORDER
# ============================================================

def process_single_order(order):

    external_id = order.get("external_id")
    number = order.get("number")

    raw_order_status = order.get("order_status")

    try:
        order_status = int(raw_order_status)
    except (TypeError, ValueError):

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "reason": "Некоректний order_status",
            "order_status": raw_order_status,
        }

    if order_status not in (
        STATUS_FULL_PREPAY,
        STATUS_PARTIAL_PREPAY,
    ):

        return {
            "success": False,
            "skipped": True,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
        }

    total_price = safe_float(
        order.get("total_price"),
        0,
    )

    if total_price <= 0:

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "reason": "total_price <= 0",
        }

    if order_status == STATUS_FULL_PREPAY:

        payment_amount = total_price

        description = (
            f"Повна оплата замовлення №{number}"
        )

    else:

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата за замовлення №{number}"
        )

    payment_amount = round(
        payment_amount,
        2,
    )

    # --------------------------------------------------------
    # IBAN
    # --------------------------------------------------------

    payment_link = create_iban_payment_link(
        number,
        payment_amount,
        description,
    )

    if not payment_link:

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "total_price": total_price,
            "payment_amount": payment_amount,
            "reason": "Не вдалося створити IBAN рахунок",
        }

    # --------------------------------------------------------
    # DNTRADE UPLOAD
    # --------------------------------------------------------

    upload_result = update_dntrade_order_note(
        order,
        payment_link,
    )

    if not upload_result["success"]:

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "total_price": total_price,
            "payment_amount": payment_amount,
            "payment_link": payment_link,
            "note_updated": False,
            "status_changed": False,
            "reason": (
                "IBAN рахунок створено, "
                "але DNTrade не прийняв /orders/upload"
            ),
            "dntrade_upload": upload_result,
        }

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    status_result = change_dntrade_order_status(
        external_id,
        STATUS_WAITING_PAYMENT,
    )

    if not status_result["success"]:

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "total_price": total_price,
            "payment_amount": payment_amount,
            "payment_link": payment_link,
            "note_updated": True,
            "status_changed": False,
            "reason": (
                "Посилання записано, "
                "але статус не змінився"
            ),
            "dntrade_status": status_result,
        }

    return {
        "success": True,
        "number": number,
        "external_id": external_id,
        "order_status_before": order_status,
        "order_status_after": STATUS_WAITING_PAYMENT,
        "total_price": total_price,
        "payment_amount": payment_amount,
        "payment_link": payment_link,
        "note_updated": True,
        "status_changed": True,
    }


# ============================================================
# CRON
# ============================================================

@app.route(
    "/cron/process",
    methods=["GET", "POST"],
)
def process_dntrade_orders():

    try:

        status_list = get_dntrade_status_list()

        orders, error = get_dntrade_orders()

        if orders is None:

            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Не вдалося отримати замовлення",
                        "error": error,
                        "status_list": status_list,
                    }
                ),
                500,
            )

        processed_orders = []
        errors = []
        skipped_count = 0
        eligible_count = 0

        for order in orders:

            if not isinstance(order, dict):
                continue

            try:
                current_status = int(
                    order.get("order_status")
                )
            except (TypeError, ValueError):

                skipped_count += 1
                continue

            if current_status not in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):

                skipped_count += 1
                continue

            eligible_count += 1

            result = process_single_order(order)

            if result.get("success"):
                processed_orders.append(result)
            else:
                errors.append(result)

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        status_statistics = {}

        for order in orders:

            if not isinstance(order, dict):
                continue

            raw_status = order.get("order_status")

            try:
                status_id = str(int(raw_status))
            except (TypeError, ValueError):
                status_id = str(raw_status)

            status_statistics[status_id] = (
                status_statistics.get(status_id, 0) + 1
            )

        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Обробка DNTrade завершена",

                    "total_orders": len(orders),

                    "eligible_orders": eligible_count,

                    "processed_count": len(
                        processed_orders
                    ),

                    "error_count": len(errors),

                    "skipped_count": skipped_count,

                    "payable_statuses": [
                        STATUS_FULL_PREPAY,
                        STATUS_PARTIAL_PREPAY,
                    ],

                    "waiting_payment_status":
                        STATUS_WAITING_PAYMENT,

                    "partial_prepayment_amount":
                        PREPAYMENT_PARTIAL_AMOUNT,

                    "status_statistics":
                        status_statistics,

                    "processed_orders":
                        processed_orders,

                    "errors":
                        errors,

                    "status_list":
                        status_list,
                }
            ),
            200,
        )

    except Exception as exc:

        logging.exception(
            "[CRON] Критична помилка"
        )

        return (
            jsonify(
                {
                    "status": "error",
                    "message": str(exc),
                }
            ),
            500,
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/",
    methods=["GET", "HEAD"],
)
def index():

    return (
        jsonify(
            {
                "status":
                    "DNTrade processor is active",
                "service":
                    "DNTrade -> IBAN Oplata",
            }
        ),
        200,
    )


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

    app.run(
        host="0.0.0.0",
        port=port,
    )
