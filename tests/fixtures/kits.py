from src.features.kits.expansion import KitExpander
from src.features.kits.repository import KitRepository


def inert_kit_expander() -> KitExpander:
    return KitExpander(KitRepository())
