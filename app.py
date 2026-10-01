import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Отримання змінних оточення з Render ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Перевірені ID статусів з вашої системи:
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


def create_full_iban_link(order: dict) -> str | None:
    """Генерація посилання на ПОВНУ суму через IBAN-Oplata API."""
    url = f"{IBAN_OPLATA_API_URL}/v1/payments/create"
    payload = {
        "order_id": str(order.get("id")),
        "amount": order.get("sum") or order.get("total_price"),
        "description": f"Оплата за замовлення №{order.get('code', order.get('id'))}"
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
    """Запис посилання у примітку/коментар та переведення у статус 'ОЧІКУЄМО ОПЛАТУ' (ID: 1)."""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "orders": [
            {
                "id": order_id,
                "comment": f"Посилання на оплату: {payment_link}",
                "status": STATUS_WAITING_PAYMENT
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        if response.status_code == 200:
            logging.info(f"Замовлення {order_id} успішно оновлено (статус переведено на {STATUS_WAITING_PAYMENT})")
            return True
        else:
            logging.error(f"Помилка оновлення DNTrade [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Виключення під час оновлення замовлення {order_id}: {e}")
        return False


def process_orders_by_status(status_id: int, is_full_prepayment: bool):
    """Отримання замовлень за ID статусу та їх обробка."""
    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"status": status_id},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Не вдалося отримати замовлення для статусу ID {status_id}: {response.text}")
            return

        orders = response.json().get("orders", [])
    except Exception as e:
        logging.error(f"Помилка запиту замовлень: {e}")
        return

    for order in orders:
        order_id = order.get("id")
        total_sum = float(order.get("sum") or order.get("total_price") or 0)
        link_to_save = None

        if is_full_prepayment:
            # 1. Повна передплата (ID 15) -> генеруємо індивідуальне посилання IBAN
            link_to_save = create_full_iban_link(order)
        else:
            # 2. Часткова передплата (ID 16) -> сталі посилання (до 1500 грн -> LINK_200, від 1500 грн -> LINK_500)
            if total_sum < 1500:
                link_to_save = LINK_200
            else:
                link_to_save = LINK_500

        if link_to_save:
            update_dntrade_order(order_id, link_to_save)


def run_pipeline():
    logging.info("--- Старт автоматичної обробки замовлень ---")
    # Обробка статусу "Передплата" (15)
    process_orders_by_status(STATUS_PREPAY_FULL, is_full_prepayment=True)
    # Обробка статусу "Передплата/Післясплата" (16)
    process_orders_by_status(STATUS_PREPAY_PARTIAL, is_full_prepayment=False)
    logging.info("--- Обробку успішно завершено ---")


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
