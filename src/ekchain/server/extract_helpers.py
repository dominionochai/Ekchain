"""Shared helpers for Tier 2 on-demand extraction tools.

Provides ``extract_and_index`` (the store pipeline used by every
extraction tool).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import datetime

from ekchain.models import WindowRow

logger = logging.getLogger(__name__)

_WINDOW_CHAR_BUDGET = 4096

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_PLASO_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})")
_SYSLOG_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})"
)
_SYSLOG_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _parse_timestamp(text: str, reference_year: int | None = None) -> str | None:
    """Best-effort timestamp extraction from a text window.

    Args:
        text: Raw text window to search for a recognizable timestamp.
        reference_year: Year to assume for syslog timestamps that omit a
            year field.  Falls back to the current year when *None*.
    """
    m = _ISO_RE.search(text)
    if m:
        try:
            return datetime.fromisoformat(m.group(0)).isoformat()
        except ValueError:
            pass

    m = _SYSLOG_RE.search(text)
    if m:
        month_str, day, hour, minute, second = m.groups()
        try:
            dt = datetime(
                year=reference_year or datetime.now().year,
                month=_SYSLOG_MONTHS[month_str],
                day=int(day),
                hour=int(hour),
                minute=int(minute),
                second=int(second),
            )
            return dt.isoformat()
        except ValueError:
            pass

    m = _PLASO_DATE_RE.search(text)
    if m:
        month, day, year, hour, minute, second = m.groups()
        try:
            dt = datetime(
                year=int(year),
                month=int(month),
                day=int(day),
                hour=int(hour),
                minute=int(minute),
                second=int(second),
            )
            return dt.isoformat()
        except ValueError:
            pass

    logger.debug("No timestamp pattern matched in window text (first 80 chars): %r", text[:80])
    return None


_INSERT_BATCH_SIZE = 5000


def _line_aligned_windows(raw_output: str) -> Iterator[tuple[str, int, int]]:
    """Yield ``(text, first_line, last_line)`` windows that never split a line.

    Windows used to be cut at absolute character offsets
    (``raw_output[offset : offset + 4096]``), which severs whichever record
    straddles the boundary.  Around twenty callers then read *one* record out
    of each window -- a PID, a module name, a command line -- so a severed row
    does not merely lose data, it yields a **wrong** value: a truncated PID
    that still parses as an integer, or a process name from one row paired
    with the PID from another.  Two windows sampled from the same output can
    then be diffed against each other and produce fabricated "hidden process"
    or "hidden module" findings.

    Line numbers are 1-based and inclusive -- which is what the ``line_start``
    and ``line_end`` columns have always claimed to hold, and did not.

    A single line longer than the window budget is still split, because it has
    to be; that is pathological input (a whole file on one line) rather than
    the routine case.
    """
    budget = _WINDOW_CHAR_BUDGET
    current: list[str] = []
    current_len = 0
    first_line = 1
    last_line = 1

    for line_no, line in enumerate(raw_output.splitlines(keepends=True), start=1):
        pieces = (
            [line[i : i + budget] for i in range(0, len(line), budget)]
            if len(line) > budget
            else [line]
        )
        for piece in pieces:
            if current and current_len + len(piece) > budget:
                text = "".join(current)
                if text.strip():
                    yield text, first_line, last_line
                current = []
                current_len = 0
                first_line = line_no
            current.append(piece)
            current_len += len(piece)
            last_line = line_no

    if current:
        text = "".join(current)
        if text.strip():
            yield text, first_line, last_line


def extract_and_index(
    raw_output: str,
    source_name: str,
    source_path: str,
    extractor_name: str,
) -> dict[str, object]:
    """Split raw tool output into windows and store in the case DB.

    Registers a source, splits text into fixed-size windows (4 lines
    each), extracts timestamps, and inserts into the database in
    batches of ``_INSERT_BATCH_SIZE`` to bound peak memory.

    Returns a summary dict with ``source_name``, ``windows_indexed``,
    ``line_count``, and ``status``.
    """
    from ekchain.server.app import get_ctx

    ctx = get_ctx()

    if not raw_output or not raw_output.strip():
        source_id = ctx.db.register_source(
            source_name=source_name,
            source_path=source_path,
            source_hash="blake2b:empty",
            extractor=extractor_name,
            line_count=0,
        )
        return {
            "source_name": source_name,
            "source_id": source_id,
            "windows_indexed": 0,
            "line_count": 0,
            "status": "indexed_empty",
        }

    original_line_count = raw_output.count("\n") + 1

    h = hashlib.blake2b(digest_size=32)
    chunk_size = 65536
    for i in range(0, len(raw_output), chunk_size):
        h.update(raw_output[i : i + chunk_size].encode())
    content_hash = "blake2b:" + h.hexdigest()

    source_id = ctx.db.register_source(
        source_name=source_name,
        source_path=source_path,
        source_hash=content_hash,
        extractor=extractor_name,
        line_count=original_line_count,
    )

    total_indexed = 0
    batch: list[WindowRow] = []

    for window_text, first_line, last_line in _line_aligned_windows(raw_output):
        event_time = _parse_timestamp(window_text)
        batch.append(
            WindowRow(
                source_id=source_id,
                line_start=first_line,
                line_end=last_line,
                event_time=event_time,
                raw_text=window_text,
            )
        )
        if len(batch) >= _INSERT_BATCH_SIZE:
            ctx.db.insert_windows(source_id, batch)
            total_indexed += len(batch)
            batch = []

    del raw_output

    if batch:
        ctx.db.insert_windows(source_id, batch)
        total_indexed += len(batch)

    return {
        "source_name": source_name,
        "source_id": source_id,
        "windows_indexed": total_indexed,
        "line_count": original_line_count,
        "status": "indexed",
    }
