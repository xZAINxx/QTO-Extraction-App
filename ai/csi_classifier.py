"""CSI Division classifier with AI primary and keyword fallback."""
from ai.client import _keyword_classify


class CSIClassifier:
    def __init__(self, ai_client, csi_keywords: dict):
        self._client = ai_client
        self._keywords = csi_keywords

    def classify(self, description: str) -> tuple[str, float]:
        """Return (csi_label, confidence). Cached in ai_client."""
        division, confidence = self._client.classify_csi(description, self._keywords)
        if not division:
            division = _keyword_classify(description, self._keywords)
            confidence = 0.5
        return division, confidence
