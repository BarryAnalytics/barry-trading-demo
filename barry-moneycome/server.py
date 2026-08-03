from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("BARRY_COCKPIT_PORT", "8765"))
SYMBOLS = ["MSFT", "AAPL", "NVDA", "AMZN", "GOOGL"]
NAMES = {"MSFT":"Microsoft","AAPL":"Apple","NVDA":"NVIDIA","AMZN":"Amazon","GOOGL":"Alphabet"}

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def backend_candidates():
    env = os.environ.get("BARRY_SENTINEL_BACKEND")
    if env:
        yield Path(env).expanduser()
    yield (
        Path.home()
        / "Documents"
        / "Barry Data & Analytics"
        / "BARRY SENTINEL"
        / "01_Aktuelle_Arbeitsversion"
        / "Barry_Sentinel_V7_IBKR_Paper"
    )
    # Optional fallback: search a few sensible levels under Documents.
    docs = Path.home() / "Documents"
    if docs.exists():
        try:
            for p in docs.glob("**/Barry_Sentinel_V7_IBKR_Paper"):
                yield p
        except Exception:
            pass

def find_backend():
    seen = set()
    for p in backend_candidates():
        try:
            p = p.resolve()
        except Exception:
            continue
        if str(p).lower() in seen:
            continue
        seen.add(str(p).lower())
        if p.is_dir() and (p / "config.paper.json").exists():
            return p
    return None

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None

