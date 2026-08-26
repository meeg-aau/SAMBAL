import re
from typing import Any, Dict, List, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta
from sample_metadata_curation.constants import MISSING_VALUES

# Keys to search for collection date in order of preference
DATE_KEYS = [
    "collection_date",
    "event_date_time",
    "collection_time",
    "sampling_date",
    "date_collected",
    "sample_collection_date",
    "collection_date_experiment",
]

# Resolution levels in order of increasing precision
# These are the options you can set with min_resolution, which defaults to None
RESOLUTION_ORDER = ["year", "month", "day"]

# Maps each resolution level to a time duration of that length (used to check date range spans)
RESOLUTION_DELTA = {
    "year": relativedelta(years=1),
    "month": relativedelta(months=1),
    "day": relativedelta(days=1),
}

# INSDC supported date formats and their resolution
# Format: (regex pattern, resolution, strptime format (fmt "%d/%m/%y") or None)
DATE_FORMATS = [
    # ISO 8601: YYYY-MM-DD
    (
        re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "day",
        "%Y-%m-%d",
    ),
    # ISO 8601: YYYY-MM
    (
        re.compile(r"^\d{4}-\d{2}$"),
        "month",
        "%Y-%m",
    ),
    # ISO 8601: YYYY
    (
        re.compile(r"^\d{4}$"),
        "year",
        "%Y",
    ),
    # INSDC legacy: DD-Mmm-YYYY e.g. 15-Jan-2019
    (
        re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"),
        "day",
        "%d-%b-%Y",
    ),
    # INSDC legacy: Mmm-YYYY e.g. Jan-2019
    (
        re.compile(r"^[A-Za-z]{3}-\d{4}$"),
        "month",
        "%b-%Y",
    ),
    # ISO 8601 with time: YYYY-MM-DDTHH:MM:SSZ
    (
        re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z?$"),
        "day",
        None,  # handled separately to ignore the time component
    ),
    # Slash format - try US (MM/DD/YYYY) then non-US (DD/MM/YYYY)
    (
        re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
        "day",
        "%m/%d/%Y",
    ),
    # Slash format - YYYY/MM/DD (unambiguous)
    (
        re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"),
        "day",
        "%Y/%m/%d",
    ),
    (
        re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
        "day",
        "%d/%m/%Y",
    ),
    # Hyphen format - US (MM-DD-YYYY)
    (
        re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
        "day",
        "%m-%d-%Y",
    ),
    # Hyphen format - non-US (DD-MM-YYYY)
    (
        re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
        "day",
        "%d-%m-%Y",
    ),
]

def _is_ambiguous_date(s: str) -> bool:
    """
    Returns True if the date string is ambiguous because both the first
    and second numeric components are <= 12, making day/month order unclear.
    """
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}$", s)
    if not m:
        return False
    first, second = int(m.group(1)), int(m.group(2))
    # If either component is > 12, the date is unambiguous
    return first <= 12 and second <= 12

def _parse_to_datetime(s: str) -> Optional[datetime]:
    """Parse a date string to a datetime object for span calculation."""
    for pattern, resolution, fmt in DATE_FORMATS:
        if pattern.match(s) and fmt is not None:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None

def _parse_date_string(date_str: str) -> Optional[Dict[str, Any]]:
    s = date_str.strip()

    if not s or s.lower() in MISSING_VALUES:
        return None

    # Check for ambiguity FIRST
    if _is_ambiguous_date(s):
        return {"collection_date": None, "date_resolution": None, "ambiguous": True}

    # Check for date range, where a slash separates two date parts each at least a year
    # Returns the start date of the range
    if "/" in s:
        parts = s.split("/")
        start, end = parts[0].strip(), parts[1].strip()
        # Only treat as a range if start looks like a complete date
        # (not just a 1-2 digit number like MM or DD)
        if len(start) >= 4 and len(end) >= 4:
            parsed_start = _parse_date_string(start)
            if parsed_start and not parsed_start.get("ambiguous"):
                return {
                    "collection_date": parsed_start["collection_date"],
                    "date_resolution": "date_range",
                    "range_start": start,
                    "range_end": end,
                }
            return None
        # Otherwise fall through to format matching

    # Parse standard formats
    for pattern, resolution, fmt in DATE_FORMATS:
        if pattern.match(s):
            if fmt is None:
                # ISO 8601 with time — extract date part
                date_part = s[:10]
                return {
                    "collection_date": s,
                    "date_resolution": "day",
                }
            try:
                dt = datetime.strptime(s, fmt)
                if resolution == "day":
                    normalised = dt.strftime("%Y-%m-%d")
                elif resolution == "month":
                    normalised = dt.strftime("%Y-%m")
                else:
                    normalised = dt.strftime("%Y")
                return {
                    "collection_date": normalised,
                    "date_resolution": resolution,
                }
            except ValueError:
                continue

    return None

