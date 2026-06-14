"""Diagnostica el bot de Telegram: muestra mensajes recibidos y chat_id real."""
import requests, urllib3, yaml, sys
urllib3.disable_warnings()
sys.path.insert(0, ".")

with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

token = cfg["telegram"]["bot_token"]
base  = f"https://api.telegram.org/bot{token}"

print("=" * 55)
print("  DIAGNOSTICO TELEGRAM BOT")
print("=" * 55)

# 1. Verificar que el token es valido
r = requests.get(f"{base}/getMe", verify=False, timeout=15)
if r.status_code != 200:
    print("ERROR: Token invalido. Verifica bot_token en config.yaml")
    sys.exit(1)

bot_info = r.json()["result"]
print(f"\nBot encontrado:")
print(f"  Nombre:    {bot_info['first_name']}")
print(f"  Username:  @{bot_info.get('username','?')}")
print(f"  ID del bot: {bot_info['id']}")

# 2. Ver mensajes recibidos (getUpdates)
r2 = requests.get(f"{base}/getUpdates", verify=False, timeout=15)
updates = r2.json().get("result", [])

print(f"\nMensajes recibidos por el bot: {len(updates)}")

if not updates:
    print("\n  El bot NO ha recibido ningun mensaje aun.")
    print(f"  Abre Telegram, busca @{bot_info.get('username','tu_bot')} y escribe /start")
else:
    print("\n  Mensajes recibidos:")
    for upd in updates[-5:]:
        msg     = upd.get("message", {})
        chat    = msg.get("chat", {})
        text    = msg.get("text", "")
        chat_id = chat.get("id", "")
        name    = chat.get("first_name", "") + " " + chat.get("last_name", "")
        print(f"    chat_id: {chat_id} | De: {name.strip()} | Texto: {text}")

    # Usar el chat_id del ultimo mensaje
    last_msg    = updates[-1].get("message", {})
    correct_id  = str(last_msg.get("chat", {}).get("id", ""))
    current_cfg = cfg["telegram"]["chat_id"]

    print(f"\n  chat_id en config.yaml:  {current_cfg}")
    print(f"  chat_id real del bot:    {correct_id}")

    if correct_id and correct_id != current_cfg:
        print(f"\n  CORRIGIENDO chat_id en config.yaml a: {correct_id}")
        with open("config.yaml", "r") as f:
            content = f.read()
        content = content.replace(
            f'chat_id: "{current_cfg}"',
            f'chat_id: "{correct_id}"'
        )
        with open("config.yaml", "w") as f:
            f.write(content)
        print("  config.yaml actualizado.")
    elif correct_id == current_cfg:
        print("  chat_id ya es correcto.")

# 3. Intentar enviar mensaje de prueba
print("\nEnviando mensaje de prueba...")
r3 = requests.post(
    f"{base}/sendMessage",
    json={"chat_id": correct_id if updates else cfg["telegram"]["chat_id"],
          "text": "Sistema de Trading XAUUSD conectado correctamente!"},
    verify=False, timeout=20
)
if r3.status_code == 200:
    print("  EXITO: Mensaje enviado. Revisa Telegram ahora.")
else:
    print(f"  ERROR {r3.status_code}: {r3.json().get('description','')}")

print("=" * 55)
