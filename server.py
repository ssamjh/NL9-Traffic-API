import glob
import os
import re
from flask import Flask, jsonify, abort, request

app = Flask(__name__)

LOG_DIR = os.environ.get("LOG_DIR", "/logs")

LINE_RE = re.compile(
    r'^(\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*?)\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})\s+(\d+)\s*$'
)


def parse_log_filename(filename):
    """Parse '022026t.log' → '022026'"""
    base = os.path.splitext(os.path.basename(filename))[0]
    match = re.match(r'^(\d{6})t$', base)
    return match.group(1) if match else None


def get_logs():
    """Discover all NL9 log files and return metadata."""
    pattern = os.path.join(LOG_DIR, "*t.log")
    files = sorted(glob.glob(pattern))
    result = []
    for f in files:
        date = parse_log_filename(f)
        if date:
            result.append({
                "filename": os.path.basename(f),
                "date": date,
                "url": f"/log/{date}",
                "entry_count": sum(1 for line in open(f, encoding='utf-8', errors='replace') if LINE_RE.match(line)),
            })
    return result


def parse_log(filepath):
    """Parse an NL9 traffic log file into a list of entry dicts."""
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
    """Apply ?q=, ?exact=, ?spot_id=, ?description=, and ?sort= filters from query params."""
    exact = request.args.get("exact", "1").lower() not in ("0", "false", "no")

    # --- per-field filters (?spot_id=, ?description=) ---
    for field in TEXT_FIELDS:
        value = request.args.get(field)
        if value is not None:
            if exact:
                entries = [e for e in entries if e.get(field, "").lower() == value.lower()]
            else:
                v_lower = value.lower()
                entries = [e for e in entries if v_lower in e.get(field, "").lower()]

    # --- cross-field search (?q=) ---
    q = request.args.get("q")
    if q is not None:
        if exact:
            entries = [
                e for e in entries
                if any(e.get(f, "").lower() == q.lower() for f in TEXT_FIELDS)
            ]
        else:
            q_lower = q.lower()
            entries = [
                e for e in entries
                if any(q_lower in e.get(f, "").lower() for f in TEXT_FIELDS)
            ]

    # --- sorting ---
    sort_key = request.args.get("sort")
    if sort_key is not None:
        reverse = request.args.get("order", "asc").lower() == "desc"
        try:
            entries = sorted(entries, key=lambda e: (e.get(sort_key) is None, e.get(sort_key, "")), reverse=reverse)
        except TypeError:
            entries = sorted(entries, key=lambda e: str(e.get(sort_key, "")), reverse=reverse)

    return entries


@app.route("/")
def index():
    return jsonify(get_logs())


@app.route("/log/<date>")
def log_day(date):
    """Return all entries for a given date (MMDDYY)."""
    pattern = os.path.join(LOG_DIR, f"{date}t.log")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    entries = parse_log(matches[0])
    entries = filter_entries(entries)
    return jsonify({"date": date, "entry_count": len(entries), "entries": entries})


@app.route("/log/<date>/<int:hour>")
def log_hour(date, hour):
    """Return entries for a specific hour (0–23) within a date."""
    pattern = os.path.join(LOG_DIR, f"{date}t.log")
    matches = glob.glob(pattern)
    if not matches:
        abort(404)
    hour_prefix = f"{hour:02d}:"
    entries = [e for e in parse_log(matches[0]) if e["time"].startswith(hour_prefix)]
    entries = filter_entries(entries)
    return jsonify({"date": date, "hour": hour, "entry_count": len(entries), "entries": entries})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
