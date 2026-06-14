"""Test de conexiones: Telegram y Finnhub (sin necesitar MT5)."""
import sys, requests, urllib3
sys.path.insert(0, ".")
import yaml

urllib3.disable_warnings()

with open("config.yaml", "r") as f:
    cfg = yaml.safe_load(f)

print("=" * 50)
print("  TEST DE CONEXIONES")
print("=" * 50)

# ── Test Telegram directo (sin parse_mode para diagnosticar) ──
print("\n[1/2] Probando Telegram...")
token   = cfg["telegram"]["bot_token"]
chat_id = cfg["telegram"]["chat_id"]

url  = f"https://api.telegram.org/bot{token}/sendMessage"
data = {"chat_id": chat_id, "text": "Sistema de Trading XAUUSD: conexion OK. El bot esta activo."}

try:
    resp = requests.post(url, json=data, timeout=30, verify=False)
    if resp.status_code == 200:
        print("  Telegram: OK - Mensaje enviado!")
    else:
        body = resp.json()
        err  = body.get("description", str(resp.status_code))
        print("  Telegram: Error " + str(resp.status_code) + " - " + err)
        if "chat not found" in err.lower() or "forbidden" in err.lower():
            print()
            print("  SOLUCION: Abre Telegram, busca tu bot y escribe /start")
            print("  Tu bot se llama como lo nombraste en @BotFather")
        elif "bad request" in err.lower():
            print()
            print("  SOLUCION: Envia /start a tu bot en Telegram primero")
except Exception as e:
    print("  Telegram: Excepcion - " + str(e))

# ── Test Finnhub ──────────────────────────────────────────────
print("\n[2/2] Probando Finnhub (noticias)...")
from data.news_feed import NewsFeed
news = NewsFeed(cfg["apis"]["finnhub_key"])
try:
    events = news.get_high_impact_events(hours_ahead=72)
    if events:
        print("  Finnhub: OK - " + str(len(events)) + " eventos proximos:")
        for ev in events[:5]:
            print("    - " + ev["time"].strftime("%d/%m %H:%M UTC") + " | "
                  + ev["country"] + " | " + ev["event"])
    else:
        print("  Finnhub: OK (sin eventos alto impacto en los proximos 3 dias)")
except Exception as e:
    print("  Finnhub: Error - " + str(e))

print("\n" + "=" * 50)
