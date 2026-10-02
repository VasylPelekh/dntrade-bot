import logging
import os
from decimal import Decimal, InvalidOperation

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
# ENVIRONMENT
# ============================================================

DNTRADE_API_URL = os.environ.get(
    "DNTRADE_API_URL",
    "https://api.dntrade.com.ua",
).rstrip("/")

DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN", "").strip()

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

# Статуси DNTrade
STATUS_FULL_PREPAY = int(
    os.environ.get("STATUS_PREPAY_FULL", "15")
)

STATUS_PARTIAL_PREPAY = int(
    os.environ.get("STATUS_PREPAY_PARTIAL", "16")
)

STATUS_WAITING_PAYMENT = int(
    os.environ.get("STATUS_WAITING_PAYMENT", "1")
)

# Сума передплати для статусу 16
PREPAYMENT_PARTIAL_AMOUNT = Decimal(
    os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", "200")
)

# DNTrade дозволяє максимум 50
DNTRADE_PAGE_SIZE = 50

# Максимальна кількість сторінок:
# 100 * 50 = 5000 замовлень
DNTRADE_MAX_PAGES = int(
    os.environ.get("DNTRADE_MAX_PAGES", "100")
)

REQUEST_TIMEOUT = int(
    os.environ.get("REQUEST_TIMEOUT", "20")
)

CRON_SECRET = os.environ.get(
    "CRON_SECRET",
    "",
).strip()


# ============================================================
# HEADERS
# ============================================================

