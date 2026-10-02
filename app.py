import logging
import os
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

PREPAYMENT_PARTIAL_AMOUNT = Decimal(
    os.environ.get(
        "PREPAYMENT_PARTIAL_AMOUNT",
        "200",
    )
)

DNTRADE_PAGE_SIZE = 50

DNTRADE_MAX_PAGES = int(
    os.environ.get(
        "DNTRADE_MAX_PAGES",
        "100",
    )
)

REQUEST_TIMEOUT = int(
    os.environ.get(
        "REQUEST_TIMEOUT",
        "20",
    )
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

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


def normalize_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value)
        .strip()
        .lower()
        .split()
    )


def check_cron_secret():
    if not CRON_SECRET:
        return True

    supplied_secret = request.headers.get(
        "X-Cron-Secret",
        "",
    ).strip()

    return supplied_secret == CRON_SECRET


# ============================================================
# DNTRADE STATUS LIST
# ============================================================

def get_dntrade_status_list():
    url = (
        f"{DNTRADE_API_URL}"
        "/orders/statuslist"
    )

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

        statuses = data.get(
            "data",
            [],
        )

        if not isinstance(statuses, list):
            statuses = []

        return {
            "success": True,
            "status_code": response.status_code,
            "statuses": statuses,
        }

    except Exception:
        logging.exception(
            "[DNTrade StatusList] Помилка"
        )

        return {
            "success": False,
            "status_code": None,
            "statuses": [],
        }


# ============================================================
# STATUS MAP
# ============================================================

def build_status_maps(status_list):
    id_to_title = {}
    title_to_id = {}

    for item in status_list:

        if not isinstance(item, dict):
            continue

        status_id = item.get("id")
        title = item.get("title")

        try:
            status_id = int(status_id)
        except (
            TypeError,
            ValueError,
        ):
            continue

        if title is None:
            continue

        title_text = str(title).strip()

        id_to_title[status_id] = title_text

        title_to_id[
            normalize_text(title_text)
        ] = status_id

    return (
        id_to_title,
        title_to_id,
    )


def resolve_order_status(
    order,
    title_to_id,
):
    raw_status = order.get("status")

    if raw_status is None:
        raw_status = order.get(
            "order_status"
        )

    if raw_status is None:
        return None

    try:
        return int(raw_status)
    except (
        TypeError,
        ValueError,
    ):
        pass

    normalized = normalize_text(
        raw_status
    )

    if normalized in title_to_id:
        return title_to_id[
            normalized
        ]

    return None


# ============================================================
# GET ORDERS
# ============================================================

def extract_orders_from_response(data):

    if not isinstance(data, dict):
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


def get_dntrade_orders():

    all_orders = []

    for page in range(
        DNTRADE_MAX_PAGES
    ):

        offset = (
            page
            * DNTRADE_PAGE_SIZE
        )

        params = {
            "limit": DNTRADE_PAGE_SIZE,
            "offset": offset,
        }

        url = (
            f"{DNTRADE_API_URL}"
            "/orders/list"
        )

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
                "[DNTrade Orders] "
                "Помилка запиту"
            )
            break

        logging.info(
            "[DNTrade Orders] HTTP %s",
            response.status_code,
        )

        if response.status_code != 200:

            logging.error(
                "[DNTrade Orders] %s",
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
                "DNTrade /orders/list "
                "повернув некоректний JSON"
            )

        orders = extract_orders_from_response(
            data
        )

        logging.info(
            "[DNTrade Orders] "
            "offset=%s orders=%s",
            offset,
            len(orders),
        )

        if not orders:
            break

        all_orders.extend(
            orders
        )

        if len(orders) < DNTRADE_PAGE_SIZE:
            break

    logging.info(
        "[DNTrade Orders] "
        "Всього: %s",
        len(all_orders),
    )

    return all_orders


# ============================================================
# STATUS STATISTICS
# ============================================================

def get_status_statistics(
    orders,
    title_to_id,
):
    statistics = {}

    for order in orders:

        status_id = resolve_order_status(
            order,
            title_to_id,
        )

        if status_id is None:
            raw_status = order.get(
                "status",
                "UNKNOWN",
            )

            key = str(
                raw_status
            )

        else:
            key = str(
                status_id
            )

        statistics[key] = (
            statistics.get(
                key,
                0,
            )
            + 1
        )

    return statistics


