import atexit
import glob
import logging
import os
import re
import zipfile
from datetime import datetime, date, timedelta
from decimal import Decimal
from flask import Flask, jsonify, abort, request
from flask.json.provider import DefaultJSONProvider
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    import pymssql
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─── JSON provider ────────────────────────────────────────────────────────────

class NL9JSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


class NL9App(Flask):
    json_provider_class = NL9JSONProvider


app = NL9App(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

LOG_DIR = os.environ.get("LOG_DIR", "/logs")

SQL_SERVER = os.environ.get("SQL_SERVER", "sqlserver")
SQL_PORT   = int(os.environ.get("SQL_PORT", "1433"))
SQL_USER   = os.environ.get("SQL_USER", "sa")
SQL_PASS   = os.environ.get("SQL_PASS", "")
SQL_DB     = os.environ.get("SQL_DB", "NL9_Traffic")

BACKUP_DIR      = os.environ.get("BACKUP_DIR", "/backups")
BACKUP_ZIP_NAME = os.environ.get("BACKUP_ZIP_NAME", "NL9_Traffic.zip")
BACKUP_BAK_NAME = os.environ.get("BACKUP_BAK_NAME", "NL9_Traffic.BAK")
# BAK files are extracted here — must be accessible to SQL Server container
BAK_STAGE_DIR   = os.environ.get("BAK_STAGE_DIR", "/mssql-backup")
# Path to the staged BAK from SQL Server's perspective (same volume, possibly different mountpoint)
BAK_SQLSERVER_DIR = os.environ.get("BAK_SQLSERVER_DIR", "/var/opt/mssql/backup")

IMPORT_STATUS_FILE = os.path.join(BAK_STAGE_DIR, ".last_imported")

IMPORT_HOUR   = int(os.environ.get("IMPORT_HOUR", "5"))
IMPORT_MINUTE = int(os.environ.get("IMPORT_MINUTE", "0"))
SCHEDULER_TZ  = os.environ.get("SCHEDULER_TZ", "UTC")

# ─── Mode ─────────────────────────────────────────────────────────────────────
# "backup" — restore daily BAK file, query the local SQL Server container
# "live"   — connect directly to the source SQL Server (read-only account)
DB_MODE = os.environ.get("DB_MODE", "backup").lower()

# ─── Log parsing (existing) ───────────────────────────────────────────────────

LINE_RE = re.compile(
    r'^(\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*?)\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})\s+(\d+)\s*$'
)


def parse_log_filename(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    match = re.match(r'^(\d{6})t$', base)
    return match.group(1) if match else None


def _log_sort_key(filename):
    """Convert MMDDYY filename to a YYMMDD sort key for chronological ordering."""
    date_str = parse_log_filename(filename)
    if not date_str:
        return ""
    return date_str[4:6] + date_str[0:2] + date_str[2:4]


def _count_entries(filepath):
    return sum(
        1 for line in open(filepath, encoding="utf-8", errors="replace")
        if LINE_RE.match(line)
    )


def get_logs(count_entries=True):
    pattern = os.path.join(LOG_DIR, "*t.log")
    files = sorted(glob.glob(pattern), key=_log_sort_key, reverse=True)
    result = []
    for f in files:
        date_str = parse_log_filename(f)
        if date_str:
            entry = {
                "filename": os.path.basename(f),
                "date": date_str,
                "url": f"/log/{date_str}",
                "_filepath": f,
            }
            if count_entries:
                entry["entry_count"] = _count_entries(f)
            result.append(entry)
    return result


def parse_log(filepath):
    entries = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            for line_number, line in enumerate(fh, 1):
                m = LINE_RE.match(line)
                if m:
                    entries.append({
                        "line_number": line_number,
                        "time": m.group(1),
                        "spot_id": m.group(2),
                        "description": m.group(3).strip(),
                        "duration": int(m.group(4)),
                        "duration_formatted": m.group(5),
                        "log_number": int(m.group(6)),
                    })
    except OSError:
        pass
    return entries


TEXT_FIELDS = ("spot_id", "description")


def filter_entries(entries):
    exact = request.args.get("exact", "1").lower() not in ("0", "false", "no")
    for field in TEXT_FIELDS:
        value = request.args.get(field)
        if value is not None:
            if exact:
                entries = [e for e in entries if e.get(field, "").lower() == value.lower()]
            else:
                v = value.lower()
                entries = [e for e in entries if v in e.get(field, "").lower()]
    q = request.args.get("q")
    if q is not None:
        if exact:
            entries = [e for e in entries if any(e.get(f, "").lower() == q.lower() for f in TEXT_FIELDS)]
        else:
            q_lower = q.lower()
            entries = [e for e in entries if any(q_lower in e.get(f, "").lower() for f in TEXT_FIELDS)]
    sort_key = request.args.get("sort")
    if sort_key is not None:
        reverse = request.args.get("order", "asc").lower() == "desc"
        try:
            entries = sorted(entries, key=lambda e: (e.get(sort_key) is None, e.get(sort_key, "")), reverse=reverse)
        except TypeError:
            entries = sorted(entries, key=lambda e: str(e.get(sort_key, "")), reverse=reverse)
    return entries

# ─── Query helpers ────────────────────────────────────────────────────────────

_ALLOWED_FIELDS = {
    "clients": {
        "CustID", "Tag", "Sponsor", "BillingName", "AgencyID",
        "Address1", "Address2", "CityState", "ZipCode",
        "Telephone", "Fax", "Contact", "Salutation", "EMail",
        "AccountRepCust", "RevenueTypeCust", "BillCycleCust",
        "Balance", "CreditLimit", "CustomerSince", "LastActiveDate", "LastPaymentDate",
    },
    "orders": {
        "Status", "OrderType", "PkgDescription", "RevenueType", "BillCycle",
        "BillBasis", "PurchaseOrder", "Product", "AccountRep", "Regarding",
        "StartDate", "EndDate",
    },
    "copy": {
        "CopyID", "CopyType", "Label", "Status", "Length", "Voice",
        "AudioFileName", "CopyInstructions", "Script", "StartDate", "EndDate",
    },
}


def is_empty_condition(field, table, endpoint):
    """Return a SQL condition for (field IS NULL OR field = '') after validating the field name."""
    allowed = _ALLOWED_FIELDS.get(endpoint, set())
    if field not in allowed:
        raise ValueError(f"Field '{field}' is not filterable on {endpoint}")
    col = f"{table}.{field}" if table else field
    return f"({col} IS NULL OR {col} = '')"


# ─── DB helpers ───────────────────────────────────────────────────────────────

def get_conn(db=None, autocommit=False):
    return pymssql.connect(
        server=SQL_SERVER,
        port=SQL_PORT,
        user=SQL_USER,
        password=SQL_PASS,
        database=db or SQL_DB,
        autocommit=autocommit,
        login_timeout=15,
        timeout=300,
    )


def db_query(sql, params=None):
    """Run a SELECT and return rows as list of dicts."""
    conn = get_conn()
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        conn.close()


def db_unavailable():
    return jsonify({"error": "Database not available. POST /db/import to load the latest backup."}), 503

# ─── Import helpers ───────────────────────────────────────────────────────────

def find_latest_backup():
    """Return (date_str, zip_path) for the most recent YYYY-MM-DD folder containing the zip."""
    try:
        entries = os.listdir(BACKUP_DIR)
    except OSError:
        return None, None
    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    dates = sorted([e for e in entries if date_re.match(e)], reverse=True)
    for d in dates:
        zip_path = os.path.join(BACKUP_DIR, d, BACKUP_ZIP_NAME)
        if os.path.exists(zip_path):
            return d, zip_path
    return None, None


def get_last_imported():
    try:
        with open(IMPORT_STATUS_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


def set_last_imported(date_str):
    os.makedirs(BAK_STAGE_DIR, exist_ok=True)
    with open(IMPORT_STATUS_FILE, "w") as f:
        f.write(date_str)


def do_import(date_str, zip_path):
    """
    Extract BAK from zip into BAK_STAGE_DIR, then restore into SQL Server.
    BAK_STAGE_DIR is mounted into the SQL Server container as BAK_SQLSERVER_DIR.
    """
    os.makedirs(BAK_STAGE_DIR, exist_ok=True)
    bak_local  = os.path.join(BAK_STAGE_DIR, BACKUP_BAK_NAME)
    bak_remote = os.path.join(BAK_SQLSERVER_DIR, BACKUP_BAK_NAME)

    # Extract BAK from zip
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extract(BACKUP_BAK_NAME, BAK_STAGE_DIR)
    os.chmod(bak_local, 0o644)

    # Restore via SQL Server
    conn = get_conn(db="master", autocommit=True)
    try:
        cur = conn.cursor(as_dict=True)

        # Get logical file names from the backup
        cur.execute("RESTORE FILELISTONLY FROM DISK = %s", (bak_remote,))
        files = cur.fetchall()

        moves = []
        for f in files:
            name = f["LogicalName"]
            ftype = f.get("Type", "D")
            ext = ".ldf" if ftype == "L" else ".mdf"
            dest = f"/var/opt/mssql/data/{name}{ext}"
            moves.append(f"MOVE N'{name}' TO N'{dest}'")
        move_str = ", ".join(moves)

        # Kill existing connections so we can replace the DB
        cur.execute(f"""
            IF DB_ID(N'{SQL_DB}') IS NOT NULL
                ALTER DATABASE [{SQL_DB}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE
        """)

        # Restore
        cur.execute(f"""
            RESTORE DATABASE [{SQL_DB}]
            FROM DISK = N'{bak_remote}'
            WITH REPLACE, {move_str}
        """)

        cur.execute(f"ALTER DATABASE [{SQL_DB}] SET MULTI_USER")
    finally:
        conn.close()

    # Remove the staged BAK — it's now in the DB, no longer needed on disk
    try:
        os.remove(bak_local)
    except OSError:
        pass

    set_last_imported(date_str)
    return date_str

# ─── Log endpoints ────────────────────────────────────────────────────────────

def _logs_response():
    all_logs = get_logs(count_entries=False)
    total = len(all_logs)
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    page_raw = all_logs[offset: offset + limit]
    page = []
    for entry in page_raw:
        filepath = entry.pop("_filepath")
        entry["entry_count"] = _count_entries(filepath)
        page.append(entry)
    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(page),
        "logs": page,
    })


@app.route("/")
def index():
    return _logs_response()


@app.route("/logs")
def logs():
    return _logs_response()



@app.route("/log/<date>")
def log_day(date):
    pattern = os.path.join(LOG_DIR, f"{date}t.log")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    entries = filter_entries(parse_log(matches[0]))
    return jsonify({"date": date, "entry_count": len(entries), "entries": entries})


@app.route("/log/<date>/<int:hour>")
def log_hour(date, hour):
    pattern = os.path.join(LOG_DIR, f"{date}t.log")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    hour_prefix = f"{hour:02d}:"
    entries = filter_entries([e for e in parse_log(matches[0]) if e["time"].startswith(hour_prefix)])
    return jsonify({"date": date, "hour": hour, "entry_count": len(entries), "entries": entries})

# ─── DB / import endpoints ────────────────────────────────────────────────────

@app.route("/db/status")
def db_status():
    if DB_MODE == "live":
        return jsonify({
            "mode": "live",
            "server": SQL_SERVER,
            "database": SQL_DB,
            "info": "Connected directly to live database. No backup import scheduled.",
        })
    latest_date, _ = find_latest_backup()
    last_imported = get_last_imported()
    job = _scheduler.get_job("daily_import") if _scheduler else None
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return jsonify({
        "mode": "backup",
        "last_imported": last_imported,
        "latest_backup": latest_date,
        "up_to_date": (last_imported == latest_date) if (last_imported and latest_date) else False,
        "next_scheduled_import": next_run,
    })


@app.route("/db/import", methods=["POST"])
def db_import():
    """Find and import the latest backup if it is newer than the last import."""
    if DB_MODE == "live":
        return jsonify({"error": "Not applicable in live mode. Set DB_MODE=backup to use backup/restore."}), 400
    if not DB_AVAILABLE:
        return jsonify({"error": "pymssql not installed"}), 500

    date_str, zip_path = find_latest_backup()
    if not date_str:
        return jsonify({"error": "No backup files found"}), 404

    last = get_last_imported()
    if last and last >= date_str:
        return jsonify({"status": "already_current", "date": date_str})

    try:
        imported = do_import(date_str, zip_path)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "imported", "date": imported})

