from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import blake2b
import json
import os
import re
from pathlib import Path

from careops.paths import ROOT

TOKEN_RE = re.compile(r"[a-z0-9_]+")
CACHE_DIR = ROOT / "data" / "embeddings"


class Embedder(ABC):
    name: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalHashEmbedder(Embedder):
    """Deterministic bag-of-tokens embedder for offline/CI fairness tests.

    Naive and governed paths must use the same embedder instance/type.
    """

    name = "local-hash"

    def __init__(self, dim: int = 128):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = TOKEN_RE.findall(text.lower())
        if not tokens:
            return vec
        for tok in tokens:
            digest = blake2b(tok.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        # L2 normalize
        norm = sum(v * v for v in vec) ** 0.5
        if norm:
            vec = [v / norm for v in vec]
        return vec


class OpenAIEmbedder(Embedder):
    name = "openai"

    def __init__(self, model: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise SystemExit("Install RAG extras: pip install -e '.[rag]'") from exc
        self.model = model or os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.client = OpenAI()
        self._cache_path = CACHE_DIR / f"openai_{self.model.replace('/', '_')}.json"
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache_path.exists():
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        return {}

    def _save_cache(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            key = blake2b(text.encode(), digest_size=16).hexdigest()
            if key in self._cache:
                out[i] = self._cache[key]
            else:
                missing.append((i, text))
        if missing:
            try:
                resp = self.client.embeddings.create(
                    model=self.model, input=[t for _, t in missing]
                )
            except Exception as exc:  # noqa: BLE001 - surface provider errors cleanly to callers
                raise RuntimeError(
                    "OpenAI embeddings failed. Check OPENAI_API_KEY "
                    "(must be a real key from platform.openai.com, not a placeholder). "
                    f"Or set CAREOPS_EMBEDDING_PROVIDER=local for offline retrieval. Detail: {exc}"
                ) from exc
            for (i, text), item in zip(missing, resp.data, strict=True):
                key = blake2b(text.encode(), digest_size=16).hexdigest()
                self._cache[key] = list(item.embedding)
                out[i] = self._cache[key]
            self._save_cache()
        return [v for v in out]  # type: ignore[return-value]


def get_embedder(provider: str | None = None) -> Embedder:
    choice = (provider or os.environ.get("CAREOPS_EMBEDDING_PROVIDER") or "auto").lower()
    if choice == "local":
        return LocalHashEmbedder()
    if choice == "openai":
        return OpenAIEmbedder()
    # auto: OpenAI when key present, else local
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIEmbedder()
    return LocalHashEmbedder()
