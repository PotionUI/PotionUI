class MediaRefError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SessionNotFoundError(LookupError):
    pass


class WriteRefused(Exception):
    pass
