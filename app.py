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

DNTRADE_URL = "https://api.dntrade.com.ua"
IBAN_URL = "https://api.ibanoplata.com"

def get_headers():
    return {
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
        elif isinstance(tag, str):
            names.append(tag)
        else:
            names.append(str(tag))
    return names

def fetch_orders():
    """Запрос списка заказов согласно Swagger DNTrade"""
    headers = get_headers()
    endpoints = [
        f"{DNTRADE_URL}/orders/list",
        f"{DNTRADE_URL}/v1/orders",
        f"{DNTRADE_URL}/api/v1/sales-orders"
    ]
    
    for url in endpoints:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                orders = data.get("data", []) if isinstance(data, dict) else data
                if isinstance(orders, list):
                    return orders, url
            elif res.status_code == 405: # Если требуется POST вместо GET
                res_post = requests.post(url, json={}, headers=headers, timeout=10)
                if res_post.status_code == 200:
                    data = res_post.json()
                    orders = data.get("data", []) if isinstance(data, dict) else data
                    if isinstance(orders, list):
                        return orders, url
            print(f"[DNTrade API] {url} -> Status: {res.status_code} | Response: {res.text[:120]}", flush=True)
        except Exception as e:
            print(f"[DNTrade API Error] {url}: {e}", flush=True)
            
    return None, None

def update_order_data(order_id, pay_link, new_tags):
    """Обновление заказа (теги + комментарий с ссылкой)"""
    headers = get_headers()
    payload = {
        "id": order_id,
        "comment": f"Посилання на оплату: {pay_link}",
        "tags": new_tags
    }
    
    # Пробуем POST /orders/upload и PUT /v1/orders/{id}
    upload_url = f"{DNTRADE_URL}/orders/upload"
    res = requests.post(upload_url, json={"orders": [payload]}, headers=headers, timeout=10)
    
    if res.status_code in [200, 201]:
        print(f"Заказ №{order_id} успешно обновлен через {upload_url}", flush=True)
        return True
    
    # Резервный PUT запрос
    put_url = f"{DNTRADE_URL}/v1/orders/{order_id}"
    res_put = requests.put(put_url, json=payload, headers=headers, timeout=10)
    print(f"Результат обновления №{order_id} (PUT): Status {res_put.status_code}", flush=True)
    return res_put.status_code in [200, 201, 204]

def process_orders():
    print("=== Запуск бота по спецификации Swagger DNTrade ===", flush=True)

    while True:
        try:
            if not DNTRADE_TOKEN:
                print("ОШИБКА: DNTRADE_TOKEN отсутствует в настройках Render!", flush=True)
                time.sleep(20)
                continue

            orders, working_url = fetch_orders()

            if orders is not None:
                print(f"Получено заказов: {len(orders)} (через {working_url})", flush=True)

                for order in orders:
                    order_id = order.get("id")
                    if not order_id:
                        continue

                    raw_tags = order.get("tags", [])
                    tag_names = extract_tag_names(raw_tags)

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
                        print(f"Предоплата для №{order_id}. Сумма: {total_sum}. Ссылка: {pay_link}", flush=True)

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
                                print(f"Полная оплата IBAN для №{order_id}: {pay_link}", flush=True)
                        except Exception as e:
                            print(f"Ошибка IBAN API: {e}", flush=True)

                    if pay_link:
                        new_tags = [name for name in tag_names if not any(k in name.lower() for k in ["передплата", "повна оплата"])]
                        new_tags.append("Посилання готове")
                        update_order_data(order_id, pay_link, new_tags)

            else:
                print("Не удалось получить заказы. Проверьте правильность токена DNTRADE_TOKEN.", flush=True)

        except Exception as e:
            print(f"Ошибка в цикле обработки: {e}", flush=True)

        time.sleep(25)

threading.Thread(target=process_orders, daemon=True).start()

@app.route("/")
def home():
    return "DNTrade Bot Active"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
