"""
main.py — Punto de entrada del Sistema de Trading XAUUSD.

Modos de uso:
  python main.py              → Loop continuo de análisis (modo normal)
  python main.py --test       → Un solo ciclo de análisis y sale
  python main.py --backtest   → Ejecuta backtesting sobre el último año

Dashboard (en otra terminal):
  streamlit run dashboard/app.py
"""

import sys
import os
import yaml
import json
import logging
import sqlite3
import schedule
import time
from datetime import datetime, timezone

# ── Logging ───────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

# Forzar UTF-8 en la terminal de Windows para evitar error con emojis
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ── Importar módulos ──────────────────────────────────────────
import MetaTrader5 as mt5
from data.mt5_connector import MT5Connector
from data.news_feed import NewsFeed
from data.macro_feed import MacroFeed
from data.intermarket_feed import IntermarketFeed
from analysis.signal_engine import SignalEngine
from analysis.indicators import add_indicators, get_ema_bias, get_rsi_state
from analysis.market_structure import detect_market_structure, find_order_blocks
from analysis.correlation_engine import CorrelationEngine
from risk.risk_manager import RiskManager
from risk.funded_account import FundedAccountTracker
from risk.personal_account import PersonalAccountTracker
from alerts.telegram_bot import TelegramBot
from alerts.news_alerts import NewsAlertManager
from alerts.signal_dedup import SignalDedup
from ml.outcome_tracker import OutcomeTracker
from ml.learning_engine import LearningEngine
from ml.neural_engine import NeuralEngine
from trade.executor import TradeExecutor
from trade.trade_manager import TradeManager
from analysis.volume_profile import VolumeProfileEngine
from analysis.delta_engine import DeltaEngine
from analysis.market_regime import MarketRegimeEngine
from analysis.quant_engine import QuantEngine


# ──────────────────────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        logger.error(f"config.yaml no encontrado. Ruta buscada: {os.path.abspath(path)}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ──────────────────────────────────────────────────────────────
# Base de datos SQLite
# ──────────────────────────────────────────────────────────────

def init_database(db_path: str):
    """Crea las tablas de la base de datos si no existen."""
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            symbol          TEXT,
            direction       TEXT,
            entry           REAL,
            sl              REAL,
            tp1             REAL,
            tp2             REAL,
            rr              REAL,
            confidence      REAL,
            confluences     INTEGER,
            bias_h4         TEXT,
            structure_h1    TEXT,
            ob_type         TEXT,
            rsi_state       TEXT,
            news_warning    TEXT,
            news_blackout   INTEGER DEFAULT 0,
            atr             REAL,
            lot_size        REAL,
            sent_telegram      INTEGER DEFAULT 0,
            outcome            TEXT    DEFAULT 'PENDING',
            pnl_amount         REAL    DEFAULT 0,
            pnl_pct            REAL    DEFAULT 0,
            breakeven_alerted  INTEGER DEFAULT 0,
            notes              TEXT    DEFAULT '',
            mt5_ticket         INTEGER DEFAULT NULL
        )
    """)
    # Migración: añadir columnas nuevas si no existen (DB antigua)
    migrations = [
        ("mt5_ticket",     "INTEGER DEFAULT NULL"),
        # Features ML — antes solo vivían en memoria y el modelo entrenaba
        # con ceros; ahora se persisten para entrenar con datos reales
        ("vp_score",       "REAL DEFAULT NULL"),
        ("delta_score",    "REAL DEFAULT NULL"),
        ("atr_pct",        "REAL DEFAULT NULL"),
        ("hurst",          "REAL DEFAULT NULL"),
        ("adx",            "REAL DEFAULT NULL"),
        ("pairs_score",    "REAL DEFAULT NULL"),
        ("ml_proba",       "REAL DEFAULT NULL"),
        ("regime",         "TEXT DEFAULT NULL"),
        ("sweep_score",    "REAL DEFAULT NULL"),
        ("fvg_score",      "REAL DEFAULT NULL"),
        ("m15_aligned",    "INTEGER DEFAULT NULL"),
        ("entry_type",     "TEXT DEFAULT NULL"),
        # Distancia (en ATR) al pool de liquidez HTF más cercano en contra
        # de la señal; 99 = sin pool — feature ML del veto de liquidez
        ("htf_liq_dist",   "REAL DEFAULT NULL"),
        # Estado de gestión activa (TradeManager)
        ("partial_closed", "INTEGER DEFAULT 0"),
        # Capa FundedNext 2K (ejecución manual del usuario)
        ("funded_lots",     "REAL DEFAULT NULL"),
        ("funded_risk_usd", "REAL DEFAULT NULL"),
        ("funded_apta",     "INTEGER DEFAULT 0"),
        ("funded_entry",    "REAL DEFAULT NULL"),
        ("funded_sl",       "REAL DEFAULT NULL"),
        ("funded_pnl",      "REAL DEFAULT NULL"),
        ("funded_applied",  "INTEGER DEFAULT 0"),
        # Modelo de entrada: OB (retroceso clásico) | SWEEP_REVERSAL | DAYTRADE
        ("model",           "TEXT DEFAULT 'OB'"),
        # Capa cuenta personal pequeña (ejecución manual del usuario)
        ("personal_lots",     "REAL DEFAULT NULL"),
        ("personal_risk_acc", "REAL DEFAULT NULL"),
        ("personal_apta",     "INTEGER DEFAULT 0"),
        ("personal_entry",    "REAL DEFAULT NULL"),
        ("personal_sl",       "REAL DEFAULT NULL"),
        ("personal_pnl",      "REAL DEFAULT NULL"),
        ("personal_applied",  "INTEGER DEFAULT 0"),
        # Features intermarket + quant (upgrade 2026-06-17) — para entrenar el ML/NN
        ("inter_score",     "REAL DEFAULT NULL"),
        ("real_yield_imp",  "REAL DEFAULT NULL"),
        ("cot_impact",      "REAL DEFAULT NULL"),
        ("cot_percentile",  "REAL DEFAULT NULL"),
        ("garch_vol",       "REAL DEFAULT NULL"),
        ("kalman_slope",    "REAL DEFAULT NULL"),
        ("neural_proba",    "REAL DEFAULT NULL"),  # LSTM modo sombra (no veta)
    ]
    for col, decl in migrations:
        try:
            cursor.execute(f"ALTER TABLE signals ADD COLUMN {col} {decl}")
            conn.commit()
            logger.info(f"Columna {col} añadida (migración DB)")
        except Exception:
            pass  # Ya existe

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS performance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT UNIQUE,
            starting_equity REAL,
            ending_equity   REAL,
            daily_pnl       REAL    DEFAULT 0,
            trades_signaled INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funded_equity (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            equity          REAL NOT NULL,
            highest_equity  REAL NOT NULL,
            source          TEXT,
            signal_id       INTEGER,
            note            TEXT
        )
    """)

    # Índices para acelerar las consultas de stats/outcome/risk cuando la
    # tabla signals crece (antes: full scan en cada /status, risk, ML).
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_signals_ts      ON signals(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome)",
        "CREATE INDEX IF NOT EXISTS idx_signals_symbol  ON signals(symbol)",
    ):
        try:
            cursor.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
    conn.close()
    logger.info(f"Base de datos lista: {db_path}")


