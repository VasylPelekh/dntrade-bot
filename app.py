import os
import logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)

# --- Переменные из Render ---
DNTRADE_API_URL = os.environ.get("DNTRADE_API_URL", "https://api.dntrade.com.ua")
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")

IBAN_OPLATA_API_URL = os.environ.get("IBAN_OPLATA_API_URL", "https://api.iban-oplata.com")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")

LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# ID статусов
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
    """Расчет общей суммы заказа по корзине или полю sum."""
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
    """Генерация ссылки на ПОЛНУЮ сумму через IBAN-Oplata API."""
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
        logging.error(f"Ошибка IBAN API: {response.text}")
        return None
    except Exception as e:
        logging.error(f"Сбой запроса к IBAN API: {e}")
        return None


def update_dntrade_order(order_id: str, payment_link: str) -> bool:
    """
    Обновление заказа в DNTrade согласно документации POST /orders/upload:
    - comment передается внутри personal_info
    - status передается на верхнем уровне
    """
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
            logging.info(f"Заказ {order_id} успешно обновлен (переведен в статус {STATUS_WAITING_PAYMENT})")
            return True
        else:
            logging.error(f"Ошибка обновления DNTrade [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Исключение при обновлении заказа {order_id}: {e}")
        return False


def run_pipeline():
    logging.info("--- Старт обработки заказов ---")
    try:
        response = requests.get(
            f"{DNTRADE_API_URL}/orders/list",
            headers=HEADERS_DNTRADE,
            params={"limit": 100},
            timeout=10
        )
        if response.status_code != 200:
            logging.error(f"Не удалось получить список заказов из DNTrade: {response.text}")
            return

        data = response.json()
        orders = data.get("data", []) if isinstance(data, dict) and "data" in data else (
            data.get("orders", []) if isinstance(data, dict) else data
        )

        if not isinstance(orders, list):
            logging.error(f"Неожиданный формат ответа от DNTrade: {data}")
            return

        logging.info(f"Получено заказов из DNTrade: {len(orders)}")

    except Exception as e:
        logging.error(f"Ошибка при запросе заказов: {e}")
        return

    for order in orders:
        order_id = order.get("id")
        order_number = order.get("number", order_id)
        raw_status = order.get("status")

        # Извлекаем ID статуса
        if isinstance(raw_status, dict):
            status_id = raw_status.get("id")
        else:
            status_id = raw_status

        try:
            status_id = int(status_id)
        except (ValueError, TypeError):
            continue

        # Обрабатываем только нужные статусы (15 и 16)
        if status_id not in (STATUS_PREPAY_FULL, STATUS_PREPAY_PARTIAL):
            continue

        logging.info(f"НАЙДЕНО СОВПАДЕНИЕ! Заказ №{order_number} (ID: {order_id}), статус: {status_id}")

        total_sum = calculate_order_total(order)
        link_to_save = None

        if status_id == STATUS_PREPAY_FULL:
            # Предоплата (15) -> полная ссылка IBAN
            link_to_save = create_full_iban_link(order_id, order_number, total_sum)
        elif status_id == STATUS_PREPAY_PARTIAL:
            # Предоплата/Наложенный платеж (16) -> фикс. ссылки
            link_to_save = LINK_200 if total_sum < 1500 else LINK_500

        if link_to_save:
            update_dntrade_order(order_id, link_to_save)

    logging.info("--- Обработка завершена ---")


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
