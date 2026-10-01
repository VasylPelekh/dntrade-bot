import os
import time
import threading
from flask import Flask
import requests

app = Flask(__name__)

DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")
LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

DNTRADE_API_URL = "https://api.dntrade.com.ua"
IBAN_URL = "https://api.ibanoplata.com"

def extract_tag_names(tags_raw):
    """Витягує назви міток з будь-якого формату повернення DNTrade API"""
    names = []
    if not tags_raw:
        return names
    for tag in tags_raw:
        if isinstance(tag, dict):
            names.append(str(tag.get("name", "")))
        elif isinstance(tag, str):
            names.append(tag)
        else:
            names.append(str(tag))
    return names

def get_orders(headers):
    """Запит замовлень за офіційною документацією Swagger"""
    endpoints = [
        f"{DNTRADE_API_URL}/v1/orders",
        f"{DNTRADE_API_URL}/orders/list",
        f"{DNTRADE_API_URL}/api/v1/orders"
    ]
    
    for url in endpoints:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                orders = data.get("data", []) if isinstance(data, dict) else data
                if isinstance(orders, list):
                    return orders, url
            else:
                print(f"Запит {url} -> Статус {res.status_code}: {res.text[:100]}", flush=True)
        except Exception as e:
            print(f"Помилка підключення до {url}: {e}", flush=True)
            
    return None, None

def update_order(order_id, pay_link, new_tags, headers):
    """Оновлення замовлення згідно зі специфікацією API"""
    payload = {
        "id": order_id,
        "comment": f"Посилання на оплату: {pay_link}",
        "note": f"Посилання на оплату: {pay_link}",
        "tags": new_tags
    }
    
    # Спроба оновлення через PUT /v1/orders/{id} та POST /orders/upload
    update_urls = [
        (f"{DNTRADE_API_URL}/v1/orders/{order_id}", "PUT"),
        (f"{DNTRADE_API_URL}/orders/upload", "POST")
    ]
    
    for url, method in update_urls:
        try:
            if method == "PUT":
                res = requests.put(url, json=payload, headers=headers, timeout=10)
            else:
                res = requests.post(url, json=payload, headers=headers, timeout=10)
                
            if res.status_code in [200, 201, 204]:
                print(f" Замовлення №{order_id} успішно оновлено через {url}", flush=True)
                return True
            else:
                print(f"Спроба оновлення {url} ({method}) -> Статус {res.status_code}: {res.text[:100]}", flush=True)
        except Exception as e:
            print(f"Помилка оновлення замовлення №{order_id}: {e}", flush=True)
            
    return False

def process_orders():
    print("--- Фонова перевірка замовлень запущена ---", flush=True)

    while True:
        try:
            if not DNTRADE_TOKEN:
                print("УВАГА: DNTRADE_TOKEN відсутній у змінних оточення Render!", flush=True)
                time.sleep(20)
                continue

            headers = {
                "ApiKey": DNTRADE_TOKEN,
                "X-Api-Key": DNTRADE_TOKEN,
                "Authorization": f"Bearer {DNTRADE_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            orders, working_url = get_orders(headers)
            
            if orders is not None:
                print(f"Отримано замовлень від DNTrade: {len(orders)} (через {working_url})", flush=True)
                
                for order in orders:
                    raw_tags = order.get("tags", [])
                    tag_names = extract_tag_names(raw_tags)
                    order_id = order.get("id")
                    
                    if not order_id:
                        continue

                    # Пропускаємо, якщо вже є мітка "Посилання готове"
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
                        print(f"Знайдено передплату №{order_id}. Сума: {total_sum}. Посилання: {pay_link}", flush=True)

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
                                print(f"Знайдено повну оплату №{order_id}. IBAN: {pay_link}", flush=True)
                            else:
                                print(f"Помилка IBAN API: {iban_res.status_code} - {iban_res.text}", flush=True)
                        except Exception as e:
                            print("Помилка створення інвойсу IBAN:", e, flush=True)

                    if pay_link:
                        new_tags = [name for name in tag_names if not any(k in name.lower() for k in ["передплата", "повна оплата"])]
                        new_tags.append("Посилання готове")
                        
                        update_order(order_id, pay_link, new_tags, headers)

            else:
                print("Не вдалося отримати замовлення від API. Перевірте статус токена.", flush=True)

        except Exception as e:
            print("Помилка у циклі обробки:", e, flush=True)

        time.sleep(25)

def start_worker():
    thread = threading.Thread(target=process_orders, daemon=True)
    thread.start()

start_worker()

@app.route("/")
def home():
    return "DNTrade Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
