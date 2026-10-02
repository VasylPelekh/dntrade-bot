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

    except Exception:

        logging.exception(
            "[DNTrade StatusList] "
            "Помилка"
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

        if not isinstance(
            item,
            dict,
        ):
            continue

        status_id = item.get(
            "id"
        )

        title = item.get(
            "title"
        )

        try:
            status_id = int(
                status_id
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if title is None:
            continue

        title_text = str(
            title
        ).strip()

        id_to_title[
            status_id
        ] = title_text

        title_to_id[
            normalize_text(
                title_text
            )
        ] = status_id

    return (
        id_to_title,
        title_to_id,
    )


# ============================================================
# GET ORDERS
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
            "[DNTrade Orders] "
            "GET %s params=%s",
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

        orders = (
            extract_orders_from_response(
                data
            )
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
# STATUS FIELD DIAGNOSTICS
# ============================================================

def collect_status_fields(
    value,
    path="",
    result=None,
):
    """
    Рекурсивно шукає всі поля,
    назва яких містить:

        status
        state

    Наприклад:

        status
        order_status
        status_id
        statusId
        orderStatus
        delivery_status
        payment_status

    Це тільки діагностика.
    """

    if result is None:
        result = {}

    if isinstance(
        value,
        dict,
    ):

        for key, child in value.items():

            key_text = str(
                key
            )

            current_path = (
                f"{path}.{key_text}"
                if path
                else key_text
            )

            key_normalized = (
                key_text.lower()
            )

            if (
                "status" in key_normalized
                or "state" in key_normalized
            ):

                result[
                    current_path
                ] = child

            if isinstance(
                child,
                (
                    dict,
                    list,
                ),
            ):

                collect_status_fields(
                    child,
                    current_path,
                    result,
                )

    elif isinstance(
        value,
        list,
    ):

        for index, child in enumerate(
            value
        ):

            current_path = (
                f"{path}[{index}]"
            )

            if isinstance(
                child,
                (
                    dict,
                    list,
                ),
            ):

                collect_status_fields(
                    child,
                    current_path,
                    result,
                )

    return result


def build_order_diagnostics(
    orders,
    status_list,
):
    """
    Повертає діагностику перших
    10 замовлень.

    Особисті дані клієнта
    навмисно не повертаємо.
    """

    diagnostics = []

    for order in orders[:10]:

        if not isinstance(
            order,
            dict,
        ):
            continue

        status_fields = (
            collect_status_fields(
                order
            )
        )

        diagnostics.append(
            {
                "external_id": order.get(
                    "external_id"
                ),
                "number": order.get(
                    "number"
                ),
                "top_level_keys": sorted(
                    list(
                        order.keys()
                    )
                ),
                "status_fields": (
                    status_fields
                ),
            }
        )

    return diagnostics


# ============================================================
# STATUS STATISTICS
# ============================================================

def get_status_statistics(
    orders,
):

    statistics = {}

    for order in orders:

        if not isinstance(
            order,
            dict,
        ):
            continue

        raw_status = order.get(
            "status",
            "UNKNOWN",
        )

        key = str(
            raw_status
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
# IBAN
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

        data = response.json()

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

        return (
            str(payment_link)
            if payment_link
            else None
        )

    except Exception:

        logging.exception(
            "[IBAN API] Помилка"
        )

        return None


# ============================================================
# UPDATE COMMENT
# ============================================================

def update_dntrade_order_comment(
    order_data,
    payment_link,
):

    url = (
        f"{DNTRADE_API_URL}"
        "/orders/upload"
    )

    payload = {
        "id": order_data.get(
            "external_id"
        ),
        "personal_info": {
            "comment": payment_link,
        },
    }

    try:

        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Upload] "
            "HTTP %s: %s",
            response.status_code,
            response.text,
        )

        return response.status_code in (
            200,
            201,
        )

    except Exception:

        logging.exception(
            "[DNTrade Upload] Помилка"
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

        response = requests.post(
            url,
            json=payload,
            headers=get_dntrade_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        logging.info(
            "[DNTrade Status] "
            "HTTP %s: %s",
            response.status_code,
            response.text,
        )

        return response.status_code in (
            200,
            201,
        )

    except Exception:

        logging.exception(
            "[DNTrade Status] Помилка"
        )

        return False


# ============================================================
# PROCESS
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
                            "список статусів"
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
                orders
            )
        )

        # ----------------------------------------------------
        # DIAGNOSTICS
        # ----------------------------------------------------

        diagnostics = (
            build_order_diagnostics(
                orders,
                status_list,
            )
        )

        # ----------------------------------------------------
        # CHECK POSSIBLE STATUS FIELDS
        # ----------------------------------------------------

        possible_status_fields = {}

        for item in diagnostics:

            fields = item.get(
                "status_fields",
                {},
            )

            for key, value in fields.items():

                if key not in possible_status_fields:

                    possible_status_fields[
                        key
                    ] = []

                value_text = repr(
                    value
                )

                if (
                    value_text
                    not in possible_status_fields[
                        key
                    ]
                ):

                    possible_status_fields[
                        key
                    ].append(
                        value_text
                    )

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        return (
            jsonify(
                {
                    "status": "success",
                    "message": (
                        "Діагностика статусів "
                        "завершена. "
                        "IBAN та зміна статусів "
                        "поки не виконуються."
                    ),
                    "total_orders": total_orders,
                    "status_statistics": (
                        status_statistics
                    ),
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
                    "possible_status_fields": (
                        possible_status_fields
                    ),
                    "order_diagnostics": (
                        diagnostics
                    ),
                }
            ),
            200,
        )

    except Exception as e:

        logging.exception(
            "[PROCESS] "
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
                    "DNTrade diagnostics"
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
                    "DNTrade processor "
                    "is active"
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
