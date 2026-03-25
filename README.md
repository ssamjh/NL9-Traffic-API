# NL9 Traffic API

A Flask API for Natural Log 9 (NL9) traffic data. Exposes two data sources:

- **Traffic logs** — parses NL9 daily broadcast log files
- **Database** — queries client, order, copy, invoice, and account rep data from a SQL Server database

---

## Operating modes

The API supports two database modes, controlled by the `DB_MODE` environment variable:

| Mode | Value | How it works |
|---|---|---|
| **Backup/restore** (default) | `DB_MODE=backup` | Downloads a daily `.BAK` file, restores it into a local SQL Server container, and queries that. Imports run automatically each day. |
| **Live** | `DB_MODE=live` | Connects directly to the source SQL Server using a read-only account. No backups are needed, no imports are scheduled. |

---

## Requirements

- Docker and Docker Compose
- NL9 log files in `./logs/`
- **Backup mode only:** NL9 daily database backups in `./backups/YYYY-MM-DD/`

---

## Setup — backup mode

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

## Setup — live mode

Point the API at the source SQL Server directly. No local SQL Server container is needed; remove or disable the `sqlserver` service from `docker-compose.yaml`.

**Finding the SQL Server port** — run this from an Administrator command prompt on the NL9 server:

```
type "C:\Program Files\Microsoft SQL Server\MSSQL12.NATURALSERVER\MSSQL\Log\ERRORLOG" | findstr /i "listening"
```

This prints the port the instance is actively listening on (typically 1433, but may differ).

Set these environment variables in `docker-compose.yaml` (or however you run the API):

```yaml
DB_MODE:    "live"
SQL_SERVER: "192.168.1.x"          # IP or hostname of the NL9 SQL Server
SQL_PORT:   "1433"                 # port from the ERRORLOG command above
SQL_USER:   "api"
SQL_PASS:   "your-api-password"
SQL_DB:     "NL9_Traffic"
```

The `BACKUP_DIR`, `BACKUP_ZIP_NAME`, `BACKUP_BAK_NAME`, `IMPORT_HOUR`, `IMPORT_MINUTE`, and `SCHEDULER_TZ` variables are ignored in live mode.

`GET /db/status` returns the current connection details. `POST /db/import` returns a 400 error (not applicable in live mode).

---

## Setting up the read-only SQL Server user

The NL9 SQL Server (`NATURALSERVER` instance) has a logon trigger that restricts access, and the Dedicated Admin Connection (DAC) is disabled by default. Follow these steps to add a read-only `api` user.

### Step 1 — Stop the SQL Server service

Open a command prompt as Administrator on the NL9 server:

```
net stop MSSQL$NATURALSERVER
```

### Step 2 — Restart with trace flag 7806 to enable DAC

```
net start MSSQL$NATURALSERVER /T7806
```

### Step 3 — Connect using the Dedicated Admin Connection

```
sqlcmd -A -S localhost\NATURALSERVER
```

### Step 4 — Find the database name

List the user databases on the server to confirm the correct database name:

```sql
SELECT name FROM sys.databases WHERE database_id > 4;
GO
```

Note the name of the database you want to grant access to.

### Step 5 — Create the login and grant read-only access

Replace `api` with the desired username, `change-me-password` with a strong password, and `NL9_Traffic` with the database name from the previous step:

```sql
CREATE LOGIN [api] WITH PASSWORD = 'change-me-password';
GO

USE [NL9_Traffic];
GO

CREATE USER [api] FOR LOGIN [api];
GO

ALTER ROLE [db_datareader] ADD MEMBER [api];
GO
```

### Step 6 — Whitelist the new user in the logon trigger

The `Prevent_login` trigger only allows specific logins. Add the new user to the allowed list:

```sql
ALTER TRIGGER Prevent_login ON ALL SERVER WITH EXECUTE AS 'NBS_Software' FOR LOGON AS
BEGIN
    DECLARE @LoginName sysname
    SET @LoginName = ORIGINAL_LOGIN()
    IF(@LoginName NOT IN ('NBS_Software', 'api'))
    BEGIN
        ROLLBACK;
    END
END;
GO
```

If adding another user in the future, add them to the `NOT IN` list as well.

