#!/usr/bin/env python3
"""Fetch EPA CEMS hourly data from the EASEY API.

Reads the API key from the EPA_API_KEY environment variable.
Writes cems_hourly_<facilityId>_<begin>_<end>.csv with columns:
    stateCode, facilityName, facilityId, unitId, unitType,
    primaryFuelInfo, secondaryFuelInfo, grossLoad, heatInput,
    co2Mass, so2Mass, noxMass, co2MassMeasureFlg, heatInputMeasureFlg,
    opTime, date, hour

Also writes <name>.manifest.json with endpoint URL (without key), query params,
retrieval timestamp (UTC), row count, and sha256 of the CSV.

Usage:
    python scripts/fetch_cems.py \\
        --facility-id 55555 \\
        --begin 2024-01-01 \\
        --end 2024-01-31 \\
        --out /path/to/output/dir

    python scripts/fetch_cems.py --dry-run \\
        --facility-id 55555 --begin 2024-01-01 --end 2024-01-31

Rate limits: 1,000 requests/hour, 15-minute timeout per request.
The streaming-services endpoint does NOT support pagination (per EPA EASEY
OpenAPI spec at https://api.epa.gov/easey/streaming-services/swagger).
Month-aligned splitting is used to bound response size and stay within
rate limits.

Endpoints verified CONFIRMED via EPA EASEY OpenAPI spec:
    GET https://api.epa.gov/easey/streaming-services/emissions/apportioned/hourly
    Auth: x-api-key request header (type apiKey, in header)
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

API_BASE = "https://api.epa.gov/easey"

# CONFIRMED from EPA EASEY OpenAPI spec:
# https://api.epa.gov/easey/streaming-services/swagger
# (path: "/streaming-services/emissions/apportioned/hourly")
HOURLY_ENDPOINT = "/streaming-services/emissions/apportioned/hourly"

# Auth: CONFIRMED from EPA EASEY OpenAPI spec securitySchemes.APIKey:
# type=apiKey, in=header, name=x-api-key
API_KEY_HEADER = "x-api-key"

# Output CSV columns: all known EPA CEMS hourly emissions schema fields
OUTPUT_COLUMNS = [
    "stateCode",
    "facilityName",
    "facilityId",
    "unitId",
    "unitType",
    "primaryFuelInfo",
    "secondaryFuelInfo",
    "grossLoad",
    "heatInput",
    "co2Mass",
    "so2Mass",
    "noxMass",
    "co2MassMeasureFlg",
    "heatInputMeasureFlg",
    "opTime",
    "date",
    "hour",
]


def _get_api_key() -> str:
    """Read API key from environment variable."""
    key = os.environ.get("EPA_API_KEY")
    if not key:
        print("Error: EPA_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def _month_range(begin: str, end: str) -> list[tuple[str, str]]:
    """Split a date range into month-aligned ranges.

    EPA's streaming-services endpoint does NOT support pagination (per the
    OpenAPI spec). Month splitting bounds response size and respects the
    1,000 requests/hour rate limit.
    """
    start = datetime.strptime(begin, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    ranges: list[tuple[str, str]] = []
    current = start
    while current <= end_dt:
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
    """Make a request with retry logic for 429 and 5xx errors.

    EPA EASEY streaming-services endpoint returns data as a JSON array
    or wrapped in a 'data'/'items' key. No pagination tokens are needed.
    """
    headers = {
        "Accept": "application/json",
        API_KEY_HEADER: api_key,
    }

    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    for attempt in range(max_retries):
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=900) as response:
                if response.status == 200:
                    content = response.read().decode("utf-8")
                    data = json.loads(content)
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        for key in ("data", "items"):
                            if key in data and isinstance(data[key], list):
                                return data[key]
                        return [data]
                    return []
                else:
                    print(
                        f"  Unexpected status: {response.status}",
                        file=sys.stderr,
                    )
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 2**attempt
                msg = f"  HTTP {e.code}, retrying in {wait}s "
                msg += f"(attempt {attempt + 1}/{max_retries})"
                print(msg, file=sys.stderr)
                time.sleep(wait)
                continue
            elif e.code == 401:
                print(
                    "  Authentication failed (401): check EPA_API_KEY",
                    file=sys.stderr,
                )
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


def _fetch_range(
    facility_id: str, begin: str, end: str, api_key: str
) -> list[dict[str, Any]]:
    """Fetch emissions for a single date range (no pagination needed)."""
    params = {
        "facilityId": facility_id,
        "beginDate": begin,
        "endDate": end,
    }
    url = API_BASE + HOURLY_ENDPOINT

    print(f"  Fetching {begin} to {end}...", file=sys.stderr)
    rows = _make_request(url, params, api_key)
    if rows is None:
        return None
    print(f"  Fetched {len(rows)} rows for {begin} to {end}", file=sys.stderr)
    return rows


def _write_csv(rows: list[dict[str, Any]], csv_path: Path) -> int:
    """Write rows to CSV with all known CEMS columns."""
    if not rows:
        return 0

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in OUTPUT_COLUMNS})

    return len(rows)


def _write_manifest(
    manifest_path: Path,
    endpoint: str,
    params: dict[str, str],
    row_count: int,
    csv_path: Path,
) -> None:
    """Write manifest JSON with retrieval metadata (no API key)."""
    h = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)

    manifest = {
        "endpoint": endpoint,
        "query_params": params,
        "retrieved_at_utc": datetime.utcnow().isoformat() + "Z",
        "row_count": row_count,
        "csv_sha256": h.hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _dry_run(facility_id: str, begin: str, end: str, out: Path) -> None:
    """Print request details without making a network call."""
    month_ranges = _month_range(begin, end)
    print("DRY RUN - no request will be made")
    print("  Method: GET")
    print(f"  Endpoint: {API_BASE}{HOURLY_ENDPOINT}")
    print(f"  Auth header: {API_KEY_HEADER}=REDACTED")
    print("  Date ranges to fetch (month-aligned):")
    for b, e in month_ranges:
        params = {"facilityId": facility_id, "beginDate": b, "endDate": e}
        query = urllib.parse.urlencode(params)
        print(f"    {API_BASE}{HOURLY_ENDPOINT}?{query}")
    print(f"  Output CSV columns: {', '.join(OUTPUT_COLUMNS)}")
    csv_name = f"cems_hourly_{facility_id}_{begin}_{end}.csv"
    print(f"  Would write CSV: {out / csv_name}")
    print(f"  Would write manifest: {out / csv_name.replace('.csv', '.manifest.json')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch EPA CEMS hourly emissions data from EASEY API."
    )
    parser.add_argument(
        "--facility-id", required=True, help="Facility ID (ORISPL code)"
    )
    parser.add_argument("--begin", required=True, help="Begin date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--out", required=True, type=Path, help="Output directory"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print method, URL, params, and header names; make no request",
    )
    args = parser.parse_args()

    # Validate dates
    try:
        begin_dt = datetime.strptime(args.begin, "%Y-%m-%d")
        end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        print(
            "Error: --begin and --end must be in YYYY-MM-DD format",
            file=sys.stderr,
        )
        sys.exit(1)

    if begin_dt > end_dt:
        print(
            "Error: --begin must be before or equal to --end",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dry_run:
        _dry_run(args.facility_id, args.begin, args.end, args.out)
        return

    api_key = _get_api_key()

    args.out.mkdir(parents=True, exist_ok=True)

    month_ranges = _month_range(args.begin, args.end)

    all_rows: list[dict[str, Any]] = []
    for begin, end in month_ranges:
        rows = _fetch_range(args.facility_id, begin, end, api_key)
        if rows is None:
            print(
                f"Failed to fetch data for {begin} to {end}",
                file=sys.stderr,
            )
            sys.exit(1)
        all_rows.extend(rows)

    if not all_rows:
        print("No data returned for the specified range", file=sys.stderr)
        sys.exit(0)

    csv_name = f"cems_hourly_{args.facility_id}_{args.begin}_{args.end}.csv"
    csv_path = args.out / csv_name
    row_count = _write_csv(all_rows, csv_path)

    manifest_name = csv_name.replace(".csv", ".manifest.json")
    manifest_path = args.out / manifest_name
    _write_manifest(
        manifest_path,
        endpoint=API_BASE + HOURLY_ENDPOINT,
        params={
            "facilityId": args.facility_id,
            "beginDate": args.begin,
            "endDate": args.end,
        },
        row_count=row_count,
        csv_path=csv_path,
    )

    print(f"Done. Wrote {row_count} rows to {csv_path}")
    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
