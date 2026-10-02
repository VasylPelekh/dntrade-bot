import os
import json
import logging
import requests
from flask import Flask, jsonify, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Конфігурація змінних оточення ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua").rstrip("/")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

# За замовчуванням ставимо правильний URL https://api.ibanoplata.com
IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.ibanoplata.com").rstrip("/")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

# Статичні посилання (якщо потрібні як фолбек)
LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Статуси з DNTrade
STATUS_FULL_PREPAY = int(os.environ.get("STATUS_PREPAY_FULL", 15))
STATUS_PARTIAL_PREPAY = int(os.environ.get("STATUS_PREPAY_PARTIAL", 16))
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1))

PREPAYMENT_PARTIAL_AMOUNT = float(os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", 200.0))

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_TOKEN}",
    "Content-Type": "application/json"
}


def create_iban_payment_link(order_number: int | str, amount: float, description: str) -> str | None:
    """Генерація посилання на оплату через API або використання готових статичних посилань"""
    
    # 1. Спроба створити динамічний інвойс через IBAN-Oplata API
    if IBAN_TOKEN:
        url = f"{IBAN_OPLATA_API_URL}/invoices"
        payload = {
            "amount": round(amount, 2),
            "order_id": str(order_number),
            "description": description
        }
        try:
            response = requests.post(url, json=payload, headers=HEADERS_IBAN, timeout=10)
            logging.info(f"[IBAN API] Response [{response.status_code}]: {response.text}")
            if response.status_code in (200, 201):
                data = response.json()
                link = data.get("page_url") or data.get("url") or data.get("link") or data.get("payment_url")
                if link:
                    return link
        except Exception as e:
            logging.error(f"[IBAN API] Помилка створення інвойсу через API: {e}")

    # 2. Фолбек на готові статичні посилання з Render, якщо API не повернуло посилання
    if amount == 200.0 and LINK_200:
        return LINK_200
    if amount == 500.0 and LINK_500:
        return LINK_500

    return None


def update_dntrade_order_note(external_id: str, number: int | str, payment_link: str) -> bool:
    """Оновлення поля 'note' замовлення через /orders/upload"""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "orders": [
            {
                "external_id": external_id,
                "number": number,
                "note": payment_link
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"[DNTrade Note] Response [{response.status_code}]: {response.text}")
        return response.status_code in (200, 201)
    except Exception as e:
        logging.error(f"[DNTrade Note] Помилка запису note: {e}")
        return False


def change_dntrade_order_status(order_id: str | int, new_status_id: int) -> bool:
    """Зміна статусу замовлення через /orders/setstatus"""
    url = f"{DNTRADE_API_URL}/orders/setstatus"
    payload = {
        "id": order_id,
        "status_id": new_status_id
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"[DNTrade Status] Response [{response.status_code}]: {response.text}")
        return response.status_code in (200, 201)
    except Exception as e:
        logging.error(f"[DNTrade Status] Помилка зміни статусу: {e}")
        return False


@app.route("/cron/process", methods=["GET", "POST"])
def process_dntrade_orders():
    try:
        params = {"limit": 50, "page": 1}
        
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params=params,
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"error": f"DNTrade API повернув помилку: {response.text}"}), response.status_code

        res_data = response.json()
        orders = res_data.get("data", []) if isinstance(res_data, dict) and "data" in res_data else (
            res_data.get("orders", []) if isinstance(res_data, dict) else (res_data if isinstance(res_data, list) else [])
        )

        processed_orders = []

        for order in orders:
            order_status = order.get("order_status")
            
            if order_status not in (STATUS_FULL_PREPAY, STATUS_PARTIAL_PREPAY):
                continue

            order_id = order.get("id")
            external_id = order.get("external_id")
            number = order.get("number")
            total_price = float(order.get("total_price", 0))

            if order_status == STATUS_FULL_PREPAY:
                payment_amount = total_price
                desc = f"Повна оплата замовлення №{number}"
            else:
                payment_amount = min(PREPAYMENT_PARTIAL_AMOUNT, total_price)
                desc = f"Передплата за замовлення №{number}"

            payment_link = create_iban_payment_link(number, payment_amount, desc)
            
            if not payment_link:
                logging.error(f"Не вдалося отримати/згенерувати посилання для замовлення №{number}")
                continue

            note_ok = update_dntrade_order_note(external_id, number, payment_link)

            status_ok = False
            if note_ok:
                status_ok = change_dntrade_order_status(order_id, STATUS_WAITING_PAYMENT)

            processed_orders.append({
                "number": number,
                "order_status_before": order_status,
                "amount": payment_amount,
                "payment_link": payment_link,
                "note_updated": note_ok,
                "status_changed_to_1": status_ok
            })

        return jsonify({
            "status": "success",
            "processed_count": len(processed_orders),
            "processed_orders": processed_orders
        }), 200

    except Exception as e:
        logging.exception("Виключення під час обробки замовлень:")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "DNTrade processor is active"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
