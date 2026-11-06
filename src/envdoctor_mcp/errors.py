"""The one exception type whose message is safe to show a client."""


class EnvDoctorError(Exception):
    """Raised for conditions the user can act on. Messages never contain file contents."""
