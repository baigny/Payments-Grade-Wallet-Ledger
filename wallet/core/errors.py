class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, *, cacheable: bool = True):
        self.code = code
        self.message = message
        self.status = status
        self.cacheable = cacheable
        super().__init__(message)
