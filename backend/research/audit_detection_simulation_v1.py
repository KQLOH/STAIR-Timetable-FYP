"""Provisional finite-frame diagnostic; never an event-accuracy confidence bound.

Uniform page defect placement and ceiling rounding require explicit approval.
No real-frame simulation is run by PRE-01 or import. Authentic execution requires
the registered frame/seed/iterations and a separately approved assumption record.
"""

from math import ceil
from random import Random

from backend.research.audit import AuditError
from backend.research.integrity import record_digest
from backend.research.models import Attestation, AuditSample, SourcePage


def simulate_detection(pages: tuple[SourcePage, ...], sample: AuditSample, approval: Attestation,
                       *, iterations=100000, seed=20260903) -> dict:
    eligible = sorted(p.page_id for p in pages if p.timetable_bearing is True and p.partition != "excluded")
    frame_digest = record_digest([p.model_dump(mode="json") for p in sorted(pages, key=lambda p: p.page_id)])
    if sample.frame_digest != frame_digest or not set(sample.page_ids).issubset(eligible):
        raise AuditError("SIMULATION_FRAME_SAMPLE_MISMATCH")
    if approval.origin != sample.finalized_by.origin or iterations < 1:
        raise AuditError("SIMULATION_APPROVAL_OR_ITERATIONS_INVALID")
    if approval.origin == "AUTHENTIC" and (len(eligible) != 168 or len(sample.page_ids) != 29 or iterations != 100000 or seed != 20260903):
        raise AuditError("REGISTERED_SIMULATION_CONDITIONS_CHANGED")
    rng, selected = Random(seed), set(sample.page_ids)
    scenarios = []
    for percent in (5, 10, 20):
        defects = ceil(len(eligible) * percent / 100)
        detected = sum(bool(selected.intersection(rng.sample(eligible, defects))) for _ in range(iterations))
        scenarios.append({"affected_page_percent": percent, "affected_pages": defects,
                          "detected": detected, "iterations": iterations, "probability": detected / iterations})
    result = {"schema_version": "m2.0", "diagnostic_only": True,
              "assumptions": "UNIFORM_PAGE_WITHOUT_REPLACEMENT_CEILING",
              "seed": seed, "frame_pages": len(eligible), "sample_digest": record_digest(sample),
              "frame_digest": frame_digest, "approval": approval.model_dump(mode="json"), "scenarios": scenarios}
    return {**result, "output_digest": record_digest(result)}
