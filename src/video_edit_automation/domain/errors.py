class DomainError(Exception):
    """Base class for expected application/domain failures."""


class EntityNotFoundError(DomainError):
    pass


class PathNotAllowedError(DomainError):
    pass


class UnsupportedMediaError(DomainError):
    pass


class InvalidEditPlanError(DomainError):
    pass


class MediaToolError(DomainError):
    pass


class PlannerUnavailableError(DomainError):
    pass


class PlannerResponseError(DomainError):
    pass