def get_dntrade_headers():
    return {
        "ApiKey": DNTRADE_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_iban_headers():
    token = IBAN_TOKEN

    return {
        "X-API-KEY": token,
        "ApiKey": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ============================================================
# HELPERS
# ============================================================

def decimal_value(value, default=Decimal("0")):
    try:
        if value is None:
            return default

        if isinstance(value, Decimal):
            return value

        text = str(value).strip()

        if not text:
            return default

        return Decimal(text)

    except (InvalidOperation, ValueError, TypeError):
        return default


def normalize_text(value):
    if value is None:
        return ""

    return " ".join(str(value).strip().lower().split())


def check_cron_secret():
    if not CRON_SECRET:
        return True

    supplied = (
        os.environ.get("CRON_SECRET_HEADER", "")
        or ""
    ).strip()

    return supplied == CRON_SECRET


# ============================================================
# DNTRADE STATUS LIST
# ============================================================

def get_dntrade_status_list():
    """
    Отримуємо реальні статуси DNTrade.

    Наприклад:

    15 -> Передплата
    16 -> Передплата/Післяплата
    1  -> ОЧІКУЄМО ОПЛАТУ
    """

    url = f"{DNTRADE_API_URL}/orders/statuslist"

    try:
        response = requests.get(
            url,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade StatusList] HTTP %s: %s",
            response.status_code,
            response.text,
        )

        if response.status_code != 200:
            return {
                "success": False,
                "status_code": response.status_code,
                "statuses": [],
            }

        data = response.json()

        statuses = data.get("data", [])

        if not isinstance(statuses, list):
            statuses = []

        return {
            "success": True,
            "status_code": response.status_code,
            "statuses": statuses,
        }

    except Exception:
        logging.exception(
            "[DNTrade StatusList] Помилка отримання статусів"
        )

        return {
            "success": False,
            "status_code": None,
            "statuses": [],
        }


# ============================================================
# STATUS RESOLVER
# ============================================================

def build_status_maps(status_list):
    """
    Створює карти:

    ID -> title
    title -> ID

    Також дозволяє знайти статус, якщо /orders/list
    повертає не число 15/16, а текстовий title.
    """

    id_to_title = {}
    title_to_id = {}

    for item in status_list:
        if not isinstance(item, dict):
            continue

        status_id = item.get("id")
        title = item.get("title")

        try:
            status_id = int(status_id)
        except (TypeError, ValueError):
            continue

        if title is None:
            continue

        title_text = str(title).strip()

        id_to_title[status_id] = title_text
        title_to_id[normalize_text(title_text)] = status_id

    return id_to_title, title_to_id


def resolve_order_status(order, title_to_id):
    """
    DNTrade у схемі /orders/list описує поле status як string.

    Тому можливі варіанти:

        15
        "15"
        "Передплата"
        "Передплата/Післяплата"

    Функція приводить усі ці варіанти до числового ID.
    """

    raw_status = order.get("status")

    # На випадок старої структури
    if raw_status is None:
        raw_status = order.get("order_status")

    # 1. Якщо вже число
    try:
        if raw_status is not None:
            return int(raw_status)
    except (TypeError, ValueError):
        pass

    # 2. Якщо це текстовий статус
    normalized = normalize_text(raw_status)

    if normalized in title_to_id:
        return title_to_id[normalized]

    return None


# ============================================================
# DNTRADE ORDERS
# ============================================================

def extract_orders_from_response(data):
    if not isinstance(data, dict):
        return []

    orders = data.get("orders")

    if isinstance(orders, list):
        return orders

    # Запасний варіант
    data_orders = data.get("data")

    if isinstance(data_orders, list):
        return data_orders

    return []


def get_dntrade_orders():
    """
    Отримуємо замовлення через:

        GET /orders/list

    ВАЖЛИВО:
    DNTrade дозволяє limit максимум 50.

    Використовуємо:
        limit=50
        offset=0
        offset=50
        offset=100
        ...
    """

    all_orders = []

    for page in range(DNTRADE_MAX_PAGES):
        offset = page * DNTRADE_PAGE_SIZE

        params = {
            "limit": DNTRADE_PAGE_SIZE,
            "offset": offset,
        }

        url = f"{DNTRADE_API_URL}/orders/list"

        logging.info(
            "[DNTrade Orders] GET %s params=%s",
            url,
            params,
        )

        try:
            response = requests.get(
                url,
                headers=get_dntrade_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

        except Exception:
            logging.exception(
                "[DNTrade Orders] Помилка GET /orders/list"
            )
            break

        logging.info(
            "[DNTrade Orders] HTTP %s",
            response.status_code,
        )

        if response.status_code != 200:
            logging.error(
                "[DNTrade Orders] Помилка: %s",
                response.text,
            )

            raise RuntimeError(
                "DNTrade /orders/list "
                f"HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                "DNTrade /orders/list повернув некоректний JSON"
            )

        orders = extract_orders_from_response(data)

        logging.info(
            "[DNTrade Orders] offset=%s отримано=%s",
            offset,
            len(orders),
        )

        if not orders:
            break

        all_orders.extend(orders)

        if len(orders) < DNTRADE_PAGE_SIZE:
            break

    logging.info(
        "[DNTrade Orders] Всього отримано: %s",
        len(all_orders),
    )

    return all_orders


# ============================================================
# STATUS STATISTICS
# ============================================================

def get_status_statistics(orders, title_to_id):
    statistics = {}

    for order in orders:
        status_id = resolve_order_status(
            order,
            title_to_id,
        )

        key = (
            str(status_id)
            if status_id is not None
            else str(
                order.get("status", "UNKNOWN")
            )
        )

        statistics[key] = statistics.get(key, 0) + 1

    return statistics


# ============================================================
# IBAN PAYMENT LINK
# ============================================================

def create_iban_payment_link(
    order_number,
    amount,
    description,
):
    url = f"{IBAN_OPLATA_API_URL}{IBAN_ENDPOINT}"

    amount = decimal_value(amount)

    payload = {
        "organizationName": IBAN_ORGANIZATION_NAME,
        "identificationCode": IBAN_IDENTIFICATION_CODE,
        "iban": IBAN_ACCOUNT,
        "amount": float(amount),
        "paymentPurpose": description,
        "notes": f"Замовлення №{order_number}",
        "clientNotes": f"Оплата замовлення №{order_number}",
        "expirationHours": 24,
    }

    try:
        headers = get_iban_headers()

        logging.info(
            "[IBAN API] POST %s",
            url,
        )

        logging.info(
            "[IBAN API] Payload: %s",
            payload,
        )

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[IBAN API] HTTP %s: %s",
            response.status_code,
            response.text,
        )

        if response.status_code not in (200, 201):
            logging.error(
                "[IBAN API] Помилка створення рахунку"
            )
            return None

        try:
            data = response.json()
        except Exception:
            logging.error(
                "[IBAN API] Відповідь не є JSON"
            )
            return None

        payment_link = (
            data.get("ibanInvoiceUrl")
            or data.get("url")
            or data.get("paymentUrl")
            or data.get("invoiceUrl")
        )

        if not payment_link:
            logging.error(
                "[IBAN API] У відповіді немає посилання: %s",
                data,
            )
            return None

        return str(payment_link)

    except Exception:
        logging.exception(
            "[IBAN API] Виключення"
        )

        return None


# ============================================================
# DNTRADE UPDATE COMMENT
# ============================================================

def update_dntrade_order_comment(
    order_data,
    payment_link,
):
    """
    DNTrade POST /orders/upload.

    За Swagger поле для коментаря:

        personal_info.comment

    ID замовлення:

        id = external_id
    """

    url = f"{DNTRADE_API_URL}/orders/upload"

    external_id = order_data.get("external_id")

    payload = {
        "id": external_id,
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
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Upload] HTTP %s: %s",
            response.status_code,
            response.text,
        )

        if response.status_code in (200, 201):
            return True

        return False

    except Exception:
        logging.exception(
            "[DNTrade Upload] Помилка оновлення коментаря"
        )

        return False


