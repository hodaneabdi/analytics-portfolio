"""
migrate_sqlite_to_azuresql.py — one-time migration: copies every row from the
local SQLite portfolio.db into the Azure SQL database, statement by
statement.

Dedup: reuses build_pipeline.delete_and_reload(), the same function the
pipeline itself calls on every load. For each SQLite Statements row, it
deletes any existing Azure row (and children) sharing that account_number +
statement_month, then the SQLite version is inserted fresh. This is how the
pre-existing test row in Azure (or any other overlap) gets replaced rather
than erroring or duplicating - no special-casing needed, it's the same path
a normal re-upload already takes.

statement_id remapping: Azure's statement_id is an IDENTITY column, so a
migrated statement gets a new id there. Child-table rows are inserted with
that new id substituted for the old SQLite one.

Usage:
    AZSQL_SERVER=... AZSQL_DATABASE=portfolio_data AZSQL_USER=... AZSQL_PASSWORD=... \
    python3 migrate_sqlite_to_azuresql.py [path-to-portfolio.db]
"""
import sys
import sqlite3
import build_pipeline as BP

SQLITE_PATH = sys.argv[1] if len(sys.argv) > 1 else "portfolio.db"

STATEMENT_COLS = ["account_number", "source_file", "statement_date", "statement_month",
                  "period_start", "period_end", "file_path", "broker_fx_rate_usdcad",
                  "loaded_at", "parse_status", "notes"]

# statement_id is handled separately (remapped), so it's excluded from these lists.
CHILD_TABLES = {
    "Holdings_CAD": ["statement_date", "asset_class", "security_name", "security_identifier",
                     "currency", "quantity", "average_cost", "adjusted_book_value", "market_price",
                     "market_value_native", "market_value_cad", "is_unpriced", "loaded_at"],
    "Holdings_USD": ["statement_date", "asset_class", "security_name", "security_identifier",
                     "currency", "quantity", "average_cost", "adjusted_book_value", "market_price",
                     "market_value_native", "fx_rate_usdcad", "fx_date_used", "market_value_cad",
                     "is_unpriced", "loaded_at"],
    "Dividends": ["statement_date", "transaction_date", "security_name", "security_identifier",
                  "dividend_type", "currency", "dividend_amount_native", "fx_rate_usdcad",
                  "fx_date_used", "dividend_amount_cad", "description", "loaded_at"],
    "CashBalances": ["statement_date", "cash_currency", "balance_type", "cash_balance_native",
                     "fx_rate_usdcad", "fx_date_used", "cash_balance_cad", "loaded_at"],
    "ParseWarnings": ["source_file", "statement_date", "warning_type", "warning", "severity", "loaded_at"],
}


def main():
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row
    az = BP.get_conn()  # also ensures the Azure SQL schema exists (idempotent DDL)

    statements = sq.execute("SELECT * FROM Statements ORDER BY statement_id").fetchall()
    print(f"Found {len(statements)} statements in {SQLITE_PATH}\n")

    migrated, replaced_count, failed = 0, 0, []
    totals = {t: 0 for t in CHILD_TABLES}

    for s in statements:
        old_id = s["statement_id"]
        acct, month = s["account_number"], s["statement_month"]
        try:
            replaced = BP.delete_and_reload(az, acct, month)
            if replaced:
                replaced_count += 1

            cur = az.cursor()
            placeholders = ",".join(["%s"] * len(STATEMENT_COLS))
            cur.execute(
                f"INSERT INTO Statements ({','.join(STATEMENT_COLS)}) "
                f"OUTPUT INSERTED.statement_id VALUES ({placeholders})",
                tuple(s[c] for c in STATEMENT_COLS),
            )
            new_id = cur.fetchone()[0]

            for tbl, cols in CHILD_TABLES.items():
                rows = sq.execute(f"SELECT * FROM {tbl} WHERE statement_id=?", (old_id,)).fetchall()
                if not rows:
                    continue
                ph = ",".join(["%s"] * (len(cols) + 1))
                values = [tuple([new_id] + [r[c] for c in cols]) for r in rows]
                cur.executemany(
                    f"INSERT INTO {tbl} (statement_id,{','.join(cols)}) VALUES ({ph})",
                    values,
                )
                totals[tbl] += len(rows)

            az.commit()
            migrated += 1
            tag = "  (replaced existing Azure row)" if replaced else ""
            print(f"  [{migrated}/{len(statements)}] sqlite id={old_id} -> azure id={new_id}  {acct} {month}{tag}")
        except Exception as exc:
            az.rollback()
            failed.append((old_id, acct, month, str(exc)))
            print(f"  [FAILED] sqlite id={old_id}  {acct} {month}: {exc}")

    print(f"\nDone. {migrated}/{len(statements)} statements migrated ({replaced_count} replaced an existing Azure row).")
    for tbl, n in totals.items():
        print(f"  {tbl}: {n} rows")
    if failed:
        print(f"\n{len(failed)} statement(s) FAILED - not migrated:")
        for old_id, acct, month, err in failed:
            print(f"  sqlite id={old_id}  {acct} {month}: {err}")

    sq.close()
    az.close()


if __name__ == "__main__":
    main()
