"""M3 bridge from uploaded source evidence and frozen B1/B2 outputs to M1."""

from hashlib import sha256
import json
from pathlib import Path
import re

from backend.baselines.b1_ocr_regex import B1OCRRegexBaseline
from backend.baselines.b2_rule_parser import B2RuleParserBaseline
from backend.baselines.b2_hardening import B2HardeningVariant, CONFIG_DIGEST as B2H_CONFIG_DIGEST
from backend.baselines.contracts import EvaluationDocumentEvidence, EvaluationTokenSpan
from backend.models.evidence import BoundingBox, EvidenceSpan, ParseIssue, SourceDocument
from backend.models.event import (
    CandidateAlternative, CanonicalEvent, CanonicalTimetable, EventContent,
    EventIdentity, EventRevision, ExtractionStatus, FieldObservation, FieldState,
)
from backend.models.extraction import DocumentEvidence
from backend.services.normalizer import normalize_class_type, normalize_date, normalize_day, normalize_time


def _contract_evidence(document: DocumentEvidence) -> tuple[EvidenceSpan, ...]:
    spans = []
    for page in document.pages:
        for region in page.regions:
            adapter = (region.adapter or "").lower()
            route = "native_pdf" if "pdf-native" in adapter else (
                "text" if document.route_type == "upload_text" else "ocr"
            )
            coord = "PDF_POINTS" if "pdf-native" in adapter else "PIXELS"
            bbox = None if region.bbox is None else BoundingBox(
                x0=region.bbox.x0, y0=region.bbox.y0, x1=region.bbox.x1,
                y1=region.bbox.y1, coordinate_space=coord,
            )
            spans.append(EvidenceSpan(
                evidence_id=region.evidence_id, source_document_id=document.source_id,
                route=route, raw_text=region.text, page=page.page, bbox=bbox,
                locator=f"page:{page.page};line:{region.line or 'unknown'}",
                confidence=region.confidence,
                transformations=(f"extractor:{document.parser_version}", f"adapter:{region.adapter}",
                                 f"source-group:{region.group or 'unassigned'}") + tuple(
                                     f"ocr-{key}:{page.runtime[key]}" for key in ("engine_version", "wrapper_version", "config", "language") if key in page.runtime),
            ))
    return tuple(spans)


def adapt_extraction(document: DocumentEvidence) -> CanonicalTimetable:
    route = "native_pdf" if document.route_type == "upload_pdf" else (
        "text" if document.route_type == "upload_text" else "ocr"
    )
    source = SourceDocument(
        source_document_id=document.source_id, content_sha256=document.checksum,
        route=route, media_type=document.media_type, content_hash_basis="original_bytes",
    )
    issues = tuple(ParseIssue(code=warning, detail="Extraction warning retained.") for warning in document.warnings)
    return CanonicalTimetable(source=source, evidence=_contract_evidence(document), issues=issues)


def _baseline_page(document: DocumentEvidence, page, pipeline: str) -> EvaluationDocumentEvidence:
    route = "PDF_NATIVE" if document.route_type == "upload_pdf" else "OCR"
    tokens = []
    for index, region in enumerate(page.regions):
        box = region.bbox
        if box:
            bbox = [box.x0, box.y0, box.x1, box.y1]
        else:
            continue  # Never fabricate geometry for a source region.
        tokens.append(EvaluationTokenSpan(
            token_id=region.evidence_id, text=region.text, bbox=bbox,
            bbox_scale="PIXEL", confidence=region.confidence, extraction_confidence=region.confidence,
            line_num=region.line or index + 1, page_num=page.page, reading_order=index + 1,
        ))
    return EvaluationDocumentEvidence(
        source_id=document.source_id, source_document_id=document.source_id,
        page_id=f"{document.source_id}-p{page.page}", split="unassigned",
        route_type=route, tokens=[] if pipeline == "B1" and any(r.bbox is None for r in page.regions) else tokens, raw_text=page.text,
        metadata={"pipeline": pipeline, "source_route": document.route_type},
    )


