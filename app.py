import logging
import os
from typing import Optional

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

DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN", "").strip()

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json",
    "Accept": "application/json",
}


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

IBAN_TOKEN = os.environ.get("IBAN_TOKEN", "").strip()

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
# ORDER STATUSES
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


# ============================================================
# HTTP SETTINGS
# ============================================================

REQUEST_TIMEOUT = int(
    os.environ.get("REQUEST_TIMEOUT", "15")
)

ORDERS_PAGE_SIZE = 50


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
    """
    Безпечно перетворює значення у float.
    """
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", ".").strip()

        return float(value)
    except (TypeError, ValueError):
        return default


def get_iban_headers():
    """
    Заголовки IBAN Oplata.
    Залишаємо всі варіанти авторизації, які були у робочій версії.
    """
    token = (IBAN_TOKEN or "").strip()

    return {
        "X-API-KEY": token,
        "ApiKey": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def extract_orders(response_data):
    """
    Витягує список замовлень з різних можливих форматів відповіді.
    """

    if isinstance(response_data, dict):
        if isinstance(response_data.get("orders"), list):
            return response_data["orders"]

        if isinstance(response_data.get("data"), list):
            return response_data["data"]

        if isinstance(response_data.get("data"), dict):
            if isinstance(response_data["data"].get("orders"), list):
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
    """
    Створює рахунок IBAN Oplata та повертає URL оплати.
    """

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
        headers = get_iban_headers()

        logging.info(
            "[IBAN API] POST %s | amount=%s | order=%s",
            url,
            round(amount, 2),
            order_number,
        )

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[IBAN API] HTTP %s | Response: %s",
            response.status_code,
            response.text,
        )

        if response.status_code not in (200, 201):
            logging.error(
                "[IBAN API] Помилка створення рахунку: %s",
                response.text,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logging.error(
                "[IBAN API] Сервер повернув не JSON: %s",
                response.text,
            )
            return None

        if not isinstance(data, dict):
            logging.error(
                "[IBAN API] Неочікуваний формат відповіді: %r",
                data,
            )
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
            "[IBAN API] У відповіді не знайдено URL оплати: %s",
            data,
        )

        return None

    except requests.RequestException:
        logging.exception(
            "[IBAN API] Помилка HTTP-запиту:"
        )
        return None

    except Exception:
        logging.exception(
            "[IBAN API] Непередбачена помилка:"
        )
        return None


# ============================================================
# DNTRADE STATUS
# ============================================================

def change_dntrade_order_status(
    order_id: str,
    new_status_id: int,
) -> bool:
    """
    Зміна статусу замовлення через:

    POST /orders/setstatus

    Swagger:
    {
        "id": "UUID",
        "status": 1
    }
    """

    if not order_id:
        logging.error(
            "[DNTrade Status] Відсутній order_id"
        )
        return False

    url = f"{DNTRADE_API_URL}/orders/setstatus"

    payload = {
        "id": order_id,
        "status": new_status_id,
    }

    try:
        logging.info(
            "[DNTrade Status] POST %s | payload=%s",
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
            "[DNTrade Status] HTTP %s | Response: %s",
            response.status_code,
            response.text,
        )

        if response.status_code in (200, 201):
            return True

        logging.error(
            "[DNTrade Status] Помилка зміни статусу: %s",
            response.text,
        )

        return False

    except requests.RequestException:
        logging.exception(
            "[DNTrade Status] HTTP помилка:"
        )
        return False

    except Exception:
        logging.exception(
            "[DNTrade Status] Непередбачена помилка:"
        )
        return False


# ============================================================
# DNTRADE COMMENT / NOTE
# ============================================================

