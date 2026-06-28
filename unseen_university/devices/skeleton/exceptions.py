"""Rack-level exceptions."""


class DeviceBlockedError(Exception):
    """
    Raised when a blocked device receives an operation request.

    Rather than hanging on a TCP timeout, callers get this immediately.
    Raised by any device that checks its own block status before operating.

    Shape (also available as .info dict):
        {
            "error": "device_blocked",
            "device_id": str,
            "block_type": "manual" | "auto",
            "blocked_since": ISO8601 str | None,
        }
    """

    def __init__(
        self,
        device_id: str,
        block_type: str = "manual",
        blocked_since: str | None = None,
    ) -> None:
        self.info = {
            "error": "device_blocked",
            "device_id": device_id,
            "block_type": block_type,
            "blocked_since": blocked_since,
        }
        super().__init__(f"Device '{device_id}' is blocked (type={block_type})")


class RegistrationError(Exception):
    """Raised when a device attempts to register under an already-taken namespace."""


class AuthError(Exception):
    """
    Raised when a caller lacks permission for a privileged operation.

    halt() and block() require from_device == 'skeleton' or == target device_id.
    v1 trust model: caller identity taken from envelope.from_device (localhost trust).
    Cryptographic ACL is Phase 5+.
    """

    def __init__(self, message: str, from_device: str = "", target: str = "") -> None:
        self.from_device = from_device
        self.target = target
        super().__init__(message)
