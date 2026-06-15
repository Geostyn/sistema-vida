"""
tuya_luces.py — Control de bombillas SmartLife/Tuya vía Tuya Cloud API.

Se usa desde vida_bot.py (Render, 24/7). No depende de la red local ni del PC:
las órdenes viajan a la nube de Tuya y de ahí a las bombillas.

Variables de entorno necesarias (Render):
  TUYA_ACCESS_ID        Access ID  del proyecto Cloud en iot.tuya.com
  TUYA_ACCESS_SECRET    Access Secret del proyecto Cloud
  TUYA_API_REGION       Región del data center: 'eu' (Central Europe), 'us', 'cn', 'in'
  TUYA_DEVICE_DORMITORIO  Device ID(s) de las luces de ambiente (los spots). Admite
                          VARIAS separadas por coma → se controlan a la vez como una.
  TUYA_DEVICE_ESCRITORIO  Device ID del flexo de escritorio

DP codes típicos de una bombilla RGB Tuya (verificar en Device Debugging):
  switch_led       bool      encendido/apagado
  work_mode        'white'|'colour'
  bright_value_v2  10-1000   brillo (modo blanco)
  colour_data_v2   {h,s,v}   color HSV  (h 0-360, s 0-1000, v 0-1000)
"""
import os
import re
import logging

logger = logging.getLogger("vida_bot")

ACCESS_ID     = os.environ.get("TUYA_ACCESS_ID", "")
ACCESS_SECRET = os.environ.get("TUYA_ACCESS_SECRET", "")
API_REGION    = os.environ.get("TUYA_API_REGION", "eu")


def _parse_ids(raw: str) -> list:
    """Una luz lógica puede tener varias bombillas: separadas por coma/espacio."""
    return [x.strip() for x in re.split(r"[,\s]+", raw or "") if x.strip()]


# Mapa lógico luz → lista de device ids (los 5 spots actúan como uno)
DEVICES = {
    "dormitorio": _parse_ids(os.environ.get("TUYA_DEVICE_DORMITORIO", "")),
    "escritorio": _parse_ids(os.environ.get("TUYA_DEVICE_ESCRITORIO", "")),
}

TUYA_OK = bool(ACCESS_ID and ACCESS_SECRET)

_cloud = None


def disponible() -> bool:
    """True si hay credenciales Tuya configuradas."""
    return TUYA_OK


def _get_cloud():
    """Cliente tinytuya.Cloud cacheado (gestiona la firma HMAC)."""
    global _cloud
    if _cloud is None:
        import tinytuya
        _cloud = tinytuya.Cloud(
            apiRegion=API_REGION,
            apiKey=ACCESS_ID,
            apiSecret=ACCESS_SECRET,
        )
    return _cloud


def _ids_para(luz: str) -> list:
    """Resuelve 'dormitorio'|'escritorio'|'ambas' → lista de device ids válidos."""
    if luz == "ambas":
        out = []
        for lst in DEVICES.values():
            out.extend(lst)
        return out
    return list(DEVICES.get(luz, []))


def _hex_a_hsv(color_hex: str):
    """'#rrggbb' → dict Tuya {h:0-360, s:0-1000, v:0-1000}."""
    s = color_hex.lstrip("#")
    if len(s) != 6:
        return {"h": 30, "s": 1000, "v": 1000}
    r, g, b = (int(s[i:i+2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    diff = mx - mn
    # Hue
    if diff == 0:
        h = 0
    elif mx == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360
    sat = 0 if mx == 0 else diff / mx
    return {"h": int(round(h)), "s": int(round(sat * 1000)), "v": int(round(mx * 1000))}


def _enviar(device_id: str, commands: list) -> bool:
    try:
        res = _get_cloud().sendcommand(device_id, {"commands": commands})
        if isinstance(res, dict) and res.get("success") is False:
            logger.error(f"Tuya sendcommand fallo {device_id}: {res}")
            return False
        return True
    except Exception as e:
        logger.error(f"Tuya sendcommand error {device_id}: {e}")
        return False


def set_luz(luz: str, on=None, brillo=None, color_hex=None) -> bool:
    """Aplica encendido/brillo/color a una luz lógica.
    brillo en 0-100 (%); se escala al rango Tuya 10-1000.
    Devuelve True si todas las bombillas afectadas respondieron OK."""
    if not TUYA_OK:
        logger.warning("Tuya no configurado: set_luz ignorado.")
        return False

    ids = _ids_para(luz)
    if not ids:
        logger.warning(f"set_luz: sin device id para '{luz}'.")
        return False

    commands = []
    if color_hex:
        commands.append({"code": "work_mode", "value": "colour"})
        commands.append({"code": "colour_data_v2", "value": _hex_a_hsv(color_hex)})
    if brillo is not None:
        val = max(10, min(1000, int(round(int(brillo) / 100 * 1000))))
        # En modo color el brillo va en el componente v; en blanco, en bright_value_v2.
        if color_hex:
            hsv = commands[-1]["value"]
            hsv["v"] = val
        else:
            commands.append({"code": "bright_value_v2", "value": val})
    if on is not None:
        commands.append({"code": "switch_led", "value": bool(on)})

    if not commands:
        return True

    return all(_enviar(d, commands) for d in ids)


# ── Escenas (leen sueno_config vía supabase_client) ──────────

def _config():
    try:
        from supabase_client import get_sueno_config
        return get_sueno_config()
    except Exception as e:
        logger.error(f"tuya_luces._config: {e}")
        return {}


def escena_despertar() -> bool:
    """Enciende el dormitorio con color/brillo cálidos de despertar."""
    cfg = _config()
    color = cfg.get("color_despertar") or "#ffb86c"
    brillo = int(cfg.get("brillo_despertar", 80) or 80)
    logger.info("☀️ Escena despertar")
    return set_luz("dormitorio", on=True, brillo=brillo, color_hex=color)


def escena_melatonina() -> bool:
    """Pone las luces en rojo tenue para favorecer la melatonina."""
    cfg = _config()
    brillo = int(cfg.get("brillo_noche", 15) or 15)
    logger.info("🌙 Escena melatonina (luz roja)")
    return set_luz("ambas", on=True, brillo=brillo, color_hex="#ff2200")


def escena_apagar() -> bool:
    """Apaga ambas luces."""
    logger.info("⚫ Escena apagar")
    return set_luz("ambas", on=False)


def ejecutar_comando(luz: str, accion: str, valor: str = "") -> bool:
    """Ejecuta una orden manual venida de la cola luces_comando."""
    accion = (accion or "").lower()
    if accion == "on":
        return set_luz(luz, on=True)
    if accion == "off":
        return set_luz(luz, on=False)
    if accion == "brillo":
        try:
            return set_luz(luz, brillo=int(float(valor)))
        except (ValueError, TypeError):
            return False
    if accion == "color":
        return set_luz(luz, color_hex=valor or "#ffffff")
    logger.warning(f"ejecutar_comando: acción desconocida '{accion}'")
    return False
