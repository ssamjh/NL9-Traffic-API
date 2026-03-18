# NL9 Traffic API

A Flask API for Natural Log 9 (NL9) traffic data. Exposes two data sources:

- **Traffic logs** — parses NL9 daily broadcast log files
- **Database** — queries client, order, copy, and account rep data from a daily SQL Server backup

---

## Requirements

- Docker and Docker Compose
- NL9 log files in `./logs/`
- NL9 daily database backups in `./backups/YYYY-MM-DD/`

---

## Setup

**1. Set a strong SQL Server password** in `docker-compose.yaml` — update `SA_PASSWORD` and `SQL_PASS` (both must match):

```yaml
SA_PASSWORD: "YourStrong@Password1"   # change this
SQL_PASS:    "YourStrong@Password1"   # must match SA_PASSWORD
```

The password must meet SQL Server complexity requirements (uppercase, lowercase, digit, symbol, min 8 chars).

**2. Start the stack:**

```bash
docker compose up -d
```

**3. Trigger the first database import:**

```bash
curl -X POST http://localhost:5000/db/import
```

After that, imports run automatically each day at 5:00 AM (configurable).

---

## Backup folder structure

```
backups/
  2026-03-17/
    NL9_Traffic.zip        ← contains NL9_Traffic.BAK
  2026-03-18/
    NL9_Traffic.zip
```

The API always imports the most recent date folder. Each import fully replaces the previous database.

---

## Configuration

All settings are environment variables configured in `docker-compose.yaml`.

| Variable | Default | Description |
|---|---|---|
| `SA_PASSWORD` / `SQL_PASS` | — | SQL Server SA password (set both to the same value) |
| `LOG_DIR` | `/logs` | Path to NL9 log files |
| `SQL_DB` | `NL9_Traffic` | Database name to restore into |
| `BACKUP_DIR` | `/backups` | Path to dated backup folders |
| `BACKUP_ZIP_NAME` | `NL9_Traffic.zip` | Zip file name inside each date folder |
| `BACKUP_BAK_NAME` | `NL9_Traffic.BAK` | BAK file name inside the zip |
| `IMPORT_HOUR` | `5` | Hour to run the daily import (0–23) |
| `IMPORT_MINUTE` | `0` | Minute to run the daily import |
| `SCHEDULER_TZ` | `Pacific/Auckland` | Timezone for the scheduler |

---

## Endpoints

### Database status

#### `GET /db/status`

Shows import state and next scheduled run.

```json
{
  "last_imported": "2026-03-18",
  "latest_backup": "2026-03-18",
  "up_to_date": true,
  "next_scheduled_import": "2026-03-19T05:00:00+13:00"
}
```

#### `POST /db/import`

Manually trigger an import. Imports the latest backup only if it is newer than the last import. Safe to call repeatedly.

```json
{ "status": "imported", "date": "2026-03-18" }
{ "status": "already_current", "date": "2026-03-18" }
```

---

### Clients

#### `GET /clients`

List clients. Returns active clients by default.

| Param | Description |
|---|---|
| `?id=` / `?custid=` | Filter by exact customer ID |
| `?q=` | Search name, billing name, or contact (substring) |
| `?inactive=1` | Only inactive clients (default: active only) |
| `?is_empty=<field>` | Only clients where the field is null or empty (e.g. `EMail`, `Telephone`) |

```json
{
  "count": 2,
  "clients": [
    {
      "CustID": 101,
      "Inactive": 0,
      "Sponsor": "Acme Radio Co.",
      "BillingName": "Acme Radio Co.",
      "Contact": "Jane Smith",
      "Telephone": "09 123 4567",
      "EMail": "jane@acme.co.nz",
      "CustomerSince": "2019-06-01T00:00:00",
      "Orders": 14
    }
  ]
}
```

#### `GET /clients/<CustID>`

Full client detail with agency information.

---

### Orders

#### `GET /orders`

List orders, sorted most recent first. Excludes deleted orders by default.

