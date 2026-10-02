import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from flask import Flask, jsonify


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

app = Flask(__name__)


# ============================================================
# DNTRADE CONFIG
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
# DNTRADE TIMEZONE
# ============================================================

DNTRADE_TIMEZONE_NAME = os.environ.get(
    "DNTRADE_TIMEZONE",
    "Europe/Kyiv",
).strip()

try:
    DNTRADE_TIMEZONE = ZoneInfo(
        DNTRADE_TIMEZONE_NAME
    )
except ZoneInfoNotFoundError:
    logging.error(
        "Невідомий timezone '%s'. "
        "Використовую Europe/Kyiv.",
        DNTRADE_TIMEZONE_NAME,
    )
    DNTRADE_TIMEZONE_NAME = "Europe/Kyiv"
    DNTRADE_TIMEZONE = ZoneInfo(
        "Europe/Kyiv"
    )


# ============================================================
# IBAN OPLATA CONFIG
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
# EXISTING IBAN LINKS
#
# STATUS 16:
#
# total_price < 1500  -> LINK_200
# total_price >= 1500 -> LINK_500
#
# Для STATUS 16 новий IBAN НІКОЛИ не створюється.
# ============================================================

LINK_200 = os.environ.get(
    "LINK_200",
    "",
).strip()

LINK_500 = os.environ.get(
    "LINK_500",
    "",
).strip()


# ============================================================
# STATUSES
# ============================================================

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

PREPAYMENT_PARTIAL_AMOUNT = float(
    os.environ.get(
        "PREPAYMENT_PARTIAL_AMOUNT",
        "200",
    )
)


# ============================================================
# SETTINGS
# ============================================================

REQUEST_TIMEOUT = int(
    os.environ.get(
        "REQUEST_TIMEOUT",
        "15",
    )
)

ORDERS_PAGE_SIZE = 50

# Для Cron раз на хвилину беремо 5 хвилин,
# щоб не пропустити зміну через затримки.
MODIFIED_LOOKBACK_MINUTES = int(
    os.environ.get(
        "MODIFIED_LOOKBACK_MINUTES",
        "5",
    )
)


# ============================================================
# RECOVERY LINKS
#
# Старі IBAN, які вже були створені раніше.
# Використовуються для STATUS 15,
# якщо в конкретного замовлення вже був
# створений IBAN.
# ============================================================

RECOVERY_PAYMENT_LINKS = {
    "c3fb713b-87b3-44c5-bd9a-e3d992688de3":
        "https://ibanoplata.com/iban-qr/0c78c24785014a668f1f28b8d361ff5b",

    "1bc47328-5ae9-4f2b-964e-3434fd2ba1b7":
        "https://ibanoplata.com/iban-qr/4cb57d66c662432585a8383b2f38fffb",

    "d20457c2-835b-4de6-8c87-253f71aa4107":
        "https://ibanoplata.com/iban-qr/5ed475ce1bc94daf99035c3691ca9af4",
}


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

        if isinstance(
            response_data.get("orders"),
            list,
        ):
            return response_data["orders"]

        if isinstance(
            response_data.get("data"),
            list,
        ):
            return response_data["data"]

        if isinstance(
            response_data.get("data"),
            dict,
        ):

            if isinstance(
                response_data["data"].get(
                    "orders"
                ),
                list,
            ):
                return response_data[
                    "data"
                ]["orders"]

    if isinstance(response_data, list):
        return response_data

    return []


def format_dntrade_date(value):
    """
    DNTrade upload очікує:

    YYYY-MM-DD HH:MM:SS

    GET /orders/list може повертати:

    2026-10-02T13:51:24.167Z
    """

    if not value:
        return None

    if isinstance(value, str):

        value = value.strip()

        try:
            normalized = value.replace(
                "Z",
                "+00:00",
            )

            dt = datetime.fromisoformat(
                normalized
            )

            return dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        except ValueError:
            pass

        try:
            dt = datetime.strptime(
                value,
                "%Y-%m-%d %H:%M:%S",
            )

            return dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        except ValueError:
            pass

    return value


