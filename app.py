import os
import json
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Змінні з Render ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

STATUS_PREPAY_FULL = int(os.environ.get("STATUS_PREPAY_FULL", 15))       # Передплата (15)
STATUS_PREPAY_PARTIAL = int(os.environ.get("STATUS_PREPAY_PARTIAL", 16)) # Передплата/Післясплата (16)
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1)) # ОЧІКУЄМО ОПЛАТУ (1)

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_TOKEN}",
    "Content-Type": "application/json"
}


def run_pipeline():
    logging.info("--- Старт обробки замовлень ---")
    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Помилка DNTrade API: {response.text}")
            return

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        logging.info(f"Отримано замовлень з DNTrade: {len(orders)}")

        if orders:
            first = orders[0]
            # Виводимо ключі, які відповідають за стан та ID
            logging.info("--- АНАЛІЗ ПОЛІВ DNTRADE ---")
            logging.info(f"Ключі об'єкта: {list(first.keys())}")
            logging.info(f"status: {first.get('status')}")
            logging.info(f"state: {first.get('state')}")
            logging.info(f"status_id / state_id: status_id={first.get('status_id')}, state_id={first.get('state_id')}")
            logging.info(f"id / number / code: id={first.get('id')}, number={first.get('number')}, code={first.get('code')}")
            logging.info(f"ПОВНИЙ JSON ПЕРШОГО ЗАМОВЛЕННЯ: {json.dumps(first, ensure_ascii=False)}")

    except Exception as e:
        logging.error(f"Помилка під час запиту замовлень: {e}")
        return

    logging.info("--- Обробку завершено ---")


@app.route("/cron/process", methods=["GET", "POST"])
def cron_handler():
    run_pipeline()
    return jsonify({"status": "success"}), 200


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "bot is running"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
