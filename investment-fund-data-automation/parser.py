"""
parser.py — extraction logic for a brokerage's monthly PDF account statements.

Produces rows shaped to match the team-lead-approved schema:
  Statements, Holdings_CAD, Holdings_USD, Dividends, CashBalances, ParseWarnings
"""
import re
import datetime
import pdfplumber

ASSET_CLASSES = {"Cash", "Cash Equivalent", "Fixed Income", "Equity",
                  "Multi-Asset", "Alternative Investments"}

# Lines that are metadata about a holding, NOT part of its name -> stop stitching
STOP_WORDS = ("See Endnote",)

# Lines in Monthly Activity that are annotations, not part of a security name
ACTIVITY_ANNOTATION_PREFIXES = (
    "REC ", "DIST ON", "CASH DIV ON", "REINVEST ", "PLUS FRACTIONS",
    "BOOK VALUE", "UNSOLICITED", "SOLICITED", "THESE ARE SECURITIES",
    "RELATED ISSUER",
)
NON_DIVIDEND_ACTIVITIES = ("BUY", "SELL", "INTEREST", "HST", "FEE", "CONVERT")

ROW_FULL = re.compile(
    r"^MRGN\s+(?P<desc>.*?)\s+"
    r"(?P<qty>-?[\d,]+\.?\d*)\s+(?P<avgcost>-?[\d,]+\.?\d*)\s+"
    r"(?P<bookval>-?[\d,]+\.?\d*)\s+(?P<mktprice>-?[\d,]+\.?\d*|UNPRICED)\s+"
    r"(?P<mktval>-?[\d,]+\.?\d*|UNPRICED)$"
)
ROW_CASH = re.compile(r"^MRGN\s+(?P<mktval>-?[\d,]+\.?\d*)$")
PENDING_DIV_LINE = re.compile(r"^(?P<name>.*?)\s+(?P<amt>\d+)$")

# Monthly-activity transaction entry start: "Mar. 02, 2026 MRGN <remainder>"
ENTRY_START = re.compile(
    r"^(?P<mon>[A-Za-z]{3,4})\.?\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\s+MRGN\s*(?P<remainder>.*)$"
)
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

REINVEST_TAIL = re.compile(r"^(?P<desc>.*?)\s+(?P<qty>\d+)$")
TWO_NUM_TAIL = re.compile(r"^(?P<desc>.*?)\s+(?P<rate>-?[\d.]+)\s+(?P<amt>-?[\d,]+\.\d{2})$")
PAY_DATE_RE = re.compile(r"PAY\s+(\d{2})/(\d{2})/(\d{2})")


def num(s):
    if s is None or s == "UNPRICED":
        return None
    return float(str(s).replace(",", ""))


# Recurring page-footer/header boilerplate that is not statement content.
# Confirmed by inspecting raw page-boundary text: every page ends with a fixed
# junk stamp (page number + internal routing codes), and every page repeats
# the account/period header and column headers.
NOISE_RES = [
    re.compile(r"^Page \d+ of \d+$"),
    re.compile(r"^\d{5,7}$"),                      # bare numeric footer codes
    re.compile(r"^-\d+-\d+-\d+-$"),                 # "-80-4000-2100-"
    re.compile(r"^-[A-Z]{2,5}$"),                    # "-IRH"
    re.compile(r"^\d+_\d+_\d+[A-Za-z]+$"),           # "900_5538104_00061MTSWS"
    re.compile(r"^-$"),
    re.compile(r"^Account Number:"),
    re.compile(r"^Statement for [A-Za-z]+ \d+ to \d+,?\s*\d{0,4}$"),
    re.compile(r"^(Details of Your Account Holdings|Monthly Activity)( - continued)?$"),
    re.compile(r"^Type Security Description"),
    re.compile(r"^(Average Adjusted Market|Cost Book Value Price)$"),
    re.compile(r"^Date\s+Type\s+Activity\s+Description"),
    re.compile(r"^(TopSpace|BottomSpace|xxxx?)$"),
]


def is_noise(line):
    if "kjfkufkufkgfhgf" in line:
        return True
    return any(rx.match(line) for rx in NOISE_RES)


def get_lines(pdf_path):
    """Raw lines, page by page. Metadata parsing needs the header lines intact,
    so noise-filtering happens downstream at the point of stitching instead."""
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=1.5) or ""
            pages.append([l.strip() for l in txt.split("\n") if l.strip()])
    return pages


