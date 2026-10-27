"""B2 Baseline: Deterministic 2D Spatial Layout Rule Parser (B2-001 ~ B2-004).

Reconstructs tabular timetable grids via bounding-box geometry and spatial intersection.
Preserves missing mandatory fields without synthetic defaulting (Policy A).
"""

from typing import Any, Dict, List, Optional
import re

from backend.baselines.base import BaseBaseline
from backend.baselines.contracts import EvaluationDocumentEvidence, EvaluationTokenSpan, PredictionRecord


class B2RuleParserBaseline(BaseBaseline):
    """Baseline B2: Spatial Bounding-Box Grid Reconstruction."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.system_id = "B2"
        self.timezone = self.config.get("timezone", "Asia/Kuala_Lumpur")

        self.subject_re = re.compile(
            r"^(?!(?:Room|Lab|Hall|DK|LT|Auditorium|Block|Level)\b)\b([A-Z]{2,5}\s*\d{3,4}[A-Z]?|[A-Za-z0-9\s\-]+(?:English|Physics|Chemistry|Calculus|Programming|Biology|Studies|Science|Management|Economics|Accounting|Development|Systems|Engineering|Thermodynamics|Phenomena|Security|Marketing))\b",
            re.I,
        )
        self.day_re = re.compile(r"\b(Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:rs(?:day)?)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Mo|Tu|We|Th|Fr|Sa|Su)\b", re.I)
        self.date_re = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", re.I)
        self.time_re = re.compile(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:-|to|–|—)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.I)
        self.venue_re = re.compile(
            r"\b(?:Room\s+[A-Za-z0-9\-]+|Lab\s+[A-Za-z0-9\-]+|PC Lab-[A-Za-z0-9\s()\-]+|Che Lab-\d+|ChE Lab-\d+|Applied Physics Lab\s+\([A-Za-z0-9\-]+\)|Electronics Lab\s+\([A-Za-z0-9\-]+\)|Circuit Lab\s+\([A-Za-z0-9\-]+\)|Machine Lab\s+\([A-Za-z0-9\-]+\)|Power-New Lab|DK\s*[A-Z0-9]+|[A-Z]\-[0-9]{1,4}|D-\d+|N-\d+|O-\d+)\b",
            re.I,
        )
        self.class_type_re = re.compile(r"\b(Lecture|Tutorial|Laboratory|Practical|Seminar|Workshop|Class|LEC|TUT|SEM|WS|PRAC|Lab(?!\s*\d))\b", re.I)
        self.recurrence_re = re.compile(r"\b(Weekly|Biweekly|Every\s+week|Fortnightly|Daily)\b", re.I)

    def fit(self, pages: List[EvaluationDocumentEvidence], **kwargs: Any) -> None:
        """B2 geometry rules are frozen."""
        pass

    def _cluster_into_rows_and_columns(
        self, tokens: List[EvaluationTokenSpan]
    ) -> List[Dict[str, Any]]:
        """Clusters 2D bounding boxes into spatial grid cells."""
        if not tokens:
            return []

        # Sort tokens by Y0 then X0
        sorted_tokens = sorted(tokens, key=lambda t: (t.bbox[1] if t.bbox else 0, t.bbox[0] if t.bbox else 0))

        # Y-clustering for rows (threshold 18 pixels)
        rows: List[List[EvaluationTokenSpan]] = []
        for t in sorted_tokens:
            y_center = (t.bbox[1] + t.bbox[3]) / 2.0 if t.bbox else 0
            placed = False
            for row in rows:
                row_y_center = sum((item.bbox[1] + item.bbox[3]) / 2.0 for item in row) / len(row)
                if abs(y_center - row_y_center) < 18.0:
                    row.append(t)
                    placed = True
                    break
            if not placed:
                rows.append([t])

        # Form cell blocks from rows
        cells: List[Dict[str, Any]] = []
        for row in rows:
            row.sort(key=lambda t: t.bbox[0] if t.bbox else 0)
            cur_cell: List[EvaluationTokenSpan] = []
            for t in row:
                if not cur_cell:
                    cur_cell.append(t)
                else:
                    prev_x1 = cur_cell[-1].bbox[2]
                    if t.bbox[0] - prev_x1 < 35.0:
                        cur_cell.append(t)
                    else:
                        cells.append({
                            "tokens": list(cur_cell),
                            "text": " ".join(item.text for item in cur_cell),
                            "bbox": [
                                min(item.bbox[0] for item in cur_cell),
                                min(item.bbox[1] for item in cur_cell),
                                max(item.bbox[2] for item in cur_cell),
                                max(item.bbox[3] for item in cur_cell),
                            ],
                        })
                        cur_cell = [t]
            if cur_cell:
                cells.append({
                    "tokens": list(cur_cell),
                    "text": " ".join(item.text for item in cur_cell),
                    "bbox": [
                        min(item.bbox[0] for item in cur_cell),
                        min(item.bbox[1] for item in cur_cell),
                        max(item.bbox[2] for item in cur_cell),
                        max(item.bbox[3] for item in cur_cell),
                    ],
                })

        return cells

    def predict_page(self, page: EvaluationDocumentEvidence) -> List[PredictionRecord]:
        """Reconstructs tabular events using deterministic geometric rules."""
        if not page.tokens:
            return []

        cells = self._cluster_into_rows_and_columns(page.tokens)
        predictions: List[PredictionRecord] = []

        # Find header days and header times from grid
        header_days: List[Dict[str, Any]] = []
        header_times: List[Dict[str, Any]] = []

        day_map = {
            "mo": "Monday", "mon": "Monday", "monday": "Monday",
            "tu": "Tuesday", "tue": "Tuesday", "tuesday": "Tuesday",
            "we": "Wednesday", "wed": "Wednesday", "wednesday": "Wednesday",
            "th": "Thursday", "thu": "Thursday", "thursday": "Thursday",
            "fr": "Friday", "fri": "Friday", "friday": "Friday",
            "sa": "Saturday", "sat": "Saturday", "saturday": "Saturday",
            "su": "Sunday", "sun": "Sunday", "sunday": "Sunday",
        }

        for c in cells:
            # Check for header day
            d_m = self.day_re.search(c["text"])
            if d_m and len(c["text"].split()) <= 2:
                matched_day_token = d_m.group(1).strip().lower()
                full_day = day_map.get(matched_day_token, d_m.group(1).strip())
                header_days.append({"day": full_day, "bbox": c["bbox"]})

            # Check for header times
            for tm in self.time_re.finditer(c["text"]):
                header_times.append({
                    "start_time": tm.group(1).strip(),
                    "end_time": tm.group(2).strip(),
                    "bbox": c["bbox"],
                })

        # Process each data cell
        for idx, cell in enumerate(cells, start=1):
            text = cell["text"]
            cell_x_center = (cell["bbox"][0] + cell["bbox"][2]) / 2.0
            cell_y_center = (cell["bbox"][1] + cell["bbox"][3]) / 2.0

            # 1. Time extraction
            t_match = self.time_re.search(text)
            if t_match:
                st = t_match.group(1).strip()
                et = t_match.group(2).strip()
            elif header_times:
                closest_t = min(
                    header_times,
                    key=lambda h: abs(((h["bbox"][0] + h["bbox"][2]) / 2.0) - cell_x_center),
                )
                st = closest_t["start_time"]
                et = closest_t["end_time"]
            else:
                continue

            # 2. Subject extraction
            s_match = self.subject_re.search(text)
            subj = s_match.group(1).strip() if s_match else ""

            # B2-003: If missing mandatory subject, Policy A produces no prediction
            if not subj or subj in ["CUI_Lahore", "Spring 2024", "FA22-CHE-B", "Timetable generated"]:
                continue

            # 3. Day extraction
            d_match = self.day_re.search(text)
            if d_match:
                matched_day = d_match.group(1).strip().lower()
                day = day_map.get(matched_day, d_match.group(1).strip())
            elif header_days:
                # Find row matching Y or nearest column
                closest_d = min(
                    header_days,
                    key=lambda h: abs(((h["bbox"][1] + h["bbox"][3]) / 2.0) - cell_y_center),
                )
                day = closest_d["day"]
            else:
                day = ""

            date_m = self.date_re.search(text)
            date_val = date_m.group(1).strip() if date_m else ""

            v_match = self.venue_re.search(text)
            venue = v_match.group(0).strip() if v_match else ""

            ct_match = self.class_type_re.search(text)
            ct = ct_match.group(1).strip() if ct_match else ""

            rec_match = self.recurrence_re.search(text)
            rec = rec_match.group(1).strip() if rec_match else ""

            pred = PredictionRecord(
                system_id="B2",
                run_id=self.config.get("run_id", "b2_run"),
                prediction_id=f"pred_b2_{page.page_id}_{idx:02d}",
                page_id=page.page_id,
                split=page.split,
                event_id=f"evt_b2_{page.page_id}_{idx:02d}",
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
                confidence=0.88,
            )
            predictions.append(pred)

        return predictions
