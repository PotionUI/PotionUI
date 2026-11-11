import sys
import traceback
from typing import Callable, TypeVar

T = TypeVar("T")


def create_or_explain(factory: Callable[[], T]) -> T:
    try:
        return factory()
    except Exception as exc:
        traceback.print_exc()
        print(f"PotionUI could not start: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
