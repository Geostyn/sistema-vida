"""
daily_digest — Resumen diario del bot por Telegram, redactado con LLM (Groq).

Al cierre de la sesión (hora UTC configurable) envía UN mensaje con:
  - los trades cerrados HOY (W/L, PnL, mejor/peor hora, sesgo de dirección),
  - el contexto de noticias del oro (news_sentiment, modo sombra),
  - un resumen en lenguaje natural + 1 sugerencia accionable, escrito por el LLM.

Diseño defensivo:
  - Las CIFRAS salen de trades.db (no del LLM) → siempre son correctas.
  - Si Groq / la clave no están disponibles, se envía el resumen factual sin
    la parte redactada (nunca rompe el ciclo).
  - Guard de "una vez al día" en main.py (self._last_digest_date).

Reutiliza el patrón de LLM de vida_bot.py: Groq(api_key=cfg['groq']['api_key']).
"""

from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def gather_stats(db_path: str, day: str | None = None) -> dict:
    """Cifras objetivas de los trades CERRADOS en `day` (UTC, por defecto hoy)."""
    day = day or _today_utc()
    out = {
        "day": day, "closed": 0, "wins": 0, "losses": 0, "pnl": 0.0,
        "buys": 0, "sells": 0, "best_hour": None, "worst_hour": None,
        "pending": 0, "by_hour": [],
    }
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur  = conn.cursor()
        rows = cur.execute(
            "SELECT direction, outcome, COALESCE(pnl_amount,0), "
            "       CAST(strftime('%H', timestamp) AS INT) AS hh "
            "FROM signals "
            "WHERE DATE(timestamp)=? AND outcome IN ('WIN','LOSS')",
            (day,),
        ).fetchall()
        out["pending"] = cur.execute(
            "SELECT COUNT(*) FROM signals WHERE DATE(timestamp)=? AND outcome='PENDING'",
            (day,),
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        logger.debug(f"[digest] gather_stats: {e}")
        return out

    per_hour: dict[int, list[int]] = {}
    for direction, outcome, pnl, hh in rows:
        out["closed"] += 1
        out["pnl"]    += float(pnl or 0)
        if outcome == "WIN":
            out["wins"] += 1
        else:
            out["losses"] += 1
        if str(direction).upper().startswith("B"):
            out["buys"] += 1
        else:
            out["sells"] += 1
        per_hour.setdefault(int(hh), [0, 0])
        per_hour[int(hh)][0 if outcome == "WIN" else 1] += 1

    # Mejor/peor hora por win-rate (mín 2 trades)
    ranked = []
    for hh, (w, l) in sorted(per_hour.items()):
        n = w + l
        wr = w / n if n else 0
        ranked.append((hh, w, l, wr))
        out["by_hour"].append({"hour": hh, "w": w, "l": l, "wr": round(wr, 2)})
    sig = [r for r in ranked if (r[1] + r[2]) >= 2]
    if sig:
        out["best_hour"]  = max(sig, key=lambda r: r[3])
        out["worst_hour"] = min(sig, key=lambda r: r[3])
    return out


def _factual_text(s: dict) -> str:
    if s["closed"] == 0:
        return (f"📊 <b>RESUMEN DEL DÍA — {s['day']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Sin trades cerrados hoy. Pendientes: {s['pending']}.")
    wr = s["wins"] / s["closed"] if s["closed"] else 0
    lines = [
        f"📊 <b>RESUMEN DEL DÍA — {s['day']}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Cerrados: <b>{s['closed']}</b>  (✅ {s['wins']} / ❌ {s['losses']})  "
        f"WR <b>{wr:.0%}</b>",
        f"P&L: <b>{s['pnl']:+,.0f}</b>   |   Pendientes: {s['pending']}",
        f"Dirección: {s['buys']} BUY / {s['sells']} SELL",
    ]
    if s["best_hour"]:
        bh = s["best_hour"]; wh = s["worst_hour"]
        lines.append(f"Mejor hora: {bh[0]:02d}h ({bh[1]}W/{bh[2]}L) · "
                     f"Peor: {wh[0]:02d}h ({wh[1]}W/{wh[2]}L)")
    return "\n".join(lines)


def _llm_comment(config: dict, s: dict, news_ctx: str = "") -> str:
    """Comentario breve del LLM (Groq). Devuelve '' si no hay clave/lib."""
    key = (os.environ.get("GROQ_API_KEY")
           or config.get("groq", {}).get("api_key", ""))
    if not key or s["closed"] == 0:
        return ""
    model = config.get("groq", {}).get("model", _DEFAULT_MODEL)
    wr = s["wins"] / s["closed"] if s["closed"] else 0
    facts = (
        f"Trades cerrados hoy: {s['closed']} (WR {wr:.0%}, {s['wins']}W/{s['losses']}L). "
        f"PnL {s['pnl']:+.0f}. Dirección {s['buys']} BUY / {s['sells']} SELL. "
        f"Mejor hora {s['best_hour'][0] if s['best_hour'] else '?'}h, "
        f"peor {s['worst_hour'][0] if s['worst_hour'] else '?'}h. "
        f"Contexto noticias oro: {news_ctx or 'neutral'}."
    )
    try:
        import httpx
        from groq import Groq
        # SSL desactivado (Windows local, mismos certificados rotos que yfinance/Marstek)
        client = Groq(api_key=key, http_client=httpx.Client(verify=False, timeout=20))
        chat = client.chat.completions.create(
            model=model,
            temperature=0.4,
            max_tokens=220,
            messages=[
                {"role": "system", "content":
                    "Eres el analista cuantitativo de un bot de trading de oro "
                    "(XAUUSD). Hablas español, directo y sin relleno. Responde en "
                    "máx 4 frases: 1) veredicto del día, 2) qué funcionó, 3) qué "
                    "falló, 4) UNA sugerencia accionable concreta. No inventes cifras."},
                {"role": "user", "content": facts},
            ],
        )
        return chat.choices[0].message.content.strip()
    except Exception as e:
        logger.debug(f"[digest] LLM no disponible: {e}")
        return ""


def build_digest(config: dict, db_path: str, news_ctx: str = "", day: str | None = None) -> str:
    """Texto completo del digest: cifras objetivas + comentario LLM (si hay)."""
    s = gather_stats(db_path, day)
    text = _factual_text(s)
    comment = _llm_comment(config, s, news_ctx)
    if comment:
        text += "\n━━━━━━━━━━━━━━━━━━━━━━\n🧠 <b>Análisis</b>\n" + comment
    return text


# ── Uso manual: python alerts/daily_digest.py ──────────────────────
if __name__ == "__main__":
    import sys, yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    dbp = cfg.get("logging", {}).get("db_path", "logs/trades.db")
    if not os.path.isabs(dbp):
        dbp = os.path.join(root, dbp)
    day = sys.argv[1] if len(sys.argv) > 1 else None
    print(build_digest(cfg, dbp, day=day))
