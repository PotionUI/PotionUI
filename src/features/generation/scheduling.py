"""
Pure per-backend dispatch selection for the generation queue.

`queue.py` holds the mutable pending list and busy slots; everything here is
math over a snapshot of it. `select_next` and `project_order` take their state
as an argument and return a new one - they never read or write anything but
their own parameters, which is what makes the fairness rules in
`tests/features/generation/test_scheduling.py` exhaustively testable without a
running queue.

Two policies:

- `"fifo"` (default): the first pending item for the backend, in arrival
  order. Ignores `state` entirely.
- `"fair"`: round-robins between users, with a model-affinity override. See
  `select_next` for the algorithm; see `docs/backends.md` ("Scheduling
  policy") for the user-facing description.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from src.features.generation.queue import QueuedGeneration


FIFO = "fifo"
FAIR = "fair"
SCHEDULING_POLICIES = (FIFO, FAIR)


@dataclass(frozen=True)
class SchedulingPolicy:
    """A backend's scheduling configuration, resolved from its persisted settings."""

    name: str = FIFO
    # "fair" only: how many consecutive dispatches of the backend's currently
    # loaded model are allowed before a waiting, different-model job is forced
    # to the front regardless of whose turn it is.
    max_consecutive_same_model: int = 3


@dataclass(frozen=True)
class BackendSchedulingState:
    """One backend's fairness bookkeeping. Immutable - `select_next` returns a
    new instance rather than mutating this one, so simulating a projection
    (`project_order`) never disturbs the real, persisted state.

    `canonical_users` is every user id ever seen waiting on this backend, in
    first-seen order, and only ever grows. It exists so "the user after
    `last_served_user`" stays well-defined even once that user's queue runs
    dry - dropping an emptied user would make the rotation cursor's position
    meaningless the next time that user shows up.
    """

    canonical_users: Tuple[str, ...] = ()
    last_served_user: Optional[str] = None
    loaded_model_key: Optional[str] = None
    consecutive_count: int = 0


def select_next(
    pending: List["QueuedGeneration"],
    backend_id: str,
    policy: SchedulingPolicy,
    state: BackendSchedulingState,
) -> Tuple[Optional["QueuedGeneration"], BackendSchedulingState]:
    """Choose the next item to dispatch for `backend_id`, if any.

    Returns `(item_or_None, new_state)`. `new_state` is `state` unchanged when
    nothing was selected or the policy is "fifo"; otherwise it reflects the
    selection having been made (as if it will run next).

    Fair algorithm:
      1. Group this backend's pending items by user, preserving arrival order
         within each user's own queue.
      2. Extend the canonical user list with any user seen for the first time.
      3. Rotation order = canonical users starting right after
         `last_served_user`, wrapping around (so the just-served user sorts
         last, not first - a new arrival from another user never queue-jumps
         a user already due, and a user who just went can't go again until
         everyone else ready has had a turn, UNLESS affinity below overrides
         that).
      4. If the backend has a loaded model and hasn't hit the allowance yet,
         scan the rotation order for the first user whose head job wants that
         model, and dispatch it instead of strict rotation (this is what lets
         a same-model job "cut in line" ahead of a different-model job from a
         user earlier in rotation - the worked example's user 3).
      5. Otherwise (no affinity match, or the allowance is used up) dispatch
         the rotation's first ready user's head job, and reset the
         consecutive-model counter - this is a policy-forced switch of whose
         turn it is, not a continuation, even if the new job happens to want
         the same model.
    """
    candidates = [item for item in pending if item.backend_id == backend_id]
    if not candidates:
        return None, state

    if policy.name != FAIR:
        return candidates[0], state

    by_user: Dict[str, List["QueuedGeneration"]] = {}
    for item in candidates:
        by_user.setdefault(item.user_id, []).append(item)

    canonical = list(state.canonical_users)
    for item in candidates:
        if item.user_id not in canonical:
            canonical.append(item.user_id)

    if state.last_served_user in canonical:
        start = canonical.index(state.last_served_user) + 1
    else:
        start = 0
    rotation_order = [
        user for user in (canonical[start:] + canonical[:start]) if user in by_user
    ]

    chosen_user: Optional[str] = None
    if state.loaded_model_key is not None and state.consecutive_count < policy.max_consecutive_same_model:
        for user in rotation_order:
            if by_user[user][0].model_key == state.loaded_model_key:
                chosen_user = user
                break
    affinity_hit = chosen_user is not None
    if chosen_user is None:
        chosen_user = rotation_order[0]

    chosen_item = by_user[chosen_user][0]
    new_state = BackendSchedulingState(
        canonical_users=tuple(canonical),
        last_served_user=chosen_user,
        loaded_model_key=state.loaded_model_key if affinity_hit else chosen_item.model_key,
        consecutive_count=(state.consecutive_count + 1) if affinity_hit else 1,
    )
    return chosen_item, new_state


def project_order(
    pending_for_backend: List["QueuedGeneration"],
    policy: SchedulingPolicy,
    state: BackendSchedulingState,
) -> List["QueuedGeneration"]:
    """Simulate dispatching `pending_for_backend` to completion under `policy`,
    starting from `state`, without mutating anything the caller holds.

    Used for reporting (`position()`/`pending_items()`/`snapshot()`) so a
    fair backend's queue positions match what will actually run, not raw
    arrival order.
    """
    if not pending_for_backend:
        return []
    backend_id = pending_for_backend[0].backend_id
    remaining = list(pending_for_backend)
    working_state = state
    ordered: List["QueuedGeneration"] = []
    while remaining:
        item, working_state = select_next(remaining, backend_id, policy, working_state)
        if item is None:
            break
        ordered.append(item)
        remaining.remove(item)
    return ordered