def _field(name: str, raw: str | None, evidence_ids: tuple[str, ...], normalized=None):
    raw = raw.strip() if isinstance(raw, str) else raw
    if not raw:
        return None, FieldObservation(field_name=name, state=FieldState.MISSING)
    if not evidence_ids or normalized is None:
        return None, FieldObservation(field_name=name, state=FieldState.UNRESOLVED,
                                      raw_value=raw, evidence_ids=evidence_ids)
    return normalized, FieldObservation(field_name=name, state=FieldState.PRESENT,
                                        raw_value=raw, evidence_ids=evidence_ids)


def adapt_baseline(document: DocumentEvidence, pipeline: str) -> CanonicalTimetable:
    """Run baseline candidate generation only on OCR routes and preserve M1 status separation."""
    if pipeline not in {"B1", "B2", "B2H-v1"}:
        raise ValueError("pipeline must be B1, B2, or B2H-v1")
    if document.route_type not in {"upload_ocr", "upload_image"}:
        raise ValueError("B1/B2 require OCR evidence; native PDF text is not substituted")
    source = SourceDocument(
        source_document_id=document.source_id, content_sha256=document.checksum,
        route="ocr", media_type=document.media_type, content_hash_basis="original_bytes",
    )
    spans = _contract_evidence(document)
    if any(span.route != "ocr" for span in spans):
        raise ValueError("B1/B2 require OCR evidence; native PDF text is not substituted")
    baseline = B2HardeningVariant() if pipeline == "B2H-v1" else (
        B1OCRRegexBaseline({"system_id": "B1", "run_id": "m3-development"}) if pipeline == "B1" else B2RuleParserBaseline({"system_id": "B2", "run_id": "m3-development"})
    )
    events = []
    issues = []
    if pipeline == "B2H-v1":
        config_hash = B2H_CONFIG_DIGEST
    else:
        code_path = Path(__file__).parents[1] / "baselines" / ("b1_ocr_regex.py" if pipeline == "B1" else "b2_rule_parser.py")
        # M3 incorrectly claimed a YAML configuration hash without loading it.
        # Record the actual executable source and constructor configuration.
        config_hash = sha256(json.dumps({"source_sha256": sha256(code_path.read_bytes()).hexdigest(),
                                        "constructor": baseline.config, "adapter": "m31"}, sort_keys=True).encode()).hexdigest()
    for page in document.pages:
        page_input = _baseline_page(document, page, pipeline)
        predictions = baseline.predict_page(page, document.source_id) if pipeline == "B2H-v1" else baseline.predict_page(page_input)
        if not predictions and page.regions:
            issues.append(ParseIssue(code=f"{pipeline}_NO_CANDIDATES", evidence_ids=tuple(r.evidence_id for r in page.regions),
                                     detail="No baseline candidates were emitted; source evidence is retained."))
        for prediction in predictions:
            values = {
                "course_code": prediction.subject if re.fullmatch(r"[A-Z]{2,5}\s*\d{3,4}[A-Z]?", prediction.subject or "", re.I) else None,
                "course_name": prediction.subject if not re.fullmatch(r"[A-Z]{2,5}\s*\d{3,4}[A-Z]?", prediction.subject or "", re.I) else None,
                "day_of_week": prediction.day,
                "date": prediction.date,
                "start_time": prediction.start_time,
                "end_time": prediction.end_time,
                "session_type": prediction.class_type,
                "location": prediction.venue,
            }
            normalized = {
                "course_code": values["course_code"], "course_name": values["course_name"],
                "day_of_week": ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY").index(normalize_day(prediction.day)) + 1 if normalize_day(prediction.day) else None,
                "date": normalize_date(prediction.date),
                "start_time": normalize_time(prediction.start_time),
                "end_time": normalize_time(prediction.end_time, allow_end_of_day=True),
                "session_type": normalize_class_type(prediction.class_type),
                "location": prediction.venue or None,
            }
            observations = []
            content_values = {}
            for name, raw in values.items():
                original_name = {"course_code": "subject", "course_name": "subject", "day_of_week": "day", "session_type": "class_type", "location": "venue"}.get(name, name)
                links = prediction.metadata.get("field_evidence", {})
                matches = tuple(links[original_name]) if original_name in links else tuple(span.evidence_id for span in spans if span.page == page.page and raw and raw.casefold() in span.raw_text.casefold())
                if raw and (re.search(r"\b(?:TBA|TBD|UNKNOWN|UNREADABLE)\b|\?{2,}", raw, re.I) or name in {"course_code", "course_name"} and "COURSE_ATTRIBUTION_UNRESOLVED" in prediction.metadata.get("issues", [])):
                    normalized[name] = None
                alternatives = prediction.metadata.get("alternatives", {}).get(original_name, [])
                if alternatives and name != "course_code":
                    distinct = {item["raw_value"]: item for item in alternatives}
                    retained = tuple(CandidateAlternative(raw_value=item["raw_value"], evidence_ids=tuple(item["evidence_ids"])) for item in distinct.values())
                    observation = FieldObservation(field_name=name, state=FieldState.CONFLICTING if len(retained) > 1 else FieldState.UNRESOLVED,
                                                   alternatives=retained)
                    observations.append(observation)
                    content_values[name] = None
                    continue
                value, observation = _field(name, raw, matches, normalized[name])
                if original_name in prediction.metadata.get("derived_fields", []) and raw:
                    observation = observation.model_copy(update={"origin": "derived", "transformations": ("B2H-v1:unambiguous-source-context",)})
                observations.append(observation)
                content_values[name] = value
            if prediction.recurrence:
                recurrence_evidence = tuple(span.evidence_id for span in spans if span.page == page.page and prediction.recurrence.casefold() in span.raw_text.casefold())
                observations.append(FieldObservation(
                    field_name="recurrence", state=FieldState.UNRESOLVED,
                    raw_value=prediction.recurrence, evidence_ids=recurrence_evidence,
                ))
                content_values["recurrence"] = None
            # The M1 registry contains additional explicit fields; baseline omissions remain MISSING.
            for name in EventContent.model_fields:
                if name not in content_values:
                    content_values[name] = None
                    observations.append(FieldObservation(field_name=name, state=FieldState.MISSING))
            content = EventContent(**content_values)
            states = {obs.field_name: obs.state for obs in observations}
            has_core = bool((content.course_code or content.course_name) and (content.day_of_week or content.date) and content.start_time and content.end_time)
            status = ExtractionStatus.EXTRACTED if has_core and all(
                states[name] == FieldState.PRESENT for name in ("course_code" if content.course_code else "course_name", "day_of_week" if content.day_of_week else "date", "start_time", "end_time")
            ) else ExtractionStatus.PARTIAL
            if FieldState.CONFLICTING in states.values():
                status = ExtractionStatus.CONFLICTING
            elif FieldState.UNRESOLVED in states.values():
                status = ExtractionStatus.AMBIGUOUS
            revision = EventRevision(
                source_document_id=document.source_id, method_id="b2h_v1_m31" if pipeline == "B2H-v1" else f"baseline_{pipeline.lower()}_m31_adapter",
                configuration_digest=config_hash, content=content, observations=tuple(observations),
            )
            events.append(CanonicalEvent(
                candidate_id=f"{pipeline.lower()}-{prediction.prediction_id}", identity=EventIdentity(),
                revision=revision, extraction_status=status,
            ))
            for code in prediction.metadata.get("issues", []):
                issues.append(ParseIssue(code=f"B2H_V1_{code}", candidate_id=f"{pipeline.lower()}-{prediction.prediction_id}",
                                         evidence_ids=tuple(prediction.metadata.get("source_evidence_ids", []))))
            if prediction.decision_status == "ACCEPT":
                issues.append(ParseIssue(code=f"{pipeline}_BASELINE_ACCEPT_NOT_VERIFICATION", candidate_id=f"{pipeline.lower()}-{prediction.prediction_id}", detail="Baseline decision preserved as a candidate-generation output only; M1 policy, human verification and calendar states remain unset."))
        # Surface baseline parser blind spots for explicit multi-session and merged-cell content.
        for region in page.regions:
            if pipeline == "B2" and region.bbox is None:
                issues.append(ParseIssue(code="B2_GEOMETRY_MISSING", evidence_ids=(region.evidence_id,)))
            if pipeline != "B2H-v1" and re.search(r"(?:;|\||\n|\s&\s)", region.text):
                issues.append(ParseIssue(code=f"{pipeline}_COMPOUND_CELL_REVIEW", evidence_ids=(region.evidence_id,), detail="Baseline parser may emit only its first match; compound cell retained for review."))
    return CanonicalTimetable(source=source, evidence=spans, events=tuple(events), issues=tuple(issues))
