"""
Neural Engine — Red neuronal LSTM (PyTorch) en MODO SOMBRA.

Pedido del usuario (2026-06-17): una red neuronal profunda que aprenda de todo.
Honestidad cuantitativa: con ~245 trades en vivo una LSTM sobreajusta. Solución:
  - Entrena sobre SECUENCIAS DE BARRAS H1 etiquetadas con triple-barrier
    (miles de muestras de varios años de histórico MT5), no sobre los 245 trades.
  - Salvaguardas anti-sobreajuste: arquitectura pequeña, dropout, weight decay (L2),
    early stopping y split walk-forward (validación = último 20% temporal, sin shuffle).
  - MODO SOMBRA: en vivo predice `neural_proba` y se REGISTRA, pero NO veta ni
    modifica señales hasta demostrar que bate al ensemble out-of-sample. Protege
    el sistema que ya funciona.

Etiqueta (triple-barrier): para cada barra y dirección, ¿un trade con SL=1.5·ATR
y TP=rr·SL resuelve en WIN antes que en LOSS dentro de `horizon` barras? → 1/0.

Uso:
    python ml/neural_engine.py --train --days 900     # entrena desde MT5
    (en vivo se carga solo si existe logs/neural_model.pt)
"""

import os
import sys
import json
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH  = os.path.join(_PROJECT_ROOT, "logs", "neural_model.pt")
SCALER_PATH = os.path.join(_PROJECT_ROOT, "logs", "neural_scaler.json")

SEQ_LEN   = 24      # 24 barras H1 = 1 día de contexto
HORIZON   = 24      # ventana de resolución del triple-barrier (barras)
K_SL      = 1.5     # SL = 1.5 × ATR (mismo que la estrategia)
RR        = 2.0     # TP = 2R (mismo que min_rr)
N_BAR_FEATS = 14    # features de contexto por barra (sin contar el flag de dirección)
N_FEATURES  = 15    # 14 de barra + 1 flag de dirección


# ── Features por barra ──────────────────────────────────────────────

def _bar_features(df: pd.DataFrame) -> np.ndarray:
    """
    Matriz [n_bars, 14] con features de contexto por barra. Todas usan SOLO
    información pasada/actual (sin lookahead) y se calculan idénticas en
    histórico y en vivo (cero datos externos → cero leakage):

      0  log_return                 8  pendiente EMA20 (3 barras)/atr
      1  range/atr                  9  volatilidad relativa (atr/atr_ma50)
      2  rsi/100                    10 momentum 6h /atr
      3  (close-ema20)/atr          11 posición en rango 20b [0,1]
      4  (close-ema50)/atr          12 volumen relativo (vol/vol_ma20)
      5  body/atr                   13 hora del día (sin)
      6  (close-ema200)/atr  →HTF   14 hora del día (cos)
      7  —                          (dir flag lo añade build_dataset)

    NOTA (2026-06-18): se probó añadir 3 features de DXY (driver macro del oro)
    pero NO mejoró OOS (AUC 0.535→0.529): el precio del oro ya incorpora el DXY.
    Se revirtió. Requiere: open, high, low, close, volume, atr, rsi,
    ema_20/50/200, time.
    """
    c   = df["close"].astype(float)
    o   = df["open"].astype(float)
    h   = df["high"].astype(float)
    l   = df["low"].astype(float)
    vol = df["volume"].astype(float)
    atr = df["atr"].astype(float).replace(0, np.nan)

    log_ret = np.log(c / c.shift(1))
    rng     = (h - l) / atr
    rsi01   = df["rsi"].astype(float) / 100.0
    e20     = (c - df["ema_20"]) / atr
    e50     = (c - df["ema_50"]) / atr
    body    = (c - o) / atr
    e200    = (c - df["ema_200"]) / atr                       # contexto tendencia HTF
    slope20 = (df["ema_20"] - df["ema_20"].shift(3)) / atr     # pendiente reciente
    rel_vol = atr / atr.rolling(50, min_periods=10).mean()     # régimen de volatilidad
    mom6    = (c - c.shift(6)) / atr                           # momentum 6h
    hi20    = h.rolling(20, min_periods=5).max()
    lo20    = l.rolling(20, min_periods=5).min()
    rng_pos = (c - lo20) / (hi20 - lo20).replace(0, np.nan)    # dónde está en el rango
    rvolume = vol / vol.rolling(20, min_periods=5).mean()      # volumen relativo

    if "time" in df.columns:
        hour = pd.to_datetime(df["time"]).dt.hour.astype(float)
    else:
        hour = pd.Series(np.zeros(len(df)), index=df.index)
    hour_sin = np.sin(2 * np.pi * hour / 24.0)
    hour_cos = np.cos(2 * np.pi * hour / 24.0)

    feats = np.column_stack([
        log_ret.fillna(0).values,
        rng.fillna(0).values,
        rsi01.fillna(0.5).values,
        e20.fillna(0).values,
        e50.fillna(0).values,
        body.fillna(0).values,
        e200.fillna(0).values,
        slope20.fillna(0).values,
        rel_vol.fillna(1.0).values,
        mom6.fillna(0).values,
        rng_pos.fillna(0.5).clip(0, 1).values,
        rvolume.fillna(1.0).clip(0, 5).values,
        hour_sin.values,
        hour_cos.values,
    ]).astype(np.float32)
    return feats


