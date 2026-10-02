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
def inspect_absolutely_latest_order():
    try:
        # 1. Запитуємо першу сторінку з дозволеним limit = 50
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50, "page": 1},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"error": response.text}), response.status_code

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        # Перевіряємо кількість сторінок
        total_pages = 1
        if isinstance(data, dict):
            meta = data.get("meta") or data.get("pagination") or {}
            total_pages = meta.get("last_page") or meta.get("total_pages") or 1

        # Якщо сторінок більше ніж одна — отримуємо найостаннішу сторінку
        if total_pages > 1:
            last_resp = requests.get(
                f"{DNTRADE_API_URL}/orders/list",
                headers=HEADERS_DNTRADE,
                params={"limit": 50, "page": total_pages},
                timeout=10
            )
            if last_resp.status_code == 200:
                last_data = last_resp.json()
                orders = last_data.get("data", []) if isinstance(last_data, dict) and "data" in last_data else (
                    last_data.get("orders", []) if isinstance(last_data, dict) else (last_data if isinstance(last_data, list) else [])
                )

        if not orders:
            return jsonify({"message": "Замовлень не знайдено"}), 200

        # Знаходимо замовлення з найбільшим номером серед отриманих
        def extract_number(item):
            try:
                return int(item.get("number", 0))
            except (ValueError, TypeError):
                return 0

        latest_order = max(orders, key=extract_number)

        # Виводимо чистий JSON у браузер
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
