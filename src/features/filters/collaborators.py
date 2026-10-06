from dataclasses import dataclass

from src.features.filters.catalog import FilterCatalog
from src.features.filters.repository import UserFilterRepository


@dataclass(frozen=True)
class FilterCollaborators:
    repository: UserFilterRepository
    catalog: FilterCatalog
