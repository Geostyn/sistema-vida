"""Envia mensaje de sistema inactivo a Telegram. Usado por DETENER.bat."""
import sys, yaml, urllib3
urllib3.disable_warnings()
sys.path.insert(0, ".")

with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

from alerts.telegram_bot import TelegramBot
tg = TelegramBot(cfg["telegram"]["bot_token"], cfg["telegram"]["chat_id"])
tg.send_inactive_message()
print("Mensaje de sistema inactivo enviado.")
