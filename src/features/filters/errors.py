class FilterError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def not_found() -> FilterError:
    return FilterError("filter_not_found", "Filter not found", 404)


def name_taken(name: str) -> FilterError:
    return FilterError("filter_name_taken", f"You already have a filter named '{name}'", 409)


def limit_reached(limit: int) -> FilterError:
    return FilterError("filter_limit_reached", f"You can keep at most {limit} filters; delete one to save another", 409)


def invalid(message: str) -> FilterError:
    return FilterError("filter_invalid", message, 422)


def lut_unsupported() -> FilterError:
    return FilterError(
        "filter_lut_unsupported",
        "LUT filters can't be copied yet; only filters made of steps can be saved to My filters",
        422,
    )
