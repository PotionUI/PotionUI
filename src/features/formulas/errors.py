class FormulaError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def not_found() -> FormulaError:
    return FormulaError("formula_not_found", "Formula not found", 404)
