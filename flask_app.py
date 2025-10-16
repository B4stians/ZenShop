from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import json, os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from functools import wraps
from urllib.parse import urlencode
import threading

app = Flask(__name__)
app.secret_key = "super_secret_key_change_me"  # zmień na losowy sekret w produkcji!

# <<< USTAW TU SWÓJ WEBHOOK DISCORDA >>>
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1428511956884787221/3CLFDnT8xZQEyRE6Z1qLgShtV5Q9XKDChoVi8aKz4Hhpz0EwTmJ6T0eBQa1U6QNOW1sY"
DISCORD_WAREHOUSE_WEBHOOK_URL = "https://discord.com/api/webhooks/1428514233972429002/OBasLmCCCm0aBeQJTm_Soz_1dpqRL-GpjvFYS7VLByJeD97LV1-S5O513PbbpyUoLddL"
# <<< -------------------------------- >>>

DISCORD_CLIENT_ID = "1428511297372291072"
DISCORD_CLIENT_SECRET = "L6jCKKGdzg-4jhxS7VZ5SNeOo0nBNVhW"
DISCORD_REDIRECT_URI = "https://sklepzenevents.pythonanywhere.com/discord/callback"  # dostosuj do produkcji
DISCORD_OAUTH_SCOPE = "identify"
DISCORD_API_BASE = "https://discord.com/api"

@app.route('/discord/login')
def discord_login():
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": DISCORD_OAUTH_SCOPE
    }
    url = f"{DISCORD_API_BASE}/oauth2/authorize?{urlencode(params)}"
    return redirect(url)

@app.route('/discord/callback')
def discord_callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    # wymiana code na token i pobranie danych usera
    token_url = f"{DISCORD_API_BASE}/oauth2/token"
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": DISCORD_OAUTH_SCOPE
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_resp = requests.post(token_url, data=data, headers=headers)
    print("DEBUG token response:", token_resp.status_code, token_resp.text)
    token_resp.raise_for_status()
    token_json = token_resp.json()
    access_token = token_json.get("access_token")

    me_resp = requests.get(f"{DISCORD_API_BASE}/users/@me",
                           headers={"Authorization": f"Bearer {access_token}"})
    me_resp.raise_for_status()
    me = me_resp.json()

    session["discord_user"] = {
        "id": me.get("id"),
        "username": me.get("username"),
        "discriminator": me.get("discriminator"),
        "avatar": me.get("avatar")
    }
    return redirect(url_for('index'))


@app.route('/discord/logout')
def discord_logout():
    session.pop("discord_user", None)
    return redirect(url_for('index'))


MAGAZYN_PASSWORD = "_LZq4C#g9W?KAvXJZ3z+K>RQu5dyk:e#pG2b-xi7c,fm2R1BRg"
BUSINESS_BALANCE = 1066376

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCT_FILE = os.path.join(BASE_DIR, 'products.json')
SALES_FILE = os.path.join(BASE_DIR, 'sales.json')
WAREHOUSE_FILE = os.path.join(BASE_DIR, 'zamowienia.json')
SALES_WAREHOUSE_FILE = os.path.join(BASE_DIR, 'saleswarehouse.json')

# ---------- globalny lock do bezpiecznego zapisu JSON ----------
save_lock = threading.Lock()