# ============================================================
# IBAN CREATE INVOICE
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
            amount
        ),
        "paymentPurpose": description,
        "notes": (
            f"Замовлення №{order_number}"
        ),
        "clientNotes": (
            f"Оплата замовлення №{order_number}"
        ),
        "expirationHours": 24,
    }

    try:

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
            headers=get_iban_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[IBAN API] HTTP %s: %s",
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

        except Exception:
            logging.error(
                "[IBAN API] "
                "Некоректний JSON"
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

            logging.error(
                "[IBAN API] "
                "Посилання відсутнє: %s",
                data,
            )

            return None

        return str(
            payment_link
        )

    except Exception:

        logging.exception(
            "[IBAN API] "
            "Виключення"
        )

        return None


# ============================================================
# UPDATE DNTRADE COMMENT
# ============================================================

def update_dntrade_order_comment(
    order_data,
    payment_link,
):

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/upload"
    )

    external_id = order_data.get(
        "external_id"
    )

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

        if response.status_code in (
            200,
            201,
        ):
            return True

        return False

    except Exception:

        logging.exception(
            "[DNTrade Upload] "
            "Помилка"
        )

        return False


# ============================================================
# CHANGE STATUS
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
        "id": order_id,
        "status": new_status_id,
    }

    try:

        logging.info(
            "[DNTrade Status] POST %s",
            url,
        )

        logging.info(
            "[DNTrade Status] Payload: %s",
            payload,
        )

        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Status] HTTP %s: %s",
            response.status_code,
            response.text,
        )

        return response.status_code in (
            200,
            201,
        )

    except Exception:

        logging.exception(
            "[DNTrade Status] "
            "Помилка"
        )

        return False


# ============================================================
# PROCESS ONE ORDER
# ============================================================

