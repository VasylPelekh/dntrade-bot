import os
import json
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- DNTrade configuration ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua").rstrip("/")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

# --- IBAN Oplata configuration ---
IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.ibanoplata.com").rstrip("/")
IBAN_ENDPOINT = os.environ.get("IBAN_ENDPOINT", "/api/v1/invoices")  # Вкажіть точний роут зі Swagger
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

# Дані вашої організації для IBAN
IBAN_ORGANIZATION_NAME = os.environ.get("IBAN_ORGANIZATION_NAME", "")
IBAN_IDENTIFICATION_CODE = os.environ.get("IBAN_IDENTIFICATION_CODE", "")
IBAN_ACCOUNT = os.environ.get("IBAN_ACCOUNT", "")

# Фолбек-посилання з Render
LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Статуси DNTrade
STATUS_FULL_PREPAY = int(os.environ.get("STATUS_PREPAY_FULL", 15))       # Передплата (100%)
STATUS_PARTIAL_PREPAY = int(os.environ.get("STATUS_PREPAY_PARTIAL", 16)) # Передплата + Післясплата
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1)) # Очікує оплату

PREPAYMENT_PARTIAL_AMOUNT = float(os.environ.get("PREPAYMENT_PARTIAL_AMOUNT", 200.0))

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}


def extract_url_from_response(data: dict | str) -> str | None:
    """Витягує посилання на оплату з відповіді API IBAN Oplata"""
    if isinstance(data, str) and data.startswith("http"):
        return data

    if isinstance(data, dict):
        for key in ["page_url", "url", "link", "payment_url", "short_url", "checkout_url", "ibanInvoiceId"]:
            val = data.get(key)
            if val and isinstance(val, str) and val.startswith("http"):
                return val

        nested = data.get("data") or data.get("result")
        if isinstance(nested, dict):
            for key in ["page_url", "url", "link", "payment_url", "short_url", "checkout_url"]:
                val = nested.get(key)
                if val and isinstance(val, str) and val.startswith("http"):
                    return val

    return None


def create_iban_payment_link(order_number: int | str, amount: float, description: str) -> str | None:
    """Генерація посилання на оплату відповідно до точної специфікації Swagger IBAN Oplata"""
    if IBAN_TOKEN:
        # Формуємо шлях запиту
        endpoint_path = IBAN_ENDPOINT if IBAN_ENDPOINT.startswith("/") else f"/{IBAN_ENDPOINT}"
        url = f"{IBAN_OPLATA_API_URL}{endpoint_path}"

        # ТОЧНЕ ТІЛО ЗАПИТУ ЗІ СВАТГЕРА
        payload = {
            "organizationName": IBAN_ORGANIZATION_NAME,
            "identificationCode": IBAN_IDENTIFICATION_CODE,
            "iban": IBAN_ACCOUNT,
            "amount": round(amount, 2),
            "paymentPurpose": description,
            "notes": f"Замовлення №{order_number}",
            "clientNotes": f"Оплата замовлення №{order_number}",
            "expirationHours": 24
        }

        try:
            logging.info(f"[IBAN API] POST {url} | Payload: {payload}")
            response = requests.post(url, json=payload, headers=HEADERS_IBAN, timeout=10)
            logging.info(f"[IBAN API] Status: {response.status_code} | Response: {response.text}")

            if response.status_code in (200, 201):
                try:
                    res_data = response.json()
                except Exception:
                    res_data = response.text

                payment_link = extract_url_from_response(res_data)
                if payment_link:
                    return payment_link
                else:
                    logging.warning(f"[IBAN API] Не вдалося знайти URL у відповіді: {res_data}")

        except Exception as e:
            logging.error(f"[IBAN API] Виключення при запиті: {e}")

    # Фолбек на готові лінки, якщо API повернуло помилку
    if amount == 200.0 and LINK_200:
        return LINK_200
    if amount == 500.0 and LINK_500:
        return LINK_500

    return None


def update_dntrade_order_note(external_id: str, number: int | str, payment_link: str) -> bool:
    """Оновлення примітки (note) у DNTrade"""
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
        logging.info(f"[DNTrade Note] Status [{response.status_code}]: {response.text}")
        return response.status_code in (200, 201)
    except Exception as e:
        logging.error(f"[DNTrade Note] Помилка запису note: {e}")
        return False


def change_dntrade_order_status(order_id: str | int, new_status_id: int) -> bool:
    """Зміна статусу замовлення в DNTrade"""
    url = f"{DNTRADE_API_URL}/orders/setstatus"
    payload = {
        "id": order_id,
        "status_id": new_status_id
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"[DNTrade Status] Status [{response.status_code}]: {response.text}")
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
                desc = f"Оплата замовлення №{number}"
            else:
                payment_amount = min(PREPAYMENT_PARTIAL_AMOUNT, total_price)
                desc = f"Передплата за замовлення №{number}"

            payment_link = create_iban_payment_link(number, payment_amount, desc)
            
            if not payment_link:
                logging.error(f"Не вдалося отримати посилання для замовлення №{number}")
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
