class EngineeringError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"ok": False, "code": self.code, "message": str(self), "details": self.details}

