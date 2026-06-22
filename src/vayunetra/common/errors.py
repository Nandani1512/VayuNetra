class VayuNetraError(Exception):
    """Base class for all VayuNetra errors."""


class ConfigError(VayuNetraError):
    pass


class IngestionError(VayuNetraError):
    pass


class UpstreamRateLimitError(IngestionError):
    pass


class DataQualityError(IngestionError):
    pass


class ModelNotFoundError(VayuNetraError):
    pass


class AuthError(VayuNetraError):
    pass
