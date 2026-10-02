import os
import json
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}


@app.route("/cron/process", methods=["GET", "POST"])
def inspect_latest_order():
    logging.info("=== ДІАГНОСТИКА: Запит останнього замовлення ===")
    try:
        # Запитуємо найостанніше замовлення
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 1, "sort": "-id"},
            timeout=10
        )

        if response.status_code != 200:
            logging.error(f"Помилка DNTrade API [{response.status_code}]: {response.text}")
            return jsonify({"error": response.text}), response.status_code

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        if not orders:
            logging.info("Замовлень не знайдено.")
            return jsonify({"status": "no orders found"}), 200

        latest_order = orders[0]

        # Виводимо повний красивий JSON замовлення в лог
        pretty_order = json.dumps(latest_order, ensure_ascii=False, indent=2)
        logging.info(f"\n--- ПОВНА СТРУКТУРА ОСТАННЬОГО ЗАМОВЛЕННЯ ---\n{pretty_order}\n---------------------------------------------")

        return jsonify({
            "order_number": latest_order.get("number"),
            "order_status": latest_order.get("order_status"),
            "status": latest_order.get("status"),
            "full_data": latest_order
        }), 200

    except Exception as e:
        logging.error(f"Збій діагностики: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "inspector is ready"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