# ─── Client endpoints ─────────────────────────────────────────────────────────

@app.route("/clients")
def clients():
    """
    GET /clients
    Query params:
      ?id=<CustID>        exact customer ID (also accepted as ?custid=)
      ?q=<text>           search Sponsor, BillingName, Contact (case-insensitive)
      ?inactive=1         only inactive clients (default: active only)
      ?is_empty=<field>   only clients where the specified field is null or empty
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        cust_id  = request.args.get("custid") or request.args.get("id")
        q        = request.args.get("q")
        inactive = request.args.get("inactive", "0").lower() in ("1", "true", "yes")
        is_empty = request.args.get("is_empty")

        conditions = []
        params = []

        conditions.append("Inactive = 1" if inactive else "Inactive = 0")
        if cust_id:
            conditions.append("CustID = %s")
            params.append(int(cust_id))
        if q:
            conditions.append("(Sponsor LIKE %s OR BillingName LIKE %s OR Contact LIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like])
        if is_empty:
            conditions.append(is_empty_condition(is_empty, "", "clients"))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = db_query(f"""
            SELECT CustID, Inactive, Tag, Sponsor, BillingName, AgencyID,
                   Address1, Address2, CityState, ZipCode,
                   Telephone, Fax, Contact, Salutation, EMail,
                   AccountRepCust, RevenueTypeCust, BillCycleCust,
                   Balance, CreditLimit, Orders,
                   CustomerSince, LastActiveDate, LastPaymentDate
            FROM Customers
            {where}
            ORDER BY Sponsor
        """, params)
        return jsonify({"count": len(rows), "clients": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/clients/<int:cust_id>")
def client_detail(cust_id):
    """GET /clients/<CustID> — full client record joined with agency."""
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        rows = db_query("""
            SELECT c.*,
                   a.AgencyName, a.AgencyTelephone, a.AgencyContact, a.AgencyEMail
            FROM Customers c
            LEFT JOIN Agencies a ON c.AgencyID = a.AgencyID
            WHERE c.CustID = %s
        """, (cust_id,))
        if not rows:
            abort(404)
        return jsonify(rows[0])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

# ─── Order endpoints ──────────────────────────────────────────────────────────

@app.route("/orders")
def orders():
    """
    GET /orders
    Query params:
      ?custid=<CustID>          filter by customer
      ?status=<Status>          filter by order status (e.g. Active, Closed)
      ?deleted=1                include deleted orders (default: excluded)
      ?start_after=YYYY-MM-DD   StartDate >= date
      ?start_before=YYYY-MM-DD  StartDate <= date
      ?end_after=YYYY-MM-DD     EndDate >= date
      ?end_before=YYYY-MM-DD    EndDate <= date
      ?valid_after=YYYY-MM-DD   Status=Ok and EndDate >= date (still valid after date)
      ?valid_before=YYYY-MM-DD  Status=Ok and StartDate <= date (valid by date)
      ?missing_copy=1           only orders with at least one line missing copy
      ?exclude_custid=1,2,3     exclude one or more customers (comma-separated)
      ?is_empty=<field>         only orders where the specified field is null or empty
      ?include_lines=1          embed order lines (with CopyLabel/CopyCode) in each order
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        cust_id        = request.args.get("custid") or request.args.get("id")
        status         = request.args.get("status")
        deleted        = request.args.get("deleted", "0").lower() in ("1", "true", "yes")
        start_after    = request.args.get("start_after")
        start_before   = request.args.get("start_before")
        end_after      = request.args.get("end_after")
        end_before     = request.args.get("end_before")
        valid_after    = request.args.get("valid_after")
        valid_before   = request.args.get("valid_before")
        missing_copy   = request.args.get("missing_copy", "0").lower() in ("1", "true", "yes")
        exclude_custid = [int(x) for x in request.args.get("exclude_custid", "").split(",") if x.strip()]
        is_empty       = request.args.get("is_empty")
        include_lines  = request.args.get("include_lines", "0").lower() in ("1", "true", "yes")

        conditions = []
        params = []

        if not deleted:
            conditions.append("o.Deleted = 0")
        if cust_id:
            conditions.append("o.CustID = %s")
            params.append(int(cust_id))
        if status:
            conditions.append("o.Status = %s")
            params.append(status)
        if missing_copy:
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM OrderLines ol
                    WHERE ol.CustID = o.CustID AND ol.OrderID = o.OrderID
                    AND (ol.CopyIDLong IS NULL OR ol.CopyIDLong = 0)
                    AND ol.LineLogType <> 'MAC'
                )
            """)
        if start_after:
            conditions.append("CAST(o.StartDate AS DATE) >= %s")
            params.append(start_after)
        if start_before:
            conditions.append("CAST(o.StartDate AS DATE) <= %s")
            params.append(start_before)
        if end_after:
            conditions.append("CAST(o.EndDate AS DATE) >= %s")
            params.append(end_after)
        if end_before:
            conditions.append("CAST(o.EndDate AS DATE) <= %s")
            params.append(end_before)
        if valid_after:
            conditions.append("o.Status = 'Ok'")
            conditions.append("CAST(o.EndDate AS DATE) >= %s")
            params.append(valid_after)
        if valid_before:
            conditions.append("o.Status = 'Ok'")
            conditions.append("CAST(o.StartDate AS DATE) <= %s")
            params.append(valid_before)
        if exclude_custid:
            placeholders = ", ".join(["%s"] * len(exclude_custid))
            conditions.append(f"o.CustID NOT IN ({placeholders})")
            params.extend(exclude_custid)
        if is_empty:
            conditions.append(is_empty_condition(is_empty, "o", "orders"))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = db_query(f"""
            SELECT o.CustID, o.OrderID, o.Status, o.Deleted, o.NonBroadcast,
                   o.OrderAmount, o.OrderSpots, o.ScheduledAmount, o.ScheduledSpots,
                   o.StartDate, o.EndDate, o.OrderType, o.PkgDescription,
                   o.RevenueType, o.BillCycle, o.BillBasis, o.PurchaseOrder,
                   o.Product, o.AccountRep, o.Regarding,
                   o.OrderEntryDateTime, o.OrderRevisionDateTime,
                   c.Sponsor, c.BillingName, c.Contact
            FROM Orders o
            LEFT JOIN Customers c ON o.CustID = c.CustID
            {where}
            ORDER BY o.StartDate DESC, o.CustID, o.OrderID
        """, params)
        if include_lines and rows:
            for r in rows:
                r["lines"] = []
            order_map = {(r["CustID"], r["OrderID"]): r for r in rows}
            pairs = list(order_map.keys())
            or_clauses = " OR ".join(["(ol.CustID = %s AND ol.OrderID = %s)"] * len(pairs))
            flat_params = [x for pair in pairs for x in pair]
            all_lines = db_query(f"""
                SELECT ol.*,
                       cm.Label AS CopyLabel, cm.CopyID AS CopyCode,
                       cm.Length AS CopyLength, cm.AudioFileName
                FROM OrderLines ol
                LEFT JOIN CopyManager cm ON ol.CopyIDLong = cm.CopyIDLong
                WHERE {or_clauses}
                ORDER BY ol.CustID, ol.OrderID, ol.LineIndex
            """, flat_params)
            for line in all_lines:
                key = (line["CustID"], line["OrderID"])
                if key in order_map:
                    order_map[key]["lines"].append(line)

        return jsonify({"count": len(rows), "orders": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/orders/<order_ref>")
def order_detail(order_ref):
    """
    GET /orders/<CustID>-<OrderID>
    Returns full order with lines (including copy labels) and station assignments.
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    parts = order_ref.split("-")
    if len(parts) != 2:
        abort(400)
    try:
        cust_id, order_id = int(parts[0]), int(parts[1])
    except ValueError:
        abort(400)
    try:
        order_rows = db_query("""
            SELECT o.*,
                   c.Sponsor, c.BillingName, c.Contact, c.Telephone, c.EMail
            FROM Orders o
            LEFT JOIN Customers c ON o.CustID = c.CustID
            WHERE o.CustID = %s AND o.OrderID = %s
        """, (cust_id, order_id))
        if not order_rows:
            abort(404)

        lines = db_query("""
            SELECT ol.*,
                   cm.Label AS CopyLabel, cm.CopyID AS CopyCode,
                   cm.Length AS CopyLength, cm.AudioFileName
            FROM OrderLines ol
            LEFT JOIN CopyManager cm ON ol.CopyIDLong = cm.CopyIDLong
            WHERE ol.CustID = %s AND ol.OrderID = %s
            ORDER BY ol.LineIndex
        """, (cust_id, order_id))

        stations = db_query("""
            SELECT os.*, s.StationName
            FROM OrderStations os
            LEFT JOIN Stations s ON os.StationID = s.StationID
            WHERE os.CustID = %s AND os.OrderID = %s
        """, (cust_id, order_id))

        result = order_rows[0]
        result["lines"]    = lines
        result["stations"] = stations
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

