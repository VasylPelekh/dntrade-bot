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

# Персональный поддомен вашей системы DNTrade
DNTRADE_URL = "https://dimaromatu.dntrade.com.ua"
IBAN_URL = "https://api.ibanoplata.com"

def extract_tag_names(tags_raw):
    """Извлекает названия меток независимо от формата данных"""
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
    """Отправка запроса к API DNTrade на персональном поддомене"""
    endpoints = [
        f"{DNTRADE_URL}/api/v1/sales-orders",
        f"{DNTRADE_URL}/api/v1/orders",
        f"{DNTRADE_URL}/api/sales-orders",
        f"{DNTRADE_URL}/api/orders"
    ]
    
    for url in endpoints:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                orders = data.get("data", []) if isinstance(data, dict) else data
                return orders, url
            else:
                print(f"Попытка {url} -> Статус: {res.status_code} | Ответ: {res.text}", flush=True)
        except Exception as e:
            print(f"Ошибка соединения с {url}: {e}", flush=True)
            
    return None, None

def process_orders():
    print("--- Фоновая проверка заказов запущена ---", flush=True)
    
    if not DNTRADE_TOKEN:
        print(" ВНИМАНИЕ: DNTRADE_TOKEN отсутствует в переменных Render!", flush=True)

    while True:
        try:
            # Авторизация согласно документации DNTrade
            headers = {
                "ApiKey": DNTRADE_TOKEN,
                "Authorization": f"Bearer {DNTRADE_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            orders, working_url = get_orders(headers)
            
            if orders is not None:
                print(f" Получено заказов от DNTrade: {len(orders)} (через {working_url})", flush=True)
                
                for order in orders:
                    raw_tags = order.get("tags", [])
                    tag_names = extract_tag_names(raw_tags)
                    order_id = order.get("id")
                    
                    total_sum = 0.0
                    try:
                        total_sum = float(order.get("sum", 0) or order.get("total_sum", 0) or order.get("amount", 0))
                    except (ValueError, TypeError):
                        total_sum = 0.0

                    # Если ссылка уже создана — пропускаем
                    if any("посилання готове" in name.lower() for name in tag_names):
                        continue

                    pay_link = None

                    has_prepay = any("передплата" in name.lower() for name in tag_names)
                    has_fullpay = any("повна оплата" in name.lower() for name in tag_names)

                    if has_prepay:
                        if total_sum <= 1500:
                            pay_link = LINK_200
                        else:
                            pay_link = LINK_500
                        print(f" Найден заказ №{order_id} (Предоплата). Сумма: {total_sum}. Ссылка: {pay_link}", flush=True)

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
                                print(f" Найден заказ №{order_id} (Полная оплата). IBAN: {pay_link}", flush=True)
                            else:
                                print(f"Ошибка IBAN API: {iban_res.status_code} - {iban_res.text}", flush=True)
                        except Exception as e:
                            print("Ошибка создания инвойса IBAN:", e, flush=True)

                    if pay_link:
                        new_tags = [name for name in tag_names if not any(k in name.lower() for k in ["передплата", "повна оплата"])]
                        new_tags.append("Посилання готове")
                        
                        update_data = {
                            "comment": f"Посилання на оплату: {pay_link}",
                            "note": f"Посилання на оплату: {pay_link}",
                            "tags": new_tags
                        }
                        
                        update_url = f"{working_url}/{order_id}"
                        update_res = requests.put(update_url, json=update_data, headers=headers)
                        print(f"Результат обновления №{order_id}: Статус {update_res.status_code} | Ответ: {update_res.text}", flush=True)

            else:
                print("Ошибка: Проверьте правильность API-токена в настройках Render.", flush=True)

        except Exception as e:
            print("Ошибка в цикле обработки:", e, flush=True)

        time.sleep(25)

def start_worker():
    thread = threading.Thread(target=process_orders, daemon=True)
    thread.start()

start_worker()

@app.route("/")
def home():
    return "Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
