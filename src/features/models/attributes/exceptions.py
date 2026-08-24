class AttributeDefinitionNotFoundException(Exception):
    """Raised when a definition id names no `model_attribute_definitions` row."""


class InvalidAttributeDefinitionException(Exception):
    """Raised when a definition create/update is malformed (bad key format,
    duplicate key, missing required field) or a value doesn't validate against
    a definition (wrong type, out of range, undeclared key)."""


class SystemAttributeDefinitionException(Exception):
    """Raised when an edit would change a system definition's `key`/`field_type`,
    or delete a system definition outright."""
