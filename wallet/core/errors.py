class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        cacheable: bool = True,
        retry_after: int | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.cacheable = cacheable
        self.retry_after = retry_after
        super().__init__(message)
