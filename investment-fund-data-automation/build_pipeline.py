"""
build_pipeline.py — end-to-end: PDFs -> parse -> dedup -> FX -> Azure SQL (approved schema).

Schema table names/columns follow the data-team's approved schema proposal
(approved 2026-06-27), with the team lead's review feedback applied:
  1. CAD/USD stay separate tables (Holdings_CAD / Holdings_USD), kept in sync.
  2. Dedup: natural key = account_number + statement_month. Re-running a
     statement deletes its old statement_id's rows first (delete-and-reload).
  3. Reinvested dividends: amount columns stay NULL, flagged in ParseWarnings
     as a known MVP gap (DRIP income excluded from dividend totals).
  4. fx_date_used follows the "on or before statement_date" rule (fx.py).

Storage backend: Azure SQL Database (Serverless). Originally SQLite - moved
off it after confirming (2026-09-05) that a Linux Consumption Function App's
/home mount does not reliably persist data across invocations. Uses pymssql
rather than pyodbc so the Python worker needs no native ODBC driver install.
"""
import os
import datetime
from pathlib import Path
import pymssql
import parser as P
import fx as FX

AZSQL_SERVER = os.environ.get("AZSQL_SERVER")
AZSQL_DATABASE = os.environ.get("AZSQL_DATABASE", "portfolio_data")
AZSQL_USER = os.environ.get("AZSQL_USER")
AZSQL_PASSWORD = os.environ.get("AZSQL_PASSWORD")

