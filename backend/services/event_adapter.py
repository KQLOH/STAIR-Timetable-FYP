"""TAR UMT structured-source adapter, separate from B1/B2/B3/B4/P1.

Capture/candidate UUIDs are local record IDs, never durable source-session IDs.
No selective controller, constraint engine, human attestation or calendar writer
is executed here. Unknown source-local values remain explicit.
"""

from hashlib import sha256
import json
import re
from uuid import uuid4

from backend.models.evidence import EvidenceSpan, ParseIssue, SourceDocument
from backend.models.event import (
    CandidateAlternative, CanonicalEvent, CanonicalTimetable, EventContent,
    EventIdentity, EventRevision, ExtractionStatus, FieldObservation, FieldState,
)
from backend.services.normalizer import (
    clean_text, normalize_class_type, normalize_date, normalize_day,
    normalize_time, normalize_venue,
)
from backend.services.timetable_parser import STATUS_COLOURS, TITLE_PATTERN, TIME_PATTERN, clean_space


ADAPTER_CONFIGURATION = {
    "adapter": "tarumt_parser_adapter_m1", "contract": "m1.0",
    "date_format": "ISO_ONLY", "timezone": "NO_DEFAULT",
    "identity": "UNRESOLVED_UNTIL_SOURCE_AUTHORITY",
    "recurrence": "NO_INFERENCE_FROM_WEEK_VIEW",
}
CONFIGURATION_DIGEST = sha256(json.dumps(
    ADAPTER_CONFIGURATION, sort_keys=True, separators=(",", ":")
).encode("utf-8")).hexdigest()
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
LEGACY_FIELDS = {
    "course_code": "subject_code", "course_name": "subject_name",
    "session_type": "class_type", "lecturer": "lecturer",
    "location": "venue", "location_detail": "venue_full",
    "day_of_week": "day", "date": "date", "start_time": "start_time",
    "end_time": "end_time", "source_class_status": "class_status",
}


def _source_fields(record: dict) -> tuple[dict, tuple[str, str] | None]:
    """Extract original substrings, reusing the application's tooltip grammar."""
    visible = clean_space(record["visible_text"])
    title = record["tooltip_title"]
    header = record["raw_header"]
    raw = {}
    course = re.search(r"\b([A-Z]{2,}\d{3,})\b", visible, re.IGNORECASE)
    if course:
        raw["course_code"] = course.group(1)
    visible_type = re.search(r"\(([A-Za-z]+)\)", visible)
    if visible_type:
        raw["session_type"] = visible_type.group(1)
    times = re.search(TIME_PATTERN, visible, re.IGNORECASE)
    if times is None:
        times = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", visible)
    if times:
        raw["start_time"], raw["end_time"] = times.groups()
    day = re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", header, re.IGNORECASE)
    date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", header)
    if day:
        raw["day_of_week"] = day.group(1)
    elif clean_text(header):
        raw["day_of_week"] = header
    if date:
        raw["date"] = date.group(1)
    title_match = re.match(TITLE_PATTERN, clean_space(title), re.IGNORECASE)
    conflict = None
    if title_match:
        name, title_type, lecturer, venue = title_match.groups()
        raw.update(course_name=name, lecturer=lecturer, location=venue.split(",", 1)[0], location_detail=venue)
        if "session_type" in raw and normalize_class_type(raw["session_type"]) != normalize_class_type(title_type):
            conflict = (raw["session_type"], title_type)
        else:
            raw["session_type"] = title_type
    elif clean_text(title):
        # Text exists but the parser cannot attribute these conditional fields.
        for name in ("course_name", "lecturer", "location", "location_detail"):
            raw[name] = title
    colour = re.search(r"background-color\s*:\s*(#[0-9a-f]{6})", record["style"], re.IGNORECASE)
    if colour:
        raw["source_class_status"] = colour.group(1)
    return raw, conflict


