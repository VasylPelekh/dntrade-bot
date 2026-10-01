import os
import time
import threading
from flask import Flask
import requests

app = Flask(__name__)

# Змінні середовища з Render
DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")
LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

# Ваш персональний домен DNTrade з документації
BASE_URL = "https://dimaromatu.dntrade.com.ua"
IBAN_URL = "https://api.ibanoplata.com"

def get_headers():
    return {
        "X-Api-Key": DNTRADE_TOKEN,
        "ApiKey": DNTRADE_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def extract_tag_names(tags_raw):
    names = []
    if not tags_raw:
        return names
    for tag in tags_raw:
        if isinstance(tag, dict):
            names.append(str(tag.get("name", "")))
        else:
            names.append(str(tag))
    return names

def process_orders():
    print("=== Запуск бота за специфікацією DNTrade Swagger ===", flush=True)

    while True:
        try:
            if not DNTRADE_TOKEN:
                print("ПОМИЛКА: DNTRADE_TOKEN відсутній у зміних оточення Render!", flush=True)
                time.sleep(20)
                continue

            # 1. Отримання замовлень згідно зі Swagger
            url = f"{BASE_URL}/api/v1/sales-orders"
            res = requests.get(url, headers=get_headers(), timeout=10)

            if res.status_code == 200:
                data = res.json()
                orders = data.get("data", []) if isinstance(data, dict) else data

                print(f"Отримано замовлень від DNTrade: {len(orders)}", flush=True)

                for order in orders:
                    order_id = order.get("id")
                    if not order_id:
                        continue

                    tag_names = extract_tag_names(order.get("tags", []))

                    # Пропускаємо вже оброблені
                    if any("посилання готове" in name.lower() for name in tag_names):
                        continue

                    total_sum = 0.0
                    try:
                        total_sum = float(order.get("sum", 0) or order.get("total_sum", 0) or order.get("amount", 0))
                    except (ValueError, TypeError):
                        total_sum = 0.0

                    pay_link = None
                    has_prepay = any("передплата" in name.lower() for name in tag_names)
                    has_fullpay = any("повна оплата" in name.lower() for name in tag_names)

                    if has_prepay:
                        pay_link = LINK_200 if total_sum <= 1500 else LINK_500
                        print(f"Передплата №{order_id} ({total_sum} грн). Посилання: {pay_link}", flush=True)

                    elif has_fullpay:
                        iban_headers = {"Authorization": f"Bearer {IBAN_TOKEN}"}
                        payload = {
                            "amount": total_sum,
                            "description": f"Оплата замовлення №{order_id}"
                        }
                        try:
                            iban_res = requests.post(f"{IBAN_URL}/v1/Invoice/create", json=payload, headers=iban_headers)
                            if iban_res.status_code in [200, 201]:
                                pay_link = iban_res.json().get("pageUrl")
                                print(f"Повна оплата №{order_id}. IBAN: {pay_link}", flush=True)
                            else:
                                print(f"Помилка IBAN API ({iban_res.status_code}): {iban_res.text}", flush=True)
                        except Exception as e:
                            print(f"Помилка створення інвойсу IBAN: {e}", flush=True)

                    if pay_link:
                        # Формуємо оновлений список міток
                        new_tags = [name for name in tag_names if not any(k in name.lower() for k in ["передплата", "повна оплата"])]
                        new_tags.append("Посилання готове")

                        update_payload = {
                            "comment": f"Посилання на оплату: {pay_link}",
                            "tags": new_tags
                        }

                        # 2. Оновлення замовлення через PUT /api/v1/sales-orders/{id}
                        update_url = f"{BASE_URL}/api/v1/sales-orders/{order_id}"
                        upd_res = requests.put(update_url, json=update_payload, headers=get_headers(), timeout=10)

                        print(f"Оновлення №{order_id} -> Статус {upd_res.status_code}: {upd_res.text[:100]}", flush=True)

            else:
                print(f"Помилка запиту DNTrade API (Статус {res.status_code}): {res.text[:150]}", flush=True)

        except Exception as e:
            print(f"Помилка в циклі: {e}", flush=True)

        time.sleep(25)

# Фоновий потік
threading.Thread(target=process_orders, daemon=True).start()

@app.route("/")
def home():
    return "DNTrade Bot Active"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
