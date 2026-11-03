"""
fx.py — Bank of Canada FXUSDCAD rates.

Rule (agreed with the team lead, 2026-06-27): use the BoC rate on or before the statement
date, so weekend/holiday month-ends resolve the same way every time.

Live fetch requires internet access to bankofcanada.ca. This module tries the real API first; if it's
unreachable it falls back to the broker-printed FX rate already captured
in Statements.broker_fx_rate_usdcad, and logs a ParseWarning so the gap
is visible rather than silent. Re-run where bankofcanada.ca is reachable
to replace the fallback with the live BoC rate.
"""
import datetime
try:
    import requests
except ImportError:
    requests = None

BOC_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"


def fetch_boc_rates(start_date, end_date):
    """Returns {date_str: rate} for FXUSDCAD between start and end (inclusive), or {} on failure."""
    if requests is None:
        return {}
    try:
        resp = requests.get(BOC_URL, params={"start_date": start_date, "end_date": end_date}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        out = {}
        for obs in data.get("observations", []):
            d = obs.get("d")
            v = obs.get("FXUSDCAD", {}).get("v")
            if d and v:
                out[d] = float(v)
        return out
    except Exception:
        return {}


def rate_on_or_before(rates, target_date_str, lookback_days=7):
    """Backward-looking lookup: most recent rate on or before target_date."""
    target = datetime.date.fromisoformat(target_date_str)
    for i in range(lookback_days + 1):
        d = (target - datetime.timedelta(days=i)).isoformat()
        if d in rates:
            return d, rates[d]
    return None, None


def get_fx_for_statement(statement_date_str, broker_fx_rate=None):
    """
    Returns (fx_rate, fx_date_used, source, warning) for one statement date.
    Tries live BoC first; falls back to the broker-printed rate with a warning.
    """
    start = (datetime.date.fromisoformat(statement_date_str) - datetime.timedelta(days=7)).isoformat()
    rates = fetch_boc_rates(start, statement_date_str)
    fx_date, rate = rate_on_or_before(rates, statement_date_str)
    if rate is not None:
        return rate, fx_date, "BoC Valet API (FXUSDCAD)", None

    if broker_fx_rate is not None:
        warning = (f"BoC Valet API unreachable from this environment; used the statement's "
                   f"own broker-printed FX rate ({broker_fx_rate}) as a temporary stand-in for "
                   f"{statement_date_str}. Re-run in an environment with internet access to "
                   f"replace with the live BoC rate.")
        return broker_fx_rate, statement_date_str, "Broker-printed rate (fallback)", warning

    return None, None, None, f"No FX rate available (BoC unreachable, no broker rate) for {statement_date_str}"
