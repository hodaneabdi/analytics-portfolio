"""
check_months.py — one-off: does Statements have any rows for statement_month
'2026-06' or '2026-07'? statement_month is stored as plain nvarchar('YYYY-MM')
text (confirmed via INFORMATION_SCHEMA before writing this), so a simple
IN (...) string match is correct - no date casting needed.
"""
import os
import pymssql

conn = pymssql.connect(
    server=os.environ["AZSQL_SERVER"], user=os.environ["AZSQL_USER"],
    password=os.environ["AZSQL_PASSWORD"], database=os.environ.get("AZSQL_DATABASE", "portfolio_data"),
)
cur = conn.cursor(as_dict=True)
cur.execute("""
    SELECT statement_id, account_number, statement_month, source_file, parse_status, loaded_at
    FROM Statements
    WHERE statement_month IN ('2026-06', '2026-07')
    ORDER BY statement_month
""")
rows = cur.fetchall()

if not rows:
    print("No rows found for statement_month '2026-06' or '2026-07'.")
else:
    print(f"{len(rows)} row(s) found:\n")
    for r in rows:
        print(r)

conn.close()
