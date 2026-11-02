"""Control-plane failures. A refusal here does not write domain state."""


class ControlError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