def _triple_barrier_label(highs, lows, closes, atr, t, direction, horizon, k_sl, rr):
    """1 si el trade gana (TP antes que SL) en `horizon` barras, 0 si pierde/no resuelve."""
    entry = closes[t]
    risk = k_sl * atr[t]
    if not np.isfinite(risk) or risk <= 0:
        return None
    if direction == 1:  # LONG
        tp, sl = entry + rr * risk, entry - risk
    else:               # SHORT
        tp, sl = entry - rr * risk, entry + risk
    end = min(t + horizon, len(closes) - 1)
    for j in range(t + 1, end + 1):
        hi, lo = highs[j], lows[j]
        if direction == 1:
            if lo <= sl:  # toca SL primero (conservador: SL prioritario si ambos)
                return 0
            if hi >= tp:
                return 1
        else:
            if hi >= sl:
                return 0
            if lo <= tp:
                return 1
    return 0  # no resolvió → tratamos como no-ganador


def build_dataset(df: pd.DataFrame, seq_len=SEQ_LEN, horizon=HORIZON,
                  k_sl=K_SL, rr=RR):
    """
    Devuelve X [m, seq_len, 7], y [m]. Cada barra genera 2 muestras (LONG y SHORT).
    """
    feats = _bar_features(df)
    highs = df["high"].astype(float).values
    lows  = df["low"].astype(float).values
    closes = df["close"].astype(float).values
    atr = df["atr"].astype(float).values

    X, y = [], []
    for t in range(seq_len, len(df) - horizon):
        seq = feats[t - seq_len:t]  # [seq_len, 6]
        for direction in (1, 0):    # LONG, SHORT
            label = _triple_barrier_label(highs, lows, closes, atr, t,
                                          direction, horizon, k_sl, rr)
            if label is None:
                continue
            dir_col = np.full((seq_len, 1), float(direction), dtype=np.float32)
            X.append(np.hstack([seq, dir_col]))
            y.append(label)
    if not X:
        return None, None
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# ── Modelo ──────────────────────────────────────────────────────────

def _build_model(dropout=0.3):
    import torch.nn as nn

    class GoldLSTM(nn.Module):
        def __init__(self, n_features=N_FEATURES, hidden=32):
            super().__init__()
            self.lstm = nn.LSTM(n_features, hidden, batch_first=True, num_layers=1)
            self.drop = nn.Dropout(dropout)
            self.fc1 = nn.Linear(hidden, 16)
            self.act = nn.ReLU()
            self.fc2 = nn.Linear(16, 1)

        def forward(self, x):
            out, _ = self.lstm(x)
            h = out[:, -1, :]            # último paso temporal
            h = self.drop(h)
            h = self.act(self.fc1(h))
            return self.fc2(h).squeeze(-1)  # logit

    return GoldLSTM()