def update_dntrade_order_note(
    order_data: dict,
    payment_link: str,
) -> bool:
    """
    Записує посилання на IBAN у:

    POST /orders/upload

    Swagger для upload використовує:

    {
        "id": "...",
        "personal_info": {
            "comment": "..."
        }
    }

    Тобто НЕ:
        external_id
        note
        orders: [...]

    """

    order_id = order_data.get("external_id")

    if not order_id:
        logging.error(
            "[DNTrade Upload] У замовлення немає external_id"
        )
        return False

    url = f"{DNTRADE_API_URL}/orders/upload"

    payload = {
        "id": order_id,
        "personal_info": {
            "comment": payment_link,
        },
    }

    try:
        logging.info(
            "[DNTrade Upload] POST %s | order=%s",
            url,
            order_id,
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

        logging.info(
            "[DNTrade Upload] HTTP %s | Response: %s",
            response.status_code,
            response.text,
        )

        if response.status_code in (200, 201):
            return True

        logging.error(
            "[DNTrade Upload] Помилка запису посилання: %s",
            response.text,
        )

        return False

    except requests.RequestException:
        logging.exception(
            "[DNTrade Upload] HTTP помилка:"
        )
        return False

    except Exception:
        logging.exception(
            "[DNTrade Upload] Непередбачена помилка:"
        )
        return False


# ============================================================
# GET ORDERS
# ============================================================

def get_dntrade_orders():
    """
    Отримує всі замовлення через /orders/list.

    DNTrade дозволяє максимум 50 замовлень за один запит.

    Використовуємо:
        limit
        offset

    НЕ використовуємо page.
    """

    all_orders = []
    offset = 0

    while True:
        params = {
            "limit": ORDERS_PAGE_SIZE,
            "offset": offset,
        }

        url = f"{DNTRADE_API_URL}/orders/list"

        try:
            logging.info(
                "[DNTrade Orders] GET %s | params=%s",
                url,
                params,
            )

            response = requests.get(
                url,
                headers=HEADERS_DNTRADE,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            logging.info(
                "[DNTrade Orders] HTTP %s",
                response.status_code,
            )

            if response.status_code != 200:
                logging.error(
                    "[DNTrade Orders] Response: %s",
                    response.text,
                )

                return None, {
                    "status_code": response.status_code,
                    "error": response.text,
                    "offset": offset,
                }

            try:
                response_data = response.json()
            except ValueError:
                return None, {
                    "status_code": response.status_code,
                    "error": "DNTrade повернув не JSON",
                    "response": response.text,
                    "offset": offset,
                }

            orders = extract_orders(response_data)

            logging.info(
                "[DNTrade Orders] Отримано %s замовлень | offset=%s",
                len(orders),
                offset,
            )

            all_orders.extend(orders)

            if len(orders) < ORDERS_PAGE_SIZE:
                break

            offset += ORDERS_PAGE_SIZE

        except requests.RequestException as exc:
            logging.exception(
                "[DNTrade Orders] HTTP помилка:"
            )

            return None, {
                "status_code": 500,
                "error": str(exc),
                "offset": offset,
            }

        except Exception as exc:
            logging.exception(
                "[DNTrade Orders] Непередбачена помилка:"
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
    """
    Отримує довідник статусів DNTrade.
    """

    url = f"{DNTRADE_API_URL}/orders/statuslist"

    try:
        response = requests.get(
            url,
            headers=HEADERS_DNTRADE,
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Status List] HTTP %s | Response: %s",
            response.status_code,
            response.text,
        )

        if response.status_code != 200:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
                "statuses": [],
            }

        data = response.json()

        statuses = []

        if isinstance(data, dict):
            raw_statuses = data.get("data", [])

            if isinstance(raw_statuses, list):
                statuses = raw_statuses

        return {
            "success": True,
            "status_code": response.status_code,
            "statuses": statuses,
        }

    except Exception as exc:
        logging.exception(
            "[DNTrade Status List] Помилка:"
        )

        return {
            "success": False,
            "status_code": 500,
            "error": str(exc),
            "statuses": [],
        }


# ============================================================
# PROCESS SINGLE ORDER
# ============================================================

def process_single_order(order: dict):
    """
    Обробляє одне замовлення зі статусом 15 або 16.
    """

    external_id = order.get("external_id")
    number = order.get("number")

    # КРИТИЧНО:
    # Саме order_status є реальним числовим статусом DNTrade.
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
            "reason": "Статус не потребує передплати",
        }

    if not external_id:
        return {
            "success": False,
            "number": number,
            "external_id": None,
            "order_status": order_status,
            "reason": "Відсутній external_id",
        }

    total_price = safe_float(
        order.get("total_price"),
        0.0,
    )

    if total_price <= 0:
        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "reason": "total_price <= 0",
            "total_price": total_price,
        }

    # --------------------------------------------------------
    # ВИЗНАЧЕННЯ СУМИ
    # --------------------------------------------------------

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

    payment_amount = round(payment_amount, 2)

    if payment_amount <= 0:
        return {
            "success": False,
            "number": number,
            "external_id": external_id,
            "order_status": order_status,
            "reason": "Сума платежу <= 0",
            "total_price": total_price,
            "payment_amount": payment_amount,
        }

    logging.info(
        "=================================================="
    )

    logging.info(
        "[ORDER] Початок обробки | №%s | id=%s | "
        "order_status=%s | total=%s | payment=%s",
        number,
        external_id,
        order_status,
        total_price,
        payment_amount,
    )

    # --------------------------------------------------------
    # 1. СТВОРЮЄМО IBAN РАХУНОК
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
    # 2. ЗАПИСУЄМО ПОСИЛАННЯ У DNTRADE
    # --------------------------------------------------------

    note_ok = update_dntrade_order_note(
        order,
        payment_link,
    )

    if not note_ok:
        # Статус НЕ змінюємо, якщо посилання не записалось.
        # Це безпечніше, бо клієнт не повинен отримати
        # статус "Очікуємо оплату", якщо посилання не збережене.
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
                "але посилання не вдалося записати в DNTrade"
            ),
        }

    # --------------------------------------------------------
    # 3. ЗМІНЮЄМО СТАТУС НА 1
    # --------------------------------------------------------

    status_ok = change_dntrade_order_status(
        external_id,
        STATUS_WAITING_PAYMENT,
    )

    if not status_ok:
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
                "Посилання записано в DNTrade, "
                "але статус не вдалося змінити на 1"
            ),
        }

    logging.info(
        "[ORDER] УСПІШНО | №%s | IBAN=%s | status=%s -> %s",
        number,
        payment_link,
        order_status,
        STATUS_WAITING_PAYMENT,
    )

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
# MAIN CRON
# ============================================================