def process_single_order(
    order,
    resolved_status,
):

    external_id = order.get(
        "external_id"
    )

    number = order.get(
        "number"
    )

    total_price = decimal_value(
        order.get(
            "total_price"
        )
    )

    logging.info(
        "[PROCESS] "
        "№%s id=%s status=%s total=%s",
        number,
        external_id,
        resolved_status,
        total_price,
    )

    if not external_id:

        return {
            "success": False,
            "error": (
                "Відсутній external_id"
            ),
            "number": number,
        }

    if total_price <= 0:

        return {
            "success": False,
            "error": (
                "total_price <= 0"
            ),
            "number": number,
            "external_id": external_id,
        }

    # Статус 15:
    # повна оплата

    if resolved_status == STATUS_FULL_PREPAY:

        payment_amount = (
            total_price
        )

        description = (
            f"Повна оплата "
            f"замовлення №{number}"
        )

    # Статус 16:
    # передплата / післяплата

    elif (
        resolved_status
        == STATUS_PARTIAL_PREPAY
    ):

        payment_amount = min(
            PREPAYMENT_PARTIAL_AMOUNT,
            total_price,
        )

        description = (
            f"Передплата "
            f"за замовлення №{number}"
        )

    else:

        return {
            "success": False,
            "skipped": True,
            "error": (
                "Статус не платіжний"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
        }

    # --------------------------------------------------------
    # IBAN
    # --------------------------------------------------------

    payment_link = create_iban_payment_link(
        order_number=number,
        amount=payment_amount,
        description=description,
    )

    if not payment_link:

        return {
            "success": False,
            "error": (
                "Не вдалося створити "
                "IBAN-посилання"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(
                payment_amount
            ),
        }

    # --------------------------------------------------------
    # COMMENT
    # --------------------------------------------------------

    comment_ok = (
        update_dntrade_order_comment(
            order_data=order,
            payment_link=payment_link,
        )
    )

    if not comment_ok:

        return {
            "success": False,
            "error": (
                "IBAN-посилання створено, "
                "але не вдалося записати "
                "його в коментар DNTrade"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(
                payment_amount
            ),
            "payment_link": payment_link,
            "comment_updated": False,
        }

    # --------------------------------------------------------
    # STATUS -> 1
    # --------------------------------------------------------

    status_ok = (
        change_dntrade_order_status(
            order_id=external_id,
            new_status_id=STATUS_WAITING_PAYMENT,
        )
    )

    if not status_ok:

        return {
            "success": False,
            "error": (
                "Коментар записано, "
                "але не вдалося змінити "
                "статус на 1"
            ),
            "number": number,
            "external_id": external_id,
            "status": resolved_status,
            "amount": float(
                payment_amount
            ),
            "payment_link": payment_link,
            "comment_updated": True,
            "status_changed": False,
        }

    return {
        "success": True,
        "number": number,
        "external_id": external_id,
        "status_before": resolved_status,
        "status_after": STATUS_WAITING_PAYMENT,
        "amount": float(
            payment_amount
        ),
        "payment_link": payment_link,
        "comment_updated": True,
        "status_changed": True,
    }


# ============================================================
# CRON
# ============================================================

@app.route(
    "/cron/process",
    methods=[
        "GET",
        "POST",
    ],
)
def process_dntrade_orders():

    try:

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

        # ----------------------------------------------------
        # STATUS LIST
        # ----------------------------------------------------

        status_data = (
            get_dntrade_status_list()
        )

        if not status_data[
            "success"
        ]:

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

        status_list = (
            status_data[
                "statuses"
            ]
        )

        (
            id_to_title,
            title_to_id,
        ) = build_status_maps(
            status_list
        )

        logging.info(
            "[STATUS MAP] %s",
            id_to_title,
        )

        # ----------------------------------------------------
        # ORDERS
        # ----------------------------------------------------

        orders = (
            get_dntrade_orders()
        )

        total_orders = len(
            orders
        )

        # ----------------------------------------------------
        # STATISTICS
        # ----------------------------------------------------

        status_statistics = (
            get_status_statistics(
                orders,
                title_to_id,
            )
        )

        logging.info(
            "[STATUS STATISTICS] %s",
            status_statistics,
        )

        # ----------------------------------------------------
        # FIND 15 / 16
        # ----------------------------------------------------

        payable_orders = []

        for order in orders:

            resolved_status = (
                resolve_order_status(
                    order,
                    title_to_id,
                )
            )

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

        eligible_orders = len(
            payable_orders
        )

        logging.info(
            "[PROCESS] "
            "Платіжних замовлень: %s",
            eligible_orders,
        )

        # ----------------------------------------------------
        # PROCESS
        # ----------------------------------------------------

        processed_orders = []

        error_count = 0

        for (
            order,
            resolved_status,
        ) in payable_orders:

            result = (
                process_single_order(
                    order,
                    resolved_status,
                )
            )

            processed_orders.append(
                result
            )

            if not result.get(
                "success"
            ):
                error_count += 1

        skipped_count = (
            total_orders
            - eligible_orders
        )

        # ----------------------------------------------------
        # MESSAGE
        # ----------------------------------------------------

        if eligible_orders == 0:

            message = (
                "Замовлень зі статусами "
                f"{STATUS_FULL_PREPAY}/"
                f"{STATUS_PARTIAL_PREPAY} "
                "не знайдено."
            )

        else:

            message = (
                f"Знайдено "
                f"{eligible_orders} "
                "замовлень для обробки."
            )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return (
            jsonify(
                {
                    "status": "success",
                    "message": message,
                    "total_orders": total_orders,
                    "eligible_orders": eligible_orders,
                    "processed_count": sum(
                        1
                        for item
                        in processed_orders
                        if item.get(
                            "success"
                        )
                    ),
                    "error_count": error_count,
                    "skipped_count": skipped_count,
                    "payable_statuses": [
                        STATUS_FULL_PREPAY,
                        STATUS_PARTIAL_PREPAY,
                    ],
                    "status_list": {
                        "success": (
                            status_data[
                                "success"
                            ]
                        ),
                        "status_code": (
                            status_data[
                                "status_code"
                            ]
                        ),
                        "statuses": status_list,
                    },
                    "status_statistics": (
                        status_statistics
                    ),
                    "orders": (
                        processed_orders
                    ),
                }
            ),
            200,
        )

    except Exception as e:

        logging.exception(
            "[PROCESS] Критична помилка"
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
    methods=[
        "GET",
        "HEAD",
    ],
)
def health():

    return (
        jsonify(
            {
                "status": "ok",
                "service": (
                    "DNTrade -> IBAN processor"
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
    methods=[
        "GET",
        "HEAD",
    ],
)
def index():

    return (
        jsonify(
            {
                "status": (
                    "DNTrade processor is active"
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

    app.run(
        host="0.0.0.0",
        port=port,
    )