# ─── Copy endpoints ───────────────────────────────────────────────────────────

@app.route("/copy")
def copy_list():
    """
    GET /copy
    Query params:
      ?custid=<CustID>          filter by customer
      ?q=<text>                 search Label, CopyID, Script (case-insensitive)
      ?status=<Status>          filter by status (e.g. Ok, Deleted)
      ?on_date=YYYY-MM-DD       only copy with Status=Ok and date within StartDate/EndDate
      ?valid_after=YYYY-MM-DD   Status=Ok and EndDate >= date (still valid after date)
      ?valid_before=YYYY-MM-DD  Status=Ok and StartDate <= date (valid by date)
      ?start_after=YYYY-MM-DD   StartDate >= date
      ?start_before=YYYY-MM-DD  StartDate <= date
      ?end_after=YYYY-MM-DD     EndDate >= date
      ?end_before=YYYY-MM-DD    EndDate <= date
      ?exclude_custid=1,2,3     exclude one or more customers (comma-separated)
      ?is_empty=<field>         only copy where the specified field is null or empty
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        cust_id        = request.args.get("custid")
        q              = request.args.get("q")
        status         = request.args.get("status")
        on_date        = request.args.get("on_date")
        valid_after    = request.args.get("valid_after")
        valid_before   = request.args.get("valid_before")
        start_after    = request.args.get("start_after")
        start_before   = request.args.get("start_before")
        end_after      = request.args.get("end_after")
        end_before     = request.args.get("end_before")
        exclude_custid = [int(x) for x in request.args.get("exclude_custid", "").split(",") if x.strip()]
        is_empty       = request.args.get("is_empty")

        conditions = []
        params = []

        if cust_id:
            conditions.append("cm.CustID = %s")
            params.append(int(cust_id))
        if q:
            conditions.append("(cm.Label LIKE %s OR cm.CopyID LIKE %s OR cm.Script LIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like])
        if status:
            conditions.append("cm.Status = %s")
            params.append(status)
        if on_date:
            valid_after = valid_after or on_date
            valid_before = valid_before or on_date
        if valid_after:
            conditions.append("cm.Status = 'Ok'")
            conditions.append("CAST(cm.EndDate AS DATE) >= %s")
            params.append(valid_after)
        if valid_before:
            conditions.append("cm.Status = 'Ok'")
            conditions.append("CAST(cm.StartDate AS DATE) <= %s")
            params.append(valid_before)
        if start_after:
            conditions.append("CAST(cm.StartDate AS DATE) >= %s")
            params.append(start_after)
        if start_before:
            conditions.append("CAST(cm.StartDate AS DATE) <= %s")
            params.append(start_before)
        if end_after:
            conditions.append("CAST(cm.EndDate AS DATE) >= %s")
            params.append(end_after)
        if end_before:
            conditions.append("CAST(cm.EndDate AS DATE) <= %s")
            params.append(end_before)
        if exclude_custid:
            placeholders = ", ".join(["%s"] * len(exclude_custid))
            conditions.append(f"cm.CustID NOT IN ({placeholders})")
            params.extend(exclude_custid)
        if is_empty:
            conditions.append(is_empty_condition(is_empty, "cm", "copy"))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = db_query(f"""
            SELECT cm.CopyIDLong, cm.CopyID, cm.Inactive, cm.CopyType,
                   cm.StationID, cm.CustID, cm.OrderID,
                   cm.Label, cm.StartDate, cm.EndDate, cm.Status, cm.Length,
                   cm.Voice, cm.AudioFileName, cm.CopyEntrydateTime,
                   cm.CopyInstructions, cm.Script,
                   c.Sponsor
            FROM CopyManager cm
            LEFT JOIN Customers c ON cm.CustID = c.CustID
            {where}
            ORDER BY cm.StartDate DESC, cm.CustID, cm.CopyID
        """, params)
        return jsonify({"count": len(rows), "copy": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/copy/<copy_id>")
def copy_detail(copy_id):
    """
    GET /copy/<CopyID> — full copy record with rotator lines.
    Query params:
      ?inactive=1  include inactive/deleted records (default: active first)
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        inactive = request.args.get("inactive", "0").lower() in ("1", "true", "yes")
        where = "WHERE cm.CopyID = %s" if inactive else "WHERE cm.CopyID = %s AND cm.Inactive = 0"
        rows = db_query(f"""
            SELECT cm.*, c.Sponsor
            FROM CopyManager cm
            LEFT JOIN Customers c ON cm.CustID = c.CustID
            {where}
            ORDER BY cm.Inactive ASC, cm.StartDate DESC
        """, (copy_id,))
        if not rows:
            abort(404)

        rotators = db_query("""
            SELECT * FROM CopyRotatorLines
            WHERE CopyIDLong = %s
            ORDER BY RotLineIndex
        """, (rows[0]["CopyIDLong"],))

        result = rows[0]
        result["rotator_lines"] = rotators
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/copy/<copy_id>/resolve")
def copy_resolve(copy_id):
    """
    GET /copy/<CopyID>/resolve
    Resolves a packet or rotator to all audio files active on a given date or date range.

    Query params:
      ?date=YYYY-MM-DD              single date to resolve for (default: today)
      ?start=YYYY-MM-DD&end=YYYY-MM-DD  resolve for every day in the range (inclusive);
                                        returns a "days" array instead of a flat "resolved" list

    Returns the resolution path taken and all resulting audio files.
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        start_str = request.args.get("start")
        end_str   = request.args.get("end")

        rows = db_query("""
            SELECT CopyIDLong, CopyID FROM CopyManager
            WHERE CopyID = %s AND Inactive = 0
            ORDER BY StartDate DESC
        """, (copy_id,))
        if not rows:
            abort(404)

        copy_id_long = rows[0]["CopyIDLong"]

        if start_str or end_str:
            if not start_str or not end_str:
                return jsonify({"error": "Both start and end are required for range resolve"}), 400
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date   = datetime.strptime(end_str,   "%Y-%m-%d").date()
            if end_date < start_date:
                return jsonify({"error": "end must be >= start"}), 400
            days = []
            d = start_date
            while d <= end_date:
                resolved = _resolve_copy(copy_id_long, d)
                days.append({
                    "date":        d.isoformat(),
                    "audio_count": len(resolved),
                    "resolved":    resolved,
                })
                d += timedelta(days=1)
            return jsonify({
                "copy_id": copy_id,
                "start":   start_date.isoformat(),
                "end":     end_date.isoformat(),
                "days":    days,
            })

        date_str = request.args.get("date")
        resolve_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
        resolved = _resolve_copy(copy_id_long, resolve_date)
        return jsonify({
            "copy_id":     copy_id,
            "date":        resolve_date.isoformat(),
            "audio_count": len(resolved),
            "resolved":    resolved,
        })
    except ValueError:
        return jsonify({"error": "Invalid date format, use YYYY-MM-DD"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _resolve_copy(copy_id_long, resolve_date, visited=None, depth=0):
    """
    Recursively resolve a CopyIDLong to leaf audio files.
    Returns a list of dicts: {"audio": str, "path": [CopyID, ...]}
    """
    if visited is None:
        visited = set()
    if copy_id_long in visited:
        return []
    visited = visited | {copy_id_long}

    rows = db_query(
        "SELECT CopyIDLong, CopyID, CopyType, AudioFileName, StartDate, EndDate, Inactive FROM CopyManager WHERE CopyIDLong = %s",
        (copy_id_long,),
    )
    if not rows:
        return []

    copy = rows[0]
    cid = (copy["CopyID"] or "").strip().upper()
    is_container = cid.startswith("P") or cid.startswith("R")

    if not is_container:
        if copy.get("Inactive"):
            return []
        start = copy.get("StartDate")
        end = copy.get("EndDate")
        if start and resolve_date < (start.date() if hasattr(start, "date") else start):
            return []
        if end and resolve_date > (end.date() if hasattr(end, "date") else end):
            return []
        return [{"audio": copy["AudioFileName"], "path": [copy["CopyID"]]}]

    day_col = f"RotLineDay{resolve_date.isoweekday()}"
    date_str = resolve_date.isoformat()
    lines = db_query(f"""
        SELECT RotCopyIDLong, RotCopyID, RotLineIndex
        FROM CopyRotatorLines
        WHERE CopyIDLong = %s
          AND CAST(RotLineStartDate AS DATE) <= %s
          AND CAST(RotLineEndDate AS DATE) >= %s
          AND {day_col} = 1
        ORDER BY RotLineIndex
    """, (copy_id_long, date_str, date_str))

    results = []
    for line in lines:
        child = _resolve_copy(line["RotCopyIDLong"], resolve_date, visited, depth + 1)
        for r in child:
            results.append({
                "audio": r["audio"],
                "path": [copy["CopyID"]] + r["path"],
            })

    return results


# ─── Account rep endpoints ────────────────────────────────────────────────────

@app.route("/reps")
def reps():
    """
    GET /reps
    Query params:
      ?inactive=1     include inactive reps (default: active only)
    """
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        inactive = request.args.get("inactive", "0").lower() in ("1", "true", "yes")
        where = "" if inactive else "WHERE InActive = 0"
        rows = db_query(f"""
            SELECT AccountRepID, InActive, AccountRepName, AccountRepEMail,
                   DefaultAgencyCommission, DefaultDirectCommission,
                   DefaultTradeCommission, QBSalesRep
            FROM AccountReps
            {where}
            ORDER BY AccountRepName
        """)
        return jsonify({"count": len(rows), "reps": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/reps/<int:rep_id>")
def rep_detail(rep_id):
    """GET /reps/<AccountRepID> — single account rep record."""
    if not DB_AVAILABLE:
        return db_unavailable()
    try:
        rows = db_query("""
            SELECT AccountRepID, InActive, AccountRepName, AccountRepEMail,
                   DefaultAgencyCommission, DefaultDirectCommission,
                   DefaultTradeCommission, QBSalesRep
            FROM AccountReps
            WHERE AccountRepID = %s
        """, (rep_id,))
        if not rows:
            abort(404)
        return jsonify(rows[0])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Scheduler ───────────────────────────────────────────────────────────────

def scheduled_import():
    """Check for a newer backup and import it if one exists. Runs daily at IMPORT_HOUR."""
    date_str, zip_path = find_latest_backup()
    if not date_str:
        logging.info("Scheduled import: no backup files found in %s", BACKUP_DIR)
        return
    last = get_last_imported()
    if last and last >= date_str:
        logging.info("Scheduled import: already current (%s)", date_str)
        return
    logging.info("Scheduled import: importing %s", date_str)
    try:
        do_import(date_str, zip_path)
        logging.info("Scheduled import: completed %s", date_str)
    except Exception as exc:
        logging.error("Scheduled import failed: %s", exc)


_scheduler = None
if DB_MODE == "backup":
    _scheduler = BackgroundScheduler(timezone=SCHEDULER_TZ)
    _scheduler.add_job(
        scheduled_import,
        CronTrigger(hour=IMPORT_HOUR, minute=IMPORT_MINUTE, timezone=SCHEDULER_TZ),
        id="daily_import",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    atexit.register(_scheduler.shutdown)
else:
    logging.info("DB_MODE=live — backup scheduler disabled, connecting directly to %s/%s", SQL_SERVER, SQL_DB)


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
