# NL9 Traffic Log API

A lightweight Flask API that parses Natural Log 9 (NL9) traffic log files and exposes them as JSON. Designed to run in Docker with a read-only mount of log files.

## Quickstart

```bash
docker compose up -d
```

Place your NL9 log files in `./logs/` (or adjust the volume path in `docker-compose.yaml`).

## File naming convention

Files must follow the NL9 pattern `MMDDYYt.log`, e.g.:

```
022026t.log   ← Feb 20, 2026
022126t.log   ← Feb 21, 2026
022226t.log   ← Feb 22, 2026
```

All matching files in the mounted directory are picked up automatically.

## Endpoints

### `GET /`

Lists all available log files with metadata.

```json
[
  {
    "filename": "022026t.log",
    "date": "022026",
    "url": "/log/022026",
    "entry_count": 87
  }
]
```

---

### `GET /log/<MMDDYY>`

Returns all entries for a given date.

```
/log/022026
```

**Example response:**
```json
{
  "date": "022026",
  "entry_count": 87,
  "entries": [
    {
      "line_number": 1,
      "time": "00:20:00",
      "spot_id": "5248",
      "description": "Celebration Shoutouts: P",
      "duration": 30,
      "duration_formatted": "00:00:30",
      "log_number": 3
    }
  ]
}
```

---

### `GET /log/<MMDDYY>/<hour>`

Returns entries for a specific hour (0–23) within a date.

```
/log/022026/6    ← entries in the 06:xx:xx hour
/log/022026/14   ← entries in the 14:xx:xx hour
```

**Example response:**
```json
{
  "date": "022026",
  "hour": 6,
  "entry_count": 12,
  "entries": [...]
}
```

## Entry fields

| Field | Type | Example | Description |
|---|---|---|---|
| `line_number` | int | `1` | Log line sequence number |
| `time` | string | `"00:20:00"` | Broadcast time (HH:MM:SS) |
| `spot_id` | string | `"5248"` | Spot identifier |
| `description` | string | `"Celebration Shoutouts: P"` | Spot description |
| `duration` | int | `30` | Duration in seconds |
| `duration_formatted` | string | `"00:00:30"` | Duration as HH:MM:SS |
| `log_number` | int | `3` | Traffic log sequence number |

## Query parameters

All filter and sort parameters work on both `/log/<date>` and `/log/<date>/<hour>`.

### Filtering by text fields

Filter on `spot_id` or `description` individually. By default matching is **exact** (case-insensitive). Add `?exact=0` for substring search.

```
?spot_id=5248
?description=Fuel&exact=0
```

Use `?q=` to search across both text fields at once:

```
?q=Fuel&exact=0
```

| Param | Default | Description |
|---|---|---|
| `?spot_id=` | — | Match spot ID field |
| `?description=` | — | Match description field |
| `?q=` | — | Match either spot_id or description |
| `?exact=` | `1` | `1` = exact match, `0` = substring |

---

### Sorting

```
?sort=duration&order=desc
?sort=time&order=asc
```

| Param | Default | Description |
|---|---|---|
| `?sort=` | — | Any entry field name to sort by |
| `?order=` | `asc` | `asc` or `desc` |

Entries with a missing value for the sort key are placed last.

---

### Combining parameters

All parameters can be combined freely:

```
/log/022026?q=Fuel&exact=0&sort=duration&order=desc
/log/022026/6?spot_id=5248
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `LOG_DIR` | `/logs` | Path to the directory containing NL9 log files |

## Notes

- Files are read on every request — no caching or database
- The `→` character in NL9 log lines is used as a field delimiter in parsing