class NeuralEngine:
    """Modo sombra: predice neural_proba; NO veta señales."""

    def __init__(self):
        self.model = None
        self.mean = None
        self.std = None
        self._torch = None
        self._load()

    def available(self) -> bool:
        return self.model is not None

    # ── Persistencia ────────────────────────────────────────────────

    def _load(self):
        if not (os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH)):
            return
        try:
            import torch
            self._torch = torch
            with open(SCALER_PATH, encoding="utf-8") as f:
                sc = json.load(f)
            self.mean = np.array(sc["mean"], dtype=np.float32)
            self.std = np.array(sc["std"], dtype=np.float32)
            self.model = _build_model()
            self.model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
            self.model.eval()
            logger.info("Red neuronal LSTM cargada (modo sombra)")
        except Exception as e:
            logger.warning(f"No se pudo cargar la red neuronal: {e}")
            self.model = None

    def _save(self, mean, std):
        import torch
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        torch.save(self.model.state_dict(), MODEL_PATH)
        with open(SCALER_PATH, "w", encoding="utf-8") as f:
            json.dump({"mean": mean.tolist(), "std": std.tolist()}, f)

    # ── Entrenamiento (walk-forward) ────────────────────────────────

    def train(self, df: pd.DataFrame, epochs=40, lr=1e-3, batch=128) -> dict:
        import torch
        import torch.nn as nn
        self._torch = torch

        X, y = build_dataset(df)
        if X is None or len(X) < 500:
            return {"trained": False, "reason": f"pocas muestras ({0 if X is None else len(X)})"}

        # Split walk-forward (sin shuffle): primeras 80% train, últimas 20% val
        n = len(X)
        split = int(n * 0.8)
        Xtr, Xval = X[:split], X[split:]
        ytr, yval = y[:split], y[split:]

        # Escalado por feature (con stats de train) — la dir-flag (col 6) no se escala
        mean = Xtr.reshape(-1, N_FEATURES).mean(axis=0)
        std = Xtr.reshape(-1, N_FEATURES).std(axis=0) + 1e-6
        mean[N_FEATURES - 1], std[N_FEATURES - 1] = 0.0, 1.0  # no escalar el flag de dirección
        Xtr = (Xtr - mean) / std
        Xval = (Xval - mean) / std

        dev = "cpu"
        self.model = _build_model().to(dev)
        # pos_weight para balancear clases
        pos = float(ytr.sum()); neg = float(len(ytr) - pos)
        pw = torch.tensor([neg / max(pos, 1)], dtype=torch.float32)
        crit = nn.BCEWithLogitsLoss(pos_weight=pw)
        opt = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)

        Xtr_t = torch.tensor(Xtr); ytr_t = torch.tensor(ytr)
        Xval_t = torch.tensor(Xval); yval_t = torch.tensor(yval)

        best_val = float("inf"); best_state = None; patience = 0
        for ep in range(epochs):
            self.model.train()
            perm = torch.randperm(len(Xtr_t))
            for i in range(0, len(Xtr_t), batch):
                idx = perm[i:i + batch]
                opt.zero_grad()
                logit = self.model(Xtr_t[idx])
                loss = crit(logit, ytr_t[idx])
                loss.backward()
                opt.step()
            # Validación
            self.model.eval()
            with torch.no_grad():
                vlogit = self.model(Xval_t)
                vloss = float(crit(vlogit, yval_t))
                vpred = (torch.sigmoid(vlogit) >= 0.5).float()
                vacc = float((vpred == yval_t).float().mean())
            if vloss < best_val - 1e-4:
                best_val = vloss
                best_state = {k: v.clone() for k, v in self.model.state_dict().items()}
                patience = 0
            else:
                patience += 1
                if patience >= 6:   # early stopping
                    break

        if best_state is not None:
            self.model.load_state_dict(best_state)
        self.model.eval()
        self.mean, self.std = mean, std
        self._save(mean, std)

        return {
            "trained": True,
            "samples": int(n),
            "train": int(split),
            "val": int(n - split),
            "val_loss": round(best_val, 4),
            "val_acc": round(vacc, 3),
            "win_rate_label": round(float(y.mean()), 3),
        }

    # ── Predicción (modo sombra) ────────────────────────────────────

    def predict_proba(self, df_recent: pd.DataFrame, direction: str) -> float:
        """
        P(win) de la red para la dirección dada, dado el contexto de barras H1
        recientes (necesita >= SEQ_LEN barras con indicadores). 0.5 si no hay modelo.
        """
        if self.model is None:
            return 0.5
        try:
            torch = self._torch
            feats = _bar_features(df_recent)
            if len(feats) < SEQ_LEN:
                return 0.5
            seq = feats[-SEQ_LEN:]
            dflag = 1.0 if direction == "BUY" else 0.0
            seq = np.hstack([seq, np.full((SEQ_LEN, 1), dflag, dtype=np.float32)])
            seq = (seq - self.mean) / self.std
            x = torch.tensor(seq[None, :, :], dtype=torch.float32)
            with torch.no_grad():
                p = float(torch.sigmoid(self.model(x)).item())
            return float(np.clip(p, 0.0, 1.0))
        except Exception as e:
            logger.debug(f"Neural predict error: {e}")
            return 0.5


# ── CLI de entrenamiento ────────────────────────────────────────────

def _main():
    import argparse
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--days", type=int, default=900)
    args = ap.parse_args()

    import yaml
    from data.mt5_connector import MT5Connector
    from analysis.indicators import add_indicators

    cfg = yaml.safe_load(open(os.path.join(_PROJECT_ROOT, "config.yaml"), encoding="utf-8"))
    mt5c = MT5Connector(cfg)
    if not mt5c.connect():
        print("ERROR: MT5 no disponible"); return
    # ~24 barras H1/día → days*24 barras (cap razonable)
    n_bars = min(args.days * 24, 50000)
    df = mt5c.get_rates("XAUUSD", "H1", n_bars)
    mt5c.disconnect()
    if df.empty:
        print("ERROR: sin datos H1"); return
    df = add_indicators(df)
    print(f"Barras H1: {len(df)} | entrenando LSTM (modo sombra)...")
    ne = NeuralEngine()
    res = ne.train(df)
    print("Resultado:", res)


if __name__ == "__main__":
    _main()
