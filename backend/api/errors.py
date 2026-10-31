from backend.contracts import ErrorBody


class PlatformError(Exception):
    def __init__(self, status: int, code: str, message: str, version=None, **details):
        super().__init__(message)
        self.status = status
        self.body = ErrorBody(code=code, message=message, current_state_version=version, details=details)
