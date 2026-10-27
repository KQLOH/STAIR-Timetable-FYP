"""Evaluation interchange contracts and DTOs (INT-004, EVAL-005).

Provides standalone interchange models for baseline systems and evaluation runners,
decoupled from Production backend API models.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EvaluationTokenSpan(BaseModel):
    """Token representation with bounding box used across baseline systems."""

    token_id: str
    text: str
    bbox: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    bbox_scale: str = "PIXEL"  # PIXEL or NORMALIZED_1000
    confidence: Optional[float] = 1.0
    extraction_confidence: Optional[float] = 1.0
    line_num: Optional[int] = 1
    block_num: Optional[int] = 1
    page_num: Optional[int] = 1
    reading_order: Optional[int] = 1


class EvaluationDocumentEvidence(BaseModel):
    """Document evidence representation for baseline evaluation."""

    evidence_schema_version: str = "2.0"
    source_id: str
    source_document_id: Optional[str] = ""
    page_id: str
    split: str = "validation"
    route_type: str = "PDF_NATIVE"
    image_path: Optional[str] = None
    width: Optional[float] = 1000.0
    height: Optional[float] = 1000.0
    tokens: List[EvaluationTokenSpan] = Field(default_factory=list)
    raw_text: Optional[str] = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PredictionRecord(BaseModel):
    """Prediction output record conforming to EVAL-005."""

    system_id: str
    run_id: str
    prediction_id: str
    page_id: str
    split: str
    event_id: Optional[str] = None
    subject: Optional[str] = ""
    subject_name: Optional[str] = ""
    day: Optional[str] = ""
    date: Optional[str] = ""
    start_time: Optional[str] = ""
    end_time: Optional[str] = ""
    venue: Optional[str] = ""
    class_type: Optional[str] = ""
    recurrence: Optional[str] = ""
    timezone: str = "Asia/Kuala_Lumpur"
    decision_status: str = "ACCEPT"  # ACCEPT, REVIEW, ABSTAIN, ERROR
    validation_flags: List[str] = Field(default_factory=list)
    confidence: Optional[float] = 1.0
    calibrated_reliability: Optional[float] = None
    latency_ms: Optional[float] = 0.0
    config_hash: Optional[str] = ""
    dataset_hash: Optional[str] = ""
    annotation_hash: Optional[str] = ""
    code_revision: Optional[str] = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def decision(self) -> str:
        return self.decision_status

    @property
    def predicted_event_id(self) -> str:
        return self.event_id or self.prediction_id

    def to_dict(self) -> Dict[str, Any]:
        """Serialize record to dictionary."""
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()

    def to_csv_dict(self) -> Dict[str, Any]:
        """Serialize record for CSV output."""
        d = self.to_dict()
        d["decision"] = self.decision_status
        d["predicted_event_id"] = self.predicted_event_id
        if isinstance(d.get("validation_flags"), list):
            d["validation_flags"] = ";".join(d["validation_flags"])
        if isinstance(d.get("metadata"), dict):
            import json
            d["metadata"] = json.dumps(d["metadata"])
        return d
