"""One ContextEpisode shape. COMMITTED names the source transition, not the store."""

from __future__ import annotations

_DROPPED = {"answer", "credential_notes", "evidence", "provider_name", "secret", "token"}


def redact_payload(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key not in _DROPPED}


def project_episode(event: dict) -> dict:
    """Project a committed domain event. This does not promote any other validity."""
    return {
        "episode_id": event["event_id"],
        "organization_id": event["organization_id"],
        "event_type": event["event_type"],
        "reference_time": event["occurred_at"],
        "observed_at": event["observed_at"],
        "source_type": "domain_event",
        "source_id": event["event_id"],
        "subject_refs": list(event["subject_refs"]),
        "payload": redact_payload(event.get("payload") or {}),
        "classification": event["classification"],
        "provenance": {
            "source_type": "domain_event",
            "source_id": event["event_id"],
            "store_authority": False,
            "validity_means": "source_transition_committed",
        },
        "validity_type": "COMMITTED",
        "trace_id": event.get("trace_id") or "",
        "correlation_id": event.get("correlation_id") or "",
        "causation_id": event.get("causation_id") or "",
    }


def present(episode: dict) -> dict:
    """Return the episode with the validity it already has."""
    return dict(episode)


def narrate(episodes: list[dict]) -> str:
    """Sequence language only. Temporal adjacency is not a cause."""
    if not episodes:
        return ""
    parts = []
    for episode in episodes:
        payload = episode.get("payload") or {}
        status = payload.get("status") or payload.get("policy_id") or episode["event_type"]
        subject = episode["subject_refs"][0]
        parts.append(
            f"{episode['reference_time'][:10]} {subject} {status} "
            f"({episode['validity_type']}) preceded later observations"
        )
    text = "Relevant historical context: " + "; ".join(parts) + "."
    if "caused" in text.lower():
        raise RuntimeError("causality_guard")
    return text
