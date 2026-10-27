class ApiError(Exception):
    """An error that tells the caller what happened and what to do next."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        next_step: str,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.next_step = next_step
        self.retryable = retryable

    def to_body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "next_step": self.next_step,
            }
        }
