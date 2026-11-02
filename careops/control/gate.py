"""Decision gate. A result selects a next control step. It does not authorize or commit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from careops.control.errors import ControlError
from careops.control.schema import DECISION_OPTIONS


@dataclass(frozen=True)
class DecisionRequest:
    workflow_id: str
    question: str
    available_options: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    workflow_version: int


@dataclass(frozen=True)
class DecisionResult:
    selected_option: str
    authoritative: bool = False


class DecisionGate(Protocol):
    async def decide(self, request: DecisionRequest) -> DecisionResult:
        ...


class FixtureDecisionGate:
    """Deterministic stand-in. CI does not call an external model."""

    def __init__(self, selected_option: str = "COMPLETE"):
        if selected_option not in DECISION_OPTIONS:
            raise ControlError("decision_option_unknown")
        self.selected_option = selected_option

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        if request.workflow_version < 1:
            raise ControlError("stale_workflow")
        if self.selected_option not in request.available_options:
            raise ControlError("decision_option_unknown")
        return DecisionResult(selected_option=self.selected_option, authoritative=False)