def parse_metadata(pages, filename):
    full = "\n".join(l for pg in pages for l in pg)
    acct = re.search(r"Account Number:\s*([\d-]+)", full)
    period = re.search(r"For the Period:\s*([A-Za-z]+)\s+(\d+)\s+to\s+(\d+),\s*(\d{4})", full)
    stmt_date = period_start = None
    if period:
        month, start_day, end_day, year = period.groups()
        stmt_date = datetime.datetime.strptime(f"{month} {end_day} {year}", "%B %d %Y").date()
        period_start = datetime.datetime.strptime(f"{month} {start_day} {year}", "%B %d %Y").date()
    return {
        "source_file": filename,
        "account_number": acct.group(1) if acct else None,
        "statement_date": stmt_date.isoformat() if stmt_date else None,
        "statement_month": stmt_date.strftime("%Y-%m") if stmt_date else None,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": stmt_date.isoformat() if stmt_date else None,
    }


def parse_summary(pages):
    full = "\n".join(l for pg in pages for l in pg)
    broker_fx = None
    usd = re.search(r"U\.S\. Dollar\s+([\d,]+)\s+1 USD = ([\d.]+) CAD\s+([\d,]+)", full)
    if usd:
        broker_fx = float(usd.group(2))
    return {"broker_fx_rate_usdcad": broker_fx}


def _flush_holding(cur_lines, currency, asset_class, out):
    """Given the buffered lines of one holding block, emit a parsed row (or None)."""
    if not cur_lines:
        return
    first = cur_lines[0]
    extra_name = " ".join(
        l for l in cur_lines[1:]
        if not any(l.startswith(sw) for sw in STOP_WORDS) and not is_noise(l)
    ).strip()
    m = ROW_FULL.match(first)
    if m:
        name = (m.group("desc").strip() + (" " + extra_name if extra_name else "")).strip()
        out.append({
            "currency": currency, "asset_class": asset_class, "security_name": name,
            "quantity": num(m.group("qty")), "average_cost": num(m.group("avgcost")),
            "adjusted_book_value": num(m.group("bookval")),
            "market_price": num(m.group("mktprice")),
            "market_value_native": num(m.group("mktval")),
            "is_unpriced": m.group("mktval") == "UNPRICED",
        })
        return
    m = ROW_CASH.match(first)
    if m and asset_class == "Cash":
        out.append({
            "currency": currency, "asset_class": "Cash", "security_name": "CASH",
            "quantity": None, "average_cost": None, "adjusted_book_value": None,
            "market_price": None, "market_value_native": num(m.group("mktval")),
            "is_unpriced": False,
        })


def parse_holdings_and_pending(pages):
    """Returns (holdings_cad, holdings_usd, pending_dividends)."""
    currency = None
    asset_class = None
    holdings_cad, holdings_usd = [], []
    pending = []
    in_pending = False
    buf = []

    def flush():
        target = holdings_cad if currency == "CAD" else holdings_usd
        _flush_holding(buf, currency, asset_class, target)
        buf.clear()

    for pg in pages:
        for line in pg:
            if "CANADIAN Account Overview" in line:
                flush(); currency, asset_class, in_pending = "CAD", None, False
                continue
            if "U.S. Account Overview" in line:
                flush(); currency, asset_class, in_pending = "USD", None, False
                continue
            if currency is None:
                continue
            if "Monthly Activity" in line:  # holdings section for this currency is done
                flush(); currency = None
                continue

            if line.startswith("Pending Dividend(s)"):
                flush(); in_pending = True
                continue
            if in_pending:
                if line.startswith(("Total", "Subtotal", "Multi-Asset", "Alternative", "MRGN")):
                    in_pending = False
                else:
                    pm = PENDING_DIV_LINE.match(line)
                    if pm:
                        pending.append({"currency": currency, "security_name": pm.group("name").strip(),
                                        "amount_native": num(pm.group("amt"))})
                    continue

            if line in ASSET_CLASSES:
                flush(); asset_class = line
                continue
            if line.startswith(("Total", "Subtotal")):
                flush()
                continue
            if line == "-":
                flush()
                continue

            if line.startswith("MRGN"):
                flush(); buf.append(line)
            elif buf:
                buf.append(line)
    flush()
    return holdings_cad, holdings_usd, pending


