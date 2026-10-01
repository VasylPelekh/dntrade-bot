import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# Параметри DNTrade згідно з документацією
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_API_KEY = os.environ.get("DNTRADE_API_KEY")

# Параметри IBAN Oplata
IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_OPLATA_API_KEY = os.environ.get("IBAN_OPLATA_API_KEY")

# ID статусів з вашої бази DNTrade
STATUS_PREPAY_FULL = int(os.environ.get("STATUS_PREPAY_FULL", 1))       # Статус: "Передплата"
STATUS_PREPAY_PARTIAL = int(os.environ.get("STATUS_PREPAY_PARTIAL", 2)) # Статус: "Передплата/Післясплата"
STATUS_READY = int(os.environ.get("STATUS_READY", 3))                   # Статус: "Оплата сформована"

# Готові посилання для часткової передплати
LINK_200 = os.environ.get("LINK_PREPAY_200", "https://your-iban-link.com/200")
LINK_500 = os.environ.get("LINK_PREPAY_500", "https://your-iban-link.com/500")

HEADERS_DNTRADE = {
    "ApiKey": DNTRADE_API_KEY,
    "Content-Type": "application/json"
}

HEADERS_IBAN = {
    "Authorization": f"Bearer {IBAN_OPLATA_API_KEY}",
    "Content-Type": "application/json"
}


def create_full_iban_link(order: dict) -> str | None:
    """Генерація унікального IBAN-посилання на ПОВНУ суму."""
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
        logging.error(f"Помилка створення посилання IBAN: {response.text}")
        return None
    except Exception as e:
        logging.error(f"Збій запиту до IBAN API: {e}")
        return None


def update_dntrade_order(order_id: str, payment_link: str, target_status_id: int) -> bool:
    """Оновлення замовлення у DNTrade (запис у 'comment' та переведення в 'Оплата сформована')."""
    url = f"{DNTRADE_API_URL}/orders/upload"
    payload = {
        "orders": [
            {
                "id": order_id,
                "comment": f"Посилання на оплату: {payment_link}",
                "status": target_status_id
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS_DNTRADE, timeout=10)
        if response.status_code == 200:
            logging.info(f"Замовлення {order_id} оновлено: додано коментар та статус {target_status_id}")
            return True
        else:
            logging.error(f"Помилка оновлення DNTrade [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Виключення під час оновлення замовлення: {e}")
        return False


def process_orders_by_status(status_id: int, is_full_prepayment: bool):
    """Отримання та обробка замовлень за конкретним статусом."""
    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"status": status_id},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Не вдалося отримати замовлення для статусу {status_id}: {response.text}")
            return

        orders = response.json().get("orders", [])
    except Exception as e:
        logging.error(f"Помилка запиту списку замовлень: {e}")
        return

    for order in orders:
        order_id = order.get("id")
        total_sum = float(order.get("sum") or order.get("total_price") or 0)
        link_to_save = None

        if is_full_prepayment:
            # Повна передплата -> генеруємо нове посилання
            link_to_save = create_full_iban_link(order)
        else:
            # Часткова передплата -> вибираємо готове за сумою
            if total_sum < 1500:
                link_to_save = LINK_200
            else:
                link_to_save = LINK_500

        # Якщо посилання отримано/визначено -> записуємо в comment і міняємо статус
        if link_to_save:
            update_dntrade_order(order_id, link_to_save, STATUS_READY)


def run_pipeline():
    """Запуск перевірки для обох статусів."""
    logging.info("--- Старт обробки замовлень ---")
    # 1. Обробка замовлень у статусі "Передплата"
    process_orders_by_status(STATUS_PREPAY_FULL, is_full_prepayment=True)
    
    # 2. Обробка замовлень у статусі "Передплата/Післясплата"
    process_orders_by_status(STATUS_PREPAY_PARTIAL, is_full_prepayment=False)
    logging.info("--- Обробку завершено ---")


@app.route("/cron/process", methods=["GET", "POST"])
def cron_handler():
    run_pipeline()
    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
