"""B1 Baseline: OCR + Frozen Regular Expressions (B1-001 ~ B1-004).

Applies deterministic regex patterns directly against flat OCR text streams.
Maintains honest baseline behavior without inventing missing fields.
"""

from typing import Any, Dict, List, Optional
import re
import uuid

from backend.baselines.base import BaseBaseline
from backend.baselines.contracts import EvaluationDocumentEvidence, PredictionRecord


class B1OCRRegexBaseline(BaseBaseline):
    """Baseline B1: OCR text stream + Deterministic Regular Expressions."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.system_id = "B1"
        self.timezone = self.config.get("timezone", "Asia/Kuala_Lumpur")
        
        patterns = self.config.get("patterns", {})
        self.subject_re = re.compile(
            patterns.get("subject", r"\b([A-Z]{2,5}\s*\d{3,4}[A-Z]?)\b"), re.I
        )
        self.day_re = re.compile(
            patterns.get("weekday", r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Tues|Wed|Thu|Thur|Fri|Sat|Sun)\b"), re.I
        )
        self.date_re = re.compile(
            patterns.get("date", r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"), re.I
        )
        self.time_range_re = re.compile(
            patterns.get("time_range", r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:-|to|–|—)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)"), re.I
        )
        self.venue_re = re.compile(
            patterns.get("venue", r"\b(?:Room|Lab|Hall|DK|LT|Auditorium|Block\s+[A-Z0-9]+|CL\d+|TR\d+|CR\d+|LR\d+|[A-Z]\-[0-9]{1,4}|[A-Z]\d{1,4})(?:\s+[A-Za-z0-9\-_]+)?\b"), re.I
        )
        self.class_type_re = re.compile(
            patterns.get("class_type", r"\b(Lecture|Tutorial|Laboratory|Practical|Seminar|Workshop|Class|LEC|TUT|SEM|WS|PRAC|Lab(?!\s*\d))\b"), re.I
        )
        self.recurrence_re = re.compile(
            patterns.get("recurrence", r"\b(Weekly|Biweekly|Every\s+week|Fortnightly|Daily)\b"), re.I
        )

    def fit(self, pages: List[EvaluationDocumentEvidence], **kwargs: Any) -> None:
        """B1 is a zero-training heuristic baseline."""
        pass

    def predict_page(self, page: EvaluationDocumentEvidence) -> List[PredictionRecord]:
        """Extracts timetable events from flat token/text stream using regex."""
        # Assemble text from tokens or raw_text
        if page.tokens:
            sorted_tokens = sorted(page.tokens, key=lambda t: (t.line_num or 1, t.reading_order or 1, t.bbox[0]))
            lines_dict: Dict[int, List[str]] = {}
            for t in sorted_tokens:
                lines_dict.setdefault(t.line_num or 1, []).append(t.text)
            text_lines = [" ".join(tokens) for _, tokens in sorted(lines_dict.items())]
            full_text = "\n".join(text_lines)
        else:
            full_text = page.raw_text or ""

        if not full_text.strip():
            return []

        # Find occurrences across lines
        predictions: List[PredictionRecord] = []
        lines = full_text.splitlines()
        current_day = ""

        for idx, line in enumerate(lines, start=1):
            d_match = self.day_re.search(line)
            if d_match and len(line.split()) <= 2:
                current_day = d_match.group(1).strip()

            s_match = self.subject_re.search(line)
            t_match = self.time_range_re.search(line)

            if s_match and t_match:
                subj = s_match.group(1).strip()
                st = t_match.group(1).strip()
                et = t_match.group(2).strip()

                day = d_match.group(1).strip() if d_match else current_day

                date_m = self.date_re.search(line)
                date_val = date_m.group(1).strip() if date_m else ""

                v_match = self.venue_re.search(line)
                venue = v_match.group(0).strip() if v_match else ""

                ct_match = self.class_type_re.search(line)
                ct = ct_match.group(1).strip() if ct_match else ""

                rec_match = self.recurrence_re.search(line)
                rec = rec_match.group(1).strip() if rec_match else ""

                pred = PredictionRecord(
                    system_id="B1",
                    run_id=self.config.get("run_id", "b1_run"),
                    prediction_id=f"pred_b1_{page.page_id}_{idx:02d}",
                    page_id=page.page_id,
                    split=page.split,
                    event_id=f"evt_b1_{page.page_id}_{idx:02d}",
                    subject=subj,
                    day=day,
                    date=date_val,
                    start_time=st,
                    end_time=et,
                    venue=venue,
                    class_type=ct,
                    recurrence=rec,
                    timezone=self.timezone,
                    decision_status="ACCEPT",
                    confidence=1.0,
                )
                predictions.append(pred)

        return predictions
