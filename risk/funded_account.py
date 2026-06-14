"""
FundedAccountTracker — Simulación de la cuenta FundedNext Stellar Instant 2K.

El bot NO se conecta a la cuenta fondeada: el usuario ejecuta a mano cada
señal "apta 2K". Este módulo mantiene el equity simulado asumiendo que el
usuario replica toda señal apta con los lotes sugeridos, y aplica la regla
de trailing drawdown de FundedNext:

  floor = min(balance_inicial, max(94% del inicial, highest_equity − 6% del inicial))
  → el suelo sube con los máximos de equity y se BLOQUEA en el balance inicial.
  → si el equity toca el floor, la cuenta se quema (breach).

Corrección de drift: el usuario puede sincronizar en cualquier momento con
/equity <importe> desde Telegram (fila MANUAL en funded_equity).
"""

import os
import json
import math
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class FundedAccountTracker:
    def __init__(self, config: dict, db_path: str):
        self.config  = config
        self.cfg     = config.get("funded", {}) or {}
        self.db_path = db_path
        self.enabled = bool(self.cfg.get("enabled", False))
        self.initial = float(self.cfg.get("balance", 2000))
        self.dd_pct  = float(self.cfg.get("trailing_dd_pct", 0.06))
        self.risk_per_trade   = float(self.cfg.get("risk_per_trade", 0.01))
        self.max_sl_usd       = float(self.cfg.get("max_sl_usd", 25))
        self.max_risk_of_room = float(self.cfg.get("max_risk_pct_of_room", 0.35))
        if self.enabled:
            self._ensure_init_row()

    # ── Estado ──────────────────────────────────────────────────────

    def _ensure_init_row(self):
        """Inserta la fila INIT si la tabla funded_equity está vacía."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
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
            cur.execute("SELECT COUNT(*) FROM funded_equity")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO funded_equity
                        (timestamp, equity, highest_equity, source, note)
                    VALUES (?, ?, ?, 'INIT', ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    self.initial, self.initial,
                    f"Cuenta FundedNext inicializada (${self.initial:,.0f})",
                ))
                conn.commit()
                logger.info(f"[FUNDED] Cuenta 2K inicializada: ${self.initial:,.0f}")
            conn.close()
        except Exception as e:
            logger.warning(f"[FUNDED] Error inicializando funded_equity: {e}")

    def get_state(self) -> dict:
        """Equity actual, máximo histórico, floor del trailing DD y room."""
        equity, highest = self.initial, self.initial
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT equity, highest_equity FROM funded_equity
                ORDER BY id DESC LIMIT 1
            """)
            row = cur.fetchone()
            conn.close()
            if row:
                equity, highest = float(row[0]), float(row[1])
        except Exception as e:
            logger.debug(f"[FUNDED] get_state: {e}")

        dd_usd = self.initial * self.dd_pct
        floor  = min(self.initial, max(self.initial - dd_usd, highest - dd_usd))
        room   = equity - floor
        return {
            "enabled":     self.enabled,
            "initial":     self.initial,
            "equity":      round(equity, 2),
            "highest":     round(highest, 2),
            "floor":       round(floor, 2),
            "room":        round(room, 2),
            "dd_used_pct": round(max(0.0, (highest - equity)) / self.initial * 100, 2),
            "breached":    equity <= floor,
        }

    # ── Evaluación de señales ───────────────────────────────────────

    def evaluate_signal(self, signal: dict) -> dict:
        """
        Calcula el bloque 2K de una señal: lotes, riesgo, apta/no-apta.
        Usa los niveles refinados M15 si existen (signal["funded_levels"]),
        si no, los niveles H1 de la demo. Solo XAUUSD (1 lote = $100/punto).
        """
        if not self.enabled:
            return {}

        levels  = signal.get("funded_levels") or {}
        entry   = float(levels.get("entry", signal.get("entry", 0)) or 0)
        sl      = float(levels.get("sl", signal.get("sl", 0)) or 0)
        refined = bool(levels.get("refined", False))
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return {}

        state = self.get_state()
        equity, room = state["equity"], state["room"]

        # XAUUSD: 0.01 lotes → $1 por cada $1 de movimiento
        risk_at_min_lot = sl_dist * 100.0 * 0.01
        target_risk     = equity * self.risk_per_trade
        raw_lots        = target_risk / (sl_dist * 100.0)
        lots            = max(0.01, round(math.floor(raw_lots / 0.01 + 1e-9) * 0.01, 2))
        risk_usd        = round(lots * sl_dist * 100.0, 2)

        apta    = (risk_at_min_lot <= self.max_sl_usd) and (room > risk_usd) \
                  and not state["breached"]
        reasons = []
        if risk_at_min_lot > self.max_sl_usd:
            reasons.append(
                f"riesgo mín. ${risk_at_min_lot:.0f} > ${self.max_sl_usd:.0f} máx "
                f"({risk_at_min_lot / equity:.1%} con 0.01 lotes)"
            )
        if room <= risk_usd:
            reasons.append(f"room DD insuficiente (${room:.0f} restante)")
        if state["breached"]:
            reasons.append("cuenta en breach — no operar")

        room_pct  = (risk_usd / room) if room > 0 else 1.0
        room_warn = room_pct > self.max_risk_of_room

        return {
            "title":       str(self.cfg.get("title", "FUNDEDNEXT 2K")),
            "lots":        lots,
            "risk_usd":    risk_usd,
            "risk_pct":    round(risk_usd / equity * 100, 2) if equity > 0 else 0,
            "apta":        apta,
            "reasons":     reasons,
            "entry":       round(entry, 2),
            "sl":          round(sl, 2),
            "refined":     refined,
            "equity":      state["equity"],
            "floor":       state["floor"],
            "room":        state["room"],
            "room_pct":    round(room_pct * 100, 1),
            "room_warn":   room_warn,
        }

    # ── Resultados (escala el R realizado de la demo al riesgo 2K) ──

    def sync_from_db(self) -> list:
        """
        Aplica al equity simulado los resultados de señales aptas ya
        resueltas en la demo (WIN/LOSS con P&L real de MT5 que hereda
        parciales/BE/trailing). EXPIRED → se marca aplicada con P&L 0
        (la LIMIT manual tampoco se habría llenado).

        Returns: lista de dicts {signal_id, outcome, funded_pnl, equity}
        para notificar por Telegram.
        """
        if not self.enabled:
            return []
        applied = []
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, outcome, pnl_amount, lot_size, entry, sl,
                       funded_risk_usd, rr
                FROM signals
                WHERE funded_apta = 1 AND funded_applied = 0
                  AND outcome IN ('WIN', 'LOSS', 'EXPIRED')
            """)
            rows = cur.fetchall()

            for sig_id, outcome, pnl_amount, lot_size, entry, sl, funded_risk, rr in rows:
                if outcome == "EXPIRED":
                    cur.execute("""
                        UPDATE signals SET funded_applied = 1, funded_pnl = 0
                        WHERE id = ?
                    """, (sig_id,))
                    continue

                if not funded_risk:
                    cur.execute("UPDATE signals SET funded_applied = 1 WHERE id = ?", (sig_id,))
                    continue

                demo_risk = float(lot_size or 0) * abs(float(entry or 0) - float(sl or 0)) * 100.0
                if demo_risk > 0:
                    # R realizado (incluye parciales, BE y trailing de la demo)
                    r_realized = float(pnl_amount or 0) / demo_risk
                else:
                    # Señal sin ejecución demo (DAYTRADE): R teórico al TP1/SL
                    r_realized = float(rr or 1.5) if outcome == "WIN" else -1.0
                r_realized = max(-1.5, min(5.0, r_realized))  # sanity cap
                funded_pnl = round(r_realized * float(funded_risk), 2)

                state      = self.get_state()
                new_equity = round(state["equity"] + funded_pnl, 2)
                new_high   = max(state["highest"], new_equity)

                cur.execute("""
                    INSERT INTO funded_equity
                        (timestamp, equity, highest_equity, source, signal_id, note)
                    VALUES (?, ?, ?, 'AUTO', ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    new_equity, new_high, sig_id,
                    f"{outcome} señal #{sig_id}: {funded_pnl:+.2f} USD (R {r_realized:+.2f})",
                ))
                cur.execute("""
                    UPDATE signals SET funded_applied = 1, funded_pnl = ?
                    WHERE id = ?
                """, (funded_pnl, sig_id))
                conn.commit()

                applied.append({
                    "signal_id":  sig_id,
                    "outcome":    outcome,
                    "funded_pnl": funded_pnl,
                    "r_realized": round(r_realized, 2),
                    "equity":     new_equity,
                })
                logger.info(
                    f"[FUNDED] Señal #{sig_id} {outcome}: {funded_pnl:+.2f} USD "
                    f"→ equity ${new_equity:,.2f}"
                )

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[FUNDED] sync_from_db: {e}")
        return applied

    # ── Corrección manual (/equity) ─────────────────────────────────

    def set_equity(self, amount: float, note: str = "Ajuste manual /equity") -> dict:
        """Sincroniza el equity simulado con el real de la cuenta FundedNext."""
        if not (0 < amount < 100000):
            return {"ok": False, "error": f"Importe inválido: {amount}"}
        try:
            state    = self.get_state()
            new_high = max(state["highest"], float(amount))
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO funded_equity
                    (timestamp, equity, highest_equity, source, note)
                VALUES (?, ?, ?, 'MANUAL', ?)
            """, (datetime.now(timezone.utc).isoformat(), float(amount), new_high, note))
            conn.commit()
            conn.close()
            return {"ok": True, "state": self.get_state()}
        except Exception as e:
            logger.warning(f"[FUNDED] set_equity: {e}")
            return {"ok": False, "error": str(e)}

    # ── Estado para el dashboard ────────────────────────────────────

    def write_state_json(self, path: str = None):
        """Vuelca el estado a logs/funded_state.json (lo lee el dashboard)."""
        if not self.enabled:
            return
        try:
            state = self.get_state()
            state["last_update"] = datetime.now(timezone.utc).isoformat()
            out = path or os.path.join("logs", "funded_state.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.debug(f"[FUNDED] write_state_json: {e}")

    def get_equity_history(self, limit: int = 50) -> list:
        """Últimas filas de equity para dashboard/reportes."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT timestamp, equity, highest_equity, source, note
                FROM funded_equity ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [
                {"timestamp": r[0], "equity": r[1], "highest": r[2],
                 "source": r[3], "note": r[4]}
                for r in rows
            ]
        except Exception:
            return []
