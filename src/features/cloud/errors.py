from typing import Sequence


class CloudCatalogError(Exception):
    status_code = 400
    code = "cloud_catalog_error"


class CloudBackendNotFound(CloudCatalogError):
    status_code = 404
    code = "cloud_backend_not_found"


class CloudBackendInactive(CloudCatalogError):
    status_code = 409
    code = "cloud_backend_inactive"


class CloudEntryNotFound(CloudCatalogError):
    status_code = 404
    code = "cloud_entry_not_found"

    def __init__(self, slugs: Sequence[str]):
        super().__init__(f"Not in this backend's catalog: {', '.join(slugs)}")
        self.slugs = list(slugs)


class CloudEntryUnavailable(CloudCatalogError):
    status_code = 409
    code = "cloud_entry_unavailable"

    def __init__(self, slugs: Sequence[str]):
        super().__init__(f"No longer offered by the provider, so it cannot be enabled: {', '.join(slugs)}")
        self.slugs = list(slugs)


class CloudCatalogFilterError(CloudCatalogError):
    status_code = 422
    code = "cloud_catalog_filter_invalid"


class CloudScopeInvalid(CloudCatalogError):
    status_code = 422
    code = "cloud_scope_invalid"

    def __init__(self, problems: Sequence[str]):
        super().__init__("; ".join(problems))
        self.problems = list(problems)
