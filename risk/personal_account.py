"""
PersonalAccountTracker — Cuenta personal pequeña del usuario (ejecución manual).

El bot NO se conecta a esta cuenta: cada señal lleva un bloque "MI CUENTA"
con lotes, riesgo en EUR y apta/no-apta para que el usuario la ejecute a mano
en su propia cuenta real. Mismo patrón que FundedAccountTracker pero sin
trailing drawdown — la única regla es el riesgo máximo por operación.

Realidad de cuentas micro (100-300 EUR) en XAUUSD:
  lote mínimo 0.01 = $1 por cada $1 de movimiento → el SL en $ ES el riesgo.
  Con 200 EUR, exigir 1% estricto (2 EUR) descartaría casi todo. Por eso:
    - lots = lo que pida el 1% objetivo, con suelo en 0.01
    - apta = el riesgo real con esos lotes <= max_risk_pct (default 3%)
  Las señales DAYTRADE M15 (SL corto) son las que más pasan el filtro.

Corrección de drift: /saldo <importe> desde Telegram (fila MANUAL).
"""

import os
import json
import math
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FALLBACK_EURUSD = 1.08  # conversión USD→EUR si MT5 no da precio


class PersonalAccountTracker:
    def __init__(self, config: dict, db_path: str):
        self.config  = config
        self.cfg     = config.get("personal", {}) or {}
        self.db_path = db_path
        self.enabled = bool(self.cfg.get("enabled", False))
        self.title   = str(self.cfg.get("title", "MI CUENTA"))
        self.initial = float(self.cfg.get("balance", 200))
        self.currency = str(self.cfg.get("currency", "EUR"))
        self.risk_per_trade = float(self.cfg.get("risk_per_trade", 0.01))
        self.max_risk_pct   = float(self.cfg.get("max_risk_pct", 0.03))
        self.min_equity     = float(self.cfg.get("min_equity", 50))
        if self.enabled:
            self._ensure_init_row()

    # ── Estado ──────────────────────────────────────────────────────

    def _ensure_init_row(self):
        """Inserta la fila INIT si la tabla personal_equity está vacía."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS personal_equity (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    equity          REAL NOT NULL,
                    source          TEXT,
                    signal_id       INTEGER,
                    note            TEXT
                )
            """)
            cur.execute("SELECT COUNT(*) FROM personal_equity")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                    INSERT INTO personal_equity (timestamp, equity, source, note)
                    VALUES (?, ?, 'INIT', ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    self.initial,
                    f"Cuenta personal inicializada ({self.initial:,.0f} {self.currency})",
                ))
                conn.commit()
                logger.info(f"[PERSONAL] Cuenta inicializada: {self.initial:,.0f} {self.currency}")
            conn.close()
        except Exception as e:
            logger.warning(f"[PERSONAL] Error inicializando personal_equity: {e}")

    def get_state(self) -> dict:
        """Equity actual y P&L acumulado de la cuenta personal."""
        equity = self.initial
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("SELECT equity FROM personal_equity ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row:
                equity = float(row[0])
        except Exception as e:
            logger.debug(f"[PERSONAL] get_state: {e}")

        return {
            "enabled":  self.enabled,
            "title":    self.title,
            "currency": self.currency,
            "initial":  self.initial,
            "equity":   round(equity, 2),
            "pnl":      round(equity - self.initial, 2),
            "too_low":  equity < self.min_equity,
        }

    # ── Evaluación de señales ───────────────────────────────────────

    def evaluate_signal(self, signal: dict, eurusd: float = None) -> dict:
        """
        Calcula el bloque MI CUENTA de una señal: lotes, riesgo, apta/no-apta.
        Usa los niveles refinados M15 si existen (signal["funded_levels"]) —
        las señales DAYTRADE ya traen niveles M15 nativos. Solo XAUUSD
        (1 lote = $100/punto; el riesgo USD se convierte a EUR con EURUSD).
        """
        if not self.enabled:
            return {}

        levels  = signal.get("funded_levels") or {}
        entry   = float(levels.get("entry", signal.get("entry", 0)) or 0)
        sl      = float(levels.get("sl", signal.get("sl", 0)) or 0)
        refined = bool(levels.get("refined", False)) or signal.get("mode") == "DAYTRADE"
        sl_dist = abs(entry - sl)
        if entry <= 0 or sl_dist <= 0:
            return {}

        state  = self.get_state()
        equity = state["equity"]
        rate   = float(eurusd or 0) or FALLBACK_EURUSD
        if self.currency == "EUR":
            equity_usd = equity * rate
        else:
            equity_usd = equity

        # XAUUSD: 0.01 lotes → $1 por cada $1 de movimiento
        target_risk_usd = equity_usd * self.risk_per_trade
        raw_lots        = target_risk_usd / (sl_dist * 100.0)
        lots            = max(0.01, round(math.floor(raw_lots / 0.01 + 1e-9) * 0.01, 2))
        risk_usd        = round(lots * sl_dist * 100.0, 2)
        risk_acc        = round(risk_usd / rate, 2) if self.currency == "EUR" else risk_usd
        risk_pct        = (risk_acc / equity) if equity > 0 else 1.0

        apta    = (risk_pct <= self.max_risk_pct) and not state["too_low"]
        reasons = []
        if risk_pct > self.max_risk_pct:
            reasons.append(
                f"riesgo {risk_pct:.1%} > {self.max_risk_pct:.0%} máx "
                f"({risk_acc:.2f} {self.currency} con {lots:.2f} lotes — SL demasiado amplio)"
            )
        if state["too_low"]:
            reasons.append(
                f"equity {equity:.0f} {self.currency} < mínimo {self.min_equity:.0f} — no operar")

        return {
            "title":     self.title,
            "currency":  self.currency,
            "lots":      lots,
            "risk_usd":  risk_usd,
            "risk_acc":  risk_acc,                 # riesgo en la divisa de la cuenta
            "risk_pct":  round(risk_pct * 100, 2),
            "apta":      apta,
            "reasons":   reasons,
            "entry":     round(entry, 2),
            "sl":        round(sl, 2),
            "refined":   refined,
            "equity":    state["equity"],
        }

    # ── Resultados (escala el R realizado de la señal al riesgo personal) ──

    def sync_from_db(self) -> list:
        """
        Aplica al equity los resultados de señales aptas resueltas
        (WIN/LOSS — EXPIRED se marca aplicada con P&L 0).

        Returns: lista de dicts {signal_id, outcome, personal_pnl, equity}
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
                       personal_risk_acc, rr
                FROM signals
                WHERE personal_apta = 1 AND personal_applied = 0
                  AND outcome IN ('WIN', 'LOSS', 'EXPIRED')
            """)
            rows = cur.fetchall()

            for sig_id, outcome, pnl_amount, lot_size, entry, sl, personal_risk, rr in rows:
                if outcome == "EXPIRED":
                    cur.execute("""
                        UPDATE signals SET personal_applied = 1, personal_pnl = 0
                        WHERE id = ?
                    """, (sig_id,))
                    continue

                if not personal_risk:
                    cur.execute("UPDATE signals SET personal_applied = 1 WHERE id = ?", (sig_id,))
                    continue

                demo_risk = float(lot_size or 0) * abs(float(entry or 0) - float(sl or 0)) * 100.0
                if demo_risk > 0:
                    # R realizado de la señal (parciales/BE incluidos si hubo demo)
                    r_realized = float(pnl_amount or 0) / demo_risk
                else:
                    # Señal sin ejecución demo (DAYTRADE): R teórico al TP1/SL
                    r_realized = float(rr or 1.5) if outcome == "WIN" else -1.0
                r_realized = max(-1.5, min(5.0, r_realized))  # sanity cap
                personal_pnl = round(r_realized * float(personal_risk), 2)

                state      = self.get_state()
                new_equity = round(state["equity"] + personal_pnl, 2)

                cur.execute("""
                    INSERT INTO personal_equity
                        (timestamp, equity, source, signal_id, note)
                    VALUES (?, ?, 'AUTO', ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    new_equity, sig_id,
                    f"{outcome} señal #{sig_id}: {personal_pnl:+.2f} {self.currency} "
                    f"(R {r_realized:+.2f})",
                ))
                cur.execute("""
                    UPDATE signals SET personal_applied = 1, personal_pnl = ?
                    WHERE id = ?
                """, (personal_pnl, sig_id))
                conn.commit()

                applied.append({
                    "signal_id":    sig_id,
                    "outcome":      outcome,
                    "personal_pnl": personal_pnl,
                    "r_realized":   round(r_realized, 2),
                    "equity":       new_equity,
                })
                logger.info(
                    f"[PERSONAL] Señal #{sig_id} {outcome}: {personal_pnl:+.2f} "
                    f"{self.currency} → equity {new_equity:,.2f}"
                )

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[PERSONAL] sync_from_db: {e}")
        return applied

    # ── Corrección manual (/saldo) ──────────────────────────────────

    def set_equity(self, amount: float, note: str = "Ajuste manual /saldo") -> dict:
        """Sincroniza el equity simulado con el saldo real de la cuenta."""
        if not (0 < amount < 1000000):
            return {"ok": False, "error": f"Importe inválido: {amount}"}
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO personal_equity (timestamp, equity, source, note)
                VALUES (?, ?, 'MANUAL', ?)
            """, (datetime.now(timezone.utc).isoformat(), float(amount), note))
            conn.commit()
            conn.close()
            return {"ok": True, "state": self.get_state()}
        except Exception as e:
            logger.warning(f"[PERSONAL] set_equity: {e}")
            return {"ok": False, "error": str(e)}

    # ── Estado para el dashboard ────────────────────────────────────

    def write_state_json(self, path: str = None):
        """Vuelca el estado a logs/personal_state.json (lo lee el dashboard)."""
        if not self.enabled:
            return
        try:
            state = self.get_state()
            state["last_update"] = datetime.now(timezone.utc).isoformat()
            out = path or os.path.join("logs", "personal_state.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.debug(f"[PERSONAL] write_state_json: {e}")
