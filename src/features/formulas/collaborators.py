from dataclasses import dataclass

from src.features.formulas.repository import FormulaRepository
from src.features.formulas.sources import FormSource, ModelRefChecker


@dataclass(frozen=True)
class FormulaCollaborators:
    repository: FormulaRepository
    forms: FormSource
    models: ModelRefChecker
