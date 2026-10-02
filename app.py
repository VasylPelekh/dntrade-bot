import logging
import os
from collections import Counter
from decimal import Decimal, InvalidOperation

import requests
from flask import Flask, jsonify, request


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

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

# Фіксована передплата для статусу 16
PREPAYMENT_PARTIAL_AMOUNT = Decimal(
    os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", "200")
)

# Скільки замовлень брати за один запит
DNTRADE_PAGE_SIZE = int(
    os.environ.get("DNTRADE_PAGE_SIZE", "100")
)

# Максимальна кількість сторінок
DNTRADE_MAX_PAGES = int(
    os.environ.get("DNTRADE_MAX_PAGES", "100")
)

REQUEST_TIMEOUT = int(
    os.environ.get("REQUEST_TIMEOUT", "20")
)

# Якщо CRON_SECRET порожній — захист вимкнений.
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()


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
    """
    Безпечно перетворює значення грошей у Decimal.
    """
    if value is None:
        return default

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def normalize_status(value):
    """
    Перетворює order_status у int, якщо це можливо.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def extract_orders_from_response(data):
    """
    Витягує список замовлень із різних можливих форматів відповіді DNTrade.
    """

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    possible_keys = [
        "data",
        "orders",
        "items",
        "results",
    ]

    for key in possible_keys:
        value = data.get(key)

        if isinstance(value, list):
            return value

        # Іноді data може бути об'єктом:
        if isinstance(value, dict):
            for nested_key in possible_keys:
                nested_value = value.get(nested_key)

                if isinstance(nested_value, list):
                    return nested_value

    return []


# ============================================================
# CRON AUTH
# ============================================================

def check_cron_secret():
    """
    Якщо CRON_SECRET заданий — вимагаємо його в query/header.
    Якщо CRON_SECRET не заданий — доступ дозволений.
    """

    if not CRON_SECRET:
        return True

    supplied_secret = (
        request.args.get("secret")
        or request.headers.get("X-Cron-Secret")
        or request.headers.get("Authorization", "")
    )

    if supplied_secret.startswith("Bearer "):
        supplied_secret = supplied_secret[7:]

    return supplied_secret == CRON_SECRET


# ============================================================
# DNTRADE: GET ORDERS
# ============================================================

def get_dntrade_orders():
    """
    Отримує всі доступні замовлення через pagination.

    ВАЖЛИВО:
    Тут навмисно НЕ підставляємо статус 15/16 у параметри,
    тому що ми ще не знаємо точного синтаксису фільтра DNTrade.

    Спочатку дивимося реальні статуси, які повертає API.
    """

    all_orders = []

    page = 1

    while page <= DNTRADE_MAX_PAGES:

        url = f"{DNTRADE_API_URL}/orders/list"

        params = {
            "limit": DNTRADE_PAGE_SIZE,
            "page": page,
        }

        logger.info(
            "[DNTrade LIST] GET %s | params=%s",
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
            logger.exception(
                "[DNTrade LIST] Помилка HTTP запиту"
            )
            raise

        logger.info(
            "[DNTrade LIST] HTTP %s",
            response.status_code,
        )

        logger.info(
            "[DNTrade LIST] Response: %s",
            response.text[:10000],
        )

        if response.status_code != 200:
            raise RuntimeError(
                "DNTrade /orders/list повернув "
                f"HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                "DNTrade /orders/list повернув не JSON: "
                + response.text
            )

        orders = extract_orders_from_response(data)

        logger.info(
            "[DNTrade LIST] Page %s: отримано %s замовлень",
            page,
            len(orders),
        )

        if not orders:
            break

        all_orders.extend(orders)

        # Якщо повернуло менше page size — це остання сторінка.
        if len(orders) < DNTRADE_PAGE_SIZE:
            break

        page += 1

    logger.info(
        "[DNTrade LIST] Всього отримано замовлень: %s",
        len(all_orders),
    )

    return all_orders


# ============================================================
# DNTRADE: PRINT STATUS STATISTICS
# ============================================================

def get_status_statistics(orders):
    counter = Counter()

    for order in orders:

        if not isinstance(order, dict):
            counter["INVALID_ORDER"] += 1
            continue

        raw_status = order.get("order_status")

        normalized = normalize_status(raw_status)

        counter[str(normalized)] += 1

    return dict(counter)


# ============================================================
# IBAN: CREATE PAYMENT LINK
# ============================================================

def create_iban_payment_link(
    order_number,
    amount,
    description,
):
    """
    Створює платіжне посилання IBAN Oplata.
    """

    url = f"{IBAN_OPLATA_API_URL}{IBAN_ENDPOINT}"

    amount = decimal_value(amount)

    payload = {
        "organizationName": IBAN_ORGANIZATION_NAME,
        "identificationCode": IBAN_IDENTIFICATION_CODE,
        "iban": IBAN_ACCOUNT,
        "amount": float(amount.quantize(Decimal("0.01"))),
        "paymentPurpose": description,
        "notes": f"Замовлення №{order_number}",
        "clientNotes": f"Оплата замовлення №{order_number}",
        "expirationHours": 24,
    }

    logger.info(
        "[IBAN] POST %s",
        url,
    )

    logger.info(
        "[IBAN] Payload: %s",
        payload,
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_iban_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        logger.exception(
            "[IBAN] Помилка HTTP запиту"
        )
        return None

    logger.info(
        "[IBAN] HTTP %s",
        response.status_code,
    )

    logger.info(
        "[IBAN] Response: %s",
        response.text[:10000],
    )

    if response.status_code not in (200, 201):
        logger.error(
            "[IBAN] Не вдалося створити рахунок. "
            "HTTP %s",
            response.status_code,
        )
        return None

    try:
        data = response.json()
    except Exception:
        logger.error(
            "[IBAN] Відповідь не є JSON: %s",
            response.text,
        )
        return None

    payment_link = (
        data.get("ibanInvoiceUrl")
        or data.get("url")
        or data.get("paymentUrl")
        or data.get("invoiceUrl")
    )

    if not payment_link:
        logger.error(
            "[IBAN] У відповіді немає URL оплати. "
            "JSON: %s",
            data,
        )
        return None

    logger.info(
        "[IBAN] Посилання створено: %s",
        payment_link,
    )

    return payment_link


# ============================================================
# DNTRADE: UPDATE NOTE
# ============================================================

def update_dntrade_order_note(
    order,
    payment_link,
):
    """
    Записує посилання на оплату у note через /orders/upload.

    ВАЖЛИВО:
    Поки що використовуємо той формат, який у нас є:
        {
            "orders": [
                {
                    "external_id": "...",
                    "number": ...,
                    "note": "...",
                    "products": [...]
                }
            ]
        }

    Найважливіше зараз — побачити ПОВНУ відповідь API,
    якщо DNTrade відхиляє цей payload.
    """

    url = f"{DNTRADE_API_URL}/orders/upload"

    external_id = order.get("external_id")
    number = order.get("number")

    products = order.get("products")

    if products is None:
        products = []

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
        "[DNTrade UPLOAD] POST %s",
        url,
    )

    logger.info(
        "[DNTrade UPLOAD] Order #%s",
        number,
    )

    logger.info(
        "[DNTrade UPLOAD] Payload: %s",
        payload,
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        logger.exception(
            "[DNTrade UPLOAD] HTTP exception"
        )
        return {
            "success": False,
            "status_code": None,
            "response_text": None,
            "response_json": None,
        }

    logger.info(
        "[DNTrade UPLOAD] HTTP %s",
        response.status_code,
    )

    logger.info(
        "[DNTrade UPLOAD] Response: %s",
        response.text[:20000],
    )

    response_json = None

    try:
        response_json = response.json()
    except Exception:
        pass

    success = response.status_code in (200, 201)

    return {
        "success": success,
        "status_code": response.status_code,
        "response_text": response.text,
        "response_json": response_json,
    }


# ============================================================
# DNTRADE: CHANGE STATUS
# ============================================================

def change_dntrade_order_status(
    external_id,
    new_status_id,
):
    """
    Переводить замовлення у потрібний статус.
    """

    url = f"{DNTRADE_API_URL}/orders/setstatus"

    payload = {
        "id": external_id,
        "external_id": external_id,
        "status_id": new_status_id,
        "status": new_status_id,
    }

    logger.info(
        "[DNTrade STATUS] POST %s",
        url,
    )

    logger.info(
        "[DNTrade STATUS] Payload: %s",
        payload,
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        logger.exception(
            "[DNTrade STATUS] HTTP exception"
        )
        return {
            "success": False,
            "status_code": None,
            "response_text": None,
            "response_json": None,
        }

    logger.info(
        "[DNTrade STATUS] HTTP %s",
        response.status_code,
    )

    logger.info(
        "[DNTrade STATUS] Response: %s",
        response.text[:10000],
    )

    response_json = None

    try:
        response_json = response.json()
    except Exception:
        pass

    return {
        "success": response.status_code in (200, 201),
        "status_code": response.status_code,
        "response_text": response.text,
        "response_json": response_json,
    }


# ============================================================
# PROCESS ONE ORDER
# ============================================================

def process_single_order(order):
    """
    Обробка одного замовлення.

    Порядок:
    1. Перевіряємо статус 15/16.
    2. Рахуємо суму.
    3. Створюємо IBAN invoice.
    4. Записуємо payment link у note.
    5. Тільки якщо note записано успішно —
       змінюємо статус на 1.
    """

    external_id = order.get("external_id")
    number = order.get("number")
    raw_status = order.get("order_status")

    order_status = normalize_status(raw_status)

    result = {
        "external_id": external_id,
        "number": number,
        "order_status": order_status,
        "success": False,
    }

    logger.info(
        "============================================================"
    )

    logger.info(
        "[ORDER] №%s | external_id=%s | status=%s",
        number,
        external_id,
        order_status,
    )

    # --------------------------------------------------------
    # Перевірка статусу
    # --------------------------------------------------------

    if order_status not in (
        STATUS_FULL_PREPAY,
        STATUS_PARTIAL_PREPAY,
    ):
        result["skipped"] = True
        result["reason"] = (
            f"status {order_status} is not payable"
        )

        logger.info(
            "[ORDER] №%s пропущено: статус %s не є payable",
            number,
            order_status,
        )

        return result

    # --------------------------------------------------------
    # Перевірка GUID
    # --------------------------------------------------------

    if not external_id:
        result["error"] = "Немає external_id"

        logger.error(
            "[ORDER] №%s: відсутній external_id",
            number,
        )

        return result

    # --------------------------------------------------------
    # Сума замовлення
    # --------------------------------------------------------

    total_price = decimal_value(
        order.get("total_price")
    )

    if total_price <= 0:
        result["error"] = (
            f"Некоректна total_price: "
            f"{order.get('total_price')}"
        )

        logger.error(
            "[ORDER] №%s: некоректна сума %s",
            number,
            order.get("total_price"),
        )

        return result

    # --------------------------------------------------------
    # Визначення суми платежу
    # --------------------------------------------------------

    if order_status == STATUS_FULL_PREPAY:

        payment_amount = total_price

        description = (
            f"Повна оплата замовлення №{number}"
        )

        payment_type = "full"

    else:

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата за замовлення №{number}"
        )

        payment_type = "partial"

    result["payment_type"] = payment_type
    result["amount"] = float(payment_amount)

    logger.info(
        "[ORDER] №%s | total_price=%s | "
        "payment_amount=%s | type=%s",
        number,
        total_price,
        payment_amount,
        payment_type,
    )

    # --------------------------------------------------------
    # Створення IBAN invoice
    # --------------------------------------------------------

    payment_link = create_iban_payment_link(
        order_number=number,
        amount=payment_amount,
        description=description,
    )

    if not payment_link:

        result["error"] = (
            "Не вдалося створити IBAN payment link"
        )

        logger.error(
            "[ORDER] №%s: IBAN link не створено",
            number,
        )

        return result

    result["payment_link"] = payment_link

    # --------------------------------------------------------
    # Запис payment link у NOTE
    # --------------------------------------------------------

    note_result = update_dntrade_order_note(
        order=order,
        payment_link=payment_link,
    )

    result["note_update"] = note_result

    if not note_result["success"]:

        result["error"] = (
            "Не вдалося записати payment link "
            "у note. Статус НЕ змінюємо."
        )

        logger.error(
            "[ORDER] №%s: note НЕ записано. "
            "Статус залишаємо %s.",
            number,
            order_status,
        )

        return result

    logger.info(
        "[ORDER] №%s: payment link успішно записано в note",
        number,
    )

    # --------------------------------------------------------
    # Тільки після успішного NOTE -> status 1
    # --------------------------------------------------------

    status_result = change_dntrade_order_status(
        external_id=external_id,
        new_status_id=STATUS_WAITING_PAYMENT,
    )

    result["status_update"] = status_result

    if not status_result["success"]:

        result["error"] = (
            "Payment link записано в note, "
            "але не вдалося змінити статус на 1."
        )

        logger.error(
            "[ORDER] №%s: note записано, "
            "але статус НЕ змінився.",
            number,
        )

        return result

    # --------------------------------------------------------
    # УСПІШНО
    # --------------------------------------------------------

    result["success"] = True

    logger.info(
        "[ORDER] №%s УСПІШНО оброблено.",
        number,
    )

    return result


# ============================================================
# CRON PROCESS
# ============================================================

@app.route(
    "/cron/process",
    methods=["GET", "POST"],
)
def process_dntrade_orders():

    # --------------------------------------------------------
    # Авторизація cron
    # --------------------------------------------------------

    if not check_cron_secret():

        logger.warning(
            "[CRON] Невірний CRON_SECRET"
        )

        return (
            jsonify(
                {
                    "status": "error",
                    "error": "Unauthorized",
                }
            ),
            401,
        )

    logger.info(
        "============================================================"
    )

    logger.info(
        "[CRON] START /cron/process"
    )

    try:

        # ----------------------------------------------------
        # Отримуємо замовлення
        # ----------------------------------------------------

        orders = get_dntrade_orders()

        # ----------------------------------------------------
        # Статистика статусів
        # ----------------------------------------------------

        status_statistics = get_status_statistics(
            orders
        )

        logger.info(
            "[DNTrade] Статистика order_status: %s",
            status_statistics,
        )

        # ----------------------------------------------------
        # Кількість payable
        # ----------------------------------------------------

        payable_orders = []

        for order in orders:

            if not isinstance(order, dict):
                continue

            status = normalize_status(
                order.get("order_status")
            )

            if status in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):
                payable_orders.append(order)

        logger.info(
            "[DNTrade] Payable orders: %s",
            len(payable_orders),
        )

        # ----------------------------------------------------
        # Якщо немає статусів 15/16
        # ----------------------------------------------------

        if not payable_orders:

            logger.warning(
                "[DNTrade] НЕМАЄ замовлень зі статусами %s або %s.",
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            )

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": (
                            "У отриманих замовленнях "
                            "немає payable статусів."
                        ),
                        "total_orders": len(orders),
                        "status_statistics": status_statistics,
                        "eligible_orders": 0,
                        "processed_count": 0,
                        "skipped_count": len(orders),
                        "error_count": 0,
                        "orders": [],
                    }
                ),
                200,
            )

        # ----------------------------------------------------
        # Обробляємо payable
        # ----------------------------------------------------

        results = []

        processed_count = 0
        skipped_count = 0
        error_count = 0

        for order in payable_orders:

            result = process_single_order(
                order
            )

            results.append(result)

            if result.get("success"):
                processed_count += 1

            elif result.get("skipped"):
                skipped_count += 1

            else:
                error_count += 1

        # ----------------------------------------------------
        # Response
        # ----------------------------------------------------

        response_data = {
            "status": "success",
            "total_orders": len(orders),
            "status_statistics": status_statistics,
            "eligible_orders": len(payable_orders),
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "orders": results,
        }

        logger.info(
            "[CRON] FINISH: %s",
            response_data,
        )

        return jsonify(response_data), 200

    except Exception as e:

        logger.exception(
            "[CRON] Критична помилка"
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
                "service": "DNTrade processor",
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
                "endpoint": "/cron/process",
                "payable_statuses": [
                    STATUS_FULL_PREPAY,
                    STATUS_PARTIAL_PREPAY,
                ],
                "waiting_payment_status": STATUS_WAITING_PAYMENT,
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

    logger.info(
        "Starting server on port %s",
        port,
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
