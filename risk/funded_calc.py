"""
Calculadora de riesgo para cuenta FONDEADA con trailing drawdown.

Problema que resuelve: en una cuenta de fondeo el límite NO es tu balance, es el
COLCHÓN de drawdown que te queda (room). Arriesgar 1% del balance puede ser el 50%
del colchón restante → quemas la cuenta en 2 trades. Esta herramienta dimensiona el
lote contra el ROOM real, no contra el balance, y te dice si el trade es viable.

Uso (no necesita MT5):
  python risk/funded_calc.py --room 20 --symbol XAUUSD --entry 4195 --sl 4210
  python risk/funded_calc.py --room 120 --symbol US30 --entry 39000 --sl 38950 --riskpct 10
  python risk/funded_calc.py --balance 2000 --highest 2000 --ddpct 6 --symbol XAUUSD --sl-usd 8

Argumentos:
  --room       Colchón de drawdown restante en USD (lo más directo). Si no lo pasas,
               se calcula con --balance/--highest/--ddpct (regla trailing FundedNext).
  --riskpct    % del ROOM a arriesgar en este trade (default 10). En cuenta fondeada
               conviene 5-10% del room, NO 1% del balance.
  --symbol --entry --sl   Instrumento y niveles. Alternativa: --sl-usd (riesgo por 0.01
               lotes ya en USD) si no tienes los precios a mano.
"""

import sys
import os
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _risk_per_lot(symbol, sl_dist, contracts):
    """USD que arriesga 1.0 lote para una distancia de SL en precio."""
    sym = symbol.upper()
    if any(x in sym for x in ("XAU", "GOLD")):
        return sl_dist * 100.0
    if sym in contracts:
        c = contracts[sym]; pip = float(c.get("pip", 1.0)) or 1.0
        return (sl_dist / pip) * float(c.get("value_per_lot", 1.0))
    if "JPY" in sym:
        return (sl_dist / 0.01) * 9.0
    return (sl_dist / 0.0001) * 10.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", type=float, default=None)
    ap.add_argument("--balance", type=float, default=2000)
    ap.add_argument("--highest", type=float, default=None)
    ap.add_argument("--ddpct", type=float, default=6.0)
    ap.add_argument("--riskpct", type=float, default=10.0, help="%% del room por trade")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--sl", type=float, default=None)
    ap.add_argument("--sl-usd", dest="sl_usd", type=float, default=None,
                    help="riesgo por 0.01 lotes en USD (si no das entry/sl)")
    args = ap.parse_args()

    # Cargar contracts del config si existe
    contracts = {}
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(_PROJECT_ROOT, "config.yaml"),
                                  encoding="utf-8"))
        contracts = (cfg.get("symbols", {}) or {}).get("contracts", {}) or {}
    except Exception:
        pass

    # Room
    if args.room is not None:
        room = args.room
    else:
        highest = args.highest if args.highest is not None else args.balance
        floor = min(args.balance, highest - args.balance * args.ddpct / 100.0)
        # equity ≈ highest si no se da; room = highest - floor (máximo teórico)
        room = highest - floor
    risk_budget = room * args.riskpct / 100.0

    # Riesgo por 0.01 lotes
    if args.sl_usd is not None:
        risk_001 = args.sl_usd
        sl_dist = None
        rpl = risk_001 / 0.01
    elif args.entry is not None and args.sl is not None:
        sl_dist = abs(args.entry - args.sl)
        rpl = _risk_per_lot(args.symbol, sl_dist, contracts)
        risk_001 = rpl * 0.01
    else:
        print("ERROR: da --entry y --sl (o --sl-usd).")
        sys.exit(1)

    print("=" * 56)
    print(f"  CALCULADORA RIESGO FONDEADA — {args.symbol}")
    print("=" * 56)
    print(f"  Colchón (room) restante : ${room:,.2f}")
    print(f"  Presupuesto de riesgo   : ${risk_budget:,.2f}  ({args.riskpct:.0f}% del room)")
    if sl_dist is not None:
        print(f"  Distancia SL            : {sl_dist:.5f}  ({args.symbol})")
    print(f"  Riesgo con lote MÍN 0.01: ${risk_001:,.2f}  "
          f"({risk_001/room*100:.1f}% del room)")
    print("-" * 56)

    # Lote máximo que respeta el presupuesto
    max_lot = risk_budget / rpl if rpl > 0 else 0
    max_lot = max(0.0, (int(max_lot / 0.01)) * 0.01)

    if risk_001 > risk_budget:
        # Ni el lote mínimo cabe → cuánto tendría que acortarse el SL
        max_sl_usd_001 = risk_budget  # con 0.01, risk = sl_dist * (rpl/sl_dist)*0.01...
        print(f"  ❌ NO VIABLE: el lote mínimo (0.01) arriesga ${risk_001:.2f}, "
              f"más que tu presupuesto ${risk_budget:.2f}.")
        print(f"     → Para que 0.01 entre en el {args.riskpct:.0f}% del room, el SL "
              f"no puede arriesgar más de ${risk_budget:.2f}.")
        if sl_dist is not None:
            # distancia SL máxima admisible con 0.01
            max_dist = risk_budget / (rpl / sl_dist) / 0.01
            print(f"       Eso es un SL de máx ~{max_dist:.5f} de distancia "
                  f"(ahora {sl_dist:.5f}).")
        print(f"     Honesto: con tan poco colchón, casi ningún trade normal es seguro.")
    else:
        print(f"  ✅ LOTE MÁXIMO: {max_lot:.2f}  → riesgo ${max_lot*rpl:,.2f} "
              f"({max_lot*rpl/room*100:.0f}% del room)")
        n_trades = int(room / (max_lot * rpl)) if max_lot * rpl > 0 else 0
        print(f"     Aguantarías ~{n_trades} pérdidas seguidas a ese lote antes de quemar.")
    print("=" * 56)
    print("  Regla: en fondeo dimensiona contra el ROOM, no el balance. 1% del balance")
    print("  puede ser 50% del room. Mantén el riesgo/trade en 5-10% del room máximo.")
    print("=" * 56)


if __name__ == "__main__":
    main()