# Define the level of resolution you want to filter to (default month)
class DateCurator:
    def __init__(
        self,
        min_resolution: Optional[str] = None,
        date_keys: Optional[List[str]] = None,
    ):
        """
        Parameters
        ----------
        min_resolution : str
            Minimum acceptable resolution: "year", "month", "day", or None (default).
            Samples with lower resolution will FAIL.
        date_keys : list of str, optional
            Additional normalised keys to search for date fields,
            on top of the defaults in DATE_KEYS.
        """
        if min_resolution is not None and min_resolution not in RESOLUTION_ORDER:
            raise ValueError(
                f"min_resolution must be one of {RESOLUTION_ORDER} or None, "
                f"got '{min_resolution}'"
            )
        self.min_resolution = min_resolution
        self.min_resolution_index = (
            RESOLUTION_ORDER.index(min_resolution) if min_resolution else None
        )
        self.date_keys = DATE_KEYS + (date_keys or [])

    def _find_date_value(self, cleaned_dict: Dict[str, Any]) -> Optional[str]:
        """Find the first present date value from the cleaned dict."""
        for key in self.date_keys:
            val = cleaned_dict.get(key)
            if val and str(val).lower() not in MISSING_VALUES:
                return str(val).strip()
        return None

    def curate_date(self, cleaned_dict: Dict[str, Any]) -> Dict[str, Any]:
        out = {
            "collection_date": None,
            "date_resolution": None,
            "date_check_status": "SKIP",
            "date_check_reason": None,
        }

        raw_date = self._find_date_value(cleaned_dict)

        if not raw_date:
            out["date_check_status"] = "SKIP"
            out["date_check_reason"] = "no_date_found"
            return out

        parsed = _parse_date_string(raw_date)

        if parsed is None:
            out["date_check_status"] = "FAIL"
            out["date_check_reason"] = "unparseable_date"
            return out

        if parsed.get("ambiguous"):
            out["date_check_status"] = "FAIL"
            out["date_check_reason"] = "ambiguous_date_format"
            return out

        out["collection_date"] = parsed["collection_date"]
        out["date_resolution"] = parsed["date_resolution"]

        # Check if the date range span is within the defined resolution
        if parsed["date_resolution"] == "date_range":
            start_dt = _parse_to_datetime(parsed["range_start"])
            end_dt = _parse_to_datetime(parsed["range_end"])
            if start_dt is None or end_dt is None:
                out["date_check_status"] = "FAIL"
                out["date_check_reason"] = "unparseable_date"
                return out
            if self.min_resolution is None:
                out["date_check_status"] = "PASS"
                out["date_check_reason"] = "date_range"
            else:
                threshold_dt = start_dt + RESOLUTION_DELTA[self.min_resolution]
                if end_dt <= threshold_dt:
                    out["date_check_status"] = "PASS"
                    out["date_check_reason"] = "date_range"
                else:
                    out["date_check_status"] = "FAIL"
                    out["date_check_reason"] = "date_range_too_broad"
            return out

        if self.min_resolution is None:
            out["date_check_status"] = "PASS"
            out["date_check_reason"] = "no_minimum_resolution"
        else:
            resolution_index = RESOLUTION_ORDER.index(parsed["date_resolution"])
            if resolution_index >= self.min_resolution_index:
                out["date_check_status"] = "PASS"
                out["date_check_reason"] = "sufficient_resolution"
            else:
                out["date_check_status"] = "FAIL"
                out["date_check_reason"] = "insufficient_resolution"

        return out