import os
import json
import logging
import requests
from flask import Flask, jsonify, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

STATUS_PREPAY_FULL = int(os.environ.get("STATUS_PREPAY_FULL", 3))
STATUS_PREPAY_PARTIAL = int(os.environ.get("STATUS_PREPAY_PARTIAL", 2))
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1))

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_TOKEN}",
    "Content-Type": "application/json"
}


def change_order_status(order_id: str | int, new_status_id: int) -> bool:
    """Зміна статусу замовлення через офіційний ендпоінт /orders/setstatus"""
    url = f"{DNTRADE_API_URL}/orders/setstatus"
    payload = {
        "id": order_id,
        "status_id": new_status_id
    }
    try:
        res = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"Відповідь setstatus [{res.status_code}]: {res.text}")
        return res.status_code in (200, 201)
    except Exception as e:
        logging.error(f"Помилка setstatus: {e}")
        return False


def update_order_note(external_id: str, number: str | int, note_text: str) -> bool:
    """Оновлення примітки замовлення через /orders/upload"""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "orders": [
            {
                "external_id": external_id,
                "number": number,
                "note": note_text
            }
        ]
    }
    try:
        res = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"Відповідь note upload [{res.status_code}]: {res.text}")
        return res.status_code in (200, 201)
    except Exception as e:
        logging.error(f"Помилка upload note: {e}")
        return False


@app.route("/cron/process", methods=["GET", "POST"])
def process_order_by_number():
    try:
        # 1. Запитуємо замовлення №225 напряму
        res = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"number": 225},
            timeout=10
        )

        if res.status_code != 200:
            return jsonify({"error": res.text}), res.status_code

        data = res.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else []

        if not orders:
            return jsonify({"message": "Замовлення №225 не знайдено"}), 404

        order = orders[0]
        order_id = order.get("id")
        external_id = order.get("external_id")
        number = order.get("number")
        current_status = order.get("order_status")

        # 2. Змінюємо статус замовлення на Очікуємо оплату (STATUS_WAITING_PAYMENT)
        status_updated = change_order_status(order_id, STATUS_WAITING_PAYMENT)

        # 3. Додаємо примітку з посиланням
        payment_link = LINK_200  # або динамічне посилання
        note_updated = update_order_note(external_id, number, f"Посилання на оплату: {payment_link}")

        return jsonify({
            "order_number": number,
            "previous_status": current_status,
            "status_updated": status_updated,
            "note_updated": note_updated,
            "order_data": order
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "ready"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
