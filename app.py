import os
import time
import threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

DNTRADE_TOKEN = os.environ.get("DNTRADE_TOKEN")
IBAN_TOKEN = os.environ.get("IBAN_TOKEN")
LINK_200 = os.environ.get("LINK_200")
LINK_500 = os.environ.get("LINK_500")

SUBDOMAIN = "dimaromatu"
DNTRADE_BASE_URL = f"https://{SUBDOMAIN}.dntrade.com.ua"
IBAN_URL = "https://api.ibanoplata.com"

def extract_tag_names(tags_raw):
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

def fetch_orders(headers):
    endpoints = [
        f"{DNTRADE_BASE_URL}/api/v1/sales-orders",
        f"{DNTRADE_BASE_URL}/api/v1/orders",
        f"{DNTRADE_BASE_URL}/api/sales-orders",
        f"{DNTRADE_BASE_URL}/api/orders",
        "https://api.dntrade.com.ua/v1/sales-orders"
    ]
    
    for url in endpoints:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                orders = data.get("data", []) if isinstance(data, dict) else data
                return orders, url
            else:
                print(f"Попытка {url} -> Статус {res.status_code}", flush=True)
        except Exception as e:
            print(f"Ошибка запроса к {url}: {e}", flush=True)
            
    return None, None

def process_orders():
    print("--- Фоновый цикл опроса заказов стартовал ---", flush=True)

    while True:
        try:
            if not DNTRADE_TOKEN:
                print("ОШИБКА: DNTRADE_TOKEN не найден в настройках Render!", flush=True)
                time.sleep(20)
                continue

            headers = {
                "ApiKey": DNTRADE_TOKEN,
                "Authorization": f"Bearer {DNTRADE_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            orders, working_url = fetch_orders(headers)
            
            if orders is not None:
                print(f"Успешно получено заказов: {len(orders)} (через {working_url})", flush=True)
                
                for order in orders:
                    raw_tags = order.get("tags", [])
                    tag_names = extract_tag_names(raw_tags)
                    order_id = order.get("id")
                    
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
                        print(f"Предоплата для заказа №{order_id}. Сумма: {total_sum}. Ссылка: {pay_link}", flush=True)

                    elif has_fullpay:
                        iban_headers = {"Authorization": f"Bearer {IBAN_TOKEN}"}
                        payload = {
                            "amount": total_sum,
                            "description": f"Оплата заказа №{order_id}"
                        }
                        try:
                            iban_res = requests.post(f"{IBAN_URL}/v1/Invoice/create", json=payload, headers=iban_headers)
                            if iban_res.status_code in [200, 201]:
                                pay_link = iban_res.json().get("pageUrl")
                                print(f"IBAN оплата для заказа №{order_id}: {pay_link}", flush=True)
                        except Exception as e:
                            print("Ошибка IBAN API:", e, flush=True)

                    if pay_link:
                        new_tags = [name for name in tag_names if not any(k in name.lower() for k in ["передплата", "повна оплата"])]
                        new_tags.append("Посилання готове")
                        
                        update_data = {
                            "comment": f"Посилання на оплату: {pay_link}",
                            "note": f"Посилання на оплату: {pay_link}",
                            "tags": new_tags
                        }
                        
                        update_res = requests.put(f"{working_url}/{order_id}", json=update_data, headers=headers)
                        print(f"Обновление заказа №{order_id}: Статус {update_res.status_code}", flush=True)

            else:
                print("Все URL вернули ошибку. Проверьте правильность токена и поддомена.", flush=True)

        except Exception as e:
            print("Ошибка в основном цикле:", e, flush=True)

        time.sleep(20)

# Гарантированный запуск потока при старте Flask
thread = threading.Thread(target=process_orders, daemon=True)
thread.start()

@app.route("/")
def home():
    return "DNTrade Bot is active!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
