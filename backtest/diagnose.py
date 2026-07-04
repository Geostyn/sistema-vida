"""
Diagnose — Autopsia del rendimiento en vivo (Fase 0 del upgrade 2026-06).

Lee logs/trades.db (tabla signals, outcomes WIN/LOSS) y responde:
  - ¿El bot tiene sesgo direccional? (WR BUY vs SELL, conteo de cada uno)
  - ¿Cuándo empezó a perder? (curva de equity + WR rodante)
  - ¿Qué confluencia/feature engaña? (WR por bucket de cada feature)
  - ¿Qué horas/sesiones/regímenes funcionan?

NO modifica nada. Solo imprime el informe y un veredicto con recomendaciones.

Ejecutar:
    python backtest/diagnose.py
    python backtest/diagnose.py --last 30      # solo los ultimos 30 cierres
    python backtest/diagnose.py --md           # salida en Markdown para Obsidian
"""

import os
import sys
import sqlite3
import argparse

import numpy as np
import pandas as pd

# Consola Windows (cp1252) no encodea emojis — forzar UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_PROJECT_ROOT, "logs", "trades.db")

# Features numéricas a correlacionar con el resultado (existen en la tabla signals)
NUMERIC_FEATURES = [
    "confidence", "confluences", "rr", "atr_pct",
    "vp_score", "delta_score", "hurst", "adx", "pairs_score",
    "ml_proba", "sweep_score", "fvg_score", "m15_aligned", "htf_liq_dist",
    "news_blackout",
]
# Features categóricas (agrupar y ver WR por categoría)
CAT_FEATURES = ["bias_h4", "structure_h1", "ob_type", "rsi_state", "regime", "entry_type"]


def _load(db_path: str, last: int | None = None) -> pd.DataFrame:
    if not os.path.exists(db_path):
        print(f"❌ No existe la DB: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM signals WHERE outcome IN ('WIN','LOSS') ORDER BY timestamp ASC",
        conn,
    )
    conn.close()
    if df.empty:
        print("⚠️  No hay trades cerrados (WIN/LOSS) en la DB todavía.")
        sys.exit(0)
    df["win"] = (df["outcome"] == "WIN").astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["hour"] = df["ts"].dt.hour
    if last:
        df = df.tail(last).reset_index(drop=True)
    return df


def _wr_block(df: pd.DataFrame, col: str, label: str) -> list[str]:
    """WR + PnL agrupado por una columna. Devuelve líneas de texto."""
    out = [f"\n== {label} =="]
    g = df.groupby(col)
    rows = []
    for key, sub in g:
        n = len(sub)
        if n == 0:
            continue
        w = int(sub["win"].sum())
        wr = w / n
        pnl = float(sub["pnl_amount"].sum()) if "pnl_amount" in sub else 0.0
        rows.append((key, n, w, n - w, wr, pnl))
    rows.sort(key=lambda r: -r[1])  # por nº de trades
    for key, n, w, l, wr, pnl in rows:
        flag = "  ⚠️" if (n >= 8 and wr < 0.40) else ("  ✅" if (n >= 8 and wr >= 0.55) else "")
        out.append(f"  {str(key):>16}: {w:>3}W/{l:<3}L  n={n:<4} WR={wr:5.1%}  PnL={pnl:>+10.0f}{flag}")
    return out


def _bucket_wr(df: pd.DataFrame, col: str, q: int = 4) -> list[str]:
    """WR por cuartiles de una feature numérica. Detecta features que engañan."""
    if col not in df.columns:
        return []
    s = pd.to_numeric(df[col], errors="coerce")
    valid = df[s.notna()].copy()
    if len(valid) < 12 or s.nunique() < 3:
        return []
    try:
        valid["bucket"] = pd.qcut(s[s.notna()], q=min(q, s.nunique()), duplicates="drop")
    except Exception:
        return []
    out = [f"\n  · {col}:"]
    for b, sub in valid.groupby("bucket", observed=True):
        n = len(sub)
        wr = sub["win"].mean()
        flag = " ⚠️ENGAÑA" if (n >= 6 and wr < 0.40) else ""
        out.append(f"      {str(b):>26}: n={n:<4} WR={wr:5.1%}{flag}")
    return out


def _point_biserial(df: pd.DataFrame) -> list[str]:
    """Correlación de cada feature numérica con el resultado (win=1/0)."""
    out = ["\n== Correlación feature → WIN (point-biserial) =="]
    corrs = []
    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        m = s.notna()
        if m.sum() < 15 or s[m].nunique() < 3:
            continue
        try:
            r = np.corrcoef(s[m].astype(float), df["win"][m].astype(float))[0, 1]
            if not np.isnan(r):
                corrs.append((col, r))
        except Exception:
            pass
    corrs.sort(key=lambda x: -abs(x[1]))
    for col, r in corrs:
        signo = "predice WIN" if r > 0 else "predice LOSS"
        fuerza = "FUERTE" if abs(r) >= 0.20 else ("media" if abs(r) >= 0.10 else "débil")
        out.append(f"  {col:>14}: r={r:+.3f}  ({signo}, {fuerza})")
    return out


