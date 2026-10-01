import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Перемінні з Render ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# ID статусів
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


def calculate_order_total(order: dict) -> float:
    """Розрахунок загальної суми замовлення."""
    if "sum" in order and order["sum"] is not None:
        try:
            return float(order["sum"])
        except ValueError:
            pass

    cart = order.get("cart", [])
    total = 0.0
    if isinstance(cart, list):
        for item in cart:
            price = float(item.get("price", 0))
            quantity = float(item.get("quantity", 1))
            total += price * quantity
    return total


def create_full_iban_link(order_id: str, order_number: str | int, amount: float) -> str | None:
    """Генерація посилання на ПОВНУ суму через IBAN-Oplata API."""
    url = f"{IBAN_OPLATA_API_URL}/v1/payments/create"
    payload = {
        "order_id": str(order_id),
        "amount": amount,
        "description": f"Оплата за замовлення №{order_number or order_id}"
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
    """Оновлення замовлення в DNTrade (comment всередині personal_info)."""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "id": order_id,
        "status": STATUS_WAITING_PAYMENT,
        "personal_info": {
            "comment": f"Посилання на оплату: {payment_link}"
        }
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        if response.status_code == 200:
            logging.info(f"Замовлення {order_id} успішно оновлено (переведено в статус {STATUS_WAITING_PAYMENT})")
            return True
        else:
            logging.error(f"Помилка оновлення DNTrade [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Виключення під час оновлення замовлення {order_id}: {e}")
        return False


def run_pipeline():
    logging.info("--- Старт обробки замовлень ---")
    try:
        # Ставимо limit=50 (максимум, дозволений DNTrade API)
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Не вдалося отримати список замовлень з DNTrade: {response.text}")
            return

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else data
        )

        if not isinstance(orders, list):
            logging.error(f"Незвичайний формат відповіді від DNTrade: {data}")
            return

        logging.info(f"Отримано замовлень з DNTrade: {len(orders)}")

    except Exception as e:
        logging.error(f"Помилка під час запиту замовлень: {e}")
        return

    for order in orders:
        order_id = order.get("id")
        order_number = order.get("number", order_id)
        raw_status = order.get("status")

        # Витягуємо ID статусу
        if isinstance(raw_status, dict):
            status_id = raw_status.get("id")
        else:
            status_id = raw_status

        try:
            status_id = int(status_id)
        except (ValueError, TypeError):
            continue

        # Обробляємо лише статуси 15 та 16
        if status_id not in (STATUS_PREPAY_FULL, STATUS_PREPAY_PARTIAL):
            continue

        logging.info(f"ЗНАЙДЕНО ЗБІГ! Замовлення №{order_number} (ID: {order_id}), статус: {status_id}")

        total_sum = calculate_order_total(order)
        link_to_save = None

        if status_id == STATUS_PREPAY_FULL:
            # Передплата (15) -> повне посилання IBAN
            link_to_save = create_full_iban_link(order_id, order_number, total_sum)
        elif status_id == STATUS_PREPAY_PARTIAL:
            # Передплата/Післясплата (16) -> фіксовані посилання
            link_to_save = LINK_200 if total_sum < 1500 else LINK_500

        if link_to_save:
            update_dntrade_order(order_id, link_to_save)

    logging.info("--- Обробка завершена ---")


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