### Step 7 — Exit sqlcmd

```
EXIT
```

### Step 8 — Restart normally (without the trace flag)

```
net stop MSSQL$NATURALSERVER
net start MSSQL$NATURALSERVER
```

### Step 9 — Test the new login

```
sqlcmd -S localhost\NATURALSERVER -U api -P change-me-password
```

---

## Backup folder structure

_(Backup mode only)_

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
| `DB_MODE` | `backup` | `backup` = restore daily BAK; `live` = connect directly to source server |
| `SQL_SERVER` | `sqlserver` | SQL Server hostname or IP |
| `SQL_PORT` | `1433` | SQL Server port |
| `SQL_USER` | `sa` | SQL Server login username |
| `SA_PASSWORD` / `SQL_PASS` | — | SQL Server SA password — backup mode only (set both to the same value) |
| `LOG_DIR` | `/logs` | Path to NL9 log files |
| `SQL_DB` | `NL9_Traffic` | Database name |
| `BACKUP_DIR` | `/backups` | Path to dated backup folders — backup mode only |
| `BACKUP_ZIP_NAME` | `NL9_Traffic.zip` | Zip file name inside each date folder — backup mode only |
| `BACKUP_BAK_NAME` | `NL9_Traffic.BAK` | BAK file name inside the zip — backup mode only |
| `IMPORT_HOUR` | `5` | Hour to run the daily import (0–23) — backup mode only |
| `IMPORT_MINUTE` | `0` | Minute to run the daily import — backup mode only |
| `SCHEDULER_TZ` | `Pacific/Auckland` | Timezone for the scheduler — backup mode only |

---

## Endpoints

### Database status

#### `GET /db/status`

Shows import state and next scheduled run.

Backup mode:

```json
{
  "mode": "backup",
  "last_imported": "2026-03-18",
  "latest_backup": "2026-03-18",
  "up_to_date": true,
  "next_scheduled_import": "2026-03-19T05:00:00+13:00"
}
```

Live mode:

```json
{
  "mode": "live",
  "server": "192.168.1.x",
  "database": "NL9_Traffic",
  "info": "Connected directly to live database. No backup import scheduled."
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
| `?billing_name=` | Search billing name only (substring) |
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
| `?status=` | Filter by status (e.g. `Ok`, `Deleted`) |
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

### Invoices

#### `GET /invoices`

List invoices, sorted most recent first. Excludes deleted invoices by default.

| Param | Description |
|---|---|
| `?custid=` | Filter by customer ID |
| `?q=` | Search sponsor or billing name (substring) |
| `?deleted=1` | Include deleted invoices |
| `?trans_type=` | Filter by transaction type (e.g. `INV`) |
| `?date_after=YYYY-MM-DD` | InvDate on or after date |
| `?date_before=YYYY-MM-DD` | InvDate on or before date |

```json
{
  "count": 1,
  "invoices": [
    {
      "InvoiceIDLong": 1,
      "InvoiceID": "26010001",
      "InvTransType": "INV",
      "InvDate": "2026-01-31T00:00:00",
      "InvCustID": 101,
      "InvOrderID": 55,
      "InvSponsor": "...",
      "InvBillingName": "...",
      "InvGross": 200.0,
      "InvTaxable": 200.0,
      "InvTaxPct": 0.15
    }
  ]
}
```

#### `GET /invoices/<InvoiceID>`

Full invoice detail by human-readable invoice number (e.g. `26010001`) or by numeric `InvoiceIDLong`. Returns the invoice joined with the customer's contact details and account rep name.

```
GET /invoices/26010001
```

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

| Location | Type | Mode | Contents |
|---|---|---|---|
| `sql-data` | Docker named volume | backup only | SQL Server data files — managed by Docker |
| `sql-backup` | Docker named volume | backup only | Staged BAK files used during restore — managed by Docker |
| `./logs` | Network-mounted bind mount | both | NL9 traffic log files |
| `./backups` | Network-mounted bind mount | backup only | NL9 daily backup zips |

---

## Notes

- Log files are read on every request — no caching
- The database is a full restore each import; existing data is replaced
- The scheduler runs inside the API process — no separate cron container needed
- SQL Server 2019 can restore SQL Server 2014 backups
