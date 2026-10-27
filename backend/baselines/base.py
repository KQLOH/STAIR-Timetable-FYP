"""Base class for all baseline evaluation systems (BR-001, INT-003)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from backend.baselines.contracts import EvaluationDocumentEvidence, PredictionRecord


class BaseBaseline(ABC):
    """Abstract Base Class for B1-B4 baseline systems."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.system_id = self.config.get("system_id", "UNKNOWN")

    @abstractmethod
    def fit(self, pages: List[EvaluationDocumentEvidence], **kwargs: Any) -> None:
        """Fit or train the baseline on development pages if required."""
        pass

    @abstractmethod
    def predict_page(self, page: EvaluationDocumentEvidence) -> List[PredictionRecord]:
        """Runs baseline inference on a single page evidence record."""
        pass
