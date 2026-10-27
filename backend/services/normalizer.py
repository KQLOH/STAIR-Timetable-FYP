"""Conservative baseline normalization port; no baseline runtime imports.

Unknown values return None and must be retained by the caller as UNRESOLVED.
Source selection and adaptations are documented in docs/m1_provenance.json.
"""

from datetime import datetime
import re


DAY_ALIASES = {
    "mon": "MONDAY", "monday": "MONDAY", "tue": "TUESDAY", "tues": "TUESDAY",
    "tuesday": "TUESDAY", "wed": "WEDNESDAY", "wednesday": "WEDNESDAY",
    "thu": "THURSDAY", "thur": "THURSDAY", "thurs": "THURSDAY", "thursday": "THURSDAY",
    "fri": "FRIDAY", "friday": "FRIDAY", "sat": "SATURDAY", "saturday": "SATURDAY",
    "sun": "SUNDAY", "sunday": "SUNDAY",
}
CLASS_ALIASES = {
    "l": "Lecture", "lec": "Lecture", "lecture": "Lecture",
    "p": "Practical", "prac": "Practical", "practical": "Practical", "lab": "Practical",
    "t": "Tutorial", "tut": "Tutorial", "tutorial": "Tutorial",
}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None


def normalize_day(value: str | None) -> str | None:
    cleaned = clean_text(value)
    return DAY_ALIASES.get(cleaned.lower().rstrip(".")) if cleaned else None


def normalize_time(value: str | None, *, allow_end_of_day: bool = False) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    if cleaned == "24:00":
        return "24:00" if allow_end_of_day else None
    cleaned = cleaned.upper().replace(".", "")
    # strptime can split short digit strings in surprising ways (e.g. 123).
    if not re.fullmatch(r"(?:\d{1,2}:\d{2}|\d{4})(?:\s*[AP]M)?|\d{1,2}\s*[AP]M", cleaned):
        return None
    for pattern in ("%I:%M %p", "%I:%M%p", "%I%M %p", "%I%M%p", "%I %p", "%I%p", "%H:%M", "%H%M"):
        try:
            return datetime.strptime(cleaned, pattern).strftime("%H:%M")
        except ValueError:
            pass
    return None


def normalize_date(value: str | None) -> str | None:
    """TAR UMT dates are ISO. Ambiguous slash dates need source-specific authority."""
    cleaned = clean_text(value)
    if not cleaned or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return None
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def normalize_class_type(value: str | None) -> str | None:
    cleaned = clean_text(value)
    return CLASS_ALIASES.get(cleaned.lower().strip("().")) if cleaned else None


def normalize_venue(value: str | None) -> str | None:
    """Whitespace only; preserve source labels rather than stripping possible content."""
    return clean_text(value)