def latest_json(directory, pattern="*.json", recursive=False):
    if not directory or not directory.exists():
        return None
    it = directory.rglob(pattern) if recursive else directory.glob(pattern)
    files = [p for p in it if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None

def get_any(obj, names, default=None):
    if not isinstance(obj, dict):
        return default
    low = {str(k).lower(): v for k, v in obj.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return default

def number(v, default=None):
    try:
        if v is None or isinstance(v, bool):
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def age_seconds(path):
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except Exception:
        return None

def walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_dicts(v)

def walk_lists(obj):
    if isinstance(obj, list):
        yield obj
        for v in obj:
            yield from walk_lists(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_lists(v)

def symbol_records(obj):
    out = {s: [] for s in SYMBOLS}
    if obj is None:
        return out
    for d in walk_dicts(obj):
        sym = get_any(d, ["symbol", "ticker", "contract_symbol", "underlying"])
        if isinstance(sym, str) and sym.upper().strip() in out:
            out[sym.upper().strip()].append(d)
        for s in SYMBOLS:
            v = d.get(s)
            if isinstance(v, dict):
                out[s].append(v)
    return out

def first_value(records, names):
    for d in records:
        v = get_any(d, names)
        if v is not None:
            return v
    return None

def normalize_signal(v):
    u = str(v or "").upper()
    if "KAUF" in u or u == "BUY":
        return "KAUF"
    if "VERKAUF" in u or u == "SELL":
        return "VERKAUF"
    if "BLOCK" in u:
        return "BLOCKIERT"
    if "WAIT" in u or "WART" in u or "HOLD" in u:
        return "WARTEN"
    return "WARTEN"

def status_from_obj(obj, keys, fallback="UNKNOWN"):
    if not isinstance(obj, dict):
        return fallback
    v = get_any(obj, keys)
    if v is not None:
        return str(v)
    for d in walk_dicts(obj):
        v = get_any(d, keys)
        if v is not None:
            return str(v)
    return fallback

def history_for_symbol(obj, symbol):
    if obj is None:
        return []
    candidates = []
    for lst in walk_lists(obj):
        pts = []
        symbol_seen = False
        for item in lst:
            if isinstance(item, dict):
                sym = get_any(item, ["symbol","ticker"])
                if isinstance(sym, str) and sym.upper() == symbol:
                    symbol_seen = True
                close = number(get_any(item, ["close","last","price","reference_price"]))
                if close is not None:
                    pts.append(close)
            elif isinstance(item, (int,float)):
                pts.append(float(item))
        if len(pts) >= 8 and (symbol_seen or len(pts) >= 24):
            candidates.append(pts)
    if candidates:
        return max(candidates, key=len)[-256:]
    return []

def extract_positions(ledger, market_assets):
    positions = []
    if ledger is None:
        return positions
    for d in walk_dicts(ledger):
        sym = get_any(d, ["symbol","ticker"])
        if not isinstance(sym, str) or sym.upper() not in SYMBOLS:
            continue
        qty = number(get_any(d, ["quantity","qty","shares","position","position_qty"]))
        if qty is None or qty <= 0:
            continue
        entry = number(get_any(d, ["entry_price","average_cost","avg_cost","cost_basis_per_share","price"]))
        current = number((market_assets.get(sym.upper()) or {}).get("price"))
        pnl = None
        if entry is not None and current is not None:
            pnl = (current-entry)*qty
        rec = {
            "symbol": sym.upper(),
            "quantity": qty,
            "entry_price": entry,
            "current_price": current,
            "pnl": pnl,
            "status": str(get_any(d, ["status","state"], "SHADOW"))
        }
        if not any(x["symbol"]==rec["symbol"] for x in positions):
            positions.append(rec)
    return positions[:10]

def read_status():
    backend = find_backend()
    base = {
        "timestamp": utc_now(),
        "backend_found": bool(backend),
        "backend_path": str(backend) if backend else None,
        "assets": {s: {"symbol":s,"name":NAMES[s],"signal":"BLOCKIERT","history":[]} for s in SYMBOLS},
        "capital": {},
        "risk": {},
        "system": {
            "health":"UNKNOWN","runtime":"UNKNOWN","reconciliation":"UNKNOWN",
            "cost_guard":"UNKNOWN","market":"UNKNOWN","new_entries":"UNKNOWN"
        },
        "positions": [],
        "sources": {}
    }
    if not backend:
        return base

    data = backend / "data"
    config_p = backend / "config.paper.json"
    cfg = load_json(config_p) or {}
    base["sources"]["config"] = str(config_p)

    # Source discovery.
    source_paths = {
        "market": latest_json(data/"market"),
        "strategy": latest_json(data/"strategy"),
        "risk": (data/"risk"/"runtime_risk_status.json") if (data/"risk"/"runtime_risk_status.json").exists() else latest_json(data/"risk", recursive=True),
        "health": (data/"health"/"health_status.json") if (data/"health"/"health_status.json").exists() else latest_json(data/"health", recursive=True),
        "runtime": (data/"runtime"/"23_shadow_daemon_status.json") if (data/"runtime"/"23_shadow_daemon_status.json").exists() else latest_json(data/"runtime", recursive=True),
        "reconciliation": (data/"reconciliation"/"broker_reconciliation_status.json") if (data/"reconciliation"/"broker_reconciliation_status.json").exists() else latest_json(data/"reconciliation", recursive=True),
        "cost_guard": (data/"cost_guard"/"trade_cost_profitability_status.json") if (data/"cost_guard"/"trade_cost_profitability_status.json").exists() else latest_json(data/"cost_guard", recursive=True),
        "ledger": (data/"shadow"/"ledger"/"shadow_ledger.json") if (data/"shadow"/"ledger"/"shadow_ledger.json").exists() else latest_json(data/"shadow", recursive=True),
        "historical": latest_json(data/"historical", recursive=True),
    }
    for k,p in source_paths.items():
        if p and p.exists():
            base["sources"][k] = str(p)

    objs = {k: load_json(p) if p and p.exists() else None for k,p in source_paths.items()}
    market_obj, strategy_obj = objs["market"], objs["strategy"]
    market_records = symbol_records(market_obj)
    strat_records = symbol_records(strategy_obj)

    for s in SYMBOLS:
        mr, sr = market_records[s], strat_records[s]
        price = number(first_value(mr, ["last","last_price","price","reference_price","close"]))
        close = number(first_value(mr, ["close","close_price","previous_close"]))
        bid = number(first_value(mr, ["bid","bid_price"]))
        ask = number(first_value(mr, ["ask","ask_price"]))
        spread = number(first_value(mr, ["spread","spread_value"]))
        if spread is None and bid is not None and ask is not None:
            spread = ask-bid
        change_pct = number(first_value(mr, ["change_pct","change_percent","percent_change","pct_change"]))
        if change_pct is None and price is not None and close not in (None,0):
            change_pct = (price/close-1)*100
        signal = normalize_signal(first_value(sr, ["decision","signal","action","strategy_decision","status"]))
        reason = first_value(sr, ["reason","reason_text","explanation","decision_reason","message"])
        score = number(first_value(sr, ["score","signal_score","confidence","confidence_score"]))
        momentum = first_value(sr, ["momentum","momentum_state","momentum_label"])
        trend = first_value(sr, ["trend","trend_state","trend_label"])
        dtype = first_value(mr, ["data_type","market_data_type","type","status"])
        hist = history_for_symbol(objs["historical"], s)
        if not hist:
            hist = history_for_symbol(market_obj, s)
        base["assets"][s].update({
            "price":price,"close":close,"bid":bid,"ask":ask,"spread":spread,
            "change_pct":change_pct,"data_type":dtype,"signal":signal,"reason":reason,
            "score":score,"momentum":momentum,"trend":trend,"history":hist,
            "age_seconds": age_seconds(source_paths["market"]) if source_paths["market"] else None,
        })

    # System statuses.
    base["system"]["health"] = status_from_obj(objs["health"], ["overall_status","health","status"], "UNKNOWN")
    base["system"]["runtime"] = status_from_obj(objs["runtime"], ["state","status","daemon_status"], "UNKNOWN")
    base["system"]["reconciliation"] = status_from_obj(objs["reconciliation"], ["result","status","reconciliation_status"], "UNKNOWN")
    base["system"]["cost_guard"] = status_from_obj(objs["cost_guard"], ["result","status","overall_status"], "UNKNOWN")
    base["system"]["market"] = status_from_obj(market_obj, ["paper_analysis","paper_analysis_status","status","result"], "UNKNOWN")
    base["system"]["new_entries"] = status_from_obj(objs["risk"], ["new_entries","new_entries_status","result","status"], "UNKNOWN")

    # Risk values — search cfg first, then risk status.
    sources_for_limits = [cfg, objs["risk"] or {}]
    def find_num(names):
        for src in sources_for_limits:
            for d in walk_dicts(src):
                v = number(get_any(d, names))
                if v is not None:
                    return v
        return None

    base["risk"] = {
        "max_risk_per_trade": find_num(["max_risk_per_trade_eur","maximum_risk_per_trade_eur","max_risk_trade_eur","max_risk_per_trade"]),
        "max_daily_loss": find_num(["max_daily_loss_eur","maximum_daily_loss_eur","max_tagesverlust_eur","max_daily_loss"]),
        "max_weekly_loss": find_num(["max_weekly_loss_eur","maximum_weekly_loss_eur","max_wochenverlust_eur","max_weekly_loss"]),
        "max_drawdown": find_num(["max_total_drawdown_eur","maximum_total_drawdown_eur","max_drawdown_eur","max_drawdown"]),
        "max_trades_per_day": find_num(["max_trades_per_day","maximum_trades_per_day"]),
        "cooldown": status_from_obj(objs["risk"], ["cooldown","cooldown_active","cooldown_status"], "NEIN"),
    }

    ledger = objs["ledger"] or {}
    def ledger_num(names):
        for d in walk_dicts(ledger):
            v = number(get_any(d,names))
            if v is not None:
                return v
        return None
    strategy_cap = ledger_num(["strategy_capital","strategy_capital_eur","approved_strategy_capital_eur"])
    if strategy_cap is None:
        strategy_cap = find_num(["approved_strategy_capital_eur","strategy_capital_eur","strategy_capital"])
    min_res = find_num(["minimum_cash_reserve_eur","minimum_cash_reserve","cash_reserve_eur"])
    max_pos = find_num(["maximum_open_positions","max_open_positions"])
    base["positions"] = extract_positions(ledger, base["assets"])
    open_pos = len(base["positions"])
    invested = ledger_num(["invested_cost_basis","invested_eur","cost_basis_eur","invested"])
    cash = ledger_num(["cash_available","cash_available_eur","available_cash_eur","cash"])
    vault = ledger_num(["profit_vault","profit_vault_eur","treasury","treasury_eur"])
    tax = ledger_num(["tax_reserve","tax_reserve_eur","estimated_tax_reserve_eur"])
    trades_today = None
    for src in [objs["risk"] or {}, ledger]:
        for d in walk_dicts(src):
            trades_today = number(get_any(d, ["trades_today","daily_trades","trades_count_today"]))
            if trades_today is not None:
                break
        if trades_today is not None:
            break
    drawdown = None
    for d in walk_dicts(objs["risk"] or {}):
        drawdown = number(get_any(d, ["total_drawdown","drawdown_eur","current_drawdown","gesamtrueckgang"]))
        if drawdown is not None:
            break
    # Optional performance values if present in ledger/risk output.
    def find_perf(names):
        for src in [objs["risk"] or {}, ledger]:
            for d in walk_dicts(src):
                v = number(get_any(d, names))
                if v is not None:
                    return v
        return None

    day_realized = find_perf(["day_realized", "daily_realized_pnl", "realized_pnl_today", "today_realized", "daily_pnl"])
    week_realized = find_perf(["week_realized", "weekly_realized_pnl", "realized_pnl_week", "weekly_pnl"])
    fees = find_perf(["fees", "fees_eur", "total_fees", "commissions", "commissions_eur"])

    base["capital"] = {
        "strategy_capital":strategy_cap,
        "minimum_cash_reserve":min_res,
        "cash_available":cash,
        "invested":invested,
        "profit_vault":vault,
        "tax_reserve":tax,
        "open_positions":open_pos,
        "max_open_positions":int(max_pos) if max_pos is not None else None,
        "trades_today":int(trades_today) if trades_today is not None else None,
        "drawdown":drawdown,
        "day_realized":day_realized,
        "week_realized":week_realized,
        "fees":fees,
    }
    return base

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def log_message(self, fmt, *args):
        # Keep terminal quiet except errors.
        if args and str(args[1]).startswith(("4","5")):
            super().log_message(fmt, *args)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            payload = json.dumps(read_status(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/healthz":
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        return super().do_GET()

def main():
    backend = find_backend()
    print("="*86)
    print("BARRY SENTINEL V7 - LOCAL READ-ONLY COCKPIT")
    print("="*86)
    print(f"[COCKPIT] http://{HOST}:{PORT}")
    print(f"[BACKEND] {backend if backend else 'NICHT GEFUNDEN'}")
    print("[SICHERHEIT] Read-only. Keine Broker-Order. Keine Auszahlung. Kein Live-Trading.")
    print("[STOP] Zum Beenden dieses Cockpit-Servers: STRG+C")
    print("="*86)

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("\n[OK] Cockpit-Server beendet.")

if __name__ == "__main__":
    main()
