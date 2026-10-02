import json
import logging
import os
import requests
from flask import Flask, jsonify

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

app = Flask(__name__)

# --- Конфігурація DNTrade ---
DNTRADE_API_URL = os.environ.get(
    "DNTRADE_API_URL", "https://api.dntrade.com.ua"
).rstrip("/")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

# --- Конфігурація IBAN Oplata ---
IBAN_OPLATA_API_URL = os.environ.get(
    "IBAN_OPLATA_API_URL", "https://api.ibanoplata.com"
).rstrip("/")
IBAN_ENDPOINT = os.environ.get("IBAN_ENDPOINT", "/v2/iban-invoice")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

IBAN_ORGANIZATION_NAME = os.environ.get("IBAN_ORGANIZATION_NAME", "")
IBAN_IDENTIFICATION_CODE = os.environ.get("IBAN_IDENTIFICATION_CODE", "")
IBAN_ACCOUNT = os.environ.get("IBAN_ACCOUNT", "")

# --- Статуси замовлень ---
STATUS_FULL_PREPAY = int(os.environ.get("STATUS_PREPAY_FULL", 15))
STATUS_PARTIAL_PREPAY = int(os.environ.get("STATUS_PREPAY_PARTIAL", 16))
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1))

PREPAYMENT_PARTIAL_AMOUNT = float(
    os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", 200.0)
)

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json",
}


def get_iban_headers():
    token = (IBAN_TOKEN or "").strip()
    return {
        "X-API-KEY": token,
        "ApiKey": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def create_iban_payment_link(
    order_number: int | str, amount: float, description: str
) -> str | None:
    """Генерація посилання на оплату через IBAN Oplata API"""
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
        logging.info(f"[IBAN API] POST {url} | Payload: {payload}")
        response = requests.post(
            url, json=payload, headers=headers, timeout=10
        )
        logging.info(
            f"[IBAN API] Status: {response.status_code} | Response: {response.text}"
        )

        if response.status_code in (200, 201):
            data = response.json()
            return data.get("ibanInvoiceUrl") or data.get("url")

        logging.error(f"[IBAN API] Помилка: {response.text}")
        return None
    except Exception:
        logging.exception("[IBAN API] Виключення при запиті:")
        return None


def update_dntrade_order_note(order_data: dict, payment_link: str) -> bool:
    """Запис посилання на оплату в поле note через /orders/upload"""
    url = f"{DNTRADE_API_URL}/orders/upload"

    # Формуємо об'єкт із передачею нових даних примітки та збереженням основних полів
    updated_order = dict(order_data)
    updated_order["note"] = payment_link

    # Переконуємося, що масив товарів заповнений
    if "products" not in updated_order or updated_order["products"] is None:
        updated_order["products"] = []

    payload = {"orders": [updated_order]}

    try:
        response = requests.post(
            url, json=payload, headers=HEADERS_DNTRADE, timeout=10
        )
        logging.info(
            f"[DNTrade Note Update] Status [{response.status_code}]: {response.text}"
        )
        return response.status_code in (200, 201)
    except Exception:
        logging.exception("[DNTrade Note Update] Помилка запису note:")
        return False


def change_dntrade_order_status(
    order_id: str, new_status_id: int
) -> bool:
    """Зміна статусу замовлення через /orders/setstatus"""
    url = f"{DNTRADE_API_URL}/orders/setstatus"

    payload = {
        "id": order_id,
        "external_id": order_id,
        "status_id": new_status_id,
        "status": new_status_id,
        "Status": new_status_id,
        "Id": order_id,
    }

    try:
        response = requests.post(
            url, json=payload, headers=HEADERS_DNTRADE, timeout=10
        )
        logging.info(
            f"[DNTrade Status Change] Status [{response.status_code}]: {response.text}"
        )
        return response.status_code in (200, 201)
    except Exception:
        logging.exception("[DNTrade Status Change] Помилка зміни статусу:")
        return False


@app.route("/cron/process", methods=["GET", "POST"])
def process_dntrade_orders():
    """Основна функція обробки замовлень"""
    try:
        params = {"limit": 50, "page": 1}

        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params=params,
            timeout=10,
        )

        if response.status_code != 200:
            return (
                jsonify(
                    {"error": f"DNTrade API повернув помилку: {response.text}"}
                ),
                response.status_code,
            )

        res_data = response.json()
        orders = (
            res_data.get("data", [])
            if isinstance(res_data, dict) and "data" in res_data
            else (
                res_data.get("orders", [])
                if isinstance(res_data, dict)
                else (res_data if isinstance(res_data, list) else [])
            )
        )

        processed_orders = []

        for order in orders:
            order_status = order.get("order_status")

            # Обробляємо тільки статуси 15 та 16
            if order_status not in (
                STATUS_FULL_PREPAY,
                STATUS_PARTIAL_PREPAY,
            ):
                continue

            external_id = order.get("external_id")
            number = order.get("number")
            total_price = float(order.get("total_price", 0))

            if order_status == STATUS_FULL_PREPAY:
                payment_amount = total_price
                desc = f"Повна оплата замовлення №{number}"
            else:
                payment_amount = min(PREPAYMENT_PARTIAL_AMOUNT, total_price)
                desc = f"Передплата за замовлення №{number}"

            # 1. Створюємо посилання на оплату
            payment_link = create_iban_payment_link(
                number, payment_amount, desc
            )

            if not payment_link:
                logging.error(
                    f"Не вдалося згенерувати посилання для замовлення №{number}"
                )
                continue

            # 2. Оновлюємо примітку (записуємо посилання)
            note_ok = update_dntrade_order_note(order, payment_link)

            # 3. Змінюємо статус на 1 ("Очікує оплату")
            status_ok = change_dntrade_order_status(
                external_id, STATUS_WAITING_PAYMENT
            )

            processed_orders.append(
                {
                    "number": number,
                    "external_id": external_id,
                    "order_status_before": order_status,
                    "amount": payment_amount,
                    "payment_link": payment_link,
                    "note_updated": note_ok,
                    "status_changed_to_1": status_ok,
                }
            )

        return (
            jsonify(
                {
                    "status": "success",
                    "processed_count": len(processed_orders),
                    "processed_orders": processed_orders,
                }
            ),
            200,
        )

    except Exception as e:
        logging.exception("Виключення під час обробки:")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "DNTrade processor is active"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