def _normalize(name: str, value: str, *, unattributed_title: bool = False):
    if unattributed_title and name in {"course_name", "lecturer", "location", "location_detail"}:
        return None
    if name == "day_of_week":
        day = normalize_day(value)
        return WEEKDAYS.index(day) + 1 if day else None
    if name == "date":
        return normalize_date(value)
    if name in {"start_time", "end_time"}:
        return normalize_time(value, allow_end_of_day=name == "end_time")
    if name == "session_type":
        return normalize_class_type(value)
    if name == "source_class_status":
        return STATUS_COLOURS.get(value.lower()) if value.startswith("#") else (value if value in STATUS_COLOURS.values() else None)
    if name in {"location", "location_detail"}:
        return normalize_venue(value)
    if name == "course_code":
        cleaned = clean_text(value)
        return cleaned.upper() if cleaned and re.fullmatch(r"[A-Za-z]{2,}\d{3,}", cleaned) else None
    return clean_text(value)


def _ambiguous_source_fields(record: dict) -> dict[str, tuple[str, ...]]:
    """Retain multiple possible values instead of choosing the first regex match."""
    visible = clean_space(record["visible_text"])
    candidates = {"course_code": re.findall(r"\b[A-Z]{2,}\d{3,}\b", visible, re.IGNORECASE)}
    time_ranges = list(re.finditer(TIME_PATTERN, visible, re.IGNORECASE))
    if len(time_ranges) > 1:
        candidates["start_time"] = [match.group(1) for match in time_ranges]
        candidates["end_time"] = [match.group(2) for match in time_ranges]
    result = {}
    for name, values in candidates.items():
        # Equivalent spellings/duplicates do not constitute separate alternatives.
        unique = {}
        for value in values:
            unique.setdefault(_normalize(name, value) or value, value)
        if len(unique) > 1:
            result[name] = tuple(unique.values())
    return result