# ============================================================
# DNTRADE CHANGE STATUS
# ============================================================

def change_dntrade_order_status(
    order_id,
    new_status_id,
):
    """
    DNTrade Swagger:

    POST /orders/setstatus

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
        logging.info(
            "[DNTrade Status Change] POST %s",
            url,
        )

        logging.info(
            "[DNTrade Status Change] Payload: %s",
            payload,
        )

        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Status Change] HTTP %s: %s",
            response.status_code,
            response.text,
        )

        return response.status_code in (200, 201)

    except Exception:
        logging.exception(
            "[DNTrade Status Change] Помилка"
        )

        return False


# ============================================================
# PROCESS ONE ORDER
# ============================================================

def process_single_order(
    order,
    resolved_status,
):
    external_id = order.get("external_id")
    number = order.get("number")

    total_price = decimal_value(
        order.get("total_price")
    )

    logging.info(
        "Обробка замовлення №%s | id=%s | status=%s | total=%s",
        number,
        external_id,
        resolved_status,
        total_price,
    )

    if not external_id:
        return {
            "success": False,
            "error": "У замовлення немає external_id",
            "number": number,
        }

    if total_price <= 0:
        return {
            "success": False,
            "error": "total_price <= 0",
            "number": number,
            "external_id": external_id,
        }

    # --------------------------------------------------------
    # STATUS 15 = ПОВНА ОПЛАТА
    # --------------------------------------------------------

    if resolved_status == STATUS_FULL_PREPAY:
        payment_amount = total_price

        description = (
            f"Повна оплата замовлення №{number}"
        )

    # --------------------------------------------------------
    # STATUS 16 = ПЕРЕДПЛАТА / ПІСЛЯПЛАТА
    # --------------------------------------------------------

    elif resolved_status == STATUS_PARTIAL_PREPAY:
        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата за замовлення №{number}"
        )

    else:
        return {
            "success": False,
            "skipped": True,
            "error": "Статус не є платіжним",
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
        }

    # --------------------------------------------------------
    # CREATE IBAN INVOICE
    # --------------------------------------------------------

    payment_link = create_iban_payment_link(
        order_number=number,
        amount=payment_amount,
        description=description,
    )

    if not payment_link:
        return {
            "success": False,
            "error": "Не вдалося створити IBAN-посилання",
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(payment_amount),
        }

    # --------------------------------------------------------
    # SAVE PAYMENT LINK TO COMMENT
    # --------------------------------------------------------

    comment_ok = update_dntrade_order_comment(
        order_data=order,
        payment_link=payment_link,
    )

    if not comment_ok:
        return {
            "success": False,
            "error": (
                "IBAN-посилання створено, "
                "але не вдалося записати його "
                "в коментар DNTrade"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(payment_amount),
            "payment_link": payment_link,
        }

    # --------------------------------------------------------
    # CHANGE STATUS TO 1
    # --------------------------------------------------------

    status_ok = change_dntrade_order_status(
        order_id=external_id,
        new_status_id=STATUS_WAITING_PAYMENT,
    )

    if not status_ok:
        return {
            "success": False,
            "error": (
                "Коментар записано, але "
                "не вдалося змінити статус на 1"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(payment_amount),
            "payment_link": payment_link,
            "comment_updated": True,
            "status_changed": False,
        }

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return {
        "success": True,
        "number": number,
        "external_id": external_id,
        "status_before": resolved_status,
        "status_after": STATUS_WAITING_PAYMENT,
        "amount": float(payment_amount),
        "payment_link": payment_link,
        "comment_updated": True,
        "status_changed": True,
    }


# ============================================================
# CRON PROCESS
# ============================================================

@app.route(
    "/cron/process",
    methods=["GET", "POST"],
)
def process_dntrade_orders():

    try:
        # ----------------------------------------------------
        # CRON SECRET
        # ----------------------------------------------------

        if CRON_SECRET:
            supplied_secret = (
                request.headers.get("X-Cron-Secret", "")
                if "request" in globals()
                else ""
            )

            if supplied_secret != CRON_SECRET:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": "Unauthorized",
                        }
                    ),
                    401,
                )

        # ----------------------------------------------------
        # STATUS LIST
        # ----------------------------------------------------

        status_data = get_dntrade_status_list()

        if not status_data["success"]:
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": (
                            "Не вдалося отримати "
                            "список статусів DNTrade"
                        ),
                        "status_list": status_data,
                    }
                ),
                502,
            )

        status_list = status_data["statuses"]

        id_to_title, title_to_id = build_status_maps(
            status_list
        )

        logging.info(
            "[Status Map] %s",
            id_to_title,
        )

        # ----------------------------------------------------
        # ORDERS
        # ----------------------------------------------------

        orders = get_dntrade_orders()

        total_orders = len(orders)

        # ----------------------------------------------------
        # STATISTICS
        # ----------------------------------------------------

        status_statistics = get_status_statistics(
            orders,
            title_to_id,
        )

        logging.info(
            "[Status Statistics] %s",
            status_statistics,
        )

        # ----------------------------------------------------
        # FIND PAYABLE ORDERS
        # ----------------------------------------------------

        payable_orders = []

        for order in orders:

            resolved_status = resolve_order_status(
                order,
                title_to_id,
            )

            # Важливо:
            # тут уже порівнюємо ID, незалежно від того,
            # що реально прийшло:
            #
            # 15
            # "15"
            # "Передплата"
            #
            # або
            #
            # 16
            # "16"
            # "Передплата/Післяплата"

            if resolved_status in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):
                payable_orders.append(
                    (
                        order,
                        resolved_status,
                    )
                )

        eligible_orders = len(payable_orders)

        logging.info(
            "[Processor] Знайдено платіжних замовлень: %s",
            eligible_orders,
        )

        # ----------------------------------------------------
        # PROCESS
        # ----------------------------------------------------

        processed_orders = []
        skipped_count = (
            total_orders - eligible_orders
        )
        error_count = 0

        for order, resolved_status in payable_orders:

            result = process_single_order(
                order,
                resolved_status,
            )

            if result.get("success"):
                processed_orders.append(result)
            else:
                error_count += 1
                processed_orders.append(result)

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        if eligible_orders == 0:
            message = (
                "Замовлень зі статусами "
                f"{STATUS_FULL_PREPAY}/"
                f"{STATUS_PARTIAL_PREPAY} не знайдено."
            )
        else:
            message = (
                f"Знайдено {eligible_orders} "
                "замовлень для оплати."
            )

        return (
            jsonify(
                {
                    "status": "success",
                    "message": message,
                    "total_orders": total_orders,
                    "eligible_orders": eligible_orders,
                    "processed_count": sum(
                        1
                        for x in processed_orders
                        if x.get("success")
                    ),
                    "error_count": error_count,
                    "skipped_count": skipped_count,
                    "payable_statuses": [
                        STATUS_FULL_PREPAY,
                        STATUS_PARTIAL_PREPAY,
                    ],
                    "status_list": {
                        "success": status_data["success"],
                        "status_code": status_data["status_code"],
                        "statuses": status_list,
                    },
                    "status_statistics": status_statistics,
                    "orders": processed_orders,
                }
            ),
            200,
        )

    except Exception as e:

        logging.exception(
            "[Processor] Критична помилка"
        )

        return (
            jsonify(
                {
                    "status": "error",
                    "error": str(e),
                }
            ),
            500,
        )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=["GET", "HEAD"],
)
def health():
    return (
        jsonify(
            {
                "status": "ok",
                "service": "DNTrade → IBAN processor",
            }
        ),
        200,
    )


# ============================================================
# ROOT
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
            }
        ),
        200,
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )

Важлива правка: у цьому коді "status_statistics" тепер теж покаже, що DNTrade реально повертає. Якщо замовлення мають ""status": "Передплата"", воно буде розпізнане як "15"; ""Передплата/Післяплата"" — як "16".

Після деплою просто відкрий "/cron/process" і скинь мені повну відповідь JSON. Тоді вже буде видно, чи дійшли ми до створення IBAN, запису коментаря та зміни статусу.
