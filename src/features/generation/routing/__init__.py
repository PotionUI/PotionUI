"""Generation routing: deciding which backend of a preset's engine executes a
generation. See docs/generation-routing.md for the full picture; `router.py`'s
and `contracts.py`'s module docstrings for the mechanics.
"""

from src.features.generation.routing.contracts import (
    Candidate,
    NoEligibleBackendError,
    RoutingContext,
    RoutingDecision,
    RoutingRequest,
    RoutingRule,
    RuleTraceEntry,
)
from src.features.generation.routing.registry import build_routing_rules
from src.features.generation.routing.router import GenerationRouter

__all__ = [
    "Candidate",
    "GenerationRouter",
    "NoEligibleBackendError",
    "RoutingContext",
    "RoutingDecision",
    "RoutingRequest",
    "RoutingRule",
    "RuleTraceEntry",
    "build_routing_rules",
]
