import os
import requests

def create_iban_invoice(amount, payment_purpose, client_notes="", expiration_hours=24):
    """
    Створення IBAN-інвойсу відповідно до API v2 IBAN Oplata
    """
    # Отримуємо налаштування зі змінних оточення
    base_url = "https://api.ibanoplata.com"
    endpoint = os.environ.get("IBAN_ENDPOINT", "/v2/iban-invoice")
    
    # Формуємо повну URL-адресу
    url = f"{base_url.rstrip('/')}{endpoint}"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ.get('IBAN_API_TOKEN')}"  # Або ваш спосіб авторизації
    }
    
    # Тіло запиту за схемою Swagger (POST /v2/iban-invoice)
    payload = {
        "organizationName": os.environ.get("IBAN_ORGANIZATION_NAME"),
        "identificationCode": os.environ.get("IBAN_IDENTIFICATION_CODE"),
        "account": os.environ.get("IBAN_ACCOUNT"),
        "amount": str(amount),
        "paymentPurpose": payment_purpose,
        "notes": client_notes,
        "clientNotes": client_notes,
        "expirationHours": expiration_hours
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Swagger повертає посилання у полі `ibanInvoiceUrl`
            return data.get("ibanInvoiceUrl")
        else:
            print(f"[IBAN API Error] Status: {response.status_code} | Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"[IBAN API Exception] Помилка запиту: {e}")
        return None