# ============================================================
# MODIFIED TIME WINDOW
# ============================================================

def get_modified_window():
    """
    Формує ОДНЕ часовe вікно для запиту DNTrade.

    ВАЖЛИВО:
    DNTrade отримує час у часовій зоні Europe/Kyiv,
    а не UTC.
    """

    now_local = datetime.now(
        DNTRADE_TIMEZONE
    )

    modified_from = (
        now_local
        - timedelta(
            minutes=MODIFIED_LOOKBACK_MINUTES
        )
    )

    modified_from_text = modified_from.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    modified_to_text = now_local.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return (
        modified_from_text,
        modified_to_text,
    )


# ============================================================
# BUILD DNTRADE CART
# ============================================================

def build_cart(order):
    products = order.get("products")

    if not isinstance(products, list):
        return []

    cart = []

    for item in products:

        if not isinstance(item, dict):
            continue

        product = item.get("product")

        if not isinstance(product, dict):
            product = {}

        store = item.get("store")

        if not isinstance(store, dict):
            store = {}

        product_id = (
            product.get("id")
            or product.get("external_id")
        )

        store_id = (
            store.get("external_id")
            or store.get("id")
        )

        quantity = safe_float(
            item.get("quantity"),
            0,
        )

        price = safe_float(
            item.get("price"),
            0,
        )

        product_bonus_sum = safe_float(
            item.get("product_bonus_sum"),
            0,
        )

        cart_item = {
            "product_id": product_id,
            "store_id": store_id,
            "price": price,
            "quantity": quantity,
            "product_bonus_sum":
                product_bonus_sum,
        }

        cart.append(cart_item)

    return cart


# ============================================================
# IBAN
#
# НОВИЙ IBAN СТВОРЮЄТЬСЯ ТІЛЬКИ ДЛЯ STATUS 15.
# ============================================================

def create_iban_payment_link(
    order_number,
    amount: float,
    description: str,
) -> Optional[str]:

    url = (
        f"{IBAN_OPLATA_API_URL}"
        f"{IBAN_ENDPOINT}"
    )

    payload = {
        "organizationName":
            IBAN_ORGANIZATION_NAME,

        "identificationCode":
            IBAN_IDENTIFICATION_CODE,

        "iban":
            IBAN_ACCOUNT,

        "amount":
            round(amount, 2),

        "paymentPurpose":
            description,

        "notes":
            f"Замовлення №{order_number}",

        "clientNotes":
            f"Оплата замовлення №{order_number}",

        "expirationHours":
            24,
    }

    try:

        logging.info(
            "[IBAN] Створення НОВОГО рахунку | "
            "№%s | amount=%s",
            order_number,
            round(amount, 2),
        )

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

        if response.status_code not in (
            200,
            201,
        ):
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        if not isinstance(data, dict):
            return None

        payment_link = (
            data.get("ibanInvoiceUrl")
            or data.get("url")
            or data.get("paymentUrl")
            or data.get("payment_url")
            or data.get("invoiceUrl")
            or data.get("invoice_url")
        )

        if payment_link:
            return str(payment_link)

        logging.error(
            "[IBAN] У відповіді немає URL: %s",
            data,
        )

        return None

    except Exception:

        logging.exception(
            "[IBAN] Помилка створення рахунку"
        )

        return None


# ============================================================
# GET LINK FOR STATUS 16
#
# ГОЛОВНА ЛОГІКА:
#
# total_price < 1500  -> LINK_200
# total_price >= 1500 -> LINK_500
#
# НОВИЙ IBAN НЕ СТВОРЮЄТЬСЯ.
# ============================================================

def get_partial_prepayment_link(
    total_price
):
    total_price = safe_float(
        total_price,
        0,
    )

    if total_price < 1500:

        if not LINK_200:

            return (
                None,
                "LINK_200_not_configured",
            )

        return (
            LINK_200,
            "environment_LINK_200",
        )

    # 1500 і більше
    if not LINK_500:

        return (
            None,
            "LINK_500_not_configured",
        )

    return (
        LINK_500,
        "environment_LINK_500",
    )


