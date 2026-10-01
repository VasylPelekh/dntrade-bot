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

def process_orders():
    while True:
        try:
            headers = {"Authorization": f"Bearer {DNTRADE_TOKEN}"}
            res = requests.get(f"{DNTRADE_URL}/api/v1/orders", headers=headers)
            if res.status_code == 200:
                orders = res.json().get("data", [])
                for order in orders:
                    tags = order.get("tags", [])
                    order_id = order.get("id")
                    total_sum = float(order.get("sum", 0))

                    if "Посилання готове" in tags:
                        continue

                    pay_link = None

                    if "Передплата / післясплата" in tags:
                        if total_sum <= 1500:
                            pay_link = LINK_200
                        else:
                            pay_link = LINK_500

                    elif "Повна оплата" in tags:
                        iban_headers = {"Authorization": f"Bearer {IBAN_TOKEN}"}
                        payload = {
                            "amount": total_sum,
                            "description": f"Оплата замовлення №{order_id}"
                        }
                        try:
                            iban_res = requests.post(f"{IBAN_URL}/v1/Invoice/create", json=payload, headers=iban_headers)
                            if iban_res.status_code in [200, 201]:
                                pay_link = iban_res.json().get("pageUrl")
                        except Exception as e:
                            print("Помилка створення інвойсу IBAN:", e)

                    if pay_link:
                        new_tags = [t for t in tags if t not in ["Передплата / післясплата", "Повна оплата"]]
                        new_tags.append("Посилання готове")
                        
                        update_data = {
                            "note": f"Посилання на оплату: {pay_link}",
                            "tags": new_tags
                        }
                        requests.put(f"{DNTRADE_URL}/api/v1/orders/{order_id}", json=update_data, headers=headers)
        except Exception as e:
            print("Помилка обробки замовлень:", e)

        time.sleep(60)

thread = threading.Thread(target=process_orders, daemon=True)
thread.start()

@app.route("/")
def home():
    return "Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
  
