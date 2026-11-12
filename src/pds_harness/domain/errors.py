class HarnessError(Exception):
    """Base exception for expected harness failures."""


class InvalidTransitionError(HarnessError):
    """Raised when a workflow attempts an illegal state transition."""


class CloudPolicyError(HarnessError):
    """Raised when data is not permitted to leave the local machine."""


class CloudModelError(HarnessError):
    """Raised for sanitized third-party model API failures."""


class CatalogValidationError(HarnessError):
    """Raised when a function catalog cannot pass a safety gate."""


class PDSAPIError(HarnessError):
    """A sanitized PDS API failure that never contains request credentials."""


class PDSConnectionError(HarnessError):
    """Raised when the dedicated PDS browser connection cannot be used."""


class PDSConnectionNotStartedError(PDSConnectionError):
    """Raised when a PDS operation requires a browser session that is not open."""


class PDSLoginRequiredError(PDSConnectionError):
    """Raised when the user still needs to finish the visible PDS login."""


class PDSProjectNotFoundError(PDSConnectionError):
    """Raised when an exact PDS project-code lookup has no unique result."""


class PDSProjectSafetyError(PDSConnectionError):
    """Raised when a project fails a read/download safety precondition."""


class SubmissionDisabledError(HarnessError):
    """Raised when the submission kill switch is disabled."""


class ConcurrentModificationError(HarnessError):
    """Raised when the remote plan changed after human review."""


class AmbiguousDraftError(HarnessError):
    """Raised when more than one resumable PDS plan exists for a project."""


class DraftWriteDisabledError(HarnessError):
    """Raised when a caller attempts to create a draft while writes are disabled."""


class GenerationSourceBlockedError(HarnessError):
    """Raised when a parsed manual is not eligible for AI generation."""