# ============================================================
# DNTRADE UPLOAD
# ============================================================

def update_dntrade_order(
    order,
    payment_link,
):
    order_id = order.get(
        "external_id"
    )

    if not order_id:

        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error":
                "Відсутній external_id",
        }

    cart = build_cart(order)

    if not cart:

        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error":
                "Не вдалося сформувати cart "
                "з products",
            "cart": cart,
        }

    payload = {
        "id": order_id,

        "number":
            order.get("number"),

        "date":
            format_dntrade_date(
                order.get("date")
            ),

        "status":
            int(
                order.get(
                    "order_status",
                    STATUS_FULL_PREPAY,
                )
            ),

        "channel":
            order.get("channel") or "",

        "labels":
            (
                order.get("labels")
                if isinstance(
                    order.get("labels"),
                    list,
                )
                else []
            ),

        "cart":
            cart,

        "personal_info": {
            "comment":
                payment_link,
        },
    }

    if order.get("reserve") is not None:
        payload["reserve"] = order.get(
            "reserve"
        )

    if order.get("private_id") is not None:
        payload["private_id"] = order.get(
            "private_id"
        )

    try:

        url = (
            f"{DNTRADE_API_URL}"
            "/orders/upload"
        )

        logging.info(
            "[DNTrade Upload] POST %s | "
            "order=%s",
            url,
            order_id,
        )

        logging.info(
            "[DNTrade Upload] cart items=%s",
            len(cart),
        )

        response = requests.post(
            url,
            json=payload,
            headers=HEADERS_DNTRADE,
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Upload] HTTP %s | %s",
            response.status_code,
            response.text,
        )

        try:
            response_json = response.json()
        except ValueError:
            response_json = response.text

        success = response.status_code in (
            200,
            201,
        )

        return {
            "success": success,
            "http_status":
                response.status_code,

            "response":
                response_json,

            "error":
                None
                if success
                else response.text,

            "cart_count":
                len(cart),

            "payload":
                payload,
        }

    except Exception as exc:

        logging.exception(
            "[DNTrade Upload] Помилка"
        )

        return {
            "success": False,
            "http_status": None,
            "response": None,
            "error": str(exc),
            "cart_count": len(cart),
            "payload": payload,
        }


# ============================================================
# DNTRADE STATUS
# ============================================================

def change_dntrade_order_status(
    order_id,
    new_status_id,
):

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/setstatus"
    )

    payload = {
        "id":
            order_id,

        "status":
            new_status_id,
    }

    try:

        logging.info(
            "[DNTrade Status] POST %s | %s",
            url,
            payload,
        )

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
            "success":
                response.status_code in (
                    200,
                    201,
                ),

            "http_status":
                response.status_code,

            "response":
                response_json,
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
# GET DNTRADE ORDERS
# ============================================================