def save_signal(signal: dict, lot_size: float, sent_tg: bool, db_path: str):
    """Guarda una señal en la base de datos."""
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
    funded   = signal.get("funded") or {}
    personal = signal.get("personal") or {}
    cursor.execute("""
        INSERT INTO signals (
            timestamp, symbol, direction, entry, sl, tp1, tp2, rr,
            confidence, confluences, bias_h4, structure_h1, ob_type,
            rsi_state, news_warning, news_blackout, atr, lot_size,
            sent_telegram, mt5_ticket,
            vp_score, delta_score, atr_pct, hurst, adx, pairs_score,
            ml_proba, regime, sweep_score, fvg_score, m15_aligned, entry_type,
            htf_liq_dist,
            funded_lots, funded_risk_usd, funded_apta, funded_entry, funded_sl,
            model,
            personal_lots, personal_risk_acc, personal_apta,
            personal_entry, personal_sl,
            inter_score, real_yield_imp, cot_impact, cot_percentile,
            garch_vol, kalman_slope, neural_proba
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        signal.get("timestamp"),
        signal.get("symbol"),
        signal.get("direction"),
        signal.get("entry"),
        signal.get("sl"),
        signal.get("tp1"),
        signal.get("tp2"),
        signal.get("rr"),
        signal.get("confidence"),
        signal.get("confluences"),
        signal.get("bias_h4"),
        signal.get("structure_h1"),
        signal.get("ob_type"),
        signal.get("rsi_state"),
        signal.get("news_warning"),
        int(signal.get("news_blackout", False)),
        signal.get("atr"),
        lot_size,
        int(sent_tg),
        signal.get("ticket"),  # mt5_ticket — None si no se colocó orden
        signal.get("vp_score"),
        signal.get("delta_score"),
        signal.get("atr_pct"),
        signal.get("regime_hurst"),
        signal.get("regime_adx"),
        signal.get("pairs_score"),
        signal.get("ml_proba"),
        signal.get("regime"),
        signal.get("sweep_score"),
        signal.get("fvg_score"),
        signal.get("m15_aligned"),
        signal.get("entry_type"),
        signal.get("htf_liq_dist"),
        funded.get("lots"),
        funded.get("risk_usd"),
        int(funded.get("apta", False)),
        funded.get("entry"),
        funded.get("sl"),
        signal.get("model", "OB"),
        personal.get("lots"),
        personal.get("risk_acc"),
        int(personal.get("apta", False)),
        personal.get("entry"),
        personal.get("sl"),
        # Features intermarket + quant (upgrade 2026-06-17)
        signal.get("inter_score"),
        (signal.get("intermarket") or {}).get("real_yields", {}).get("gold_impact"),
        (signal.get("intermarket") or {}).get("cot", {}).get("gold_impact"),
        (signal.get("intermarket") or {}).get("cot", {}).get("percentile"),
        signal.get("garch_vol"),
        signal.get("kalman_slope"),
        signal.get("neural_proba"),
    ))
    conn.commit()
    conn.close()


def _get_trade_stats(db_path: str) -> dict:
    """Lee estadísticas de trades desde SQLite. EXPIRED no cuenta como LOSS en WR/PF."""
    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        cur.execute("SELECT outcome, COUNT(*), COALESCE(SUM(pnl_amount),0) FROM signals GROUP BY outcome")
        by_outcome = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT symbol, direction, confidence, outcome, timestamp FROM signals ORDER BY timestamp DESC LIMIT 5")
        recent = cur.fetchall()
        # PF solo con WIN y LOSS reales
        cur.execute("SELECT COALESCE(SUM(pnl_amount),0) FROM signals WHERE outcome='WIN'")
        gross_win = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(ABS(SUM(pnl_amount)),0) FROM signals WHERE outcome='LOSS'")
        gross_loss = cur.fetchone()[0]
        conn.close()
        wins_n,   wins_pnl   = by_outcome.get("WIN",     (0, 0))
        losses_n, losses_pnl = by_outcome.get("LOSS",    (0, 0))
        expired_n             = by_outcome.get("EXPIRED", (0, 0))[0]
        pending_n             = by_outcome.get("PENDING", (0, 0))[0]
        closed = wins_n + losses_n  # EXPIRED excluido
        return {
            "wins":    wins_n,
            "losses":  losses_n,
            "expired": expired_n,
            "pending": pending_n,
            "pnl_total":     wins_pnl + losses_pnl,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0,
            "wr_str": f"{wins_n/closed:.1%}" if closed > 0 else "sin cierres",
            "recent": recent,
        }
    except Exception:
        return {"wins": 0, "losses": 0, "expired": 0, "pending": 0,
                "pnl_total": 0, "profit_factor": 0, "wr_str": "sin datos", "recent": []}


def update_obsidian_state(config: dict, acct: dict, db_path: str):
    """Escribe ESTADO-SISTEMA.md en el vault de Obsidian con estado actual completo."""
    vault = config.get("obsidian", {}).get("vault_path", "")
    fname = config.get("obsidian", {}).get("state_file", "ESTADO-SISTEMA.md")
    if not vault or not os.path.isdir(vault):
        return

    risk = config.get("risk", {})
    sess = config.get("sessions", {}).get("allowed_hours_utc", {})
    syms = config.get("symbols", {})
    st   = _get_trade_stats(db_path)

    # Estado cuentas manuales (personal + 2K) — para el bloque de estado
    p_cfg  = config.get("personal", {}) or {}
    dt_cfg = config.get("daytrade", {}) or {}
    p_line = "- Cuenta personal: desactivada"
    if p_cfg.get("enabled"):
        p_eq = float(p_cfg.get("balance", 200))
        try:
            _c = sqlite3.connect(db_path)
            _r = _c.execute(
                "SELECT equity FROM personal_equity ORDER BY id DESC LIMIT 1"
            ).fetchone()
            _c.close()
            if _r:
                p_eq = float(_r[0])
        except Exception:
            pass
        p_line = (
            f"- 💼 {p_cfg.get('title', 'MI CUENTA')}: {p_eq:,.2f} "
            f"{p_cfg.get('currency', 'EUR')} | riesgo "
            f"{p_cfg.get('risk_per_trade', 0.01)*100:.0f}%/trade | "
            f"apta si riesgo ≤ {p_cfg.get('max_risk_pct', 0.03)*100:.0f}% | /saldo para sync"
        )
    dt_line = "- ⚡ DAYTRADE M15: desactivado"
    if dt_cfg.get("enabled"):
        dt_hours = dt_cfg.get("hours_utc", "") or "sesión global"
        dt_line = (
            f"- ⚡ DAYTRADE M15: bias H1 + OB M15 | umbral "
            f"{dt_cfg.get('min_confluences', 4.0)}/8.0 | TP1 "
            f"{dt_cfg.get('min_rr', 1.5)}R | horas {dt_hours} UTC | "
            f"ilimitadas (dedup contexto) | manual | backtest: WR 41% PF 1.31"
        )

    recent_lines = "\n".join(
        f"  - {r[0]} {r[1]} {r[2]:.0%} → {r[3]} ({r[4][:16]})" for r in st["recent"]
    ) or "  - Sin señales aún"

    pnl_str = f"+€{st['pnl_total']:,.0f}" if st["pnl_total"] >= 0 else f"-€{abs(st['pnl_total']):,.0f}"
    pf_str  = f"{st['profit_factor']:.2f}" if st["profit_factor"] > 0 else "sin datos"

    content = f"""---
title: Estado del Sistema de Trading
auto-generado: true
ultima-actualizacion: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC
tags:
  - sistema/trading
  - estado/activo
---

# Estado del Sistema de Trading — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

> [!tip] Lee este archivo primero en cada sesión nueva con el trading system

## Sistema
- **Ruta código:** `C:\\Users\\geost\\Desktop\\trading-system\\`
- **Arrancar:** doble click `INICIAR TRADING` en el escritorio (AutoTrading MT5 debe estar VERDE ▶)
- **Dashboard:** http://127.0.0.1:8501

## Cuenta MT5
- Cuenta: {acct.get('login', '?')} | Balance: €{acct.get('balance', 0):,.0f} {acct.get('currency', 'EUR')}
- Servidor: {acct.get('server', 'MetaQuotes-Demo')} | Leverage: 1:{acct.get('leverage', '?')}

## Parámetros activos (config.yaml)
- Sesión: {sess.get('start', 7)}:00–{sess.get('end', 21)}:00 UTC (solo Londres + NY)
- Confluencias mín: {risk.get('min_confluences', 5.0)}/16.5 | R:R: {risk.get('min_rr', 2.0)} | Riesgo: {risk.get('risk_per_trade', 0.01)*100:.0f}%
- Trading: solo XAUUSD (forex desactivado tras backtest — WR 27-31%)
- Módulos: SMC + VP COMEX + Delta ticks + TPO + ML + Macro + DXY + FVG + Sweep + M15
- Gestión activa: entry retroceso OB · parcial 50% en TP1 · runner a TP2 con trailing ATR

## Streams de señales (2 en paralelo)
- 📊 INTRADAY H1: auto-ejecutada en demo (aprendizaje — el producto son las señales)
{dt_line}

## Cuenta manual (bloque en cada señal Telegram)
{p_line}

## Backtest validado (XAUUSD H1, 1 año con comisiones — upgrade 2026-06-11)
- Win Rate: **42.1%** | PF: **1.61** | Max DD: **12.0%** | Sharpe: **3.02** | Retorno: **+105%**
- Entry retroceso OB + BE@1R + parcial 50%@TP1 + trailing runner (64 trades salvados por BE)

## Rendimiento en vivo
- WIN: {st['wins']} | LOSS: {st['losses']} | EXPIRED: {st.get('expired',0)} | PENDING: {st['pending']}
- Win Rate real: **{st['wr_str']}** | PF: {pf_str} | P&L: {pnl_str}
{recent_lines}

## Estado módulos avanzados
- Volume Profile COMEX (GC=F): cache 60 min — se auto-recupera si rate limited
- Delta/Footprint (ticks MT5): activo por barra H1
- Ejecución MT5: automática (LIMIT orders) — órdenes expiran en 8h
- Telegram bidireccional: /cancelar /modificar /cerrar
- /radiografia — qué dice cada módulo del cerebro sobre XAUUSD ahora (o RADIOGRAFIA.bat)

## Para la próxima sesión con Claude
- Acumular 20+ trades cerrados → ML se entrena automáticamente
- Revisar [[proyectos/trading-demo-30-dias]] — tabla auto-actualizada con cada cierre
- Evaluar cuenta real: necesita ≥30 trades y WR ≥ 38%

## Referencias
- [[proyectos/sistema-trading-xauusd]] — nota principal con arquitectura y decisiones
- [[proyectos/trading-demo-30-dias]] — seguimiento demo (auto-actualizado)
- [[ideas/mejoras-sistema-trading]] — backlog de mejoras
"""

    out = os.path.join(vault, fname)
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Obsidian estado actualizado: {fname}")


def update_obsidian_demo_log(config: dict, db_path: str):
    """
    Actualiza automáticamente la tabla de seguimiento demo en Obsidian
    con los resultados de trades cerrados desde SQLite.
    Se llama cuando el outcome_tracker detecta nuevos WIN/LOSS.
    """
    vault     = config.get("obsidian", {}).get("vault_path", "")
    demo_path = os.path.join(vault, "proyectos", "trading-demo-30-dias.md")
    if not vault or not os.path.isdir(vault) or not os.path.exists(demo_path):
        return

    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        # Leer todos los trades cerrados
        cur.execute("""
            SELECT DATE(timestamp), symbol, direction, outcome, pnl_amount, rr, confidence
            FROM signals
            WHERE outcome IN ('WIN','LOSS')
            ORDER BY timestamp ASC
        """)
        closed = cur.fetchall()
        # Estadísticas globales
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) FROM signals WHERE outcome IN ('WIN','LOSS')")
        total_closed, total_wins = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(pnl_amount),0) FROM signals WHERE outcome IN ('WIN','LOSS')")
        total_pnl = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(pnl_amount),0) FROM signals WHERE outcome='WIN'")
        gross_win = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(ABS(SUM(pnl_amount)),0) FROM signals WHERE outcome='LOSS'")
        gross_loss = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM signals WHERE outcome='PENDING'")
        total_pending = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        logger.warning(f"Error leyendo trades para Obsidian: {e}")
        return

    if not closed:
        return

    # Agrupar por semana (ISO week) y día
    from collections import defaultdict
    by_week_day = defaultdict(lambda: defaultdict(list))
    for row in closed:
        date_str, sym, direc, outcome, pnl, rr, conf = row
        try:
            from datetime import date as ddate
            d = ddate.fromisoformat(date_str)
            week_num = d.isocalendar()[1]
            by_week_day[week_num][date_str].append({
                "sym": sym, "direction": direc, "outcome": outcome,
                "pnl": float(pnl or 0), "rr": float(rr or 2.0),
            })
        except Exception:
            continue

    # Calcular métricas globales
    total_losses = (total_closed or 0) - (total_wins or 0)
    wr    = (total_wins / total_closed * 100) if total_closed else 0
    pf    = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0
    dd_pct = 0  # TODO: calcular drawdown real
    pnl_str = f"+€{total_pnl:,.0f}" if (total_pnl or 0) >= 0 else f"-€{abs(total_pnl or 0):,.0f}"

    # Determinar si pasa los criterios
    wr_status  = "✅" if wr >= 38 else ("⚠️" if wr >= 33 else "❌")
    pf_status  = "✅" if pf >= 1.2 else "⚠️"
    cnt_status = "✅" if total_closed >= 20 else "⏳"

    # Construir sección de registro de trades cerrados
    trades_log = "## 📋 Registro de trades cerrados (auto-actualizado)\n\n"
    trades_log += "| Fecha | Símbolo | Dir | Resultado | P&L EUR | R:R |\n"
    trades_log += "|-------|---------|-----|-----------|---------|-----|\n"
    for row in closed:
        date_str, sym, direc, outcome, pnl, rr, conf = row
        pnl_v   = float(pnl or 0)
        pnl_fmt = f"+€{pnl_v:,.0f}" if pnl_v >= 0 else f"-€{abs(pnl_v):,.0f}"
        emoji   = "✅" if outcome == "WIN" else "❌"
        trades_log += f"| {date_str} | {sym} | {direc} | {emoji} {outcome} | {pnl_fmt} | 1:{float(rr or 2):.1f} |\n"

    # Leer el archivo actual
    with open(demo_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Actualizar criterios en la tabla
    content = content.replace(
        "| Win Rate | 42.1% | ≥ 38% | ⏳ |",
        f"| Win Rate | 42.1% | ≥ 38% | {wr_status} {wr:.1f}% |"
    )
    content = content.replace(
        "| Profit Factor | 1.45 | ≥ 1.2 | ⏳ |",
        f"| Profit Factor | 1.45 | ≥ 1.2 | {pf_status} {pf} |"
    )
    content = content.replace(
        "| Mínimo de trades | 361/año | ≥ 20 en demo | ⏳ |",
        f"| Mínimo de trades | 361/año | ≥ 20 en demo | {cnt_status} {total_closed}/20 |"
    )

    # Actualizar totales del período
    content = content.replace("| Total señales | - | 361/año → ~30/mes |",
        f"| Total señales | {total_closed + total_pending} | 361/año → ~30/mes |")
    content = content.replace("| Ganadores | - | - |", f"| Ganadores | {total_wins or 0} | - |")
    content = content.replace("| Perdedores | - | - |", f"| Perdedores | {total_losses} | - |")
    content = content.replace("| Win Rate | - | 42.1% |", f"| Win Rate | {wr:.1f}% | 42.1% |")
    content = content.replace("| Profit Factor | - | 1.45 |", f"| Profit Factor | {pf} | 1.45 |")
    content = content.replace("| P&L total EUR | - | - |", f"| P&L total EUR | {pnl_str} | - |")

    # Añadir / reemplazar sección de registro detallado
    marker = "## 📋 Registro de trades cerrados"
    if marker in content:
        # Reemplazar la sección existente
        end_marker = "\n---\n"
        start_idx = content.index(marker)
        end_idx   = content.find(end_marker, start_idx)
        if end_idx == -1:
            end_idx = len(content)
        content = content[:start_idx] + trades_log + content[end_idx:]
    else:
        # Insertar antes de "## ⚠️ Reglas de parada"
        insert_before = "## ⚠️ Reglas de parada"
        if insert_before in content:
            content = content.replace(insert_before, trades_log + "\n---\n\n" + insert_before)
        else:
            content += "\n---\n\n" + trades_log

    with open(demo_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Obsidian demo log actualizado: {total_closed} trades cerrados")


def update_market_state(state: dict):
    """Guarda el estado del mercado en JSON para que el dashboard lo lea."""
    state["last_update"] = datetime.now(timezone.utc).isoformat()
    path = os.path.join("logs", "market_state.json")

    def default_serializer(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if hasattr(obj, "item"):       # numpy scalar
            return obj.item()
        return str(obj)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=default_serializer)


# ──────────────────────────────────────────────────────────────
# Sistema principal
# ──────────────────────────────────────────────────────────────

class TradingSystem:
    def __init__(self, config: dict):
        self.config  = config
        self.db_path = config.get("logging", {}).get("db_path", "logs/trades.db")

        # Módulos base
        self.mt5   = MT5Connector(config)
        self.news  = NewsFeed(config.get("apis", {}).get("finnhub_key", ""))
        self.risk  = RiskManager(config)

        # Cuenta FundedNext 2K (simulación — ejecución manual del usuario)
        self.funded = FundedAccountTracker(config, self.db_path)

        # Cuenta personal pequeña (ejecución manual — bloque MI CUENTA)
        self.personal = PersonalAccountTracker(config, self.db_path)

        # Módulos nuevos: correlaciones, macro, ML
        self.corr  = CorrelationEngine(self.mt5)
        # DXY en tiempo real desde MT5 (formula ICE 6 pares) — yfinance solo fallback
        self.macro = MacroFeed(correlation_engine=self.corr)
        self.ml    = LearningEngine(self.db_path)
        self.tracker = OutcomeTracker(self.mt5, self.db_path, executor=None, config=config)  # executor se asigna en start()

        # Módulos avanzados: Volume Profile (COMEX) + Delta (ticks MT5)
        self.vp    = VolumeProfileEngine(config)
        self.delta = DeltaEngine(self.mt5)

        # Régimen de mercado cuántico (Hurst + ADX + Volatility)
        self.regime = MarketRegimeEngine(self.mt5)

        # Intermarket: rendimientos reales (FRED/TIP) + COT + oro/plata + riesgo
        self.intermarket = IntermarketFeed(config, mt5_connector=self.mt5)

        # Red neuronal LSTM (modo sombra) — se carga sola si existe el modelo
        self.neural = NeuralEngine()

        # Quant: GARCH (volatilidad prevista) + Kalman (tendencia suavizada)
        self.quant = QuantEngine(config)

        # Motor de señales con todos los módulos
        self.signals = SignalEngine(
            self.mt5, self.news, config,
            correlation_engine=self.corr,
            macro_feed=self.macro,
            learning_engine=self.ml,
            volume_profile=self.vp,
            delta_engine=self.delta,
            regime_engine=self.regime,
            intermarket_feed=self.intermarket,
            neural_engine=self.neural,
            quant_engine=self.quant,
        )

        tg = config.get("telegram", {})
        self.telegram = TelegramBot(
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
        )

        # Deduplicador PERSISTENTE de señales (fix spam Telegram 2026-07):
        # última línea de defensa antes de enviar/guardar. Sobrevive reinicios
        # (tabla sent_signals en trades.db) y cubre los 4 streams por igual.
        self.dedup = SignalDedup(self.db_path, config)

        # Alertas proactivas de noticias (T-60/T-15 + post-release)
        self.news_alerts = NewsAlertManager(self.news, self.telegram, config)

        # Motor de ejecución de órdenes en MT5
        self.executor = TradeExecutor(self.mt5, config)
        self._autotrading_warned = False  # evitar spam de aviso

        # Gestión activa de posiciones: parcial en TP1 + trailing del runner
        self.manager = TradeManager(
            self.mt5, self.executor, self.db_path, config,
            telegram=self.telegram if self.telegram.enabled else None,
        )

        primary   = config["symbols"]["primary"]
        secondary = config["symbols"].get("secondary", [])
        self.symbols = [primary] + secondary
        # alt: forex/indices SOLO para los streams alert-only (daytrade M15 + scalp M5).
        # NUNCA entran en self.symbols -> nunca pasan por analyze() (stream H1 que
        # auto-ejecuta en demo con el cerebro del oro). Se rellena tras validar backtest.
        self.alt_symbols = config["symbols"].get("alt", []) or []
        self._cycle_count = 0
        self._autotrading_warned = False
        self._known_positions = set()  # tickets de posiciones abiertas
        self._last_digest_date = None  # guard "1 digest al día" (hora UTC)

    def start(self):
        """Inicializa conexiones y verifica el setup."""
        logger.info("=" * 55)
        logger.info("  SISTEMA DE TRADING XAUUSD — Iniciando")
        logger.info("=" * 55)

        if not self.mt5.connect():
            logger.error("No se pudo conectar a MetaTrader 5.")
            logger.error("Asegúrate de que MT5 esté abierto con tu cuenta demo activa.")
            sys.exit(1)

        acct = self.mt5.test_connection()
        logger.info(
            f"MT5 conectado | Cuenta: {acct.get('login')} | "
            f"Balance: {acct.get('balance')} {acct.get('currency')}"
        )

        init_database(self.db_path)
        # Conectar executor al tracker para auto-BE (disponible tras init)
        self.tracker.executor = self.executor
        update_obsidian_state(self.config, acct, self.db_path)

        if self.telegram.enabled:
            balance  = acct.get("balance", 0)
            currency = acct.get("currency", "EUR")
            self.telegram.send_active_message(balance=balance, currency=currency)
            logger.info("Telegram [OK] - Mensaje de SISTEMA ACTIVO enviado")
        else:
            logger.warning("Telegram desactivado - configura bot_token y chat_id en config.yaml")

        logger.info(f"Monitoreando: {', '.join(self.symbols)}")

    def _process_user_command(self, cmd: dict):
        """Procesa comandos de Telegram: /cancelar, /modificar, /cerrar, /status, /revisar."""
        ctype  = cmd.get("type")
        ticket = cmd.get("ticket")

        if ctype == "cancelar" and ticket:
            ok  = self.executor.cancel_pending_order(ticket)
            msg = f"✅ Orden #{ticket} cancelada" if ok else f"❌ Error cancelando #{ticket}"
            self.telegram.send_message(msg)

        elif ctype == "cerrar" and ticket:
            ok  = self.executor.close_position(ticket)
            msg = f"✅ Posición #{ticket} cerrada" if ok else f"❌ Error cerrando #{ticket}"
            self.telegram.send_message(msg)

        elif ctype == "modificar" and ticket:
            new_sl = cmd.get("sl")
            new_tp = cmd.get("tp")
            ok     = self.executor.modify_order(ticket, new_sl=new_sl, new_tp=new_tp)
            msg = f"✅ #{ticket} modificado" if ok else f"❌ Error modificando #{ticket}"
            self.telegram.send_message(msg)

        elif ctype == "status":
            self._send_status()

        elif ctype == "estado":
            self._send_estado()

        elif ctype == "revisar":
            self._send_revisar()

        elif ctype == "radiografia":
            self._send_radiografia()

        elif ctype == "salud":
            self._send_salud()

        elif ctype == "funded":
            if self.config.get("funded", {}).get("enabled", False):
                self.telegram.send_funded_status(self.funded.get_state())
            else:
                self.telegram.send_message(
                    "🏦 La cuenta de fondeo está <b>desactivada</b>. "
                    "El bot solo envía señales y opera la demo para aprender."
                )

        elif ctype == "micuenta":
            self.telegram.send_personal_status(self.personal.get_state())

        elif ctype == "saldo":
            amount = cmd.get("amount", 0)
            result = self.personal.set_equity(amount)
            if result.get("ok"):
                cur = self.personal.currency
                self.telegram.send_message(
                    f"✅ Saldo MI CUENTA sincronizado: <b>{amount:,.2f} {cur}</b>"
                )
                self.telegram.send_personal_status(result["state"])
                self.personal.write_state_json()
            else:
                self.telegram.send_message(
                    f"❌ /saldo: {result.get('error', 'error desconocido')}\n"
                    f"Uso: <code>/saldo 250.00</code>"
                )

        elif ctype == "equity":
            if not self.config.get("funded", {}).get("enabled", False):
                self.telegram.send_message(
                    "🏦 La cuenta de fondeo está <b>desactivada</b> — /equity no aplica."
                )
                return
            amount = cmd.get("amount", 0)
            result = self.funded.set_equity(amount)
            if result.get("ok"):
                self.telegram.send_message(
                    f"✅ Equity FundedNext sincronizado: <b>${amount:,.2f}</b>"
                )
                self.telegram.send_funded_status(result["state"])
                self.funded.write_state_json()
            else:
                self.telegram.send_message(
                    f"❌ /equity: {result.get('error', 'error desconocido')}\n"
                    f"Uso: <code>/equity 1985.50</code>"
                )

        elif ctype == "lote":
            self._send_lote(cmd.get("room", 0), cmd.get("entry", 0),
                            cmd.get("sl", 0), cmd.get("symbol", "XAUUSD"))

    def _risk_per_lot(self, symbol: str, sl_dist: float) -> float:
        """Moneda de cuenta que arriesga 1.0 lote para una distancia de SL en precio
        (gold/JPY/forex/índices vía symbols.contracts). Mismo criterio que el lotaje."""
        sym = symbol.upper()
        contracts = (self.config.get("symbols", {}) or {}).get("contracts", {}) or {}
        if any(x in sym for x in ("XAU", "GOLD")):
            return sl_dist * 100.0
        if sym in contracts:
            c = contracts[sym]; pip = float(c.get("pip", 1.0)) or 1.0
            return (sl_dist / pip) * float(c.get("value_per_lot", 1.0))
        if "JPY" in sym:
            return (sl_dist / 0.01) * 9.0
        return (sl_dist / 0.0001) * 10.0

    def _send_lote(self, room: float, entry: float, sl: float, symbol: str,
                   riskpct: float = 10.0):
        """Responde /lote: lote máx para una cuenta fondeada según el COLCHÓN (room)
        de drawdown restante — no el balance. 1% del balance puede ser 50% del room."""
        sl_dist = abs(entry - sl)
        if room <= 0 or sl_dist <= 0:
            self.telegram.send_message(
                "❌ /lote: datos inválidos.\n"
                "Uso: <code>/lote &lt;room$&gt; &lt;entry&gt; &lt;sl&gt; [símbolo]</code>\n"
                "Ej: <code>/lote 120 4195 4210</code> (oro) · "
                "<code>/lote 120 39000 38950 US30</code>"
            )
            return
        rpl       = self._risk_per_lot(symbol, sl_dist)
        risk_001  = rpl * 0.01
        budget    = room * riskpct / 100.0
        lines = [
            f"📐 <b>LOTE FONDEADA — {symbol}</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"🛡️ Colchón (room): <b>${room:,.2f}</b>",
            f"🎯 Presupuesto: <b>${budget:,.2f}</b> ({riskpct:.0f}% del room)",
            f"📏 SL: {sl_dist:.5f} → 0.01 lote arriesga <b>${risk_001:,.2f}</b> "
            f"({risk_001/room*100:.0f}% del room)",
            f"━━━━━━━━━━━━━━━━━━━━━━",
        ]
        if risk_001 > budget:
            max_dist = budget / (rpl / sl_dist) / 0.01 if rpl > 0 else 0
            lines += [
                f"❌ <b>NO VIABLE</b>: ni el lote mínimo cabe.",
                f"El SL no puede arriesgar más de <b>${budget:,.2f}</b> "
                f"(máx ~{max_dist:.5f} de distancia).",
                f"<i>Con tan poco colchón, casi ningún trade normal es seguro.</i>",
            ]
        else:
            max_lot = max(0.0, int(budget / rpl / 0.01) * 0.01)
            n = int(room / (max_lot * rpl)) if max_lot * rpl > 0 else 0
            lines += [
                f"✅ <b>LOTE MÁX: {max_lot:.2f}</b> → riesgo ${max_lot*rpl:,.2f} "
                f"({max_lot*rpl/room*100:.0f}% del room)",
                f"Aguantas ~{n} pérdidas seguidas a ese lote.",
            ]
        lines.append("<i>En fondeo: dimensiona contra el ROOM, no el balance (5-10%/trade).</i>")
        self.telegram.send_message("\n".join(lines))

    def _send_status(self):
        """Responde /status con estadísticas en tiempo real."""
        try:
            stats  = _get_trade_stats(self.db_path)
            risk   = self.risk.get_risk_summary(self.db_path)
            mult   = risk.get("risk_multiplier", 1.0)
            regime = {}
            try:
                regime = self.regime.analyze(self.config["symbols"]["primary"])
            except Exception:
                pass

            risk_str  = f"{risk['effective_risk_pct']:.2f}%"
            risk_note = f" ⚠️ reducido ({mult*100:.0f}%)" if mult < 1.0 else ""
            dd_str    = f"{risk['daily_loss']:.0f}€/{risk['daily_limit']:.0f}€"
            wdd_str   = f"{risk['weekly_loss']:.0f}€/{risk['weekly_limit']:.0f}€"
            training_mode = self.config.get("risk", {}).get("training_mode", False)
            if not risk.get("within_limits", True) and training_mode:
                wdd_str += "\n⚠️ <b>ENTRENAMIENTO: límites DD superados — demo sigue ejecutando</b>"
            regime_str = regime.get("regime", "?")
            adx_str    = f"{regime.get('adx', 0):.1f}"
            hurst_str  = f"{regime.get('hurst', 0):.2f}"

            msg = (
                f"📊 <b>Estado del Sistema</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ WIN: {stats['wins']}  ❌ LOSS: {stats['losses']}  "
                f"⏰ EXP: {stats.get('expired',0)}  ⏳ PEND: {stats['pending']}\n"
                f"📈 Win Rate: <b>{stats['wr_str']}</b> | PF: {stats['profit_factor']}\n"
                f"💰 P&L total: {stats['pnl_total']:+.0f}€\n\n"
                f"🎯 Riesgo activo: {risk_str}{risk_note}\n"
                f"📉 DD hoy: {dd_str}  |  Semana: {wdd_str}\n\n"
                f"📊 Régimen: <b>{regime_str}</b> | ADX {adx_str} | Hurst {hurst_str}\n"
                f"🔄 Ciclo #{self._cycle_count} | "
                f"{datetime.now(timezone.utc).strftime('%H:%M')} UTC"
            )
            self.telegram.send_message(msg)
        except Exception as e:
            logger.warning(f"/status error: {e}")

    def _send_salud(self):
        """Responde /salud — estado de las fuentes de datos por FRESCURA de
        caché (no dispara fetches lentos que bloqueen el ciclo). Solo MT5 se
        prueba en vivo (es local y rápido). Marca ✅ OK / ⚠️ STALE / ❌ FAIL."""
        try:
            now = datetime.now(timezone.utc)

            def _age_min(ts):
                if ts is None:
                    return None
                try:
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts)
                    return (now - ts).total_seconds() / 60.0
                except Exception:
                    return None

            def _line(name, age, limit_min):
                if age is None:
                    return f"❌ <b>{name}</b>: sin datos"
                icon = "✅" if age <= limit_min else "⚠️"
                tag  = "OK" if age <= limit_min else "STALE"
                return f"{icon} <b>{name}</b>: {tag} (hace {age:.0f} min)"

            lines = ["🩺 <b>SALUD DE FUENTES DE DATOS</b>",
                     "━━━━━━━━━━━━━━━━━━━━━━"]

            # MT5 — prueba en vivo (local, rápida)
            try:
                mt5s = self.mt5.test_connection()
                if mt5s.get("ok"):
                    lines.append(f"✅ <b>MT5</b>: OK (cuenta {mt5s.get('login')}, "
                                 f"{mt5s.get('balance', 0):,.0f} {mt5s.get('currency','')})")
                else:
                    lines.append(f"❌ <b>MT5</b>: {mt5s.get('error','sin conexión')}")
            except Exception as e:
                lines.append(f"❌ <b>MT5</b>: {e}")

            # Macro yfinance (VIX/yields/SPX) — caché 30 min
            lines.append(_line("Macro (yfinance)",
                               _age_min(getattr(self.macro, "_cache_time", None)), 35))

            # Volume Profile COMEX (GC=F) — caché 60 min
            lines.append(_line("VP COMEX (GC=F)",
                               _age_min(getattr(self.vp, "_cache_time", None)), 65))

            # Intermarket (reales/COT/oro-plata) — por mtime del cache en disco
            try:
                icf = os.path.join(os.path.dirname(__file__), "logs", "intermarket_cache.json")
                if os.path.exists(icf):
                    age = (now.timestamp() - os.path.getmtime(icf)) / 60.0
                    lines.append(_line("Intermarket (FRED/COT)", age, 720))
                else:
                    lines.append("⚠️ <b>Intermarket (FRED/COT)</b>: sin caché aún")
            except Exception:
                lines.append("❌ <b>Intermarket (FRED/COT)</b>: error leyendo caché")

            # Dedup persistente — nº de fingerprints activos (últimas 24h)
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                n = conn.execute("SELECT COUNT(*) FROM sent_signals").fetchone()[0]
                conn.close()
                lines.append(f"✅ <b>Anti-duplicado</b>: activo ({n} señales registradas 24h)")
            except Exception:
                lines.append("⚠️ <b>Anti-duplicado</b>: tabla no inicializada")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<i>STALE = la fuente cayó y el bot usa su última caché. "
                         "Se auto-recupera al volver la fuente.</i>")
            self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.warning(f"/salud error: {e}")
            self.telegram.send_message(f"❌ /salud error: {e}")

    def _send_radiografia(self):
        """
        Responde /radiografia — qué dice CADA módulo del cerebro sobre el
        símbolo primario ahora mismo (con datos reales), más el veredicto de
        analyze(). Reutiliza los módulos ya cableados en self.
        """
        try:
            from radiografia import gather, render_telegram
            symbol = self.config["symbols"]["primary"]
            R = gather(
                cfg=self.config, symbol=symbol,
                mt5=self.mt5, news=self.news, corr=self.corr, macro=self.macro,
                ml=self.ml, vp=self.vp, delta=self.delta, regime=self.regime,
                inter=self.intermarket, neural=self.neural, quant=self.quant,
                engine=self.signals,
            )
            self.telegram.send_message(render_telegram(R))
        except Exception as e:
            logger.warning(f"/radiografia error: {e}")
            self.telegram.send_message(f"❌ /radiografia: {e}")

    def _send_estado(self):
        """
        Responde /estado — diagnóstico de señales: por qué NO hay señal
        ahora mismo, última señal enviada y estado de la cuenta 2K.
        Resuelve el problema del 12-jun: "no me llegó ninguna notificación
        y no sé por qué".
        """
        try:
            lines = [
                "🩺 <b>DIAGNÓSTICO DE SEÑALES</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
            ]
            now = datetime.now(timezone.utc)

            # Última señal enviada (de la DB)
            try:
                conn = sqlite3.connect(self.db_path)
                cur  = conn.cursor()
                cur.execute("""
                    SELECT timestamp, symbol, direction, entry, outcome
                    FROM signals ORDER BY id DESC LIMIT 1
                """)
                row = cur.fetchone()
                conn.close()
            except Exception:
                row = None
            if row:
                try:
                    last_t  = datetime.fromisoformat(row[0])
                    if last_t.tzinfo is None:
                        last_t = last_t.replace(tzinfo=timezone.utc)
                    age_min = (now - last_t).total_seconds() / 60
                    age_str = f"hace {age_min/60:.1f}h" if age_min >= 90 else f"hace {age_min:.0f} min"
                except Exception:
                    age_str = row[0][:16]
                lines.append(
                    f"📡 Última señal: {row[1]} {row[2]} @ {row[3]:.2f} "
                    f"({age_str}) → {row[4]}"
                )
            else:
                lines.append("📡 Última señal: ninguna registrada aún")

            # Motivo del último descarte por símbolo (lo escribe SignalEngine)
            lines.append("")
            lines.append("🚫 <b>Por qué no hay señal ahora:</b>")
            discards = getattr(self.signals, "last_discard", {}) or {}
            if discards:
                for sym, d in discards.items():
                    try:
                        d_t   = datetime.fromisoformat(d["time"])
                        d_age = (now - d_t).total_seconds() / 60
                        t_str = f" (hace {d_age:.0f} min)"
                    except Exception:
                        t_str = ""
                    lines.append(f"  • {sym}: {d['reason']}{t_str}")
            else:
                lines.append("  • Sin análisis descartados todavía en esta sesión")

            # Próxima noticia de alto impacto
            try:
                stat = self.news.is_news_blackout(
                    minutes_buffer=self.config.get("sessions", {}).get("avoid_news_minutes", 30))
                nxt = stat.get("next_event")
                if stat.get("blackout"):
                    lines.append(f"\n📰 ⛔ BLACKOUT: {stat['reason']}")
                elif nxt:
                    mins = (nxt["time"] - now).total_seconds() / 60
                    lines.append(
                        f"\n📰 Próximo dato: {nxt['event']} ({nxt['country']}) "
                        f"en {mins/60:.1f}h" if mins >= 90 else
                        f"\n📰 Próximo dato: {nxt['event']} ({nxt['country']}) "
                        f"en {mins:.0f} min"
                    )
            except Exception:
                pass

            # Estado 2K compacto
            try:
                st = self.funded.get_state()
                if st.get("enabled"):
                    breach = " 🚨 BREACH" if st.get("breached") else ""
                    lines.append(
                        f"\n🏦 2K: ${st['equity']:,.2f} | floor ${st['floor']:,.2f} "
                        f"| room ${st['room']:,.2f}{breach}"
                    )
            except Exception:
                pass

            # Estado MI CUENTA compacto
            try:
                ps = self.personal.get_state()
                if ps.get("enabled"):
                    low = " ⚠️ BAJO MÍNIMO" if ps.get("too_low") else ""
                    lines.append(
                        f"💼 {ps['title']}: {ps['equity']:,.2f} {ps['currency']} "
                        f"(P&L {ps['pnl']:+,.2f}){low}"
                    )
            except Exception:
                pass

            lines.append(
                f"\n🔄 Ciclo #{self._cycle_count} | {now.strftime('%H:%M')} UTC | "
                f"el bot está vivo y analizando cada 60s"
            )
            self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.warning(f"/estado error: {e}")

    def _send_revisar(self):
        """Responde /revisar con estado de órdenes pendientes + validez del setup."""
        try:
            pending = self.executor.get_pending_orders()
            if not pending:
                self.telegram.send_message("📋 No hay órdenes LIMIT pendientes en MT5.")
                return

            lines = ["📋 <b>Órdenes pendientes</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
            for order in pending:
                symbol    = order.symbol
                direction = "BUY" if order.type == 0 else "SELL"
                entry_p   = order.price_open

                # Verificar si el bias H4 sigue alineado
                try:
                    df_h4_now = self.mt5.get_rates(symbol, "H4", 100)
                    bias_now  = get_ema_bias(add_indicators(df_h4_now))
                    expected  = "BULLISH" if direction == "BUY" else "BEARISH"
                    valid_str = "✅ Setup vigente" if bias_now == expected else f"⚠️ Bias cambió a {bias_now}"
                except Exception:
                    valid_str = "❓ No verificado"

                dir_emoji = "🟢" if direction == "BUY" else "🔴"
                lines.append(
                    f"\n{dir_emoji} #{order.ticket} {symbol} {direction}\n"
                    f"   Entry: <code>{entry_p:.5f}</code>\n"
                    f"   {valid_str}\n"
                    f"   Cancelar: <code>/cancelar {order.ticket}</code>"
                )

            self.telegram.send_message("\n".join(lines))
        except Exception as e:
            logger.warning(f"/revisar error: {e}")

    def _review_pending_orders(self):
        """
        Revisa todas las órdenes LIMIT pendientes cada 5 ciclos.
        Cancela automáticamente si:
          1. El bias H4 cambió en dirección opuesta
          2. El precio se alejó > 3 ATR del entry
        """
        try:
            pending = self.executor.get_pending_orders()
            for order in pending:
                symbol    = order.symbol
                direction = "BUY" if order.type == 0 else "SELL"
                entry_p   = float(order.price_open)

                # 1. Verificar bias H4
                try:
                    df_h4 = self.mt5.get_rates(symbol, "H4", 100)
                    if df_h4.empty:
                        continue
                    bias_now = get_ema_bias(add_indicators(df_h4))
                    expected = "BULLISH" if direction == "BUY" else "BEARISH"
                    if bias_now not in (expected, "NEUTRAL"):
                        ok = self.executor.cancel_pending_order(order.ticket)
                        if ok:
                            self.telegram.send_message(
                                f"⚠️ <b>ORDEN CANCELADA AUTO</b>\n"
                                f"Ticket #{order.ticket} {symbol} {direction}\n"
                                f"Motivo: Bias H4 cambió a <b>{bias_now}</b>\n"
                                f"El setup ya no está alineado con la estructura."
                            )
                            logger.info(
                                f"[AutoCancel] #{order.ticket} {symbol} — "
                                f"bias H4 cambió a {bias_now}"
                            )
                            continue
                except Exception as e:
                    logger.debug(f"Review bias H4 error: {e}")

                # 2. Verificar distancia al entry (> 3 ATR)
                try:
                    df_h1 = self.mt5.get_rates(symbol, "H1", 50)
                    if df_h1.empty:
                        continue
                    df_h1_ind = add_indicators(df_h1)
                    atr       = float(df_h1_ind["atr"].iloc[-1])
                    tick      = self.mt5.get_current_price(symbol)
                    price_now = float(tick["bid"]) if tick else 0
                    dist      = abs(price_now - entry_p)
                    if atr > 0 and dist > atr * 3:
                        ok = self.executor.cancel_pending_order(order.ticket)
                        if ok:
                            self.telegram.send_message(
                                f"⚠️ <b>ORDEN CANCELADA — Setup caducado</b>\n"
                                f"Ticket #{order.ticket} {symbol} {direction}\n"
                                f"Precio se alejó {dist/atr:.1f}x ATR del entry\n"
                                f"El punto de entrada ya no es institucional."
                            )
                            logger.info(
                                f"[AutoCancel] #{order.ticket} {symbol} — "
                                f"precio alejado {dist/atr:.1f}x ATR"
                            )
                except Exception as e:
                    logger.debug(f"Review distancia error: {e}")

        except Exception as e:
            logger.debug(f"_review_pending_orders: {e}")

    def run_cycle(self):
        """Un ciclo completo de análisis para todos los símbolos."""
        logger.info(f"---- Ciclo {datetime.now().strftime('%H:%M:%S')} ----")

        # ── Comandos del usuario desde Telegram ──────────────
        try:
            commands = self.telegram.get_pending_commands()
            for cmd in commands:
                self._process_user_command(cmd)
        except Exception as e:
            logger.debug(f"Telegram commands: {e}")

        market_state = {
            "symbols":       {},
            "news":          {},
            "news_calendar": [],
            "risk":          {},
        }

        # ── Alertas proactivas de noticias (T-60/T-15 + post-release) ──
        try:
            n_alerts = self.news_alerts.check()
            if n_alerts:
                logger.info(f"[NEWS] {n_alerts} alerta(s) de noticias enviadas")
        except Exception as e:
            logger.debug(f"News alerts: {e}")

        # ── Noticias ──────────────────────────────────────────
        avoid_min = self.config.get("sessions", {}).get("avoid_news_minutes", 30)
        try:
            news_stat    = self.news.is_news_blackout(minutes_buffer=avoid_min)
            news_calendar = self.news.get_daily_summary()
            next_ev_name  = ""
            if news_stat.get("next_event"):
                next_ev_name = news_stat["next_event"].get("event", "")

            market_state["news"] = {
                "blackout":        news_stat["blackout"],
                "reason":          news_stat["reason"],
                "next_event_name": next_ev_name,
            }
            market_state["news_calendar"] = [
                {**ev, "time": ev["time"].isoformat() if hasattr(ev["time"], "isoformat") else str(ev["time"])}
                for ev in news_calendar
            ]
        except Exception as e:
            logger.warning(f"Error en news feed: {e}")
            news_stat = {"blackout": False, "reason": ""}

        # ── Riesgo ────────────────────────────────────────────
        risk_summary = self.risk.get_risk_summary(self.db_path)
        market_state["risk"] = risk_summary

        # Modo observación: analiza y manda señales pero NO ejecuta órdenes
        observation_mode  = False
        observation_reason = ""

        session_ok = self.risk.is_session_allowed()
        if not session_ok["ok"]:
            # Fuera de sesión → señales 24h pero sin ejecución MT5
            observation_mode   = True
            observation_reason = f"🌙 {session_ok['reason']}"
            logger.info(f"Fuera de horario — modo observación (señales sin ejecución)")

        training_mode = self.config.get("risk", {}).get("training_mode", False)
        dd_warning    = ""
        if not risk_summary.get("within_limits", True):
            dd_type = "semanal" if risk_summary.get("weekly_loss", 0) >= risk_summary.get("weekly_limit", 1e9) else "diario"
            if training_mode:
                # Honestidad: sigue ejecutando pero el estado se muestra en
                # Telegram, /status y dashboard (antes era invisible)
                dd_warning = f"⚠️ Drawdown {dd_type} superado — demo sigue ejecutando (modo entrenamiento)"
                market_state["risk"]["training_override_active"] = True
                logger.warning(f"[ENTRENAMIENTO] Límite drawdown {dd_type} alcanzado — continuando por training_mode=True")
            else:
                observation_mode   = True
                observation_reason = f"⛔ Drawdown {dd_type} alcanzado — solo señales, sin ejecución"
                logger.warning(f"Límite drawdown — modo observación")

        # ── Analizar cada símbolo ─────────────────────────────
        # FIX CRÍTICO: contar posiciones abiertas + órdenes pendientes REALES
        # en MT5 — antes empezaba en 0 cada ciclo y el límite max_simultaneous
        # nunca se aplicaba (señales apiladas en la misma dirección)
        try:
            active_count = (
                len(self.executor.get_open_positions()) +
                len(self.executor.get_pending_orders())
            )
        except Exception:
            active_count = 0

        # Equity viva para position sizing (compuesto real, no capital fijo)
        try:
            live_equity = float(self.mt5.get_account_info().get("equity", 0)) or None
        except Exception:
            live_equity = None

        # EURUSD para convertir el riesgo USD del bloque MI CUENTA a EUR
        eurusd_rate = None
        try:
            eur_tick = self.mt5.get_current_price("EURUSD")
            if eur_tick:
                eurusd_rate = float(eur_tick["bid"])
        except Exception:
            pass

        for symbol in self.symbols:
            try:
                # Obtener datos base para el dashboard
                tick  = self.mt5.get_current_price(symbol)
                df_h1 = self.mt5.get_rates(symbol, "H1", 300)
                df_h4 = self.mt5.get_rates(symbol, "H4", 300)

                if not tick or df_h1.empty or df_h4.empty:
                    continue

                df_h1_ind = add_indicators(df_h1)
                df_h4_ind = add_indicators(df_h4)

                bias_h4  = get_ema_bias(df_h4_ind)
                struct_h1 = detect_market_structure(df_h1_ind)
                obs       = find_order_blocks(df_h1_ind)
                rsi       = df_h1_ind["rsi"].iloc[-1]  if not df_h1_ind.empty and "rsi"  in df_h1_ind.columns else None
                atr       = df_h1_ind["atr"].iloc[-1]  if not df_h1_ind.empty and "atr"  in df_h1_ind.columns else None

                # Estado para el dashboard
                market_state["symbols"][symbol] = {
                    "price":        round(float(tick["bid"]), 5),
                    "bias_h4":      bias_h4,
                    "structure_h1": struct_h1.get("trend", "NEUTRAL"),
                    "atr":          round(float(atr), 5)  if atr is not None else None,
                    "rsi":          round(float(rsi), 1)  if rsi is not None else None,
                    "order_blocks": [
                        {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                         for k, v in ob.items()}
                        for ob in obs[:8]
                    ],
                    "last_signal_time": None,
                }

                # ── Generar señal ─────────────────────────────
                # Las señales NUNCA se suprimen: max_simultaneous solo
                # bloquea la ejecución automática en demo, no el envío
                can_exec = self.risk.can_open_trade(active_count)

                signal = self.signals.analyze(symbol)
                if signal is None and self.config.get("reversal", {}).get("enabled", False) \
                        and hasattr(self.signals, "analyze_reversal"):
                    signal = self.signals.analyze_reversal(symbol)

                # ── Señal swing — corre SIEMPRE, independiente del intraday ──
                # Se evalúa antes del `continue` para no perderse swings cuando
                # el intraday se descarta (distintos timeframes, distinto setup).
                if self.config.get("swing", {}).get("enabled", False):
                    try:
                        swing_sig = self.signals.analyze_swing(symbol)
                        if swing_sig is not None:
                            s_lot = self.risk.calculate_lot_size(
                                swing_sig["entry"], swing_sig["sl"], symbol,
                                risk_multiplier=1.0, capital_override=live_equity,
                            )
                            swing_sig["lot_size"] = s_lot
                            try:
                                sw_funded = self.funded.evaluate_signal(swing_sig)
                                if sw_funded:
                                    swing_sig["funded"] = sw_funded
                            except Exception as fe:
                                logger.debug(f"Funded swing: {fe}")
                            try:
                                sw_personal = self.personal.evaluate_signal(
                                    swing_sig, eurusd=eurusd_rate)
                                if sw_personal:
                                    swing_sig["personal"] = sw_personal
                            except Exception as pe:
                                logger.debug(f"Personal swing: {pe}")
                            if observation_mode and observation_reason:
                                swing_sig["observation_note"] = observation_reason
                            if dd_warning:
                                swing_sig["dd_warning"] = dd_warning
                            logger.info(
                                f"[SWING] [{symbol}] {swing_sig['direction']} | "
                                f"Entry:{swing_sig['entry']} SL:{swing_sig['sl']} "
                                f"TP1:{swing_sig['tp1']} | R:R:{swing_sig['rr']} | "
                                f"Conf:{swing_sig['confidence']:.0%} | Lotes:{s_lot}"
                            )
                            if self.dedup.should_send(swing_sig):
                                self.dedup.mark_sent(swing_sig)
                                self.telegram.send_signal(swing_sig)
                                save_signal(swing_sig, s_lot, True, self.db_path)
                            else:
                                logger.info(
                                    f"[SWING] [{symbol}] {swing_sig['direction']} "
                                    "duplicada (dedup persistente) — no se reenvía"
                                )
                    except Exception as sw_e:
                        logger.debug(f"Swing analysis {symbol}: {sw_e}")

                # ── Señales MANUALES (alert-only) en paralelo: DAYTRADE M15 +
                # SCALP M5. Nunca colocan órdenes en MT5. Dispatch unificado en
                # _emit_manual_signal (bloque MI CUENTA / 2K + Telegram + DB).
                if self.config.get("daytrade", {}).get("enabled", False):
                    try:
                        self._emit_manual_signal(
                            self.signals.analyze_daytrade(symbol),
                            eurusd_rate, dd_warning)
                    except Exception as dt_e:
                        logger.debug(f"Daytrade analysis {symbol}: {dt_e}")
                if self.config.get("scalp", {}).get("enabled", False):
                    try:
                        self._emit_manual_signal(
                            self.signals.analyze_scalp(symbol),
                            eurusd_rate, dd_warning)
                    except Exception as sc_e:
                        logger.debug(f"Scalp analysis {symbol}: {sc_e}")

                if signal is None:
                    continue

                if not can_exec["ok"]:
                    signal["exec_block_note"] = (
                        f"📵 {can_exec['reason']} — señal informativa, sin ejecución demo"
                    )
                    logger.info(f"[{symbol}] {can_exec['reason']} — señal enviada solo informativa")

                market_state["symbols"][symbol]["last_signal_time"] = signal["timestamp"]

                # Dedup persistente: una señal H1 idéntica (mismo stream/vela/
                # dirección) no se reenvía ni tras reinicio. Se salta ANTES de
                # ejecutar en MT5 para no colocar una orden duplicada tampoco.
                if not self.dedup.should_send(signal):
                    logger.info(
                        f"[{symbol}] Señal H1 {signal['direction']} duplicada "
                        "(dedup persistente) — no se reenvía ni ejecuta"
                    )
                    continue
                self.dedup.mark_sent(signal)

                # Calcular lot size: racha × confianza de la señal × equity viva
                streak_mult = self.risk.get_risk_multiplier(self.db_path)
                conf_mult   = self.risk.get_confidence_multiplier(signal)
                risk_mult   = max(0.25, min(1.25, streak_mult * conf_mult))
                lot = self.risk.calculate_lot_size(
                    signal["entry"], signal["sl"], symbol,
                    risk_multiplier=risk_mult, capital_override=live_equity,
                )
                if risk_mult != 1.0:
                    logger.info(
                        f"[RISK] Multiplicador {risk_mult:.2f} "
                        f"(racha {streak_mult:.2f} × confianza {conf_mult:.2f}) → {lot:.2f} lotes"
                    )
                signal["lot_size"] = lot

                # Bloque FundedNext 2K: solo si la cuenta de fondeo está activa
                # (desactivada 2026-06-17 — el usuario la quitó)
                if self.config.get("funded", {}).get("enabled", False):
                    try:
                        funded_block = self.funded.evaluate_signal(signal)
                        if funded_block:
                            # Modelo reversal: experimental hasta validar en vivo —
                            # nunca apta para la cuenta fondeada
                            if signal.get("model") == "SWEEP_REVERSAL" and funded_block.get("apta"):
                                funded_block["apta"] = False
                                funded_block["reasons"] = ["modelo reversal experimental — no validado"]
                            signal["funded"] = funded_block
                    except Exception as e:
                        logger.debug(f"Funded evaluate: {e}")

                # Bloque MI CUENTA: lotes/riesgo/apta para tu cuenta personal
                try:
                    personal_block = self.personal.evaluate_signal(
                        signal, eurusd=eurusd_rate)
                    if personal_block:
                        if signal.get("model") == "SWEEP_REVERSAL" and personal_block.get("apta"):
                            personal_block["apta"] = False
                            personal_block["reasons"] = ["modelo reversal experimental — no validado"]
                        signal["personal"] = personal_block
                except Exception as e:
                    logger.debug(f"Personal evaluate: {e}")

                logger.info(
                    f"[SEÑAL] [{symbol}] {signal['direction']} | "
                    f"Entry:{signal['entry']} SL:{signal['sl']} TP1:{signal['tp1']} | "
                    f"R:R:{signal['rr']} | Conf:{signal['confidence']:.0%} | Lotes:{lot}"
                )

                # Telegram: la señal se envía SIEMPRE — los estados
                # (observación, blackout, drawdown, límite demo) son avisos
                sent = False
                if observation_mode and observation_reason:
                    signal["observation_note"] = observation_reason
                if dd_warning:
                    signal["dd_warning"] = dd_warning
                if signal.get("news_blackout"):
                    prev_note = signal.get("observation_note", "")
                    signal["observation_note"] = (
                        (prev_note + " | " if prev_note else "")
                        + "⚠️ Precaución: noticia de alto impacto próxima"
                    )
                sent = self.telegram.send_signal(signal)

                # ── Ejecución automática en MT5 ──────────────
                # Solo si: auto_execute=True Y sin blackout Y NO en modo
                # observación Y bajo el límite max_simultaneous Y la señal
                # no se marcó como solo-informativa (p.ej. reversal en HIGH_VOL)
                ticket    = None
                trade_cfg = self.config.get("trading", {})
                if trade_cfg.get("auto_execute", False) and not signal.get("news_blackout") \
                        and not observation_mode and can_exec["ok"] \
                        and not signal.get("no_auto_execute"):
                    # Verificar que AutoTrading está habilitado en MT5
                    term_info = mt5.terminal_info()
                    if term_info and not term_info.trade_allowed:
                        if not self._autotrading_warned:
                            msg = (
                                "⚠️ <b>AutoTrading desactivado en MT5</b>\n\n"
                                "Para que el sistema ejecute órdenes automáticamente:\n"
                                "1. Abre MetaTrader 5\n"
                                "2. Haz clic en el botón <b>AutoTrading</b> (barra superior)\n"
                                "3. Debe estar en VERDE ✅\n\n"
                                "Las señales seguirán llegando — solo falta activar AutoTrading."
                            )
                            self.telegram.send_message(msg)
                            logger.warning("[MT5] AutoTrading desactivado — actívalo en MT5 para ejecutar órdenes")
                            self._autotrading_warned = True
                    else:
                        self._autotrading_warned = False  # resetear si ya está activo
                        try:
                            # Con gestión activa: TP de la orden = TP2 (runner).
                            # El TradeManager cierra el parcial en TP1 y hace
                            # trailing del resto. Sin gestión: TP = TP1 clásico.
                            mgmt_on  = trade_cfg.get("management", {}).get("enabled", True)
                            order_tp = signal["tp2"] if (mgmt_on and signal.get("tp2")) else signal["tp1"]
                            ticket = self.executor.place_limit_order(
                                symbol    = symbol,
                                direction = signal["direction"],
                                volume    = float(lot),
                                entry     = signal["entry"],
                                sl        = signal["sl"],
                                tp        = order_tp,
                                comment   = f"SMC {signal['confidence']:.0%}",
                            )
                            if ticket:
                                signal["ticket"] = ticket
                                active_count += 1  # solo exposición real cuenta para el límite
                                self.telegram.send_order_placed(signal, ticket)
                                logger.info(f"[MT5] Orden LIMIT #{ticket} colocada para {symbol}")
                            else:
                                logger.warning(f"[MT5] No se pudo colocar orden para {symbol}")
                        except Exception as ex:
                            logger.error(f"Error ejecutando orden {symbol}: {ex}")

                # Guardar en DB (con ticket si se colocó)
                save_signal(signal, lot, sent, self.db_path)

            except Exception as e:
                logger.error(f"Error analizando {symbol}: {e}", exc_info=True)

        # ── Símbolos ALT (forex/índices): SOLO streams alert-only ──────────
        # NO pasan por analyze() (auto-exec H1 gold) ni por la gestión de demo.
        # Solo DAYTRADE M15 + SCALP M5, que el usuario ejecuta a mano.
        for symbol in self.alt_symbols:
            if self.config.get("daytrade", {}).get("enabled", False):
                try:
                    self._emit_manual_signal(
                        self.signals.analyze_daytrade(symbol),
                        eurusd_rate, dd_warning)
                except Exception as dt_e:
                    logger.debug(f"[ALT] Daytrade {symbol}: {dt_e}")
            if self.config.get("scalp", {}).get("enabled", False):
                try:
                    self._emit_manual_signal(
                        self.signals.analyze_scalp(symbol),
                        eurusd_rate, dd_warning)
                except Exception as sc_e:
                    logger.debug(f"[ALT] Scalp {symbol}: {sc_e}")

        # ── Detectar órdenes LIMIT ejecutadas (PENDING → posición abierta) ──
        try:
            current_positions = {p.ticket for p in self.executor.get_open_positions()}
            new_fills = current_positions - self._known_positions
            for ticket in new_fills:
                positions = mt5.positions_get(ticket=ticket)
                if positions:
                    pos = positions[0]
                    direction = "BUY" if pos.type == 0 else "SELL"
                    self.telegram.send_order_executed(pos.symbol, direction, pos.price_open, ticket)
                    logger.info(f"[MT5] Orden ejecutada: {pos.symbol} {direction} @ {pos.price_open} ticket #{ticket}")
            self._known_positions = current_positions
        except Exception as e:
            logger.debug(f"Position tracking: {e}")

        # ── Gestión activa: parcial en TP1 + trailing del runner ──
        try:
            mgmt_actions = self.manager.manage_positions()
            if mgmt_actions > 0:
                logger.info(f"TradeManager: {mgmt_actions} accion(es) de gestión")
        except Exception as e:
            logger.debug(f"TradeManager: {e}")

        # ── Outcome tracking: detectar resultados + alertas breakeven ──
        try:
            updated = self.tracker.check_pending_signals()
            if updated > 0:
                logger.info(f"Outcomes detectados automaticamente: {updated}")
                # Auto-guardar en Obsidian cuando hay nuevos cierres
                update_obsidian_demo_log(self.config, self.db_path)
            alerts = self.tracker.check_breakeven_alerts(
                telegram_bot=self.telegram if self.telegram.enabled else None
            )
            if alerts > 0:
                logger.info(f"Breakeven alerts enviados: {alerts}")
        except Exception as e:
            logger.debug(f"Outcome tracker: {e}")

        # ── Digest diario (resumen + análisis LLM) a la hora UTC configurada ──
        self._maybe_send_daily_digest()

        # ── FundedNext 2K: aplicar resultados resueltos al equity simulado ──
        # Solo si la cuenta de fondeo está activa (desactivada 2026-06-17)
        if self.config.get("funded", {}).get("enabled", False):
            try:
                applied = self.funded.sync_from_db()
                if applied and self.telegram.enabled:
                    state = self.funded.get_state()
                    for res in applied:
                        self.telegram.send_funded_result(res, state)
                self.funded.write_state_json()
            except Exception as e:
                logger.debug(f"Funded sync: {e}")

        # ── MI CUENTA: aplicar resultados resueltos al equity personal ──
        try:
            p_applied = self.personal.sync_from_db()
            if p_applied and self.telegram.enabled:
                p_state = self.personal.get_state()
                for res in p_applied:
                    self.telegram.send_personal_result(res, p_state)
            self.personal.write_state_json()
        except Exception as e:
            logger.debug(f"Personal sync: {e}")

        # ── Revisar validez de órdenes pendientes (cada 5 ciclos ≈ 5 min) ──
        self._cycle_count += 1
        if self._cycle_count % 5 == 0:
            try:
                self._review_pending_orders()
            except Exception as e:
                logger.debug(f"Review orders: {e}")

        # ── Verificación DXY externa (sintético MT5 vs fuente externa) ──
        dxy_cfg = self.config.get("dxy", {}) or {}
        if dxy_cfg.get("external_check", False) and \
                self._cycle_count % int(dxy_cfg.get("check_every_cycles", 5)) == 0:
            try:
                self._check_dxy_divergence(dxy_cfg)
            except Exception as e:
                logger.debug(f"DXY check: {e}")

        # ── Limpiar órdenes expiradas (cada 30 ciclos ≈ 30 min) ─────
        if self._cycle_count % 30 == 0:
            try:
                expired = self.executor.cleanup_expired_orders()
                for exp in expired:
                    self.telegram.send_order_expired(
                        exp["symbol"], exp["direction"], exp["entry"], exp["ticket"]
                    )
            except Exception as e:
                logger.debug(f"Cleanup órdenes: {e}")

        # ── ML: reentrenar si hay suficientes datos nuevos ────────
        if self._cycle_count % 10 == 0:  # Revisar cada 10 ciclos (~10 minutos)
            try:
                if self.ml.should_retrain():
                    result = self.ml.train()
                    if result.get("trained"):
                        stats = result
                        logger.info(
                            f"Modelo ML reentrenado | {stats['n_trades']} trades | "
                            f"Win rate real: {stats['win_rate']:.1%} | "
                            f"CV accuracy: {stats['cv_accuracy']:.1%}"
                        )
                        if self.telegram.enabled:
                            msg = (
                                f"🤖 <b>Modelo ML actualizado</b>\n"
                                f"Entrenado con {stats['n_trades']} trades\n"
                                f"Win rate real: {stats['win_rate']:.1%}\n"
                                f"Accuracy CV: {stats['cv_accuracy']:.1%}"
                            )
                            self.telegram.send_message(msg)
            except Exception as e:
                logger.debug(f"ML training: {e}")

        # Añadir datos macro al estado para el dashboard
        try:
            macro_data = self.macro.get_macro_bias()
            market_state["macro"] = {
                "dxy_trend":   macro_data.get("dxy_trend", "NEUTRAL"),
                "yields_trend": macro_data.get("yields_trend", "NEUTRAL"),
                "risk_mood":   macro_data.get("risk_mood", "NEUTRAL"),
                "gold_bias":   macro_data.get("gold_bias", "NEUTRAL"),
                "score":       macro_data.get("score", 0),
                "details":     macro_data.get("details", {}),
            }
        except Exception:
            market_state["macro"] = {}

        # Actualizar dashboard y estado Obsidian (cada 10 ciclos ≈ 10 min)
        update_market_state(market_state)
        if self._cycle_count % 10 == 0:
            acct_refresh = self.mt5.test_connection()
            update_obsidian_state(self.config, acct_refresh, self.db_path)
        logger.info(f"Ciclo completado - {active_count} posicion(es)/orden(es) activas en demo")

    def _emit_manual_signal(self, sig, eurusd_rate, dd_warning):
        """
        Dispatch de una señal alert-only (DAYTRADE M15 o SCALP M5): añade el
        bloque 2K (si la cuenta fondeada está activa) + el bloque MI CUENTA,
        la envía por Telegram y la guarda en DB. NUNCA auto-ejecuta en MT5
        (estas señales son de ejecución manual). sig=None → no hace nada.
        """
        if sig is None:
            return
        mode = sig.get("mode", "MANUAL")
        # Dedup persistente: no reenviar la misma idea (mismo stream/vela) ni
        # tras un reinicio del bot. Cierra Telegram Y el guardado en DB.
        if not self.dedup.should_send(sig):
            logger.info(
                f"[{mode}] [{sig['symbol']}] {sig['direction']} duplicada "
                "(dedup persistente) — no se reenvía"
            )
            return
        self.dedup.mark_sent(sig)
        if self.config.get("funded", {}).get("enabled", False):
            try:
                fb = self.funded.evaluate_signal(sig)
                if fb:
                    sig["funded"] = fb
            except Exception as fe:
                logger.debug(f"Funded {mode}: {fe}")
        try:
            pb = self.personal.evaluate_signal(sig, eurusd=eurusd_rate)
            if pb:
                sig["personal"] = pb
        except Exception as pe:
            logger.debug(f"Personal {mode}: {pe}")
        if dd_warning:
            sig["dd_warning"] = dd_warning
        logger.info(
            f"[{mode}] [{sig['symbol']}] {sig['direction']} {sig.get('timeframe','')} | "
            f"Entry:{sig['entry']} SL:{sig['sl']} TP1:{sig['tp1']} | "
            f"Conf:{sig['confidence']:.0%}"
        )
        self.telegram.send_signal(sig)
        save_signal(sig, 0.0, True, self.db_path)

    def _check_dxy_divergence(self, dxy_cfg: dict):
        """
        Compara el DXY sintético (MT5 tiempo real, fórmula ICE 6 pares)
        con la fuente externa (yfinance ^DXY ≈ TradingView/FastBull).
        Si divergen > N puntos → avisar UNA vez por hora por Telegram.
        El valor operativo SIEMPRE es el sintético MT5 (tiempo real);
        el externo lleva ~15 min de retraso y es solo verificación.
        """
        synth = self.corr.get_dxy_realtime()
        ext   = self.macro.get_dxy_external()
        if not synth or not ext:
            return
        diff      = abs(float(synth) - float(ext))
        warn_pts  = float(dxy_cfg.get("divergence_warn_points", 0.20))
        logger.info(f"[DXY] sintético {synth:.3f} vs externo {ext:.3f} (Δ {diff:.3f})")
        if diff <= warn_pts:
            return
        # Anti-spam: máx 1 aviso por hora
        now = datetime.now(timezone.utc)
        last = getattr(self, "_last_dxy_warn", None)
        if last and (now - last).total_seconds() < 3600:
            return
        self._last_dxy_warn = now
        self.telegram.send_message(
            f"⚠️ <b>DXY: divergencia detectada</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 Sintético MT5 (tiempo real): <b>{synth:.3f}</b>\n"
            f"🌐 Externo (≈TradingView, ~15 min retraso): <b>{ext:.3f}</b>\n"
            f"Δ {diff:.3f} puntos (umbral {warn_pts:.2f})\n\n"
            f"Si la divergencia persiste, el feed de pares de MT5 puede estar "
            f"degradado — verifica el DXY en TradingView/FastBull antes de "
            f"ejecutar la próxima señal."
        )

    def _maybe_send_daily_digest(self):
        """Envía UN digest diario (resumen + análisis LLM) a la hora UTC
        configurada. Guard por fecha para no repetir. No bloquea el ciclo si
        falla (fail-safe dentro de build_digest)."""
        acfg = self.config.get("alerts", {})
        if not acfg.get("daily_digest_enabled", True):
            return
        now = datetime.now(timezone.utc)
        hour_target = int(acfg.get("daily_digest_hour_utc", 21))
        today = now.strftime("%Y-%m-%d")
        if now.hour < hour_target or self._last_digest_date == today:
            return
        try:
            from alerts.daily_digest import build_digest
            news_ctx = ""
            try:
                if getattr(self, "news_sentiment", None):
                    snap = self.news_sentiment.get_sentiment(self.symbols[0])
                    news_ctx = snap.get("label", "") if isinstance(snap, dict) else ""
            except Exception:
                pass
            msg = build_digest(self.config, self.db_path, news_ctx=news_ctx)
            self.telegram.send_message(msg)
            self._last_digest_date = today
            logger.info("Digest diario enviado a Telegram")
        except Exception as e:
            logger.warning(f"Digest diario error: {e}")
            self._last_digest_date = today  # no reintentar en bucle si algo falla

    def _send_weekly_report(self):
        """Genera y envía el reporte semanal de rendimiento a Telegram."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            # P&L de los últimos 7 días
            cur.execute("""
                SELECT outcome, COUNT(*), COALESCE(SUM(pnl_amount),0)
                FROM signals
                WHERE DATE(timestamp) >= DATE('now','-7 days')
                GROUP BY outcome
            """)
            by_outcome = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM signals WHERE outcome='PENDING'")
            pending = cur.fetchone()[0]

            wins_n,   wins_pnl   = by_outcome.get("WIN",  (0, 0))
            losses_n, losses_pnl = by_outcome.get("LOSS", (0, 0))
            weekly_pnl = wins_pnl + losses_pnl
            conn.close()

            pf = (wins_n * 2.0) / losses_n if losses_n > 0 else float("inf")
            acct = self.mt5.test_connection()
            balance = acct.get("balance", 0)

            stats = {
                "wins": wins_n, "losses": losses_n, "pending": pending,
                "profit_factor": round(pf, 2), "weekly_pnl": weekly_pnl,
                "balance": balance, "backtest_wr": 0.421,
            }
            if self.telegram.enabled:
                self.telegram.send_weekly_report(stats)
                logger.info("Reporte semanal enviado a Telegram")
        except Exception as e:
            logger.warning(f"Reporte semanal error: {e}")

    def run_loop(self):
        """Inicia el loop de análisis continuo."""
        refresh = int(self.config.get("dashboard", {}).get("refresh_seconds", 60))

        self.run_cycle()  # Primer ciclo inmediato

        schedule.every(refresh).seconds.do(self.run_cycle)
        schedule.every().sunday.at("18:00").do(self._send_weekly_report)

        logger.info(f"Loop activo — análisis cada {refresh}s")
        logger.info("Para el dashboard abre otra terminal y ejecuta:")
        logger.info("  streamlit run dashboard/app.py")
        logger.info("Ctrl+C para detener el sistema")

        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Apagando el sistema...")
            if self.telegram.enabled:
                self.telegram.send_inactive_message()
                logger.info("Telegram [OK] - Mensaje de SISTEMA INACTIVO enviado")
            self.mt5.disconnect()
            logger.info("Sistema detenido.")


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config("config.yaml")

    if "--backtest" in sys.argv:
        # ── Modo backtesting ──────────────────────────────────
        # Flags: --no-filters --no-reversal --baseline --from/--to YYYY-MM-DD
        from backtest.backtester import run_backtest, parse_cli_flags
        from datetime import timedelta

        conn = MT5Connector(cfg)
        if not conn.connect():
            print("ERROR: MT5 no disponible. ¿Está abierto?")
            sys.exit(1)

        bt_flags = parse_cli_flags(sys.argv)
        dt_to    = bt_flags["date_to"]   or datetime.now(timezone.utc)
        dt_from  = bt_flags["date_from"] or (dt_to - timedelta(days=365))

        # Símbolos a testear — XAUUSD siempre + secundarios si se pasa --all
        symbols_to_test = [cfg["symbols"]["primary"]]
        if "--all" in sys.argv:
            symbols_to_test += cfg["symbols"].get("secondary", [])

        # Poblar el almacen de entrenamiento solo en la corrida de produccion
        # (config real, XAUUSD) — no en baseline/no-filters ni en secundarios
        record_train = ("--baseline" not in sys.argv and "--no-filters" not in sys.argv)

        all_results = {}
        for sym in symbols_to_test:
            print(f"\nIniciando backtest sobre {sym} H1...")
            result = run_backtest(conn, sym, "H1", dt_from, dt_to, cfg,
                                  use_filters=bt_flags["use_filters"],
                                  use_reversal=bt_flags["use_reversal"],
                                  record_training=(record_train and sym == cfg["symbols"]["primary"]))
            if result and "metrics" in result:
                all_results[sym] = result["metrics"]

        conn.disconnect()

        if len(all_results) > 1:
            print(f"\n{'='*55}")
            print(f"  RESUMEN MULTI-SÍMBOLO")
            print(f"{'='*55}")
            print(f"  {'Símbolo':<10} {'Win Rate':>9} {'PF':>6} {'Max DD':>8} {'Retorno':>10}")
            print(f"  {'-'*45}")
            for sym, m in all_results.items():
                print(
                    f"  {sym:<10} {m['win_rate']:.1%}  {m['profit_factor']:>5.2f}"
                    f"  {m['max_drawdown']:.1%}  {m['total_return_pct']:>+8.1f}%"
                )
            print(f"{'='*55}")

    elif "--test" in sys.argv:
        # ── Modo test: un ciclo y sale ─────────────────────────
        system = TradingSystem(cfg)
        system.start()
        system.run_cycle()
        system.mt5.disconnect()
        print("\nModo --test completado. Revisa los logs y logs/market_state.json")

    else:
        # ── Modo normal: loop continuo ─────────────────────────
        system = TradingSystem(cfg)
        system.start()
        system.run_loop()
