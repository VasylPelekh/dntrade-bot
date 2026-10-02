import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Змінні середовища ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Звірені ID статусів з DNTrade:
# 2 = Передплата (Повна)
# 3 = Передплата/Післясплата (Часткова)
STATUS_PREPAY_FULL = int(os.environ.get("STATUS_PREPAY_FULL", 2))
STATUS_PREPAY_PARTIAL = int(os.environ.get("STATUS_PREPAY_PARTIAL", 3))
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
    url = f"{IBAN_OPLATA_API_URL}/v1/payments/create"
    payload = {
        "order_id": str(order_id),
        "amount": amount,
        "description": f"Оплата за замовлення №{order_number or order_id}"
    }
    try:
        logging.info(f"Надсилаємо запит до IBAN API для замовлення №{order_number} на суму {amount} грн...")
        response = requests.post(url, json=payload, headers=HEADERS_IBAN, timeout=10)
        logging.info(f"Відповідь IBAN API [{response.status_code}]: {response.text}")
        if response.status_code in (200, 201):
            return response.json().get("payment_url")
        return None
    except Exception as e:
        logging.error(f"Збій запиту до IBAN API: {e}")
        return None


def update_dntrade_order(external_id: str, order_number: str | int, payment_link: str) -> bool:
    url = f"{DNTRADE_API_URL}/orders/upload"

    payload = {
        "orders": [
            {
                "external_id": external_id,
                "number": order_number,
                "order_status": STATUS_WAITING_PAYMENT,
                "note": f"Посилання на оплату: {payment_link}"
            }
        ]
    }

    try:
        logging.info(f"Відправляємо оновлення в DNTrade для №{order_number} з посиланням: {payment_link}")
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        logging.info(f"Відповідь DNTrade orders/upload [{response.status_code}]: {response.text}")
        if response.status_code in (200, 201):
            logging.info(f"Замовлення №{order_number} успішно оновлено!")
            return True
        return False
    except Exception as e:
        logging.error(f"Помилка під час оновлення замовлення №{order_number}: {e}")
        return False


def run_pipeline():
    logging.info("--- Старт обробки замовлень ---")
    
    # Перевірка наявності ключів та посилань
    logging.info(f"ПЕРЕВІРКА НАЛАШТУВАНЬ: LINK_200={LINK_200}, LINK_500={LINK_500}, IBAN_TOKEN={'Присутній' if IBAN_TOKEN else 'ВІДСУТНІЙ'}")

    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 50, "sort": "-id"},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Помилка DNTrade API: {response.text}")
            return

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        )

        logging.info(f"Завантажено замовлень: {len(orders)}")
        processed_count = 0

        for order in orders:
            order_status = order.get("order_status")
            external_id = order.get("external_id")
            number = order.get("number")

            try:
                order_status = int(order_status)
            except (ValueError, TypeError):
                continue

            if order_status not in (STATUS_PREPAY_FULL, STATUS_PREPAY_PARTIAL):
                continue

            logging.info(f"ЗНАЙДЕНО ЗБІГ! Замовлення №{number} (external_id: {external_id}), стан: {order_status}")

            total_sum = calculate_order_total(order)
            logging.info(f"Загальна сума замовлення №{number}: {total_sum} грн")

            link_to_save = None

            if order_status == STATUS_PREPAY_FULL:
                link_to_save = create_full_iban_link(external_id, number, total_sum)
            elif order_status == STATUS_PREPAY_PARTIAL:
                link_to_save = LINK_200 if total_sum < 1500 else LINK_500
                if not link_to_save:
                    logging.warning(f"ПРОПУСК: LINK_200 або LINK_500 не задано у зміних середовища Render!")

            if link_to_save:
                if update_dntrade_order(external_id, number, link_to_save):
                    processed_count += 1
            else:
                logging.warning(f"Замовлення №{number} пропущено, бо не вдалося отримати/згенерувати посилання на оплату.")

        logging.info(f"--- Обробку завершено. Опрацьовано замовлень: {processed_count} ---")

    except Exception as e:
        logging.error(f"Помилка під час виконання: {e}")
        return


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
