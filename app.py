import os
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


def create_full_iban_link(order_id: str, amount: float) -> str | None:
    """Генерація посилання на ПОВНУ суму через IBAN-Oplata API."""
    url = f"{IBAN_OPLATA_API_URL}/v1/payments/create"
    payload = {
        "order_id": str(order_id),
        "amount": amount,
        "description": f"Оплата за замовлення №{order_id}"
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_IBAN, timeout=10)
        if response.status_code in (200, 201):
            return response.json().get("payment_url")
        logging.error(f"Помилка IBAN API: {response.text}")
        return None
    except Exception as e:
        logging.error(f"Збій запиту до IBAN API: {e}")
        return None


def update_dntrade_order(order_id: str, payment_link: str) -> bool:
    """Запис посилання у примітку та переведення статусу на 'ОЧІКУЄМО ОПЛАТУ' (ID: 1)."""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "orders": [
            {
                "id": str(order_id),
                "number": str(order_id),
                "comment": f"Посилання на оплату: {payment_link}",
                "status": STATUS_WAITING_PAYMENT
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        if response.status_code == 200:
            logging.info(f"Замовлення {order_id} успішно оновлено")
            return True
        else:
            logging.error(f"Помилка оновлення DNTrade [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Виключення під час оновлення {order_id}: {e}")
        return False


def process_by_status(status_id: int, is_full: bool):
    """Отримання замовлень напряму за конкретним ID статусу."""
    try:
        # Запит списку замовлень конкретного статусу
        res = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"status_id": status_id},
            timeout=10
        )
        if res.status_code != 200:
            logging.error(f"Не вдалося отримати замовлення для статусу {status_id}: {res.text}")
            return

        data = res.json()
        orders = data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        logging.info(f"Знайдено {len(orders)} замовлень для статусу {status_id}")

        for order in orders:
            order_id = order.get("number") or order.get("id") or order.get("code")
            if not order_id:
                continue

            total_sum = float(order.get("sum") or order.get("total_price") or 0)
            link_to_save = None

            if is_full:
                link_to_save = create_full_iban_link(order_id, total_sum)
            else:
                link_to_save = LINK_200 if total_sum < 1500 else LINK_500

            if link_to_save:
                update_dntrade_order(order_id, link_to_save)

    except Exception as e:
        logging.error(f"Помилка обробки статусу {status_id}: {e}")


def run_pipeline():
    logging.info("--- Старт обробки замовлень ---")
    # Обробляємо статус 15 (Передплата)
    process_by_status(STATUS_PREPAY_FULL, is_full=True)
    # Обробляємо статус 16 (Передплата/Післясплата)
    process_by_status(STATUS_PREPAY_PARTIAL, is_full=False)
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
