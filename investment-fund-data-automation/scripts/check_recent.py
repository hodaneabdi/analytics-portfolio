"""
check_recent.py — quick diagnostic: print the 5 most recently loaded rows
from Statements (the table build_pipeline.py writes one row to per parsed
PDF) plus a total row count, to confirm a test upload processed correctly.

Usage:
    AZSQL_SERVER=<your-server>.database.windows.net \
    AZSQL_DATABASE=<database> \
    AZSQL_USER=<user> \
    AZSQL_PASSWORD='<password>' \
    python3 check_recent.py
"""
import os
import pymssql

# Same env var names build_pipeline.py already uses, so this script can
# share local.settings.json / your shell exports instead of hardcoding
# the password into a file that could end up committed somewhere.
SERVER = os.environ["AZSQL_SERVER"]
DATABASE = os.environ.get("AZSQL_DATABASE", "portfolio_data")
USER = os.environ["AZSQL_USER"]
PASSWORD = os.environ["AZSQL_PASSWORD"]


def main():
    # pymssql.connect() opens the TDS connection to the Azure SQL server.
    # as_dict=True on the cursor makes fetchall() return {column: value}
    # dicts instead of plain tuples, so the print below can reference
    # columns by name.
    conn = pymssql.connect(server=SERVER, user=USER, password=PASSWORD, database=DATABASE)
    cur = conn.cursor(as_dict=True)

    # Total row count in Statements - one row per statement ever loaded
    # (dedup means a re-uploaded account+month replaces its old row rather
    # than adding a new one, so this is "distinct statements on file", not
    # "uploads ever attempted").
    cur.execute("SELECT COUNT(*) AS n FROM Statements")
    total = cur.fetchone()["n"]
    print(f"Total rows in Statements: {total}\n")

    # The 5 most recent rows, newest first. loaded_at is set to
    # datetime.utcnow().isoformat() at insert time in build_pipeline.py, so
    # sorting on it (not statement_id) reflects "most recently processed"
    # even if IDs and load order ever diverge (e.g. after a dedup reload).
    cur.execute("""
        SELECT TOP 5 statement_id, account_number, statement_month, source_file,
               parse_status, loaded_at
        FROM Statements
        ORDER BY loaded_at DESC
    """)
    for row in cur.fetchall():
        print(f"statement_id={row['statement_id']}  "
              f"account={row['account_number']}  "
              f"month={row['statement_month']}  "
              f"status={row['parse_status']}  "
              f"loaded_at={row['loaded_at']}")
        print(f"  source_file: {row['source_file']}")

    conn.close()


if __name__ == "__main__":
    main()
