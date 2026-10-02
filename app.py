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

DNTRADE_TOKEN = os.environ.get(
    "DNTRADE_TOKEN",
    "",
).strip()


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
# DNTRADE STATUSES
# ============================================================

# 15 = Повна передплата
STATUS_FULL_PREPAY = int(
    os.environ.get(
        "STATUS_PREPAY_FULL",
        "15",
    )
)

# 16 = Передплата / післясплата
STATUS_PARTIAL_PREPAY = int(
    os.environ.get(
        "STATUS_PREPAY_PARTIAL",
        "16",
    )
)

# 1 = Очікує оплату
STATUS_WAITING_PAYMENT = int(
    os.environ.get(
        "STATUS_WAITING_PAYMENT",
        "1",
    )
)


# ============================================================
# PAYMENT SETTINGS
# ============================================================

PREPAYMENT_PARTIAL_AMOUNT = Decimal(
    os.environ.get(
        "PREPAYMENT_PARTIAL_AMOUNT",
        "200",
    )
)


# ============================================================
# DNTRADE PAGINATION
# ============================================================

DNTRADE_PAGE_SIZE = 50

DNTRADE_MAX_PAGES = int(
    os.environ.get(
        "DNTRADE_MAX_PAGES",
        "100",
    )
)


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = int(
    os.environ.get(
        "REQUEST_TIMEOUT",
        "20",
    )
)


# ============================================================
# CRON SECURITY
# ============================================================

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
    if value is None:
        return default

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


def normalize_status(value):
    """
    DNTrade у документації повертає status як string.
    Наприклад "15".

    Також залишаємо fallback для старого формату
    order_status.
    """

    if value is None:
        return None

    try:
        return int(str(value).strip())
    except (
        ValueError,
        TypeError,
    ):
        return value


def get_order_status(order):
    """
    Основне поле за офіційною документацією:
        status

    Fallback:
        order_status
    """

    if "status" in order:
        return normalize_status(
            order.get("status")
        )

    return normalize_status(
        order.get("order_status")
    )


# ============================================================
# CRON AUTH
# ============================================================

def check_cron_secret():

    if not CRON_SECRET:
        return True

    supplied_secret = (
        request.args.get("secret")
        or request.headers.get(
            "X-Cron-Secret"
        )
        or request.headers.get(
            "Authorization",
            "",
        )
    )

    if supplied_secret.startswith(
        "Bearer "
    ):
        supplied_secret = supplied_secret[7:]

    return supplied_secret == CRON_SECRET


# ============================================================
# DNTRADE — STATUS LIST
# ============================================================