@app.route(
    "/cron/process",
    methods=["GET", "POST"],
)
def process_dntrade_orders():
    """
    Основний endpoint автоматичної обробки.

    Логіка:

    order_status = 15
        -> IBAN на всю суму
        -> запис посилання
        -> статус 1

    order_status = 16
        -> IBAN на 200 грн
        -> запис посилання
        -> статус 1
    """

    try:
        logging.info(
            "=================================================="
        )

        logging.info(
            "[CRON] Початок обробки DNTrade"
        )

        # ----------------------------------------------------
        # Отримуємо статуси для діагностики
        # ----------------------------------------------------

        status_list = get_dntrade_status_list()

        # ----------------------------------------------------
        # Отримуємо всі замовлення
        # ----------------------------------------------------

        orders, error = get_dntrade_orders()

        if orders is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Не вдалося отримати замовлення DNTrade",
                        "error": error,
                        "status_list": status_list,
                    }
                ),
                int(
                    error.get("status_code", 500)
                    if isinstance(error, dict)
                    else 500
                ),
            )

        logging.info(
            "[CRON] Всього отримано замовлень: %s",
            len(orders),
        )

        # ----------------------------------------------------
        # Обробка
        # ----------------------------------------------------

        processed_orders = []
        skipped_orders = []
        error_orders = []

        eligible_count = 0

        for order in orders:
            if not isinstance(order, dict):
                continue

            raw_status = order.get("order_status")

            try:
                current_status = int(raw_status)
            except (TypeError, ValueError):
                skipped_orders.append(
                    {
                        "number": order.get("number"),
                        "external_id": order.get("external_id"),
                        "order_status": raw_status,
                        "reason": "order_status не є числом",
                    }
                )
                continue

            # Тільки 15 та 16
            if current_status not in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):
                skipped_orders.append(
                    {
                        "number": order.get("number"),
                        "external_id": order.get("external_id"),
                        "order_status": current_status,
                    }
                )
                continue

            eligible_count += 1

            result = process_single_order(order)

            if result.get("success"):
                processed_orders.append(result)
            else:
                error_orders.append(result)

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        status_statistics = {}

        for order in orders:
            if not isinstance(order, dict):
                continue

            raw_status = order.get("order_status")

            try:
                status_id = int(raw_status)
            except (TypeError, ValueError):
                status_id = str(raw_status)

            status_statistics[str(status_id)] = (
                status_statistics.get(str(status_id), 0) + 1
            )

        logging.info(
            "[CRON] Завершено | processed=%s | errors=%s | skipped=%s",
            len(processed_orders),
            len(error_orders),
            len(skipped_orders),
        )

        logging.info(
            "=================================================="
        )

        return (
            jsonify(
                {
                    "status": "success",
                    "message": (
                        "Обробка DNTrade завершена"
                    ),
                    "total_orders": len(orders),
                    "eligible_orders": eligible_count,
                    "processed_count": len(processed_orders),
                    "error_count": len(error_orders),
                    "skipped_count": len(skipped_orders),
                    "payable_statuses": [
                        STATUS_FULL_PREPAY,
                        STATUS_PARTIAL_PREPAY,
                    ],
                    "waiting_payment_status": STATUS_WAITING_PAYMENT,
                    "partial_prepayment_amount": PREPAYMENT_PARTIAL_AMOUNT,
                    "status_statistics": status_statistics,
                    "processed_orders": processed_orders,
                    "errors": error_orders,
                    "status_list": status_list,
                }
            ),
            200,
        )

    except Exception as exc:
        logging.exception(
            "[CRON] Критична помилка:"
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
# ROOT / HEALTH CHECK
# ============================================================

@app.route(
    "/",
    methods=["GET", "HEAD"],
)
def index():
    return (
        jsonify(
            {
                "status": "DNTrade processor is active",
                "service": "DNTrade -> IBAN Oplata",
            }
        ),
        200,
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