| Param | Description |
|---|---|
| `?custid=` | Filter by customer ID |
| `?status=` | Filter by status (e.g. `Active`, `Closed`) |
| `?deleted=1` | Include deleted orders |
| `?start_after=YYYY-MM-DD` | StartDate on or after date |
| `?start_before=YYYY-MM-DD` | StartDate on or before date |
| `?end_after=YYYY-MM-DD` | EndDate on or after date |
| `?end_before=YYYY-MM-DD` | EndDate on or before date |
| `?valid_after=YYYY-MM-DD` | `Status=Ok` and EndDate on or after date |
| `?valid_before=YYYY-MM-DD` | `Status=Ok` and StartDate on or before date |
| `?missing_copy=1` | Only orders with at least one line missing a copy assignment |
| `?exclude_custid=1,2,3` | Exclude one or more customers (comma-separated IDs) |
| `?is_empty=<field>` | Only orders where the field is null or empty (e.g. `PurchaseOrder`, `AccountRep`) |
| `?include_lines=1` | Embed order lines (with `CopyLabel`, `CopyCode`, `CopyLength`, `AudioFileName`) into each order |

Without `?include_lines=1`:

```json
{
  "count": 1,
  "orders": [
    {
      "CustID": 101,
      "OrderID": 55,
      "Status": "Ok",
      "OrderAmount": 1200.0,
      "OrderSpots": 20,
      "StartDate": "2026-03-01T00:00:00",
      "EndDate": "2026-03-31T00:00:00",
      "Sponsor": "Acme Radio Co."
    }
  ]
}
```

With `?include_lines=1`, each order gains a `lines` array (same fields as `GET /orders/<CustID>-<OrderID>`):

```json
{
  "count": 1,
  "orders": [
    {
      "CustID": 101,
      "OrderID": 55,
      "Status": "Ok",
      "Sponsor": "Acme Radio Co.",
      "lines": [
        {
          "LineIndex": 1,
          "CopyIDLong": 8842,
          "CopyCode": "ACM001",
          "CopyLabel": "Acme Summer Sale :30",
          "CopyLength": "30",
          "AudioFileName": "ACM001.wav"
        }
      ]
    }
  ]
}
```

#### `GET /orders/<CustID>-<OrderID>`

Full order detail including lines and station assignments.

```
GET /orders/101-55
```

Response includes:
- Order header fields
- `lines` — each order line with copy label, length, cost, and scheduling details
- `stations` — stations the order runs on

---

### Copy

#### `GET /copy`

List copy records, sorted most recent first. Returns active copy by default.

| Param | Description |
|---|---|
| `?custid=` | Filter by customer ID |
| `?q=` | Search label, copy ID, or script text (substring) |
| `?status=` | Filter by status (e.g. `Ok`, `Deleted`) |
| `?on_date=YYYY-MM-DD` | Only copy with `Status=Ok` whose start/end range covers the date (TFN-safe) |
| `?valid_after=YYYY-MM-DD` | `Status=Ok` and EndDate on or after date |
| `?valid_before=YYYY-MM-DD` | `Status=Ok` and StartDate on or before date |
| `?start_after=YYYY-MM-DD` | StartDate on or after date |
| `?start_before=YYYY-MM-DD` | StartDate on or before date |
| `?end_after=YYYY-MM-DD` | EndDate on or after date |
| `?end_before=YYYY-MM-DD` | EndDate on or before date |
| `?exclude_custid=1,2,3` | Exclude one or more customers (comma-separated IDs) |
| `?is_empty=<field>` | Only copy where the field is null or empty (e.g. `AudioFileName`, `Voice`) |

```json
{
  "count": 1,
  "copy": [
    {
      "CopyIDLong": 8842,
      "CopyID": "ACM001",
      "Label": "Acme Summer Sale :30",
      "Length": "30",
      "Status": "Active",
      "Voice": "John",
      "AudioFileName": "ACM001.wav",
      "Sponsor": "Acme Radio Co."
    }
  ]
}
```

#### `GET /copy/<CopyID>`

Full copy detail including script, copy instructions, and rotator lines.

#### `GET /copy/<CopyID>/resolve`

Resolves a packet or rotator to all audio files active on a given date or date range. Follows nested packets and rotators recursively, filtering each level by date range and day of week.