def get_dntrade_status_list():
    """
    Отримує реальний список статусів
    із DNTrade.

    Це допоможе перевірити:
        1 -> Очікує оплату
        15 -> ?
        16 -> ?
    """

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/statuslist"
    )

    logger.info(
        "[DNTrade STATUS LIST] GET %s",
        url,
    )

    try:

        response = requests.get(
            url,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

    except Exception:

        logger.exception(
            "[DNTrade STATUS LIST] "
            "HTTP exception"
        )

        return {
            "success": False,
            "status_code": None,
            "statuses": [],
        }

    logger.info(
        "[DNTrade STATUS LIST] HTTP %s",
        response.status_code,
    )

    logger.info(
        "[DNTrade STATUS LIST] Response: %s",
        response.text[:15000],
    )

    if response.status_code != 200:

        return {
            "success": False,
            "status_code": response.status_code,
            "statuses": [],
        }

    try:

        data = response.json()

    except Exception:

        return {
            "success": False,
            "status_code": response.status_code,
            "statuses": [],
        }

    statuses = data.get(
        "data",
        [],
    )

    if not isinstance(
        statuses,
        list,
    ):
        statuses = []

    return {
        "success": True,
        "status_code": response.status_code,
        "statuses": statuses,
    }


# ============================================================
# DNTRADE — EXTRACT ORDERS
# ============================================================

def extract_orders_from_response(data):

    if not isinstance(
        data,
        dict,
    ):
        return []

    orders = data.get(
        "orders"
    )

    if isinstance(
        orders,
        list,
    ):
        return orders

    data_orders = data.get(
        "data"
    )

    if isinstance(
        data_orders,
        list,
    ):
        return data_orders

    return []


# ============================================================
# DNTRADE — GET ORDERS
# ============================================================

def get_dntrade_orders():

    all_orders = []

    offset = 0
    page_number = 1

    while page_number <= DNTRADE_MAX_PAGES:

        url = (
            f"{DNTRADE_API_URL}"
            "/orders/list"
        )

        # За офіційною документацією:
        #
        # limit — максимум 50
        # offset — зміщення
        #
        params = {
            "limit": DNTRADE_PAGE_SIZE,
            "offset": offset,
        }

        logger.info(
            "[DNTrade LIST] GET %s",
            url,
        )

        logger.info(
            "[DNTrade LIST] params=%s",
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
                "[DNTrade LIST] "
                "HTTP exception"
            )

            raise

        logger.info(
            "[DNTrade LIST] HTTP %s",
            response.status_code,
        )

        logger.info(
            "[DNTrade LIST] Response: %s",
            response.text[:30000],
        )

        if response.status_code != 200:

            raise RuntimeError(
                "DNTrade /orders/list "
                f"повернув HTTP "
                f"{response.status_code}: "
                f"{response.text}"
            )

        try:

            data = response.json()

        except Exception:

            raise RuntimeError(
                "DNTrade /orders/list "
                "повернув не JSON: "
                + response.text
            )

        orders = (
            extract_orders_from_response(
                data
            )
        )

        logger.info(
            "[DNTrade LIST] "
            "Page=%s Offset=%s "
            "отримано=%s",
            page_number,
            offset,
            len(orders),
        )

        if not orders:
            break

        all_orders.extend(
            orders
        )

        # Якщо повернуло менше 50,
        # це остання сторінка.
        if len(orders) < DNTRADE_PAGE_SIZE:
            break

        offset += DNTRADE_PAGE_SIZE
        page_number += 1

    logger.info(
        "[DNTrade LIST] "
        "Всього отримано: %s",
        len(all_orders),
    )

    return all_orders


# ============================================================
# STATUS STATISTICS
# ============================================================

def get_status_statistics(orders):

    counter = Counter()

    for order in orders:

        if not isinstance(
            order,
            dict,
        ):
            counter["INVALID_ORDER"] += 1
            continue

        status = get_order_status(
            order
        )

        counter[str(status)] += 1

    return dict(counter)


# ============================================================
# IBAN — CREATE PAYMENT LINK
# ============================================================

def create_iban_payment_link(
    order_number,
    amount,
    description,
):

    url = (
        f"{IBAN_OPLATA_API_URL}"
        f"{IBAN_ENDPOINT}"
    )

    amount = decimal_value(
        amount
    )

    payload = {
        "organizationName": (
            IBAN_ORGANIZATION_NAME
        ),
        "identificationCode": (
            IBAN_IDENTIFICATION_CODE
        ),
        "iban": IBAN_ACCOUNT,
        "amount": float(
            amount.quantize(
                Decimal("0.01")
            )
        ),
        "paymentPurpose": description,
        "notes": (
            f"Замовлення №{order_number}"
        ),
        "clientNotes": (
            f"Оплата замовлення "
            f"№{order_number}"
        ),
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
            "[IBAN] "
            "HTTP exception"
        )

        return None

    logger.info(
        "[IBAN] HTTP %s",
        response.status_code,
    )

    logger.info(
        "[IBAN] Response: %s",
        response.text[:20000],
    )

    if response.status_code not in (
        200,
        201,
    ):

        logger.error(
            "[IBAN] "
            "Не вдалося створити invoice"
        )

        return None

    try:

        data = response.json()

    except Exception:

        logger.error(
            "[IBAN] "
            "Відповідь не JSON"
        )

        return None

    payment_link = (
        data.get(
            "ibanInvoiceUrl"
        )
        or data.get(
            "url"
        )
        or data.get(
            "paymentUrl"
        )
        or data.get(
            "invoiceUrl"
        )
    )

    if not payment_link:

        logger.error(
            "[IBAN] "
            "У відповіді немає "
            "посилання на оплату."
        )

        logger.error(
            "[IBAN] JSON=%s",
            data,
        )

        return None

    logger.info(
        "[IBAN] "
        "Payment link=%s",
        payment_link,
    )

    return payment_link


# ============================================================
# DNTRADE — UPDATE COMMENT
# ============================================================

def update_dntrade_order_comment(
    order,
    payment_link,
):
    """
    Офіційна документація:

    POST /orders/upload

    Body:

    {
        "id": "...",
        "personal_info": {
            "comment": "..."
        }
    }

    У документації немає:
        "orders": [...]
        "external_id": ...
        "note": ...

    Тому використовуємо саме id +
    personal_info.comment.
    """

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/upload"
    )

    external_id = order.get(
        "external_id"
    )

    number = order.get(
        "number"
    )

    if not external_id:

        logger.error(
            "[DNTrade UPLOAD] "
            "№%s: немає external_id",
            number,
        )

        return {
            "success": False,
            "status_code": None,
            "response_text": (
                "Missing external_id"
            ),
            "response_json": None,
        }

    # Офіційний формат /orders/upload
    payload = {
        "id": external_id,
        "personal_info": {
            "comment": payment_link,
        },
    }

    logger.info(
        "[DNTrade UPLOAD] POST %s",
        url,
    )

    logger.info(
        "[DNTrade UPLOAD] "
        "Order #%s",
        number,
    )

    logger.info(
        "[DNTrade UPLOAD] "
        "Payload=%s",
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
            "[DNTrade UPLOAD] "
            "HTTP exception"
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
        "[DNTrade UPLOAD] "
        "Response=%s",
        response.text[:30000],
    )

    response_json = None

    try:
        response_json = response.json()
    except Exception:
        pass

    success = response.status_code in (
        200,
        201,
    )

    return {
        "success": success,
        "status_code": response.status_code,
        "response_text": response.text,
        "response_json": response_json,
    }


