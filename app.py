import os
import json
import logging
import requests
from flask import Flask, jsonify, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}


@app.route("/cron/process", methods=["GET", "POST"])
def inspect_single_order():
    try:
        # Запитуємо останні замовлення
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"error": response.text}), response.status_code

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        if not orders:
            return jsonify({"message": "Замовлень не знайдено в CRM"}), 200

        # Гарантовано сортуємо за id по спаданню (найновіше першим)
        try:
            sorted_orders = sorted(orders, key=lambda x: int(x.get("id", 0)), reverse=True)
        except Exception:
            sorted_orders = orders

        latest_order = sorted_orders[0]

        # Формуємо чистий і читабельний JSON для виводу в браузер
        pretty_json = json.dumps(latest_order, ensure_ascii=False, indent=4)
        return Response(pretty_json, mimetype="application/json")

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "inspector is ready"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