def get_dntrade_orders_modified():

    all_orders = []

    offset = 0

    # Один раз формуємо часовe вікно.
    modified_from, modified_to = (
        get_modified_window()
    )

    logging.info(
        "[DNTrade Orders] Перевірка "
        "змінених замовлень: "
        "modified_from=%s | "
        "modified_to=%s | "
        "timezone=%s",
        modified_from,
        modified_to,
        DNTRADE_TIMEZONE_NAME,
    )

    while True:

        params = {
            "limit":
                ORDERS_PAGE_SIZE,

            "offset":
                offset,

            "modified_from":
                modified_from,

            "modified_to":
                modified_to,
        }

        url = (
            f"{DNTRADE_API_URL}"
            "/orders/list"
        )

        try:

            response = requests.get(
                url,
                headers=HEADERS_DNTRADE,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            logging.info(
                "[DNTrade Orders] HTTP %s | "
                "offset=%s | params=%s",
                response.status_code,
                offset,
                params,
            )

            if response.status_code != 200:

                return (
                    None,
                    {
                        "status_code":
                            response.status_code,

                        "error":
                            response.text,

                        "offset":
                            offset,

                        "params":
                            params,
                    },
                    modified_from,
                    modified_to,
                )

            try:
                data = response.json()

            except ValueError:

                return (
                    None,
                    {
                        "status_code":
                            response.status_code,

                        "error":
                            "DNTrade повернув "
                            "не-JSON відповідь",

                        "response":
                            response.text,

                        "offset":
                            offset,

                        "params":
                            params,
                    },
                    modified_from,
                    modified_to,
                )

            orders = extract_orders(
                data
            )

            logging.info(
                "[DNTrade Orders] Отримано "
                "%s замовлень | offset=%s",
                len(orders),
                offset,
            )

            all_orders.extend(orders)

            if len(orders) < ORDERS_PAGE_SIZE:
                break

            offset += ORDERS_PAGE_SIZE

        except Exception as exc:

            logging.exception(
                "[DNTrade Orders] Помилка"
            )

            return (
                None,
                {
                    "status_code": 500,
                    "error": str(exc),
                    "offset": offset,
                },
                modified_from,
                modified_to,
            )

    return (
        all_orders,
        None,
        modified_from,
        modified_to,
    )


# ============================================================
# STATUS LIST
# ============================================================

def get_dntrade_status_list():

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/statuslist"
    )

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

            if isinstance(
                data.get("data"),
                list,
            ):
                statuses = data["data"]

        return {
            "success":
                response.status_code == 200,

            "status_code":
                response.status_code,

            "statuses":
                statuses,

            "error":
                None
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
# PROCESS SINGLE ORDER
# ============================================================

def process_single_order(order):

    external_id = order.get(
        "external_id"
    )

    number = order.get(
        "number"
    )

    try:

        order_status = int(
            order.get("order_status")
        )

    except (
        TypeError,
        ValueError,
    ):

        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "reason":
                "Некоректний order_status",
            "order_status":
                order.get(
                    "order_status"
                ),
        }

    # --------------------------------------------------------
    # Тільки статуси 15 та 16
    # --------------------------------------------------------

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
            "order_status":
                order_status,

            "reason":
                "total_price <= 0",
        }

    # ========================================================
    # STATUS 16
    #
    # ТУТ МИ ВЗАГАЛІ НЕ ПЕРЕВІРЯЄМО
    # І НЕ ВИКОРИСТОВУЄМО СТАРИЙ IBAN.
    #
    # Завжди:
    #
    # < 1500 -> LINK_200
    # >=1500 -> LINK_500
    #
    # Новий IBAN НЕ створюється.
    # ========================================================

    if (
        order_status
        == STATUS_PARTIAL_PREPAY
    ):

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата за "
            f"замовлення №{number}"
        )

        (
            payment_link,
            iban_source,
        ) = get_partial_prepayment_link(
            total_price
        )

        if not payment_link:

            if iban_source == (
                "LINK_200_not_configured"
            ):

                reason = (
                    "Для STATUS 16 і "
                    f"total_price={total_price} "
                    "потрібен LINK_200, "
                    "але Environment Variable "
                    "LINK_200 не заданий"
                )

            elif iban_source == (
                "LINK_500_not_configured"
            ):

                reason = (
                    "Для STATUS 16 і "
                    f"total_price={total_price} "
                    "потрібен LINK_500, "
                    "але Environment Variable "
                    "LINK_500 не заданий"
                )

            else:

                reason = (
                    "Не вдалося отримати "
                    "існуюче посилання "
                    "для STATUS 16"
                )

            logging.error(
                "[ORDER] №%s | STATUS 16 | %s",
                number,
                reason,
            )

            return {
                "success": False,
                "number": number,
                "external_id":
                    external_id,
                "order_status":
                    order_status,
                "total_price":
                    total_price,
                "payment_amount":
                    payment_amount,
                "reason":
                    reason,
                "iban_created":
                    False,
                "iban_source":
                    iban_source,
            }

        logging.info(
            "[ORDER] №%s | STATUS 16 | "
            "total_price=%s | "
            "використовую %s",
            number,
            total_price,
            iban_source,
        )

    # ========================================================
    # STATUS 15
    #
    # Тут створення нового IBAN дозволено.
    # Але якщо IBAN вже є в note або recovery —
    # повторно не створюємо.
    # ========================================================

    else:

        # ----------------------------------------------------
        # Перевіряємо існуючий note
        # ----------------------------------------------------

        existing_note = str(
            order.get("note") or ""
        ).strip()

        payment_link = None
        iban_source = None

        if (
            "ibanoplata.com/iban-qr/"
            in existing_note
        ):

            payment_link = existing_note
            iban_source = "existing_note"

        # ----------------------------------------------------
        # Recovery
        # ----------------------------------------------------

        if (
            not payment_link
            and external_id
            in RECOVERY_PAYMENT_LINKS
        ):

            payment_link = (
                RECOVERY_PAYMENT_LINKS[
                    external_id
                ]
            )

            iban_source = (
                "recovery_existing_iban"
            )

        payment_amount = total_price

        description = (
            f"Повна оплата "
            f"замовлення №{number}"
        )

        # ----------------------------------------------------
        # Якщо немає старого IBAN —
        # створюємо новий.
        # ----------------------------------------------------

        if not payment_link:

            payment_link = (
                create_iban_payment_link(
                    number,
                    payment_amount,
                    description,
                )
            )

            iban_source = "new"

            if not payment_link:

                return {
                    "success": False,
                    "number": number,
                    "external_id":
                        external_id,

                    "order_status":
                        order_status,

                    "total_price":
                        total_price,

                    "payment_amount":
                        payment_amount,

                    "reason":
                        "Не вдалося створити "
                        "IBAN рахунок",
                }

        logging.info(
            "[ORDER] №%s | STATUS 15 | "
            "IBAN source=%s | %s",
            number,
            iban_source,
            payment_link,
        )

    # ========================================================
    # ЗАПИСУЄМО ПОСИЛАННЯ В DNTRADE
    # ========================================================

    upload_result = (
        update_dntrade_order(
            order,
            payment_link,
        )
    )

    if not upload_result["success"]:

        return {
            "success": False,
            "number": number,
            "external_id":
                external_id,

            "order_status":
                order_status,

            "total_price":
                total_price,

            "payment_amount":
                payment_amount,

            "payment_link":
                payment_link,

            "iban_source":
                iban_source,

            "note_updated":
                False,

            "status_changed":
                False,

            "reason":
                "IBAN є, але DNTrade "
                "не прийняв "
                "/orders/upload",

            "dntrade_upload":
                upload_result,
        }

    # ========================================================
    # СТАТУС -> 1
    # ========================================================

    status_result = (
        change_dntrade_order_status(
            external_id,
            STATUS_WAITING_PAYMENT,
        )
    )

    if not status_result["success"]:

        return {
            "success": False,
            "number": number,
            "external_id":
                external_id,

            "order_status":
                order_status,

            "total_price":
                total_price,

            "payment_amount":
                payment_amount,

            "payment_link":
                payment_link,

            "iban_source":
                iban_source,

            "note_updated":
                True,

            "status_changed":
                False,

            "reason":
                "Посилання успішно "
                "записано, але статус "
                "не вдалося змінити "
                "на 1",

            "dntrade_status":
                status_result,
        }

    # ========================================================
    # SUCCESS
    # ========================================================

    return {
        "success": True,

        "number":
            number,

        "external_id":
            external_id,

        "order_status_before":
            order_status,

        "order_status_after":
            STATUS_WAITING_PAYMENT,

        "total_price":
            total_price,

        "payment_amount":
            payment_amount,

        "payment_link":
            payment_link,

        "iban_source":
            iban_source,

        "note_updated":
            True,

        "status_changed":
            True,
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

        (
            orders,
            error,
            modified_from,
            modified_to,
        ) = get_dntrade_orders_modified()

        if orders is None:

            return (
                jsonify(
                    {
                        "status":
                            "error",

                        "message":
                            "Не вдалося отримати "
                            "змінені замовлення "
                            "DNTrade",

                        "error":
                            error,

                        "modified_from":
                            modified_from,

                        "modified_to":
                            modified_to,

                        "timezone":
                            DNTRADE_TIMEZONE_NAME,
                    }
                ),
                500,
            )

        status_list = (
            get_dntrade_status_list()
        )

        processed_orders = []
        errors = []

        skipped_count = 0
        eligible_count = 0

        changed_orders_debug = []

        # ----------------------------------------------------
        # DEBUG
        # ----------------------------------------------------

        for order in orders:

            if not isinstance(
                order,
                dict,
            ):
                continue

            raw_status = order.get(
                "order_status"
            )

            try:

                status_id = int(
                    raw_status
                )

            except (
                TypeError,
                ValueError,
            ):

                status_id = raw_status

            changed_orders_debug.append(
                {
                    "number":
                        order.get("number"),

                    "external_id":
                        order.get(
                            "external_id"
                        ),

                    "order_status":
                        status_id,

                    "total_price":
                        order.get(
                            "total_price"
                        ),

                    "note":
                        order.get("note"),
                }
            )

        # ----------------------------------------------------
        # PROCESS
        # ----------------------------------------------------

        for order in orders:

            if not isinstance(
                order,
                dict,
            ):

                skipped_count += 1
                continue

            try:

                current_status = int(
                    order.get(
                        "order_status"
                    )
                )

            except (
                TypeError,
                ValueError,
            ):

                skipped_count += 1
                continue

            if current_status not in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):

                skipped_count += 1
                continue

            eligible_count += 1

            logging.info(
                "[PROCESS] Знайдено "
                "платіжне замовлення "
                "№%s | status=%s",
                order.get("number"),
                current_status,
            )

            result = (
                process_single_order(
                    order
                )
            )

            if result.get(
                "success"
            ):

                processed_orders.append(
                    result
                )

            else:

                errors.append(
                    result
                )

        # ----------------------------------------------------
        # STATUS STATISTICS
        # ----------------------------------------------------

        status_statistics = {}

        for order in orders:

            if not isinstance(
                order,
                dict,
            ):
                continue

            raw_status = order.get(
                "order_status"
            )

            try:

                status_id = str(
                    int(raw_status)
                )

            except (
                TypeError,
                ValueError,
            ):

                status_id = str(
                    raw_status
                )

            status_statistics[
                status_id
            ] = (
                status_statistics.get(
                    status_id,
                    0,
                )
                + 1
            )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return (
            jsonify(
                {
                    "status":
                        "success",

                    "message":
                        "Обробка змінених "
                        "DNTrade завершена",

                    "timezone":
                        DNTRADE_TIMEZONE_NAME,

                    "modified_from":
                        modified_from,

                    "modified_to":
                        modified_to,

                    "lookback_minutes":
                        MODIFIED_LOOKBACK_MINUTES,

                    "total_changed_orders":
                        len(orders),

                    "eligible_orders":
                        eligible_count,

                    "processed_count":
                        len(
                            processed_orders
                        ),

                    "error_count":
                        len(errors),

                    "skipped_count":
                        skipped_count,

                    "payable_statuses": [
                        STATUS_FULL_PREPAY,
                        STATUS_PARTIAL_PREPAY,
                    ],

                    "waiting_payment_status":
                        STATUS_WAITING_PAYMENT,

                    "partial_prepayment_amount":
                        PREPAYMENT_PARTIAL_AMOUNT,

                    "partial_prepayment_rules": {
                        "less_than_1500":
                            "LINK_200",

                        "greater_or_equal_1500":
                            "LINK_500",
                    },

                    "status_statistics":
                        status_statistics,

                    "changed_orders_debug":
                        changed_orders_debug,

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
                    "status":
                        "error",

                    "message":
                        str(exc),
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

                "mode":
                    "modified_from polling",

                "timezone":
                    DNTRADE_TIMEZONE_NAME,

                "lookback_minutes":
                    MODIFIED_LOOKBACK_MINUTES,

                "partial_prepayment_rules": {
                    "less_than_1500":
                        "LINK_200",

                    "greater_or_equal_1500":
                        "LINK_500",
                },
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