def parse_cash_balances(pages):
    """Opening/closing cash per currency, from the Monthly Activity section."""
    currency = None
    balances = []
    for pg in pages:
        for line in pg:
            if "CANADIAN Account Overview" in line:
                currency = "CAD"; continue
            if "U.S. Account Overview" in line:
                currency = "USD"; continue
            if currency is None:
                continue
            m = re.search(r"Opening Cash Balance\s+\$?(-?[\d,]+\.\d{2})", line)
            if m:
                balances.append({"currency": currency, "balance_type": "opening",
                                 "cash_balance_native": num(m.group(1))})
                continue
            m = re.search(r"Closing Cash Balance\s+\$?(-?[\d,]+\.\d{2})", line)
            if m:
                balances.append({"currency": currency, "balance_type": "closing",
                                 "cash_balance_native": num(m.group(1))})
    return balances


def parse_dividends(pages, statement_year):
    """
    Groups Monthly Activity lines into transaction entries, keeps only
    dividend/distribution/reinvested-dividend entries, discards trades/fees/interest/conversions.
    """
    currency = None
    entries = []
    cur_entry = None

    for pg in pages:
        for line in pg:
            if "CANADIAN Account Overview" in line:
                currency = "CAD"; continue
            if "U.S. Account Overview" in line:
                currency = "USD"; continue
            if currency is None:
                continue
            if line.startswith(("Closing Cash Balance", "Summary", "Income Summary")):
                if cur_entry:
                    entries.append(cur_entry); cur_entry = None
                continue

            m = ENTRY_START.match(line)
            if m:
                if cur_entry:
                    entries.append(cur_entry)
                mon = MONTHS.get(m.group("mon")[:3])
                day, yr = int(m.group("day")), int(m.group("year"))
                tdate = datetime.date(yr, mon, day).isoformat() if mon else None
                cur_entry = {"currency": currency, "transaction_date": tdate,
                            "lines": [m.group("remainder")]}
                continue
            if cur_entry is not None:
                cur_entry["lines"].append(line)
    if cur_entry:
        entries.append(cur_entry)

    dividends = []
    for e in entries:
        lines = e["lines"]
        head = lines[0]
        head_word = head.split(" ", 1)[0] if head else ""

        if head_word in NON_DIVIDEND_ACTIVITIES:
            continue  # BUY / SELL / INTEREST / HST / FEE / CONVERT — not a dividend

        is_dividend_kw = head.startswith("DIVIDEND ")
        body = head[len("DIVIDEND "):].strip() if is_dividend_kw else head

        annotations = [l for l in lines[1:]
                      if any(l.startswith(p) for p in ACTIVITY_ANNOTATION_PREFIXES)]
        raw_desc_extra = [l for l in lines[1:] if l not in annotations and not is_noise(l)]

        pay_date = None
        for a in annotations:
            pm = PAY_DATE_RE.search(a)
            if pm:
                mm, dd, yy = pm.groups()
                pay_date = datetime.date(2000 + int(yy), int(mm), int(dd)).isoformat()

        m2 = TWO_NUM_TAIL.match(body)
        if m2:
            name = (m2.group("desc").strip() + " " + " ".join(raw_desc_extra)).strip()
            div_type = "cash_dividend" if is_dividend_kw else "distribution"
            dividends.append({
                "currency": e["currency"], "transaction_date": e["transaction_date"],
                "pay_date": pay_date, "security_name": name, "dividend_type": div_type,
                "per_share_rate": float(m2.group("rate")),
                "dividend_amount_native": num(m2.group("amt")),
                "description": " | ".join([head] + [l for l in lines[1:] if not is_noise(l)]),
            })
            continue

        m1 = REINVEST_TAIL.match(body)
        if is_dividend_kw and m1:
            name = (m1.group("desc").strip() + " " + " ".join(raw_desc_extra)).strip()
            dividends.append({
                "currency": e["currency"], "transaction_date": e["transaction_date"],
                "pay_date": pay_date, "security_name": name, "dividend_type": "reinvested_dividend",
                "per_share_rate": None,
                "dividend_amount_native": None,  # NULL for reinvested dividends (known MVP gap, flagged in ParseWarnings)
                "description": " | ".join([head] + [l for l in lines[1:] if not is_noise(l)]),
            })
    return dividends


def parse_statement(pdf_path, filename):
    pages = get_lines(pdf_path)
    meta = parse_metadata(pages, filename)
    summary = parse_summary(pages)
    holdings_cad, holdings_usd, pending = parse_holdings_and_pending(pages)
    cash = parse_cash_balances(pages)
    year = int(meta["statement_date"][:4]) if meta["statement_date"] else None
    dividends = parse_dividends(pages, year)
    return {
        "meta": {**meta, **summary},
        "holdings_cad": holdings_cad, "holdings_usd": holdings_usd,
        "pending": pending, "cash_balances": cash, "dividends": dividends,
    }