**Single date** (default: today):

| Param | Default | Description |
|---|---|---|
| `?date=YYYY-MM-DD` | today | Date to resolve for |

```json
{
  "copy_id": "P001",
  "date": "2026-03-18",
  "audio_count": 2,
  "resolved": [
    { "audio": "ACM001.wav", "path": ["P001", "R002", "ACM001"] },
    { "audio": "ACM002.wav", "path": ["P001", "ACM002"] }
  ]
}
```

**Date range** — resolves every day in the range in one call:

| Param | Description |
|---|---|
| `?start=YYYY-MM-DD` | First date (required with `end`) |
| `?end=YYYY-MM-DD` | Last date inclusive (required with `start`) |

```json
{
  "copy_id": "P001",
  "start": "2026-03-18",
  "end": "2026-03-20",
  "days": [
    { "date": "2026-03-18", "audio_count": 2, "resolved": [ ... ] },
    { "date": "2026-03-19", "audio_count": 1, "resolved": [ ... ] },
    { "date": "2026-03-20", "audio_count": 2, "resolved": [ ... ] }
  ]
}
```

`audio_count: 0` for a day means no lines are active for that date/day-of-week.

---

### Account reps

#### `GET /reps`

List account reps. Returns active reps by default.

| Param | Description |
|---|---|
| `?inactive=1` | Include inactive reps |

```json
{
  "count": 2,
  "reps": [
    {
      "AccountRepID": 1,
      "InActive": 0,
      "AccountRepName": "Jane Smith",
      "AccountRepEMail": "jane@example.com",
      "DefaultAgencyCommission": 0.0,
      "DefaultDirectCommission": 0.0,
      "DefaultTradeCommission": 0.0,
      "QBSalesRep": ""
    }
  ]
}
```

#### `GET /reps/<AccountRepID>`

Single account rep record.

---

### Traffic logs

#### `GET /logs`

List available log files, sorted chronologically (newest last). Also available at `GET /`. Returns the most recent 100 by default.

**Query parameters:**

| Param | Default | Description |
|---|---|---|
| `?limit=` | `100` | Number of results to return |
| `?offset=` | `0` | Number of results to skip (for pagination) |

```json
{
  "total": 312,
  "limit": 100,
  "offset": 0,
  "count": 100,
  "logs": [
    { "filename": "031826t.log", "date": "031826", "url": "/log/031826", "entry_count": 94 }
  ]
}
```

#### `GET /log/<MMDDYY>`

All entries for a date.

#### `GET /log/<MMDDYY>/<hour>`

Entries for a specific hour (0–23).

**Log query parameters** (apply to both log endpoints):

| Param | Default | Description |
|---|---|---|
| `?spot_id=` | — | Match spot ID |
| `?description=` | — | Match description |
| `?q=` | — | Match either field |
| `?exact=` | `1` | `1` = exact, `0` = substring |
| `?sort=` | — | Field to sort by |
| `?order=` | `asc` | `asc` or `desc` |

**Log entry fields:**

| Field | Example | Description |
|---|---|---|
| `time` | `"14:00:00"` | Broadcast time |
| `spot_id` | `"5248"` | Spot identifier |
| `description` | `"Acme Summer Sale :30"` | Spot description |
| `duration` | `30` | Duration in seconds |
| `duration_formatted` | `"00:00:30"` | Duration as HH:MM:SS |
| `log_number` | `3` | Traffic log sequence number |

---

## Data directories

| Location | Type | Contents |
|---|---|---|
| `sql-data` | Docker named volume | SQL Server data files — managed by Docker |
| `sql-backup` | Docker named volume | Staged BAK files used during restore — managed by Docker |
| `./logs` | Network-mounted bind mount | NL9 traffic log files |
| `./backups` | Network-mounted bind mount | NL9 daily backup zips |

---

## Notes

- Log files are read on every request — no caching
- The database is a full restore each import; existing data is replaced
- The scheduler runs inside the API process — no separate cron container needed
- SQL Server 2019 can restore SQL Server 2014 backups