# T-SQL has no CREATE TABLE IF NOT EXISTS, hence the OBJECT_ID guard per table.
# NVARCHAR sizes are generous-but-bounded (not MAX) so account_number/
# statement_month stay indexable for the dedup unique index below.
DDL_STATEMENTS = [
    """
    IF OBJECT_ID('Statements', 'U') IS NULL
    CREATE TABLE Statements (
        statement_id INT IDENTITY(1,1) PRIMARY KEY,
        account_number NVARCHAR(50), source_file NVARCHAR(255), statement_date NVARCHAR(20),
        statement_month NVARCHAR(10), period_start NVARCHAR(20), period_end NVARCHAR(20),
        file_path NVARCHAR(1000), broker_fx_rate_usdcad FLOAT, loaded_at NVARCHAR(40),
        parse_status NVARCHAR(20), notes NVARCHAR(500)
    )
    """,
    # SQL Server's default unique constraint/index allows only one NULL per
    # column combination (unlike SQLite/Postgres, where NULLs never collide).
    # A filtered index restores the SQLite-equivalent behavior for the rare
    # malformed-PDF case where account_number/statement_month come back NULL.
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Statements_acct_month')
    CREATE UNIQUE INDEX UQ_Statements_acct_month ON Statements(account_number, statement_month)
    WHERE account_number IS NOT NULL AND statement_month IS NOT NULL
    """,
    """
    IF OBJECT_ID('Holdings_CAD', 'U') IS NULL
    CREATE TABLE Holdings_CAD (
        holding_id INT IDENTITY(1,1) PRIMARY KEY, statement_id INT, statement_date NVARCHAR(20),
        asset_class NVARCHAR(50), security_name NVARCHAR(500), security_identifier NVARCHAR(500),
        currency NVARCHAR(3) DEFAULT 'CAD', quantity FLOAT, average_cost FLOAT,
        adjusted_book_value FLOAT, market_price FLOAT, market_value_native FLOAT,
        market_value_cad FLOAT, is_unpriced BIT, loaded_at NVARCHAR(40)
    )
    """,
    """
    IF OBJECT_ID('Holdings_USD', 'U') IS NULL
    CREATE TABLE Holdings_USD (
        holding_id INT IDENTITY(1,1) PRIMARY KEY, statement_id INT, statement_date NVARCHAR(20),
        asset_class NVARCHAR(50), security_name NVARCHAR(500), security_identifier NVARCHAR(500),
        currency NVARCHAR(3) DEFAULT 'USD', quantity FLOAT, average_cost FLOAT,
        adjusted_book_value FLOAT, market_price FLOAT, market_value_native FLOAT,
        fx_rate_usdcad FLOAT, fx_date_used NVARCHAR(20), market_value_cad FLOAT,
        is_unpriced BIT, loaded_at NVARCHAR(40)
    )
    """,
    """
    IF OBJECT_ID('Dividends', 'U') IS NULL
    CREATE TABLE Dividends (
        dividend_id INT IDENTITY(1,1) PRIMARY KEY, statement_id INT, statement_date NVARCHAR(20),
        transaction_date NVARCHAR(20), security_name NVARCHAR(500), security_identifier NVARCHAR(500),
        dividend_type NVARCHAR(30), currency NVARCHAR(3), dividend_amount_native FLOAT,
        fx_rate_usdcad FLOAT, fx_date_used NVARCHAR(20), dividend_amount_cad FLOAT,
        description NVARCHAR(2000), loaded_at NVARCHAR(40)
    )
    """,
    """
    IF OBJECT_ID('CashBalances', 'U') IS NULL
    CREATE TABLE CashBalances (
        cash_balance_id INT IDENTITY(1,1) PRIMARY KEY, statement_id INT, statement_date NVARCHAR(20),
        cash_currency NVARCHAR(3), balance_type NVARCHAR(20), cash_balance_native FLOAT,
        fx_rate_usdcad FLOAT, fx_date_used NVARCHAR(20), cash_balance_cad FLOAT, loaded_at NVARCHAR(40)
    )
    """,
    """
    IF OBJECT_ID('ParseWarnings', 'U') IS NULL
    CREATE TABLE ParseWarnings (
        warning_id INT IDENTITY(1,1) PRIMARY KEY, statement_id INT, source_file NVARCHAR(255),
        statement_date NVARCHAR(20), warning_type NVARCHAR(50), warning NVARCHAR(2000),
        severity NVARCHAR(20), loaded_at NVARCHAR(40)
    )
    """,
]


def now():
    return datetime.datetime.utcnow().isoformat()


def _exec(conn, sql, params=()):
    """pymssql connections don't have sqlite3's convenience .execute() shorthand -
    this restores that call shape so the rest of the file reads the same way."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def get_conn():
    conn = pymssql.connect(
        server=AZSQL_SERVER, user=AZSQL_USER, password=AZSQL_PASSWORD,
        database=AZSQL_DATABASE, autocommit=False,
    )
    cur = conn.cursor()
    for stmt in DDL_STATEMENTS:
        cur.execute(stmt)
    conn.commit()
    return conn


def delete_and_reload(conn, account_number, statement_month):
    """Dedup: a re-run of the same account+month wipes the old child rows first."""
    row = _exec(conn, "SELECT statement_id FROM Statements WHERE account_number=%s AND statement_month=%s",
                (account_number, statement_month)).fetchone()
    if row:
        sid = row[0]
        for tbl in ("Holdings_CAD", "Holdings_USD", "Dividends", "CashBalances", "ParseWarnings"):
            _exec(conn, f"DELETE FROM {tbl} WHERE statement_id=%s", (sid,))
        _exec(conn, "DELETE FROM Statements WHERE statement_id=%s", (sid,))
        conn.commit()
        return True
    return False


def log_warning(conn, statement_id, source_file, statement_date, wtype, text, severity="info"):
    _exec(conn,
        "INSERT INTO ParseWarnings (statement_id, source_file, statement_date, warning_type, "
        "warning, severity, loaded_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (statement_id, source_file, statement_date, wtype, text, severity, now()))


def load_statement(conn, pdf_path, filename=None):
    """filename overrides the recorded source_file name (e.g. when pdf_path is
    a temp file and the caller knows the original upload name); defaults to
    the path's own basename, preserving the batch-script behavior below."""
    filename = filename or Path(pdf_path).name
    parsed = P.parse_statement(pdf_path, filename)
    meta = parsed["meta"]
    ts = now()

    reran = delete_and_reload(conn, meta["account_number"], meta["statement_month"])

    cur = _exec(conn,
        "INSERT INTO Statements (account_number, source_file, statement_date, statement_month, "
        "period_start, period_end, file_path, broker_fx_rate_usdcad, loaded_at, parse_status, notes) "
        "OUTPUT INSERTED.statement_id "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (meta["account_number"], meta["source_file"], meta["statement_date"], meta["statement_month"],
         meta["period_start"], meta["period_end"], str(pdf_path), meta["broker_fx_rate_usdcad"],
         ts, "success", "reloaded (dedup)" if reran else None))
    sid = cur.fetchone()[0]

    # --- FX for this statement (USD side only) ---
    fx_rate, fx_date, fx_source, fx_warn = FX.get_fx_for_statement(
        meta["statement_date"], meta["broker_fx_rate_usdcad"])
    if fx_warn:
        log_warning(conn, sid, filename, meta["statement_date"], "fx_failure", fx_warn, "warning")

    # --- Holdings CAD ---
    for h in parsed["holdings_cad"]:
        _exec(conn,
            "INSERT INTO Holdings_CAD (statement_id, statement_date, asset_class, security_name, "
            "security_identifier, currency, quantity, average_cost, adjusted_book_value, market_price, "
            "market_value_native, market_value_cad, is_unpriced, loaded_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, meta["statement_date"], h["asset_class"], h["security_name"], h["security_name"], "CAD",
             h["quantity"], h["average_cost"], h["adjusted_book_value"], h["market_price"],
             h["market_value_native"], h["market_value_native"], int(h["is_unpriced"]), ts))
        if h["is_unpriced"]:
            log_warning(conn, sid, filename, meta["statement_date"], "unpriced_security",
                       f"{h['security_name']} is UNPRICED (CAD)", "info")

    # --- Holdings USD (with FX) ---
    for h in parsed["holdings_usd"]:
        mv_cad = round(h["market_value_native"] * fx_rate, 2) if (h["market_value_native"] is not None and fx_rate) else None
        _exec(conn,
            "INSERT INTO Holdings_USD (statement_id, statement_date, asset_class, security_name, "
            "security_identifier, currency, quantity, average_cost, adjusted_book_value, market_price, "
            "market_value_native, fx_rate_usdcad, fx_date_used, market_value_cad, is_unpriced, loaded_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, meta["statement_date"], h["asset_class"], h["security_name"], h["security_name"], "USD",
             h["quantity"], h["average_cost"], h["adjusted_book_value"], h["market_price"],
             h["market_value_native"], fx_rate, fx_date, mv_cad, int(h["is_unpriced"]), ts))
        if h["is_unpriced"]:
            log_warning(conn, sid, filename, meta["statement_date"], "unpriced_security",
                       f"{h['security_name']} is UNPRICED (USD)", "info")

    # --- Dividends ---
    n_reinvested = 0
    for d in parsed["dividends"]:
        is_usd = d["currency"] == "USD"
        rate_used = fx_rate if is_usd else 1.0
        date_used = fx_date if is_usd else meta["statement_date"]
        amt_cad = (round(d["dividend_amount_native"] * rate_used, 2)
                  if d["dividend_amount_native"] is not None else None)
        _exec(conn,
            "INSERT INTO Dividends (statement_id, statement_date, transaction_date, security_name, "
            "security_identifier, dividend_type, currency, dividend_amount_native, fx_rate_usdcad, "
            "fx_date_used, dividend_amount_cad, description, loaded_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, meta["statement_date"], d["transaction_date"], d["security_name"], d["security_name"],
             d["dividend_type"], d["currency"], d["dividend_amount_native"], rate_used, date_used,
             amt_cad, d["description"], ts))
        if d["dividend_type"] == "reinvested_dividend":
            n_reinvested += 1
    if n_reinvested:
        log_warning(conn, sid, filename, meta["statement_date"], "known_gap",
                   f"{n_reinvested} reinvested dividend line(s) have NULL native/CAD amounts "
                   f"(MVP gap agreed with the team lead 2026-06-27 — DRIP income excluded from dividend "
                   f"totals until reinvestment price/shares fields are added).", "info")

    # --- Cash balances ---
    for c in parsed["cash_balances"]:
        is_usd = c["currency"] == "USD"
        rate_used = fx_rate if is_usd else 1.0
        date_used = fx_date if is_usd else meta["statement_date"]
        cad_val = round(c["cash_balance_native"] * rate_used, 2) if c["cash_balance_native"] is not None else None
        _exec(conn,
            "INSERT INTO CashBalances (statement_id, statement_date, cash_currency, balance_type, "
            "cash_balance_native, fx_rate_usdcad, fx_date_used, cash_balance_cad, loaded_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sid, meta["statement_date"], c["currency"], c["balance_type"], c["cash_balance_native"],
             rate_used, date_used, cad_val, ts))

    # --- Reconciliation check (extraction integrity, not just FX) ---
    cad_total = sum(h["market_value_native"] for h in parsed["holdings_cad"] if h["market_value_native"] is not None)
    cad_total += sum(p["amount_native"] for p in parsed["pending"] if p["currency"] == "CAD")
    usd_total = sum(h["market_value_native"] for h in parsed["holdings_usd"] if h["market_value_native"] is not None)
    usd_total += sum(p["amount_native"] for p in parsed["pending"] if p["currency"] == "USD")

    import re as _re
    full_text = "\n".join(l for pg in P.get_lines(pdf_path) for l in pg)
    cad_stated = _re.search(r"Canadian Dollar\s+([\d,]+)\s+([\d,]+)", full_text)
    usd_stated = _re.search(r"U\.S\. Dollar\s+([\d,]+)", full_text)
    ok = True
    if cad_stated and abs(round(cad_total) - int(cad_stated.group(1).replace(",", ""))) > 2:
        ok = False
        log_warning(conn, sid, filename, meta["statement_date"], "layout_change",
                   f"CAD holdings do not reconcile to statement total (parsed {round(cad_total)} vs "
                   f"stated {cad_stated.group(1)}).", "error")
    if usd_stated and abs(round(usd_total) - int(usd_stated.group(1).replace(",", ""))) > 2:
        ok = False
        log_warning(conn, sid, filename, meta["statement_date"], "layout_change",
                   f"USD holdings do not reconcile to statement total (parsed {round(usd_total)} vs "
                   f"stated {usd_stated.group(1)}).", "error")
    _exec(conn, "UPDATE Statements SET parse_status=%s WHERE statement_id=%s",
                ("success" if ok else "partial", sid))

    conn.commit()
    return sid, ok, fx_source, fx_warn is not None


if __name__ == "__main__":
    # Usage: python build_pipeline.py <folder-of-statement-PDFs> [--reset]
    # --reset empties every table first (full rebuild). Without it, statements
    # are loaded with the normal delete-and-reload dedup, so it is safe to re-run.
    import sys
    if len(sys.argv) < 2:
        sys.exit("Usage: python build_pipeline.py <folder-of-statement-PDFs> [--reset]")
    conn = get_conn()
    cur = conn.cursor()
    if "--reset" in sys.argv[2:]:
        for tbl in ("Holdings_CAD", "Holdings_USD", "Dividends", "CashBalances", "ParseWarnings", "Statements"):
            cur.execute(f"DELETE FROM {tbl}")
        conn.commit()
    files = sorted(Path(sys.argv[1]).glob("*.pdf"))
    print(f"Loading {len(files)} statements into {AZSQL_DATABASE}\n")
    for f in files:
        sid, ok, fx_source, used_fallback = load_statement(conn, f)
        flag = "OK" if ok else "*** RECONCILIATION FAILED ***"
        fb = "  [FX fallback used]" if used_fallback else ""
        print(f"  statement_id={sid:<3} {f.name:<26} reconciled={flag}{fb}")
    conn.close()
    print("\nDone.")
