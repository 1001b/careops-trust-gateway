"""Application-owned temporal store. Callers do not talk to a graph library."""

from __future__ import annotations

import json
from typing import Protocol

from careops.context.ontology import validate_ontology
from careops.paths import SEMANTIC_DIR


class ContextUnavailable(Exception):
    def __init__(self, code: str = "context_unavailable"):
        self.code = code
        super().__init__(code)


class TemporalContextStore(Protocol):
    """The application depends on this surface. A graph library is one adapter behind it."""

    def visible(self, organization_id: str, as_of: str) -> list[dict]:
        ...

    def preceding(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        ...

    def current(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        ...


def _day(value: str) -> str:
    return value[:10]


class LocalTemporalStore:
    """Deterministic CI store. A copy of an episode is not an authority."""

    def __init__(self, episodes: list[dict]):
        validate_ontology()
        self.episodes = [dict(episode) for episode in episodes]

    @classmethod
    def from_fixture(cls) -> "LocalTemporalStore":
        raw = json.loads((SEMANTIC_DIR / "episodes.json").read_text(encoding="utf-8"))
        return cls(raw)

    def _included(self, episode: dict, organization_id: str, as_of: str) -> bool:
        if episode["organization_id"] != organization_id:
            return False
        if _day(episode["reference_time"]) > _day(as_of):
            return False
        effective_from = (episode.get("payload") or {}).get("effective_from")
        if effective_from and effective_from > _day(as_of):
            return False
        return True

    def visible(self, organization_id: str, as_of: str) -> list[dict]:
        rows = [episode for episode in self.episodes if self._included(episode, organization_id, as_of)]
        return sorted(rows, key=lambda episode: (episode["reference_time"], episode["episode_id"]))

    def preceding(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        return [
            episode
            for episode in self.visible(organization_id, as_of)
            if subject_ref in episode["subject_refs"]
        ]

    def current(self, organization_id: str, subject_ref: str, as_of: str) -> list[dict]:
        rows = [
            episode
            for episode in self.preceding(organization_id, subject_ref, as_of)
            if episode["validity_type"] == "COMMITTED"
        ]
        superseded = {
            (episode.get("payload") or {}).get("supersedes")
            for episode in rows
            if (episode.get("payload") or {}).get("supersedes")
        }
        still = []
        for episode in rows:
            payload = episode.get("payload") or {}
            effective_to = payload.get("effective_to")
            if episode["episode_id"] in superseded:
                continue
            if effective_to and effective_to < _day(as_of):
                continue
            still.append(episode)
        latest: dict[str, dict] = {}
        for episode in still:
            relationship = (episode.get("payload") or {}).get("relationship") or episode["event_type"]
            current = latest.get(relationship)
            if current is None or episode["reference_time"] >= current["reference_time"]:
                latest[relationship] = episode
        return sorted(latest.values(), key=lambda episode: episode["episode_id"])
