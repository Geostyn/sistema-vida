"""
Descubridor de símbolos MT5 — lista los nombres EXACTOS y el valor de lote de
los majors de forex y de los índices disponibles en tu broker, y genera el
bloque `symbols.contracts` listo para pegar en config.yaml.

Por qué hace falta: el nombre y el valor del tick de los índices varían por
broker (US30 / DJ30 / WS30 / US100 / NAS100 / US500 / SPX500…). Este script
los detecta con mt5.symbols_get() y calcula:
    value_per_lot = trade_tick_value / trade_tick_size   (USD por 1.0 de precio y lote)
con pip = 1.0, que es justo lo que espera risk_manager.calculate_lot_size.

Ejecutar (MT5 abierto):
    python backtest/discover_symbols.py
    python backtest/discover_symbols.py --all       (vuelca TODOS los símbolos)
"""

import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# Consola Windows (cp1252) no puede con caracteres → á … — forzar UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yaml
import MetaTrader5 as mt5
from data.mt5_connector import MT5Connector

# Patrones típicos de nombres por broker (se hace match por substring, mayúsculas)
FOREX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
         "NZDUSD", "EURGBP", "EURJPY", "GBPJPY"]
INDEX = ["US30", "DJ30", "WS30", "DOW",        # Dow Jones
         "US100", "NAS100", "NASDAQ", "USTEC",  # Nasdaq
         "US500", "SPX500", "SP500", "SPX",     # S&P 500
         "GER40", "DE40", "DAX",                # DAX (extra)
         "UK100", "FTSE"]                        # FTSE (extra)


def _match(name: str, patterns) -> bool:
    u = name.upper()
    return any(p in u for p in patterns)


def main():
    with open(os.path.join(_PROJECT_ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    conn = MT5Connector(cfg)
    if not conn.connect():
        print("ERROR: MT5 no disponible. ¿Está abierto y logueado?")
        sys.exit(1)

    all_syms = mt5.symbols_get()
    if not all_syms:
        print("ERROR: mt5.symbols_get() devolvió vacío.")
        conn.disconnect()
        sys.exit(1)

    dump_all = "--all" in sys.argv
    print(f"\nTotal símbolos en el broker: {len(all_syms)}\n")

    forex_found, index_found, contracts = [], [], {}

    print(f"{'SÍMBOLO':<14}{'TIPO':<8}{'digits':>7}{'tick_size':>12}"
          f"{'tick_value':>12}{'$/punto/lote':>14}{'spread':>8}")
    print("-" * 76)

    for s in sorted(all_syms, key=lambda x: x.name):
        name = s.name
        is_fx, is_ix = _match(name, FOREX), _match(name, INDEX)
        if not (is_fx or is_ix or dump_all):
            continue

        tick_size  = float(getattr(s, "trade_tick_size", 0) or 0)
        tick_value = float(getattr(s, "trade_tick_value", 0) or 0)
        vpl = (tick_value / tick_size) if tick_size > 0 else 0.0   # USD/1.0 precio/lote
        kind = "FOREX" if is_fx else ("INDEX" if is_ix else "OTRO")

        print(f"{name:<14}{kind:<8}{s.digits:>7}{tick_size:>12.5f}"
              f"{tick_value:>12.5f}{vpl:>14.3f}{s.spread:>8}")

        if is_fx:
            forex_found.append(name)
        elif is_ix:
            index_found.append(name)
            # pip=1.0 → value_per_lot = USD por 1.0 de movimiento de precio y lote
            contracts[name] = {"pip": 1.0, "value_per_lot": round(vpl, 4)}

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"Forex majors encontrados : {forex_found or '— ninguno'}")
    print(f"Índices encontrados      : {index_found or '— ninguno (prueba otro broker)'}")

    if contracts:
        print("\nPega esto en config.yaml bajo  symbols.contracts:  (verifica los números)")
        print("-" * 60)
        print(yaml.safe_dump({"contracts": contracts}, sort_keys=False,
                             allow_unicode=True))
        print("-" * 60)

    sugeridos = forex_found + index_found
    if sugeridos:
        print("Candidatos para  symbols.alt  (tras validar cada uno con backtest):")
        print(f"  alt: {sugeridos}")
    print("\nNOTA: forex no necesita 'contracts' (default 0.0001/$10, JPY 0.01/$9).")
    print("      Los índices SÍ — usa el bloque de arriba.\n")

    conn.disconnect()


if __name__ == "__main__":
    main()
