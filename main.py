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
from analysis.signal_engine import SignalEngine
from analysis.indicators import add_indicators, get_ema_bias, get_rsi_state
from analysis.market_structure import detect_market_structure, find_order_blocks
from analysis.correlation_engine import CorrelationEngine
from risk.risk_manager import RiskManager
from alerts.telegram_bot import TelegramBot
from ml.outcome_tracker import OutcomeTracker
from ml.learning_engine import LearningEngine
from trade.executor import TradeExecutor
from analysis.volume_profile import VolumeProfileEngine
from analysis.delta_engine import DeltaEngine
from analysis.market_regime import MarketRegimeEngine


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
    # Migración: añadir columna mt5_ticket si no existe (DB antigua)
    try:
        cursor.execute("ALTER TABLE signals ADD COLUMN mt5_ticket INTEGER DEFAULT NULL")
        conn.commit()
        logger.info("Columna mt5_ticket añadida (migración DB)")
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

    conn.commit()
    conn.close()
    logger.info(f"Base de datos lista: {db_path}")


def save_signal(signal: dict, lot_size: float, sent_tg: bool, db_path: str):
    """Guarda una señal en la base de datos."""
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO signals (
            timestamp, symbol, direction, entry, sl, tp1, tp2, rr,
            confidence, confluences, bias_h4, structure_h1, ob_type,
            rsi_state, news_warning, news_blackout, atr, lot_size,
            sent_telegram, mt5_ticket
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
- Confluencias mín: {risk.get('min_confluences', 4.0)}/13.5 | R:R: {risk.get('min_rr', 2.0)} | Riesgo: {risk.get('risk_per_trade', 0.01)*100:.0f}%
- Trading: solo XAUUSD (forex desactivado tras backtest — WR 27-31%)
- Módulos: SMC + VP COMEX + Delta ticks + TPO + ML + Macro + DXY sintético

## Backtest validado (XAUUSD H1, 1 año con comisiones)
- Win Rate: **42.1%** | PF: **1.45** | Max DD: **24.9%** | Sharpe: **2.82** | Retorno: **+146%**

## Rendimiento en vivo
- WIN: {st['wins']} | LOSS: {st['losses']} | EXPIRED: {st.get('expired',0)} | PENDING: {st['pending']}
- Win Rate real: **{st['wr_str']}** | PF: {pf_str} | P&L: {pnl_str}
{recent_lines}

## Estado módulos avanzados
- Volume Profile COMEX (GC=F): cache 60 min — se auto-recupera si rate limited
- Delta/Footprint (ticks MT5): activo por barra H1
- Ejecución MT5: automática (LIMIT orders) — órdenes expiran en 8h
- Telegram bidireccional: /cancelar /modificar /cerrar

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

        # Módulos nuevos: correlaciones, macro, ML
        self.corr  = CorrelationEngine(self.mt5)
        self.macro = MacroFeed()
        self.ml    = LearningEngine(self.db_path)
        self.tracker = OutcomeTracker(self.mt5, self.db_path, executor=None)  # executor se asigna en start()

        # Módulos avanzados: Volume Profile (COMEX) + Delta (ticks MT5)
        self.vp    = VolumeProfileEngine(config)
        self.delta = DeltaEngine(self.mt5)

        # Régimen de mercado cuántico (Hurst + ADX + Volatility)
        self.regime = MarketRegimeEngine(self.mt5)

        # Motor de señales con todos los módulos
        self.signals = SignalEngine(
            self.mt5, self.news, config,
            correlation_engine=self.corr,
            macro_feed=self.macro,
            learning_engine=self.ml,
            volume_profile=self.vp,
            delta_engine=self.delta,
            regime_engine=self.regime,
        )

        tg = config.get("telegram", {})
        self.telegram = TelegramBot(
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
        )

        # Motor de ejecución de órdenes en MT5
        self.executor = TradeExecutor(self.mt5, config)
        self._autotrading_warned = False  # evitar spam de aviso

        primary   = config["symbols"]["primary"]
        secondary = config["symbols"].get("secondary", [])
        self.symbols = [primary] + secondary
        self._cycle_count = 0
        self._autotrading_warned = False
        self._known_positions = set()  # tickets de posiciones abiertas

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

        elif ctype == "revisar":
            self._send_revisar()

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

        if not risk_summary.get("within_limits", True):
            dd_type = "semanal" if risk_summary.get("weekly_loss", 0) >= risk_summary.get("weekly_limit", 1e9) else "diario"
            observation_mode   = True
            observation_reason = f"⛔ Drawdown {dd_type} alcanzado — solo señales, sin ejecución"
            logger.warning(f"Límite drawdown — modo observación")

        # ── Analizar cada símbolo ─────────────────────────────
        active_count = 0

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
                can = self.risk.can_open_trade(active_count)
                if not can["ok"]:
                    logger.info(f"[{symbol}] {can['reason']}")
                    continue

                signal = self.signals.analyze(symbol)
                if signal is None:
                    continue

                active_count += 1
                market_state["symbols"][symbol]["last_signal_time"] = signal["timestamp"]

                # Calcular lot size con multiplicador dinámico por racha
                risk_mult = self.risk.get_risk_multiplier(self.db_path)
                lot = self.risk.calculate_lot_size(
                    signal["entry"], signal["sl"], symbol, risk_multiplier=risk_mult
                )
                if risk_mult < 1.0:
                    logger.info(f"[RISK] Lotes reducidos a {risk_mult*100:.0f}% ({lot:.2f}) — racha pérdidas")
                signal["lot_size"] = lot

                logger.info(
                    f"[SEÑAL] [{symbol}] {signal['direction']} | "
                    f"Entry:{signal['entry']} SL:{signal['sl']} TP1:{signal['tp1']} | "
                    f"R:R:{signal['rr']} | Conf:{signal['confidence']:.0%} | Lotes:{lot}"
                )

                # Telegram: señal siempre (modo observación incluido)
                # Añadir aviso si estamos en modo observación
                sent = False
                if observation_mode and observation_reason:
                    signal["observation_note"] = observation_reason
                if not signal.get("news_blackout"):
                    sent = self.telegram.send_signal(signal)
                elif observation_mode:
                    # En modo observación mandamos aunque haya blackout (solo aviso)
                    signal["observation_note"] = observation_reason + " | ⚠️ Precaución: noticia próxima"
                    sent = self.telegram.send_signal(signal)

                # ── Ejecución automática en MT5 ──────────────
                # Solo si: auto_execute=True Y sin blackout Y NO en modo observación
                ticket    = None
                trade_cfg = self.config.get("trading", {})
                if trade_cfg.get("auto_execute", False) and not signal.get("news_blackout") and not observation_mode:
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
                            ticket = self.executor.place_limit_order(
                                symbol    = symbol,
                                direction = signal["direction"],
                                volume    = float(lot),
                                entry     = signal["entry"],
                                sl        = signal["sl"],
                                tp        = signal["tp1"],
                                comment   = f"SMC {signal['confidence']:.0%}",
                            )
                            if ticket:
                                signal["ticket"] = ticket
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

        # ── Revisar validez de órdenes pendientes (cada 5 ciclos ≈ 5 min) ──
        self._cycle_count += 1
        if self._cycle_count % 5 == 0:
            try:
                self._review_pending_orders()
            except Exception as e:
                logger.debug(f"Review orders: {e}")

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
        logger.info(f"Ciclo completado - {active_count} senal(es) generada(s)")

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
        from backtest.backtester import run_backtest
        from datetime import timedelta

        conn = MT5Connector(cfg)
        if not conn.connect():
            print("ERROR: MT5 no disponible. ¿Está abierto?")
            sys.exit(1)

        dt_to   = datetime.now(timezone.utc)
        dt_from = dt_to - timedelta(days=365)

        # Símbolos a testear — XAUUSD siempre + secundarios si se pasa --all
        symbols_to_test = [cfg["symbols"]["primary"]]
        if "--all" in sys.argv:
            symbols_to_test += cfg["symbols"].get("secondary", [])

        all_results = {}
        for sym in symbols_to_test:
            print(f"\nIniciando backtest de 1 año sobre {sym} H1...")
            result = run_backtest(conn, sym, "H1", dt_from, dt_to, cfg)
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
