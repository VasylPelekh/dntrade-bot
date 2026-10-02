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
def inspect_latest_order():
    try:
        # Пробуємо витягнути замовлення з явним напрямком сортування по спаданню
        params_list = [
            {"limit": 10, "sort_by": "id", "sort_dir": "desc"},
            {"limit": 10, "order_by": "id", "order_dir": "desc"},
            {"limit": 10, "sort": "-id"},
            {"limit": 10}
        ]

        all_orders = []
        for params in params_list:
            response = requests.get(
                f"{DNTRADE_API_URL}/orders/list",
                headers=HEADERS_DNTRADE,
                params=params,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
                    data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                )
                if orders:
                    all_orders.extend(orders)

        if not all_orders:
            return jsonify({"message": "Замовлень не знайдено в CRM"}), 200

        # Пошук замовлення з максимальним ID / новером
        def get_order_key(item):
            try:
                # Перевіряємо номер або ID
                num = item.get("number") or item.get("id") or 0
                return int(num)
            except (ValueError, TypeError):
                return 0

        latest_order = max(all_orders, key=get_order_key)

        # Повертаємо структурований JSON тільки для найновішого замовлення
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