def _equity_curve(df: pd.DataFrame) -> list[str]:
    """Curva de equity acumulada + WR rodante (detecta cuándo empezó a sangrar)."""
    out = ["\n== Curva de equity y deterioro temporal =="]
    df = df.copy()
    df["cum_pnl"] = df["pnl_amount"].cumsum()
    peak = df["cum_pnl"].cummax()
    dd = (df["cum_pnl"] - peak)
    out.append(f"  PnL acumulado final: {df['cum_pnl'].iloc[-1]:+,.0f}")
    out.append(f"  Pico de equity:      {peak.iloc[-1]:+,.0f}")
    out.append(f"  Drawdown actual:     {dd.iloc[-1]:+,.0f}")
    out.append(f"  Max drawdown:        {dd.min():+,.0f}")
    # WR rodante de 20 trades — primer punto donde cae por debajo de 40%
    df["roll_wr"] = df["win"].rolling(20).mean()
    bad = df[df["roll_wr"] < 0.40]
    if not bad.empty:
        first = bad.iloc[0]
        out.append(
            f"  ⚠️ WR rodante (20) cayó <40% por primera vez el "
            f"{str(first['ts'])[:16]} (trade #{bad.index[0]+1})"
        )
    # Últimos 20 vs primeros 20
    if len(df) >= 40:
        early = df["win"].head(20).mean()
        late = df["win"].tail(20).mean()
        out.append(f"  WR primeros 20: {early:.1%}  →  WR últimos 20: {late:.1%}")
    return out


def _verdict(df: pd.DataFrame) -> list[str]:
    """Veredicto automático con recomendaciones accionables."""
    out = ["\n" + "=" * 60, "VEREDICTO", "=" * 60]
    n = len(df)
    wr = df["win"].mean()
    out.append(f"Trades cerrados: {n} | WR global: {wr:.1%}")

    # Sesgo direccional
    by_dir = df.groupby("direction")["win"].agg(["mean", "count"])
    if {"BUY", "SELL"}.issubset(by_dir.index):
        buy_n, sell_n = int(by_dir.loc["BUY", "count"]), int(by_dir.loc["SELL", "count"])
        buy_wr, sell_wr = by_dir.loc["BUY", "mean"], by_dir.loc["SELL", "mean"]
        ratio = sell_n / max(buy_n, 1)
        if ratio >= 3 or ratio <= 0.33:
            dom = "SELL" if sell_n > buy_n else "BUY"
            out.append(
                f"🔴 SESGO DIRECCIONAL: {sell_n} SELL vs {buy_n} BUY "
                f"({ratio:.1f}× hacia {dom}). El bot opera casi en una sola dirección."
            )
        if abs(buy_wr - sell_wr) >= 0.10:
            mejor = "BUY" if buy_wr > sell_wr else "SELL"
            peor = "SELL" if mejor == "BUY" else "BUY"
            out.append(
                f"🔴 Una dirección funciona mucho mejor: {mejor} WR={max(buy_wr,sell_wr):.0%} "
                f"vs {peor} WR={min(buy_wr,sell_wr):.0%}. "
                f"→ El lado {peor} está operando contra la tendencia. "
                f"FIX: puerta direccional (no {peor} si macro/HTF en contra)."
            )

    # Confianza baja perdedora
    if "confidence" in df.columns:
        lowconf = df[pd.to_numeric(df["confidence"], errors="coerce") < 0.40]
        if len(lowconf) >= 8:
            out.append(
                f"🟠 Señales de baja confianza (<0.40): n={len(lowconf)} WR={lowconf['win'].mean():.0%}. "
                f"→ FIX: subir min_confluences o bajar tier / no auto-ejecutar las marginales."
            )

    # Deterioro reciente
    if len(df) >= 30:
        late = df["win"].tail(15).mean()
        if late < 0.35:
            out.append(
                f"🔴 Racha reciente tóxica: últimos 15 cierres WR={late:.0%}. "
                f"→ Probable cambio de régimen no detectado (tendencia nueva)."
            )

    out.append(
        "\nPróximos pasos (Fase 3): corregir bias H4 con puerta macro "
        "(DXY+reales), GARCH para régimen, subir suelo de calidad."
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=None, help="Solo los últimos N cierres")
    ap.add_argument("--md", action="store_true", help="Salida en Markdown")
    ap.add_argument("--db", default=DB_PATH, help="Ruta a trades.db")
    args = ap.parse_args()

    df = _load(args.db, args.last)

    lines = []
    lines.append("=" * 60)
    lines.append(f"AUTOPSIA DEL BOT — {len(df)} trades cerrados (WIN/LOSS)")
    lines.append("=" * 60)

    # Bloques de WR por categoría
    lines += _wr_block(df, "direction", "WR por dirección")
    if "model" in df.columns:
        lines += _wr_block(df, "model", "WR por modelo")
    lines += _wr_block(df, "hour", "WR por hora UTC")
    for cat in CAT_FEATURES:
        if cat in df.columns and df[cat].notna().any():
            lines += _wr_block(df, cat, f"WR por {cat}")

    # Buckets de features numéricas
    lines.append("\n== WR por bucket de feature (¿cuál engaña?) ==")
    for col in ["confidence", "confluences", "ml_proba", "adx", "hurst",
                "vp_score", "delta_score", "htf_liq_dist", "rr"]:
        lines += _bucket_wr(df, col)

    # Correlaciones
    lines += _point_biserial(df)

    # Equity curve
    lines += _equity_curve(df)

    # Veredicto
    lines += _verdict(df)

    text = "\n".join(lines)
    if args.md:
        text = "```\n" + text + "\n```"
    print(text)


if __name__ == "__main__":
    main()
