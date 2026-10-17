#!/usr/bin/env python3
"""List EPA CAMD facilities and their units from the EASEY API.

Reads the API key from the EPA_API_KEY environment variable.
Prints a table: facilityId, facilityName, stateCode, unitId, unitType,
primaryFuelInfo, and whether CO2 is measured or calculated per unit.

Usage:
    python scripts/list_facilities.py --state TX --year 2023
    python scripts/list_facilities.py --state TX --year 2023 --dry-run

Note: The EPA EASEY facilities API does NOT expose whether CO2 is measured
or calculated per unit (no co2MassMeasureFlg or monitoring-method field
in the FacilityAttributesDTO schema). This script prints "N/A (not exposed)"
for that column.

Endpoints verified CONFIRMED via EPA EASEY OpenAPI spec:
    https://api.epa.gov/easey/facilities-mgmt/swagger
    GET /facilities-mgmt/facilities (page, perPage pagination)
    GET /facilities-mgmt/facilities/attributes (year, page, perPage pagination)
    Auth: x-api-key request header (type apiKey, in header)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.epa.gov/easey"

# CONFIRMED from EPA EASEY OpenAPI spec:
# https://api.epa.gov/easey/facilities-mgmt/swagger
FACILITIES_ENDPOINT = "/facilities-mgmt/facilities"
FACILITY_ATTRIBUTES_ENDPOINT = "/facilities-mgmt/facilities/attributes"

API_KEY_HEADER = "x-api-key"


def _get_api_key() -> str:
    """Read API key from environment variable."""
    key = os.environ.get("EPA_API_KEY")
    if not key:
        print("Error: EPA_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def _make_request(
    url: str,
    params: dict[str, str],
    api_key: str,
    max_retries: int = 3,
) -> list[dict[str, Any]] | None:
    """Make a request with retry logic for 429 and 5xx errors.

    EPA EASEY paginated endpoints return response objects with an "items"
    key (per OpenAPI spec). This function unwraps the items array.
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
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, dict) and "items" in data:
                        return data["items"]
                    if isinstance(data, list):
                        return data
                    return [data]
                else:
                    print(
                        f"  Unexpected status: {response.status}",
                        file=sys.stderr,
                    )
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 2**attempt
                print(
                    f"  HTTP {e.code}, retrying in {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            elif e.code == 401:
                print(
                    "  Authentication failed (401): check EPA_API_KEY",
                    file=sys.stderr,
                )
                return None
            elif e.code == 404:
                print("  Not found (404)", file=sys.stderr)
                return []
            else:
                print(f"  HTTP {e.code}: {e.reason}", file=sys.stderr)
                return None
        except urllib.error.URLError as e:
            print(
                f"  Network error: {e.reason}, retrying...",
                file=sys.stderr,
            )
            time.sleep(2**attempt)
            continue
        except Exception as e:
            print(f"  Unexpected error: {e}", file=sys.stderr)
            return None

    print("  Max retries exceeded", file=sys.stderr)
    return None


def _fetch_facilities(state: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all facilities for a state.

    CONFIRMED from spec: /facilities-mgmt/facilities supports page and
    perPage pagination (perPage max 500 per EPA FAQ).
    """
    all_facilities: list[dict[str, Any]] = []
    page = 1
    page_size = 500  # EPA FAQ: "Endpoints that support paging are limited to 500 rows per page"

    while True:
        params = {
            "stateCode": state,
            "page": str(page),
            "perPage": str(page_size),
        }
        url = API_BASE + FACILITIES_ENDPOINT

        facilities = _make_request(url, params, api_key)
        if facilities is None:
            return None
        if not facilities:
            break

        all_facilities.extend(facilities)
        if len(facilities) < page_size:
            break
        page += 1

    return all_facilities


def _fetch_unit_attributes(
    facility_id: str, year: str, api_key: str
) -> list[dict[str, Any]]:
    """Fetch unit attributes for a facility.

    CONFIRMED from spec: /facilities-mgmt/facilities/attributes supports
    page and perPage pagination. The year parameter is required.

    NOTE: The FacilityAttributesDTO schema does NOT include a CO2 measurement
    method field (co2MassMeasureFlg or monitoring_method). This means the
    API does not expose whether CO2 is measured (CEMS) or calculated per unit.
    """
    all_attrs: list[dict[str, Any]] = []
    page = 1
    page_size = 500

    while True:
        params = {
            "facilityId": facility_id,
            "year": year,
            "page": str(page),
            "perPage": str(page_size),
        }
        url = API_BASE + FACILITY_ATTRIBUTES_ENDPOINT

        attrs = _make_request(url, params, api_key)
        if attrs is None:
            return None
        if not attrs:
            break

        all_attrs.extend(attrs)
        if len(attrs) < page_size:
            break
        page += 1

    return all_attrs


def _dry_run(state: str, year: str, out: Path | None) -> None:
    """Print request details without making a network call."""
    print("DRY RUN - no request will be made")
    print("  Auth header:", f"{API_KEY_HEADER}=REDACTED")
    print("  Facilities endpoint:")
    params1 = {"stateCode": state, "page": "1", "perPage": "500"}
    query1 = urllib.parse.urlencode(params1)
    print(f"    GET {API_BASE}{FACILITIES_ENDPOINT}?{query1}")
    print("  Unit attributes endpoint:")
    params2 = {"facilityId": "55555", "year": year, "page": "1", "perPage": "500"}
    query2 = urllib.parse.urlencode(params2)
    print(f"    GET {API_BASE}{FACILITY_ATTRIBUTES_ENDPOINT}?{query2}")
    print("  CO2 measurement method: NOT exposed by EPA API")
    print("    (FacilityAttributesDTO has no co2MassMeasureFlg field)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List EPA EASEY facilities and their units for a given state."
    )
    parser.add_argument(
        "--state", required=True, help="Two-letter state code (e.g., TX, CA, OH)"
    )
    parser.add_argument(
        "--year", default="2023", help="Calendar year for unit attributes (default: 2023)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional: write results to CSV at this path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print method, URL, params, and header names; make no request",
    )
    args = parser.parse_args()

    if len(args.state) != 2:
        print(
            "Error: --state must be a two-letter state code",
            file=sys.stderr,
        )
        sys.exit(1)

    state = args.state.upper()

    if args.dry_run:
        _dry_run(state, args.year, args.out)
        return

    api_key = _get_api_key()

    print(f"Fetching facilities for state {state}...", file=sys.stderr)
    facilities = _fetch_facilities(state, api_key)
    if facilities is None:
        print("Failed to fetch facilities", file=sys.stderr)
        sys.exit(1)

    if not facilities:
        print(f"No facilities found for state {state}")
        sys.exit(0)

    print(
        f"Found {len(facilities)} facilities. Fetching unit attributes for {args.year}...",
        file=sys.stderr,
    )

    rows: list[dict[str, Any]] = []
    for facility in facilities:
        facility_id = facility.get("facilityId")
        facility_name = facility.get("facilityName")

        attrs = _fetch_unit_attributes(str(facility_id), args.year, api_key)
        if attrs is None:
            print(
                f"  Failed to fetch attributes for facility {facility_id}",
                file=sys.stderr,
            )
            rows.append({
                "facilityId": facility_id,
                "facilityName": facility_name,
                "stateCode": state,
                "unitId": "",
                "unitType": "",
                "primaryFuelInfo": "",
                "co2Method": "N/A (not exposed)",
            })
            continue

        if not attrs:
            rows.append({
                "facilityId": facility_id,
                "facilityName": facility_name,
                "stateCode": state,
                "unitId": "",
                "unitType": "",
                "primaryFuelInfo": "",
                "co2Method": "N/A (not exposed)",
            })
            continue

        for unit in attrs:
            rows.append({
                "facilityId": facility_id,
                "facilityName": facility_name,
                "stateCode": state,
                "unitId": unit.get("unitId", ""),
                "unitType": unit.get("unitType", ""),
                "primaryFuelInfo": unit.get("primaryFuelInfo", ""),
                # EPA EASEY FacilityAttributesDTO schema does NOT include a
                # CO2 measurement method field. Print this plainly.
                "co2Method": "N/A (not exposed by EPA API)",
            })

    headers = [
        "facilityId",
        "facilityName",
        "stateCode",
        "unitId",
        "unitType",
        "primaryFuelInfo",
        "co2Method",
    ]
    col_widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row.get(h, ""))))

    header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
    print(header_line)
    print("-" * len(header_line))

    for row in rows:
        print(" | ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers))

    # Write CSV if requested
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in headers})
        print(f"CSV written to {args.out}")

    # Note: CO2 measurement method is not available from the EPA API
    print(
        "\nNote: The EPA EASEY facilities API (FacilityAttributesDTO) does NOT"
        " expose whether CO2 is measured or calculated per unit. Use "
        "parse_cems_hourly on downloaded hourly emissions data to check "
        "co2MassMeasureFlg values.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