def adapt_tarumt_response(parsed: dict) -> CanonicalTimetable:
    """Use original cell evidence when available, otherwise label parser-only evidence.

    A selected teaching-week view is not evidence of a full recurring schedule.
    No source-family/session IDs, timezone, excluded weeks or term boundaries are
    inferred from content or a capture digest.
    """
    capture_id = f"capture:{uuid4()}"
    original_available = "_source_records" in parsed
    if original_available:
        digest = parsed["_source_sha256"]
        records = parsed["_source_records"]
    else:
        serialized = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        records = [{"parsed_event": event} for event in parsed["events"]]
    source = SourceDocument(
        source_document_id=capture_id, content_sha256=digest,
        route="tarumt_html" if original_available else "text",
        media_type="text/html" if original_available else "application/json",
        content_hash_basis="decoded_text_utf8" if original_available else "parser_output_json",
    )
    evidence, events, issues = [], [], []
    metadata = parsed.get("_metadata_text")
    if metadata:
        evidence.append(EvidenceSpan(
            evidence_id=f"{capture_id}:metadata", source_document_id=capture_id,
            route="tarumt_html", raw_text=metadata,
            locator="semester strong / #fweek option[selected]",
            transformations=("select_semester_and_week_text",),
        ))
    for index, record in enumerate(records):
        prefix = f"{capture_id}:candidate:{index}"
        cell_id, header_id = f"{prefix}:cell", f"{prefix}:header"
        conflict = None
        ambiguous_fields = {}
        unattributed_title = False
        if original_available:
            locator = f"weekly-table/tr[{record['row_index']}]/td[{record['cell_index']}] (zero-based)"
            if "tooltip_index" in record:
                locator += f" / tooltip[{record['tooltip_index']}] (whole-cell crop retained)"
            evidence.extend((
                EvidenceSpan(evidence_id=cell_id, source_document_id=capture_id, route="tarumt_html",
                             raw_text=record["raw_text"], raw_html=record["raw_html"], locator=locator,
                             transformations=("BeautifulSoup:lxml:cell_subtree_serialization",)),
                EvidenceSpan(evidence_id=header_id, source_document_id=capture_id, route="tarumt_html",
                             raw_text=record["raw_header"], locator=f"weekly-table/tr[{record['row_index']}]/td[0] (zero-based)"),
            ))
            raw, conflict = _source_fields(record)
            ambiguous_fields = _ambiguous_source_fields(record)
            unattributed_title = bool(clean_text(record["tooltip_title"]) and not re.match(
                TITLE_PATTERN, clean_space(record["tooltip_title"]), re.IGNORECASE))
            if record["issue_code"]:
                issues.append(ParseIssue(code=record["issue_code"], evidence_ids=(cell_id, header_id), candidate_id=prefix))
        else:
            event = record["parsed_event"]
            evidence.append(EvidenceSpan(
                evidence_id=cell_id, source_document_id=capture_id, route="text",
                raw_text=json.dumps(event, sort_keys=True, ensure_ascii=False),
                locator=f"parser-output/events[{index}]", transformations=("legacy_parser_output_only",),
            ))
            raw = {name: str(event[key]) for name, key in LEGACY_FIELDS.items() if event.get(key) is not None}
            issues.append(ParseIssue(code="ORIGINAL_SOURCE_UNAVAILABLE", evidence_ids=(cell_id,), candidate_id=prefix))
        values, observations = {}, []
        for name in EventContent.model_fields:
            evidence_ids = (header_id,) if original_available and name in {"date", "day_of_week"} else (cell_id,)
            if name in ambiguous_fields:
                observations.append(FieldObservation(
                    field_name=name, state=FieldState.UNRESOLVED,
                    raw_value=record["visible_text"], evidence_ids=evidence_ids,
                    alternatives=tuple(CandidateAlternative(raw_value=value,
                        normalized_value=_normalize(name, value), evidence_ids=evidence_ids) for value in ambiguous_fields[name]),
                    justification="Multiple possible source values; no first-match selection",
                ))
                issues.append(ParseIssue(code="AMBIGUOUS_SOURCE_FIELD", evidence_ids=evidence_ids, candidate_id=prefix, detail=name))
                continue
            if name == "session_type" and conflict:
                observations.append(FieldObservation(
                    field_name=name, state=FieldState.CONFLICTING, evidence_ids=evidence_ids,
                    alternatives=tuple(CandidateAlternative(raw_value=value,
                        normalized_value=normalize_class_type(value), evidence_ids=evidence_ids) for value in conflict),
                    justification="Visible session type and tooltip type disagree",
                ))
                issues.append(ParseIssue(code="CONFLICTING_SESSION_TYPE", evidence_ids=evidence_ids, candidate_id=prefix))
                continue
            if name not in raw or not clean_text(raw[name]):
                observations.append(FieldObservation(field_name=name))
                continue
            value = _normalize(name, raw[name], unattributed_title=unattributed_title)
            if value is not None:
                values[name] = value
            observations.append(FieldObservation(
                field_name=name, state=FieldState.PRESENT if value is not None else FieldState.UNRESOLVED,
                raw_value=raw[name], evidence_ids=evidence_ids,
                origin="derived" if name == "source_class_status" else "source",
                transformations=(f"tarumt_m1:{name}:conservative_normalization",),
            ))
        revision = EventRevision(source_document_id=capture_id, configuration_digest=CONFIGURATION_DIGEST,
                                 content=EventContent(**values), observations=tuple(observations))
        states = {observation.state for observation in observations}
        if FieldState.CONFLICTING in states:
            status = ExtractionStatus.CONFLICTING
        elif FieldState.UNRESOLVED in states:
            status = ExtractionStatus.AMBIGUOUS
        elif not revision.core_fields_present or record.get("issue_code"):
            status = ExtractionStatus.PARTIAL
        else:
            status = ExtractionStatus.EXTRACTED
        events.append(CanonicalEvent(candidate_id=prefix, identity=EventIdentity(),
                                     revision=revision, extraction_status=status))
    return CanonicalTimetable(source=source, evidence=tuple(evidence), events=tuple(events), issues=tuple(issues))


def canonical_api_data(parsed: dict) -> dict:
    """Add the contract while retaining the existing public metadata/events fields."""
    return {**{key: value for key, value in parsed.items() if not key.startswith("_")},
            "canonical": adapt_tarumt_response(parsed).model_dump(mode="json")}
