import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Перемінні середовища ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Точні ID статусів з вашої CRM DNTrade:
# 3 = Передплата (Повна)
# 2 = Передплата/Післясплата (Часткова)
# 1 = Очікуємо оплату
STATUS_PREPAY_FULL = int(os.environ.get("STATUS_PREPAY_FULL", 3))
STATUS_PREPAY_PARTIAL = int(os.environ.get("STATUS_PREPAY_PARTIAL", 2))
STATUS_WAITING_PAYMENT = int(os.environ.get("STATUS_WAITING_PAYMENT", 1))

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_TOKEN,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_TOKEN}",
    "Content-Type": "application/json"
}


def calculate_order_total(order: dict) -> float:
    """Обчислює загальну суму замовлення."""
    if "total_price" in order and order["total_price"] is not None:
        try:
            return float(order["total_price"])
        except (ValueError, TypeError):
            pass

    products = order.get("products", [])
    total = 0.0
    if isinstance(products, list):
        for item in products:
            try:
                price = float(item.get("price", 0))
                quantity = float(item.get("quantity", 1))
                total += price * quantity
            except (ValueError, TypeError):
                continue
    return round(total, 2)


def create_full_iban_link(order_id: str, order_number: str | int, amount: float) -> str | None:
    """Генерує динамічне посилання через IBAN API."""
    url = f"{IBAN_OPLATA_API_URL}/v1/payments/create"
    payload = {
        "order_id": str(order_id),
        "amount": amount,
        "description": f"Оплата за замовлення №{order_number or order_id}"
    }
    try:
        logging.info(f"Запит до IBAN API для №{order_number} (сума {amount} грн)...")
        response = requests.post(url, json=payload, headers=HEADERS_IBAN, timeout=10)
        logging.info(f"Відповідь IBAN API [{response.status_code}]: {response.text}")
        if response.status_code in (200, 201):
            return response.json().get("payment_url")
        return None
    except Exception as e:
        logging.error(f"Збій запиту до IBAN API: {e}")
        return None


def update_dntrade_order(external_id: str, order_number: str | int, payment_link: str, existing_note: str = "") -> bool:
    """Оновлює замовлення в DNTrade: передає status, новий order_status та примітку."""
    url = f"{DNTRADE_API_URL}/orders/upload"

    note_text = f"Посилання на оплату: {payment_link}"
    if existing_note and note_text not in existing_note:
        note_text = f"{existing_note}\n{note_text}"

    # Передаємо коректний об'єкт оновлення
    payload = {
        "orders": [
            {
                "external_id": external_id,
                "number": order_number,
                "status": "active",
                "order_status": STATUS_WAITING_PAYMENT,
                "note": note_text,
                "comment": note_text
            }
        ]
    }

    try:
        logging.info(f"Відправка оновлення у DNTrade для №{order_number}...")
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"Відповідь DNTrade orders/upload [{response.status_code}]: {response.text}")
        if response.status_code in (200, 201):
            logging.info(f"Замовлення №{order_number} успішно оновлено!")
            return True
        return False
    except Exception as e:
        logging.error(f"Помилка під час оновлення в DNTrade: {e}")
        return False


def run_pipeline():
    logging.info("--- Старт обробки замовлень ---")
    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50, "sort": "-id"},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Помилка DNTrade API [{response.status_code}]: {response.text}")
            return

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        logging.info(f"Отримано замовлень з DNTrade: {len(orders)}")
        processed_count = 0

        for order in orders:
            order_status = order.get("order_status")
            external_id = order.get("external_id")
            number = order.get("number")
            current_note = order.get("note") or order.get("comment") or ""

            try:
                order_status = int(order_status)
            except (ValueError, TypeError):
                continue

            # Фільтруємо замовлення у статусах Передплата (3 та 2)
            if order_status not in (STATUS_PREPAY_FULL, STATUS_PREPAY_PARTIAL):
                continue

            logging.info(f"ЗНАЙДЕНО ЗБІГ! Замовлення №{number} (external_id: {external_id}), стан: {order_status}")

            total_sum = calculate_order_total(order)
            logging.info(f"Сума замовлення №{number}: {total_sum} грн")

            link_to_save = None

            # 1. Повна передплата (ID = 3)
            if order_status == STATUS_PREPAY_FULL:
                link_to_save = create_full_iban_link(external_id, number, total_sum)

            # 2. Часткова передплата (ID = 2)
            elif order_status == STATUS_PREPAY_PARTIAL:
                link_to_save = LINK_200 if total_sum < 1500 else LINK_500

            if link_to_save:
                if update_dntrade_order(external_id, number, link_to_save, current_note):
                    processed_count += 1
            else:
                logging.warning(f"Не вдалося згенерувати посилання для замовлення №{number}")

        logging.info(f"--- Обробку завершено. Опрацьовано замовлень: {processed_count} ---")

    except Exception as e:
        logging.error(f"Помилка виконанння: {e}")


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
