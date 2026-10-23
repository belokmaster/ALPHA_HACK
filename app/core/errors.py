from contracts.v1 import ErrorCode


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
