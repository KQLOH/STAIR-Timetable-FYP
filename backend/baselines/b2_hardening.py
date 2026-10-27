"""B2H-v1: template geometry and explicit cell delimiters, never P1 repair."""

from hashlib import sha256
import json
from pathlib import Path
import re

from backend.baselines.b2_rule_parser import B2RuleParserBaseline
from backend.baselines.contracts import PredictionRecord

CONFIG = json.loads(Path(__file__).with_name("b2h_v1.json").read_text(encoding="utf-8"))
CONFIG_DIGEST = sha256(json.dumps(CONFIG, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
CODE = re.compile(r"\b[A-Z]{2,5}\s*\d{3,4}[A-Z]?\b", re.I)
UNKNOWN = re.compile(r"\b(?:TBA|TBD|UNKNOWN|UNREADABLE)\b|\?{2,}", re.I)


class B2HardeningVariant:
    def __init__(self):
        self.rules = B2RuleParserBaseline()  # borrowed lexical rules; original class remains unchanged

    def _headers(self, page):
        headers = {"day": [], "time": []}
        for region in page.regions:
            text = region.text.strip()
            if self.rules.day_re.fullmatch(text):
                headers["day"].append((region, (text,)))
            if self.rules.time_re.fullmatch(text):
                match = self.rules.time_re.fullmatch(text)
                headers["time"].append((region, match.groups()))
        return headers

    def _inherit(self, region, headers):
        if region.bbox is None:
            return []
        box = region.bbox
        candidates = []
        for header, values in headers:
            other = header.bbox
            if other is None or header.evidence_id == region.evidence_id:
                continue
            # Row labels precede to the left; column labels precede above.
            row = min(box.y1, other.y1) > max(box.y0, other.y0) and other.x1 <= box.x0
            column = min(box.x1, other.x1) > max(box.x0, other.x0) and other.y1 <= box.y0
            gap = box.x0 - other.x1 if row else box.y0 - other.y1 if column else None
            if gap is not None and gap <= CONFIG["header_max_gap_pixels"]:
                candidates.append((header, values, gap, "row" if row else "column"))
        # A repeated header in the same column supersedes its older occurrence.
        # Wide merged cells can still overlap several distinct column headers.
        selected = []
        for candidate in candidates:
            header, values, gap, axis = candidate
            newer = any(other[3] == axis and other[2] < gap and other[0].bbox.x0 == header.bbox.x0 and other[0].bbox.x1 == header.bbox.x1 for other in candidates)
            if not newer:
                selected.append((header, values))
        return selected

    def _fragments(self, text):
        fragments = [part.strip() for part in re.split(r"[;|\n]|\s+&\s+", text) if part.strip()]
        output = []
        for part in fragments:
            codes = list(CODE.finditer(part))
            if len(codes) > 1:
                anchors = [match.start() for match in codes]
            else:
                types = list(re.finditer(r"\b(?:Lecture|Tutorial|Practical)\b", part, re.I))
                anchors = [0] + [match.start() for match in types[1:]] if len(types) > 1 and len(self.rules.time_re.findall(part)) >= len(types) else [0]
            if anchors[0] != 0:
                anchors.insert(0, 0)
            output.extend(part[start:end].strip() for start, end in zip(anchors, anchors[1:] + [len(part)]) if part[start:end].strip())
        return output

    def predict_page(self, page, source_id):
        headers = self._headers(page)
        predictions = []
        cell_courses = {}
        for region in page.regions:
            key = region.group or region.evidence_id
            for code in CODE.findall(region.text):
                cell_courses.setdefault(key, []).append((code, region.evidence_id))
        by_id = {region.evidence_id: region for region in page.regions}
        for region in page.regions:
            if any(region is item[0] for kind in headers.values() for item in kind):
                continue
            fragments = self._fragments(region.text)
            for index, fragment in enumerate(fragments):
                subject = self.rules.subject_re.search(fragment)
                times = list(self.rules.time_re.finditer(fragment))
                types = list(self.rules.class_type_re.finditer(fragment))
                day = self.rules.day_re.search(fragment)
                venue = self.rules.venue_re.search(fragment)
                if not (subject or times or types or venue or UNKNOWN.search(fragment) or region.continuation_of):
                    continue
                fields = {"subject": subject.group(1).strip() if subject else "",
                          "day": day.group(0) if day else "", "start_time": "", "end_time": "",
                          "class_type": types[0].group(0) if len(types) == 1 else "",
                          "venue": venue.group(0) if venue else ""}
                links = {name: [region.evidence_id] for name, value in fields.items() if value}
                derived = []
                alternatives = {}
                issues = []
                if (region.group or "").startswith("page-"):
                    issues.append("CELL_BOUNDARY_UNCONFIRMED")
                if not fields["subject"]:
                    context = cell_courses.get(region.group or region.evidence_id, [])
                    if region.continuation_of:
                        prior = by_id.get(region.continuation_of)
                        context = [(code, prior.evidence_id) for code in CODE.findall(prior.text)] if prior else []
                        issues.append("EXPLICIT_CONTINUATION" if prior else "CONTINUATION_TARGET_MISSING")
                    unique = {code.upper() for code, _ in context}
                    if len(unique) == 1:
                        fields["subject"] = context[0][0]
                        links["subject"] = list(dict.fromkeys(item[1] for item in context))
                        derived.append("subject")
                    elif len(unique) > 1:
                        issues.append("AMBIGUOUS_SHARED_COURSE")
                        alternatives["subject"] = [{"raw_value": code, "evidence_ids": [evidence]} for code, evidence in context]
                if len(times) == 1:
                    fields["start_time"], fields["end_time"] = times[0].groups()
                    links.update(start_time=[region.evidence_id], end_time=[region.evidence_id])
                elif len(times) > 1:
                    issues.append("AMBIGUOUS_SESSION_BOUNDARIES")
                    for name, offset in (("start_time", 1), ("end_time", 2)):
                        alternatives[name] = [{"raw_value": item.group(offset), "evidence_ids": [region.evidence_id]} for item in times]
                else:
                    unknown = re.search(r"(?:\bTime\s*:?\s*|\b(?:Lecture|Tutorial|Practical)\s+)(TBA|TBD|UNKNOWN|UNREADABLE)\b", fragment, re.I)
                    if unknown:
                        fields["start_time"] = fields["end_time"] = unknown.group(1)
                        links.update(start_time=[region.evidence_id], end_time=[region.evidence_id])
                    else:
                        inherited = self._inherit(region, headers["time"])
                        self._apply_header(fields, links, derived, alternatives, inherited, ("start_time", "end_time"))
                if not fields["day"]:
                    self._apply_header(fields, links, derived, alternatives, self._inherit(region, headers["day"]), ("day",))
                if len(types) > 1:
                    alternatives["class_type"] = [{"raw_value": match.group(0), "evidence_ids": [region.evidence_id]} for match in types]
                placeholder_venue = re.search(r"\b(?:Room|Venue)\s*:?\s*(TBA|TBD|UNKNOWN)\b", fragment, re.I)
                if placeholder_venue:
                    fields["venue"] = placeholder_venue.group(1)
                    links["venue"] = [region.evidence_id]
                if not fields["subject"] and not alternatives.get("subject"):
                    fields["subject"] = "UNREADABLE" if UNKNOWN.search(fragment) else fragment
                    links["subject"] = [region.evidence_id]
                    issues.append("COURSE_ATTRIBUTION_UNRESOLVED")
                if not region.bbox:
                    issues.append("CELL_GEOMETRY_UNAVAILABLE")
                if not fields["start_time"] and not alternatives.get("start_time"):
                    issues.append("SESSION_TIME_MISSING")
                predictions.append(PredictionRecord(
                    system_id="B2H-v1", run_id="m31-application", prediction_id=f"b2h-{region.evidence_id}-{index}",
                    page_id=f"{source_id}-p{page.page}", split="unassigned", timezone="",
                    decision_status="CANDIDATE", confidence=None, **fields,
                    metadata={"field_evidence": links, "derived_fields": derived, "alternatives": alternatives,
                              "issues": issues, "raw_fragment": fragment, "source_cell": region.group or region.evidence_id,
                              "source_evidence_ids": [region.evidence_id]},
                ))
        return predictions

    @staticmethod
    def _apply_header(fields, links, derived, alternatives, headers, names):
        unique = {values for _, values in headers}
        if len(unique) == 1:
            for index, name in enumerate(names):
                fields[name] = headers[0][1][index]
                links[name] = [header.evidence_id for header, _ in headers]
                derived.append(name)
        elif len(unique) > 1:
            for index, name in enumerate(names):
                alternatives[name] = [{"raw_value": values[index], "evidence_ids": [header.evidence_id]} for header, values in headers]
