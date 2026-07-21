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


class InvalidHighlightReviewError(DomainError):
    pass


class InvalidAgentWorkflowError(DomainError):
    pass


class InvalidCaptureSessionError(DomainError):
    pass


class MediaToolError(DomainError):
    pass


class PlannerUnavailableError(DomainError):
    pass


class PlannerResponseError(DomainError):
    pass


class ReviewerUnavailableError(DomainError):
    pass


class ReviewerResponseError(DomainError):
    pass


class AnalyzerUnavailableError(DomainError):
    pass
