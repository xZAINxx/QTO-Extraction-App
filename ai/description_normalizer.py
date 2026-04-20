"""Normalize raw keynote descriptions to clean professional text."""


class DescriptionNormalizer:
    def __init__(self, ai_client):
        self._client = ai_client

    def normalize(self, raw: str) -> str:
        """Normalize description. Uses AI client's built-in cache."""
        if not raw or not raw.strip():
            return raw
        return self._client.normalize_description(raw)

    def normalize_batch(self, descriptions: list[str]) -> list[str]:
        return [self.normalize(d) for d in descriptions]
