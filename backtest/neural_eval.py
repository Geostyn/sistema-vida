"""
Evaluación OUT-OF-SAMPLE limpia de la red neuronal LSTM (sin data leakage).

Pregunta que responde: ¿la red predice de verdad sobre datos que NUNCA vio?
Si su probabilidad no separa ganadores de perdedores fuera de muestra, NO debe
influir en las señales en vivo (regla del proyecto: validar antes de activar).

Método honesto (anti-leakage):
  1. Descarga histórico H1 de MT5.
  2. Corte temporal: 70% antiguo = ENTRENO, 30% reciente = TEST (nunca visto).
  3. Entrena una LSTM fresca SOLO con el tramo de entreno.
  4. En el tramo TEST: etiqueta con triple-barrier (igual que el entreno) y mide
     win-rate por cuartil de probabilidad. Si el cuartil alto gana mucho más que
     el bajo → la red tiene ventaja real → activar justificado.
  5. Restaura el modelo en vivo intacto (no toca logs/neural_model.pt).

Ejecutar:
    PY=/c/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe
    $PY backtest/neural_eval.py            # 900 días por defecto
    $PY backtest/neural_eval.py --days 1200
"""

import os
import sys
import shutil
import argparse

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
logging.disable(logging.CRITICAL)

import yaml
from data.mt5_connector import MT5Connector
from analysis.indicators import add_indicators
from ml.neural_engine import (
    NeuralEngine, build_dataset, SEQ_LEN, MODEL_PATH, SCALER_PATH,
)

TRAIN_FRAC = 0.70


def _wr_by_bucket(proba, y, n_buckets=4):
    """Win-rate por cuantil de probabilidad. Devuelve lista de (rango, n, wr)."""
    order = np.argsort(proba)
    proba_s, y_s = proba[order], y[order]
    out = []
    edges = np.linspace(0, len(proba), n_buckets + 1, dtype=int)
    for i in range(n_buckets):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        seg_p, seg_y = proba_s[a:b], y_s[a:b]
        out.append((seg_p.min(), seg_p.max(), len(seg_y), float(seg_y.mean())))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    symbol = args.symbol or cfg.get("symbols", {}).get("primary", "XAUUSD")

    mt5 = MT5Connector(cfg)
    if not mt5.connect():
        print("❌ MT5 no disponible. Abre MetaTrader 5."); return
    n_bars = min(args.days * 24, 50000)
    df = add_indicators(mt5.get_rates(symbol, "H1", n_bars))
    mt5.disconnect()
    if df.empty or len(df) < 2000:
        print(f"❌ Pocas barras H1 ({len(df)})."); return

    split = int(len(df) * TRAIN_FRAC)
    df_train = df.iloc[:split].reset_index(drop=True)
    df_test  = df.iloc[split - SEQ_LEN:].reset_index(drop=True)  # contexto previo

    print("═" * 64)
    print(f"🧠 EVALUACIÓN OUT-OF-SAMPLE — LSTM {symbol}")
    print("═" * 64)
    print(f"Barras H1: {len(df)}  →  entreno {len(df_train)} | test {len(df_test)} (nunca visto)")
    print(f"Periodo test ≈ {len(df_test)//24} días reservados\n")

    # Backup del modelo en vivo (train() sobrescribe los ficheros)
    had_model = os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH)
    if had_model:
        shutil.copy2(MODEL_PATH, MODEL_PATH + ".bak")
        shutil.copy2(SCALER_PATH, SCALER_PATH + ".bak")

    try:
        ne = NeuralEngine()
        ne.model = None  # forzar entreno desde cero (ignorar el modelo en vivo)
        print("Entrenando LSTM solo con el tramo antiguo (puede tardar 1-3 min)...")
        res = ne.train(df_train)
        if not res.get("trained"):
            print(f"❌ No se pudo entrenar: {res}"); return
        print(f"  Entreno OK — val_acc interno {res['val_acc']}  "
              f"(base WR etiquetas {res['win_rate_label']:.1%})\n")

        # ── TEST out-of-sample ────────────────────────────────────
        Xte, yte = build_dataset(df_test)
        if Xte is None or len(Xte) < 200:
            print("❌ Test insuficiente."); return

        import torch
        Xs = (Xte - ne.mean) / ne.std
        with torch.no_grad():
            logits = ne.model(torch.tensor(Xs, dtype=torch.float32))
            proba = torch.sigmoid(logits).numpy()

        base = float(yte.mean())
        acc  = float(((proba >= 0.5).astype(float) == yte).mean())

        # AUC (Mann-Whitney) sin sklearn
        pos, neg = proba[yte == 1], proba[yte == 0]
        auc = float("nan")
        if len(pos) and len(neg):
            ranks = np.argsort(np.argsort(proba))
            auc = (ranks[yte == 1].sum() - len(pos) * (len(pos) - 1) / 2) / (len(pos) * len(neg))

        print("── Resultados sobre datos NUNCA vistos ───────────────────────")
        print(f"  Muestras test: {len(yte)}  |  base rate (WR aleatorio): {base:.1%}")
        print(f"  Accuracy @0.5: {acc:.1%}   AUC: {auc:.3f}  "
              f"({'sin poder' if auc < 0.53 else 'algo de señal' if auc < 0.57 else 'señal real'})")
        print("\n  Win-rate por cuartil de probabilidad de la red:")
        buckets = _wr_by_bucket(proba, yte, 4)
        for lo, hi, n, wr in buckets:
            bar = "█" * int(wr * 20)
            print(f"    proba {lo:.2f}-{hi:.2f}: n={n:<5} WR={wr:5.1%}  {bar}")

        edge = buckets[-1][3] - buckets[0][3] if len(buckets) >= 2 else 0.0
        print(f"\n  Edge (WR cuartil alto − bajo): {edge:+.1%}")

        # ── Veredicto ─────────────────────────────────────────────
        print("\n" + "═" * 64)
        if auc >= 0.55 and edge >= 0.08:
            verdict = ("✅ LA RED TIENE VENTAJA REAL OOS → activar como confluencia "
                       "suave es razonable (influence:true, sin veto al principio).")
        elif auc >= 0.53 and edge >= 0.05:
            verdict = ("🟡 Señal débil pero positiva → se puede activar SOLO como "
                       "confluencia suave (conf_bonus bajo), NUNCA como veto.")
        else:
            verdict = ("⛔ La red NO bate al azar fuera de muestra → NO activar. "
                       "Dejar en modo sombra y reentrenar con más datos/features.")
        print("VEREDICTO:", verdict)
        print("═" * 64)

    finally:
        # Restaurar el modelo en vivo intacto
        if had_model:
            shutil.move(MODEL_PATH + ".bak", MODEL_PATH)
            shutil.move(SCALER_PATH + ".bak", SCALER_PATH)
            print("\n(Modelo en vivo restaurado — no se tocó logs/neural_model.pt)")
        else:
            for p in (MODEL_PATH, SCALER_PATH):
                if os.path.exists(p):
                    os.remove(p)


if __name__ == "__main__":
    main()
