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
def inspect_last_page_order():
    try:
        page = 1
        all_orders = []

        # Проходимо по сторінках по 50 замовлень, поки вони є
        while True:
            response = requests.get(
                f"{DNTRADE_API_URL}/orders/list",
                headers=HEADERS_DNTRADE,
                params={"limit": 50, "page": page},
                timeout=10
            )

            if response.status_code != 200:
                break

            data = response.json()
            orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
                data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            )

            if not orders:
                break

            all_orders.extend(orders)

            # Якщо повернулося менше 50, значить це була остання сторінка
            if len(orders) < 50:
                break

            page += 1

        if not all_orders:
            return jsonify({"message": "Замовлень не знайдено"}), 200

        # Знаходимо замовлення з максимальним номером серед усіх сторінок
        def get_num(item):
            try:
                return int(item.get("number", 0))
            except (ValueError, TypeError):
                return 0

        latest_order = max(all_orders, key=get_num)

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