# ============================================================
# DNTRADE — CHANGE STATUS
# ============================================================

def change_dntrade_order_status(
    external_id,
    new_status_id,
):
    """
    Офіційна документація:

    POST /orders/setstatus

    Body:

    {
        "id": "...",
        "status": 1
    }
    """

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/setstatus"
    )

    payload = {
        "id": external_id,
        "status": new_status_id,
    }

    logger.info(
        "[DNTrade STATUS] POST %s",
        url,
    )

    logger.info(
        "[DNTrade STATUS] "
        "Payload=%s",
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
            "[DNTrade STATUS] "
            "HTTP exception"
        )

        return {
            "success": False,
            "status_code": None,
            "response_text": None,
            "response_json": None,
        }

    logger.info(
        "[DNTrade STATUS] "
        "HTTP %s",
        response.status_code,
    )

    logger.info(
        "[DNTrade STATUS] "
        "Response=%s",
        response.text[:15000],
    )

    response_json = None

    try:
        response_json = response.json()
    except Exception:
        pass

    return {
        "success": response.status_code in (
            200,
            201,
        ),
        "status_code": response.status_code,
        "response_text": response.text,
        "response_json": response_json,
    }


# ============================================================
# PROCESS ONE ORDER
# ============================================================

def process_single_order(order):

    external_id = order.get(
        "external_id"
    )

    number = order.get(
        "number"
    )

    order_status = get_order_status(
        order
    )

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
        "[ORDER] "
        "№%s | id=%s | status=%s",
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
            f"status {order_status} "
            "is not payable"
        )

        return result

    # --------------------------------------------------------
    # Перевірка ID
    # --------------------------------------------------------

    if not external_id:

        result["error"] = (
            "Відсутній external_id"
        )

        return result

    # --------------------------------------------------------
    # Сума
    # --------------------------------------------------------

    total_price = decimal_value(
        order.get(
            "total_price"
        )
    )

    if total_price <= 0:

        result["error"] = (
            "Некоректна total_price: "
            f"{order.get('total_price')}"
        )

        return result

    # --------------------------------------------------------
    # Визначаємо суму
    # --------------------------------------------------------

    if order_status == (
        STATUS_FULL_PREPAY
    ):

        payment_amount = (
            total_price
        )

        description = (
            f"Повна оплата "
            f"замовлення №{number}"
        )

        payment_type = "full"

    else:

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата "
            f"за замовлення №{number}"
        )

        payment_type = "partial"

    result["payment_type"] = (
        payment_type
    )

    result["amount"] = float(
        payment_amount
    )

    logger.info(
        "[ORDER] "
        "№%s | total=%s | "
        "payment=%s | type=%s",
        number,
        total_price,
        payment_amount,
        payment_type,
    )

    # --------------------------------------------------------
    # 1. Створюємо IBAN invoice
    # --------------------------------------------------------

    payment_link = (
        create_iban_payment_link(
            order_number=number,
            amount=payment_amount,
            description=description,
        )
    )

    if not payment_link:

        result["error"] = (
            "Не вдалося створити "
            "IBAN payment link"
        )

        return result

    result["payment_link"] = (
        payment_link
    )

    # --------------------------------------------------------
    # 2. Записуємо посилання
    #    у personal_info.comment
    # --------------------------------------------------------

    comment_result = (
        update_dntrade_order_comment(
            order=order,
            payment_link=payment_link,
        )
    )

    result["comment_update"] = (
        comment_result
    )

    if not comment_result["success"]:

        result["error"] = (
            "Не вдалося записати "
            "payment link у "
            "personal_info.comment. "
            "Статус НЕ змінюємо."
        )

        logger.error(
            "[ORDER] "
            "№%s: comment не записано. "
            "Статус залишається %s.",
            number,
            order_status,
        )

        return result

    logger.info(
        "[ORDER] "
        "№%s: payment link "
        "записано в comment.",
        number,
    )

    # --------------------------------------------------------
    # 3. Переводимо у статус 1
    # --------------------------------------------------------

    status_result = (
        change_dntrade_order_status(
            external_id=external_id,
            new_status_id=(
                STATUS_WAITING_PAYMENT
            ),
        )
    )

    result["status_update"] = (
        status_result
    )

    if not status_result["success"]:

        result["error"] = (
            "Comment записано, "
            "але статус не вдалося "
            "змінити на 1."
        )

        return result

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    result["success"] = True

    logger.info(
        "[ORDER] "
        "№%s УСПІШНО оброблено.",
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

    if not check_cron_secret():

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
        # 1. Отримуємо список статусів
        # ----------------------------------------------------

        status_list_result = (
            get_dntrade_status_list()
        )

        # ----------------------------------------------------
        # 2. Отримуємо замовлення
        # ----------------------------------------------------

        orders = (
            get_dntrade_orders()
        )

        # ----------------------------------------------------
        # 3. Статистика статусів
        # ----------------------------------------------------

        status_statistics = (
            get_status_statistics(
                orders
            )
        )

        logger.info(
            "[DNTrade] "
            "Статистика status=%s",
            status_statistics,
        )

        # ----------------------------------------------------
        # 4. Визначаємо payable
        # ----------------------------------------------------

        payable_orders = []

        for order in orders:

            if not isinstance(
                order,
                dict,
            ):
                continue

            status = get_order_status(
                order
            )

            if status in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):

                payable_orders.append(
                    order
                )

        logger.info(
            "[DNTrade] "
            "Payable orders=%s",
            len(payable_orders),
        )

        # ----------------------------------------------------
        # 5. Якщо payable немає
        # ----------------------------------------------------

        if not payable_orders:

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": (
                            "Замовлень зі "
                            "статусами 15/16 "
                            "не знайдено."
                        ),
                        "total_orders": len(
                            orders
                        ),
                        "status_statistics": (
                            status_statistics
                        ),
                        "payable_statuses": [
                            STATUS_FULL_PREPAY,
                            STATUS_PARTIAL_PREPAY,
                        ],
                        "status_list": (
                            status_list_result
                        ),
                        "eligible_orders": 0,
                        "processed_count": 0,
                        "skipped_count": len(
                            orders
                        ),
                        "error_count": 0,
                        "orders": [],
                    }
                ),
                200,
            )

        # ----------------------------------------------------
        # 6. Обробляємо payable
        # ----------------------------------------------------

        results = []

        processed_count = 0
        skipped_count = 0
        error_count = 0

        for order in payable_orders:

            result = (
                process_single_order(
                    order
                )
            )

            results.append(
                result
            )

            if result.get(
                "success"
            ):

                processed_count += 1

            elif result.get(
                "skipped"
            ):

                skipped_count += 1

            else:

                error_count += 1

        # ----------------------------------------------------
        # 7. Response
        # ----------------------------------------------------

        response_data = {
            "status": "success",
            "total_orders": len(
                orders
            ),
            "status_statistics": (
                status_statistics
            ),
            "payable_statuses": [
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ],
            "status_list": (
                status_list_result
            ),
            "eligible_orders": len(
                payable_orders
            ),
            "processed_count": (
                processed_count
            ),
            "skipped_count": (
                skipped_count
            ),
            "error_count": (
                error_count
            ),
            "orders": results,
        }

        logger.info(
            "[CRON] FINISH"
        )

        return (
            jsonify(response_data),
            200,
        )

    except Exception as e:

        logger.exception(
            "[CRON] "
            "Критична помилка"
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
                "service": (
                    "DNTrade processor"
                ),
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
                "status": (
                    "DNTrade processor "
                    "is active"
                ),
                "cron": "/cron/process",
                "payable_statuses": [
                    STATUS_FULL_PREPAY,
                    STATUS_PARTIAL_PREPAY,
                ],
                "waiting_payment_status": (
                    STATUS_WAITING_PAYMENT
                ),
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
