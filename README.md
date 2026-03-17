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

**2. Create host directories** (once, before first run):

```bash
chmod +x setup.sh && ./setup.sh
```

This creates the `logs` and `backups` bind-mount directories. SQL Server data is kept in Docker named volumes (`sql-data`, `sql-backup`) which Docker manages automatically — no permission setup needed.

**3. Start the stack:**

```bash
docker compose up -d
```

**4. Trigger the first database import:**

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
| `?id=` | Filter by exact customer ID |
| `?q=` | Search name, billing name, or contact (substring) |
| `?inactive=1` | Include inactive clients |

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

List orders. Excludes deleted orders by default.

| Param | Description |
|---|---|
| `?custid=` | Filter by customer ID |
| `?status=` | Filter by status (e.g. `Active`, `Closed`) |
| `?deleted=1` | Include deleted orders |

```json
{
  "count": 1,
  "orders": [
    {
      "CustID": 101,
      "OrderID": 55,
      "Status": "Active",
      "OrderAmount": 1200.0,
      "OrderSpots": 20,
      "StartDate": "2026-03-01T00:00:00",
      "EndDate": "2026-03-31T00:00:00",
      "Sponsor": "Acme Radio Co."
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

List copy records. Returns active copy by default.

| Param | Description |
|---|---|
| `?custid=` | Filter by customer ID |
| `?q=` | Search label, copy ID, or script text (substring) |
| `?inactive=1` | Include inactive copy |

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

#### `GET /copy/<CopyIDLong>`

Full copy detail including script, copy instructions, and rotator lines.

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

List all available log files. Also available at `GET /`.

```json
[
  { "filename": "031826t.log", "date": "031826", "url": "/log/031826", "entry_count": 94 }
]
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

These local directories are created automatically by Docker on first run:

| Location | Type | Contents |
|---|---|---|
| `sql-data` | Docker named volume | SQL Server data files — managed by Docker |
| `sql-backup` | Docker named volume | Staged BAK files used during restore — managed by Docker |
| `./logs` | Bind mount | NL9 traffic log files |
| `./backups` | Bind mount | NL9 daily backup zips |

---

## Notes

- Log files are read on every request — no caching
- The database is a full restore each import; existing data is replaced
- The scheduler runs inside the API process — no separate cron container needed
- SQL Server 2019 can restore SQL Server 2014 backups
