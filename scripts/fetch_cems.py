#!/usr/bin/env python3
"""Fetch EPA CEMS hourly data from the EASEY API.

Reads the API key from the EPA_API_KEY environment variable.
Writes cems_hourly_<facilityId>_<begin>_<end>.csv with columns:
    facilityId, facilityName, unitId, date, hour, grossLoad, heatInput,
    co2Mass, so2Mass, noxMass

Also writes <name>.manifest.json with endpoint URL (without key), query params,
retrieval timestamp (UTC), row count, and sha256 of the CSV.

Usage:
    python scripts/fetch_cems.py \
        --facility-id 55555 \
        --begin 2024-01-01 \
        --end 2024-01-31 \
        --out /path/to/output/dir

Rate limits: one month per request, exponential backoff on 429/5xx.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# EPA EASEY API base URL
API_BASE = "https://api.epa.gov/easey"

# Hourly endpoint path (confirmed from EPA EASEY OpenAPI docs)
HOURLY_ENDPOINT = "/camd/hourly/emissions"

# Required columns in output CSV
REQUIRED_COLUMNS = [
    "facilityId",
    "facilityName",
    "unitId",
    "date",
    "hour",
    "grossLoad",
    "heatInput",
    "co2Mass",
    "so2Mass",
    "noxMass",
]


def _get_api_key() -> str:
    """Read API key from environment variable."""
    key = os.environ.get("EPA_API_KEY")
    if not key:
        print("Error: EPA_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def _month_range(begin: str, end: str) -> list[tuple[str, str]]:
    """Split a date range into month-aligned ranges."""
    start = datetime.strptime(begin, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    ranges: list[tuple[str, str]] = []
    current = start
    while current <= end_dt:
        # Last day of current month
        if current.month == 12:
            month_end = datetime(current.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = datetime(current.year, current.month + 1, 1) - timedelta(days=1)
        if month_end > end_dt:
            month_end = end_dt
        ranges.append((current.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")))
        current = month_end + timedelta(days=1)
    return ranges


def _make_request(
    url: str, params: dict[str, str], api_key: str, max_retries: int = 5
) -> list[dict[str, Any]] | None:
    """Make a request with retry logic for 429 and 5xx errors."""
    headers = {
        "Accept": "application/json",
        "X-Api-Key": api_key,  # EPA EASEY uses header authentication
    }

    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    for attempt in range(max_retries):
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    # EPA EASEY returns data in a "data" field
                    return data.get("data", [])
                else:
                    print(f"  Unexpected status: {response.status}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 2**attempt
                msg = f"  HTTP {e.code}, retrying in {wait}s "
                msg += f"(attempt {attempt + 1}/{max_retries})"
                print(msg, file=sys.stderr)
                time.sleep(wait)
                continue
            elif e.code == 401:
                print("  Authentication failed (401): check EPA_API_KEY", file=sys.stderr)
                return None
            elif e.code == 404:
                print(f"  Not found (404): {e.url}", file=sys.stderr)
                return []
            else:
                print(f"  HTTP {e.code}: {e.reason}", file=sys.stderr)
                return None
        except urllib.error.URLError as e:
            print(f"  Network error: {e.reason}, retrying...", file=sys.stderr)
            time.sleep(2**attempt)
            continue
        except Exception as e:
            print(f"  Unexpected error: {e}", file=sys.stderr)
            return None

    print("  Max retries exceeded", file=sys.stderr)
    return None


def _fetch_month(
    facility_id: str, begin: str, end: str, api_key: str
) -> list[dict[str, Any]]:
    """Fetch all pages for a single month."""
    all_rows: list[dict[str, Any]] = []
    page = 1
    page_size = 10000  # API max

    while True:
        params = {
            "facility_id": facility_id,
            "begin_date": begin,
            "end_date": end,
            "page": str(page),
            "per_page": str(page_size),
        }
        url = API_BASE + HOURLY_ENDPOINT

        print(f"  Fetching page {page} ({begin} to {end})...", file=sys.stderr)
        rows = _make_request(url, params, api_key)
        if rows is None:
            return None
        if not rows:
            break

        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    print(f"  Fetched {len(all_rows)} rows for {begin} to {end}", file=sys.stderr)
    return all_rows


def _write_csv(rows: list[dict[str, Any]], csv_path: Path) -> int:
    """Write rows to CSV, keeping only required columns plus any extras."""
    if not rows:
        return 0

    # Determine all columns present
    all_cols = set()
    for row in rows:
        all_cols.update(row.keys())

    # Put required columns first, then extras
    ordered_cols = [c for c in REQUIRED_COLUMNS if c in all_cols]
    extra_cols = sorted(c for c in all_cols if c not in REQUIRED_COLUMNS)
    ordered_cols.extend(extra_cols)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ordered_cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in ordered_cols})

    return len(rows)


def _write_manifest(
    manifest_path: Path,
    endpoint: str,
    params: dict[str, str],
    row_count: int,
    csv_path: Path,
) -> None:
    """Write manifest JSON with retrieval metadata (no API key)."""
    # Compute sha256 of CSV
    h = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)

    manifest = {
        "endpoint": endpoint,
        "query_params": {k: v for k, v in params.items() if k != "api_key"},
        "retrieved_at_utc": datetime.utcnow().isoformat() + "Z",
        "row_count": row_count,
        "csv_sha256": h.hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch EPA CEMS hourly emissions data for a facility and date range."
    )
    parser.add_argument("--facility-id", required=True, help="Facility ID (ORISPL code)")
    parser.add_argument("--begin", required=True, help="Begin date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    args = parser.parse_args()

    # Validate dates
    try:
        begin_dt = datetime.strptime(args.begin, "%Y-%m-%d")
        end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        print("Error: --begin and --end must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)

    if begin_dt > end_dt:
        print("Error: --begin must be before or equal to --end", file=sys.stderr)
        sys.exit(1)

    api_key = _get_api_key()

    # Create output directory
    args.out.mkdir(parents=True, exist_ok=True)

    # Split into month ranges
    month_ranges = _month_range(args.begin, args.end)

    all_rows: list[dict[str, Any]] = []
    for begin, end in month_ranges:
        rows = _fetch_month(args.facility_id, begin, end, api_key)
        if rows is None:
            print(f"Failed to fetch data for {begin} to {end}", file=sys.stderr)
            sys.exit(1)
        all_rows.extend(rows)

    if not all_rows:
        print("No data returned for the specified range", file=sys.stderr)
        sys.exit(0)

    # Write CSV
    csv_name = f"cems_hourly_{args.facility_id}_{args.begin}_{args.end}.csv"
    csv_path = args.out / csv_name
    row_count = _write_csv(all_rows, csv_path)

    # Write manifest
    manifest_name = csv_name.replace(".csv", ".manifest.json")
    manifest_path = args.out / manifest_name
    _write_manifest(
        manifest_path,
        endpoint=API_BASE + HOURLY_ENDPOINT,
        params={
            "facility_id": args.facility_id,
            "begin_date": args.begin,
            "end_date": args.end,
        },
        row_count=row_count,
        csv_path=csv_path,
    )

    print(f"Done. Wrote {row_count} rows to {csv_path}")
    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()