# ---------- helpers ----------
def magazyn_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("magazyn_authenticated"):
            return redirect(url_for("magazyn_login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def save_json(path, data):
    """
    Bezpieczny zapis JSON z blokadą, aby uniknąć kolizji przy równoczesnym zapisie.
    """
    with save_lock:  # 🔒 uniemożliwia zapis dwóch wątków w tym samym czasie
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def load_products(): return load_json(PRODUCT_FILE)
def save_products(data): save_json(PRODUCT_FILE, data)
def load_sales(): return load_json(SALES_FILE)
def save_sales(data): save_json(SALES_FILE, data)
def load_warehouse(): return load_json(WAREHOUSE_FILE)
def load_sales_warehouse(): return load_json(SALES_WAREHOUSE_FILE)
def save_sales_warehouse(data): save_json(SALES_WAREHOUSE_FILE, data)

# ---------- stock management ----------
def update_stock_after_sale(cart):
    """
    Odejmij quantity z products.json dla każdego elementu w cart,
    ale pomiń elementy typu 'order' (zamówienia do realizacji).
    """
    products = load_products()
    changed = False
    for item in cart:
        if item.get("type") == "order":
            continue  # NIE odejmujemy stocku dla zamówień
        for p in products:
            if p.get("name") == item.get("name"):
                old = p.get("stock", 0)
                p["stock"] = max(0, old - item.get("quantity", 0))
                changed = True
                break
    if changed:
        save_products(products)

def update_stock_after_purchase(cart):
    """
    Dodaj quantity do products.json (zakupy magazynowe).
    """
    products = load_products()
    changed = False
    for item in cart:
        found = False
        for p in products:
            if p.get("name") == item.get("name"):
                p["stock"] = p.get("stock", 0) + item.get("quantity", 0)
                found = True
                changed = True
                break
        if not found:
            products.append({
                "name": item.get("name"),
                "price": item.get("price", 0),
                "emoji": item.get("emoji", "📦"),
                "stock": item.get("quantity", 0)
            })
            changed = True
    if changed:
        save_products(products)

def can_fulfill_cart_from_stock(cart):
    """
    Sprawdza czy można zrealizować cart z aktualnego stocku,
    ale ignoruje elementy typu 'order'.
    """
    products = load_products()
    for item in cart:
        if item.get("type") == "order":
            continue
        name = item.get("name")
        qty = item.get("quantity", 0)
        match = next((p for p in products if p.get("name") == name), None)
        stock = match.get("stock", 0) if match else 0
        if qty > stock:
            return False, f"Brak wystarczającej ilości: {name} (w magazynie: {stock}, próbowano sprzedać: {qty})"
    return True, ""

def restore_stock_after_sale(cart):
    """
    Przywraca sprzedane ilości po usunięciu paragonu,
    ale pomija elementy typu 'order'.
    """
    products = load_products()
    changed = False
    for item in cart:
        if item.get("type") == "order":
            continue
        for p in products:
            if p.get("name") == item.get("name"):
                p["stock"] = p.get("stock", 0) + item.get("quantity", 0)
                changed = True
                break
    if changed:
        save_products(products)

def revert_stock_after_purchase(cart):
    """
    Cofnięcie zakupu magazynowego.
    """
    products = load_products()
    changed = False
    for item in cart:
        for p in products:
            if p.get("name") == item.get("name"):
                p["stock"] = max(0, p.get("stock", 0) - item.get("quantity", 0))
                changed = True
                break
    if changed:
        save_products(products)

# ---------- kontekst ----------
@app.context_processor
def inject_business_info():
    sales = load_sales()
    total_sales_amount = sum(s.get('total', 0) for s in sales)
    total_items = sum(it.get('quantity', 0) for s in sales for it in s.get('items', []))
    warehouse_sales = load_sales_warehouse()
    total_warehouse_amount = sum(s.get('total', 0) for s in warehouse_sales)
    discord_user = session.get("discord_user")
    return dict(
        business_balance=BUSINESS_BALANCE,
        total_sales_amount=total_sales_amount,
        total_items=total_items,
        total_warehouse_amount=total_warehouse_amount,
        discord_user=discord_user
    )

# ---------- sklep ----------
@app.route('/')
def index():
    if 'discord_user' not in session:
        # jeśli brak zalogowanego – przekieruj do OAuth Discorda
        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": DISCORD_OAUTH_SCOPE
        }
        url = f"{DISCORD_API_BASE}/oauth2/authorize?{urlencode(params)}"
        return redirect(url)
    return render_template('index.html', products=load_products())

@app.route('/sell', methods=['POST'])
def sell():
    payload = request.json or {}
    cart = payload.get('cart', [])
    phone = payload.get('phone')  # nowość: telefon z frontendu
    if not cart:
        return jsonify({'status': 'empty'}), 400

    ok, msg = can_fulfill_cart_from_stock(cart)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 400

    total = sum(item.get('price', 0) * item.get('quantity', 0) for item in cart)
    discord_user = session.get('discord_user')
    sale = {
        "date": datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S"),
        "items": cart,
        "total": total,
        "user": discord_user,   # zapisujemy kto zatwierdził (może być None)
        "phone": phone
    }

    msg_id = None
    if DISCORD_WEBHOOK_URL:
        try:
            lines = [f"**🧾 Nowa sprzedaż {sale['date']}**"]
            if discord_user:
                lines.append(f"**Zatwierdził:** {discord_user.get('username')}#{discord_user.get('discriminator')} (id: {discord_user.get('id')})")
            if phone:
                lines.append(f"**Telefon:** {phone}")
            for it in cart:
                extra = " - do realizacji" if it.get("type") == "order" else ""
                lines.append(f"- {it.get('name')} x{it.get('quantity')} ({it.get('price')} $/szt.){extra}")
            lines.append(f"**Suma: {total} $**")
            resp = requests.post(DISCORD_WEBHOOK_URL + "?wait=true", json={"content": "\n".join(lines)})
            if resp.status_code == 200:
                msg_id = resp.json().get('id', None)
        except Exception as e:
            print("Błąd przy wysyłaniu na Discord:", e)

    sale["discord_message_id"] = msg_id
    sales = load_sales()
    sales.append(sale)
    save_sales(sales)

    update_stock_after_sale(cart)
    return jsonify({'status': 'ok'})

# ---------- API: aktualne dane produktów ----------
@app.route('/api/products')
def api_products():
    """
    Zwraca najnowsze dane produktów w formacie JSON.
    Używane przez frontend, aby zawsze mieć aktualne stany magazynowe.
    """
    return jsonify(load_products())

@app.route('/sold')
def sold():
    sales = load_sales()
    return render_template(
        'sold.html',
        sales=sales,
        business_balance=BUSINESS_BALANCE,
        total_sales_amount=sum(s.get('total', 0) for s in sales),
        total_items=sum(it.get('quantity', 0) for s in sales for it in s.get('items', []))
    )

@app.route('/delete_sale/<int:index>', methods=['POST'])
def delete_sale(index):
    sales = load_sales()
    if 0 <= index < len(sales):
        sale = sales.pop(index)
        msg_id = sale.get("discord_message_id")
        if msg_id and DISCORD_WEBHOOK_URL:
            try:
                delete_url = f"{DISCORD_WEBHOOK_URL}/messages/{msg_id}"
                requests.delete(delete_url)
            except Exception as e:
                print("Błąd przy usuwaniu wiadomości z Discord:", e)
        restore_stock_after_sale(sale.get("items", []))
        save_sales(sales)
    return redirect(url_for('sold'))

@app.route('/reset', methods=['POST'])
def reset():
    save_sales([])
    return redirect(url_for('sold'))

# ---------- magazyn ----------
@app.route('/magazyn')
@magazyn_required
def magazyn():
    return render_template('magazyn.html', products=load_warehouse())

@app.route('/magazyn/sell', methods=['POST'])
@magazyn_required
def magazyn_sell():
    cart = request.json.get('cart', [])
    if not cart:
        return jsonify({'status': 'empty'}), 400

    total = sum(item.get('price', 0) * item.get('quantity', 0) for item in cart)
    sale = {
        "date": datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S"),
        "items": cart,
        "total": total
    }

    msg_id = None
    if DISCORD_WAREHOUSE_WEBHOOK_URL:
        try:
            lines = [f"**📦 Nowe zamówienie (magazyn) {sale['date']}**"]
            for it in cart:
                extra = " - do realizacji" if it.get("type") == "order" else ""
                lines.append(f"- {it.get('name')} x{it.get('quantity')} ({it.get('price')} $/szt.){extra}")
            lines.append(f"**Suma: {total} $**")
            resp = requests.post(DISCORD_WAREHOUSE_WEBHOOK_URL + "?wait=true", json={"content": "\n".join(lines)})
            if resp.status_code == 200:
                msg_id = resp.json().get('id', None)
        except Exception as e:
            print("Błąd Discord (magazyn):", e)

    sale["discord_message_id"] = msg_id
    sales = load_sales_warehouse()
    sales.append(sale)
    save_sales_warehouse(sales)

    update_stock_after_purchase(cart)
    return jsonify({'status': 'ok'})

@app.route('/zakupy')
@magazyn_required
def zakupy():
    sales = load_sales_warehouse()
    return render_template(
        'zakupy.html',
        sales=sales,
        business_balance=BUSINESS_BALANCE,
        total_sales_amount=sum(s.get('total', 0) for s in sales),
        total_items=sum(it.get('quantity', 0) for s in sales for it in s.get('items', []))
    )

@app.route('/zakupy/delete/<int:index>', methods=['POST'])
@magazyn_required
def delete_zakup(index):
    sales = load_sales_warehouse()
    if 0 <= index < len(sales):
        sale = sales.pop(index)
        msg_id = sale.get("discord_message_id")
        if msg_id and DISCORD_WAREHOUSE_WEBHOOK_URL:
            try:
                delete_url = f"{DISCORD_WAREHOUSE_WEBHOOK_URL}/messages/{msg_id}"
                requests.delete(delete_url)
            except Exception as e:
                print("Błąd przy usuwaniu z Discord (magazyn):", e)
        revert_stock_after_purchase(sale.get("items", []))
        save_sales_warehouse(sales)
    return redirect(url_for('zakupy'))

# ---------- logowanie magazyn ----------
@app.route('/magazyn/login', methods=['GET', 'POST'])
def magazyn_login():
    error = None
    if request.method == 'POST':
        password = request.form.get("password")
        if password == MAGAZYN_PASSWORD:
            session["magazyn_authenticated"] = True
            next_page = request.args.get("next") or url_for("magazyn")
            return redirect(next_page)
        else:
            error = "Nieprawidłowe hasło!"
    return render_template("magazyn_login.html", error=error)

@app.route('/magazyn/logout')
def magazyn_logout():
    session.pop("magazyn_authenticated", None)
    return redirect(url_for("magazyn_login"))

# ---------- wyłączanie cache ----------
@app.after_request
def add_header(response):
    """
    Wyłącza cache przeglądarki, aby zawsze pobierała najnowsze dane.
    Dzięki temu po restarcie komputera strona nie używa starych stanów magazynowych.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


if __name__ == '__main__':
    app.run(debug=True)
