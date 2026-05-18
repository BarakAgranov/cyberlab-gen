"""Project exception hierarchy.

Architectural source: ``coding-conventions.md`` §6.1. Root is
``CyberlabGenError``; subdivisions follow the architecture's stage
boundaries. Phase 0 only populates the registry branch; other stage
classes (``IngestionError``, ``ExtractionError``, ``PlanningError``,
``GenerationError``, ``ValidationLayerError``, ``ProviderError``) land
in the tasks that first raise them.

Every error carries the structured context §6.1 requires (``stage``,
``run_id``, ``cause``). Phase 0 has no pipeline runner yet so ``run_id``
is always ``None``; ADR 0009 records the deferral and Phase 1's runner
task wires it through.

Use ``raise X from Y`` per §6.1 so ``__cause__`` chains preserve the
original error for the structured run report.
"""

from pathlib import Path


class CyberlabGenError(Exception):
    """Root of every framework-raised exception.

    Carries structured context (``stage``, ``run_id``, ``cause``) for
    inclusion in the run report. ``cause`` mirrors ``__cause__`` for
    callers that read attributes; ``raise X from Y`` still populates the
    traceback's ``__cause__`` separately.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.run_id = run_id
        self.cause = cause


class RegistryError(CyberlabGenError):
    """Registry-stage errors (load, merge, lookup).

    Pins ``stage='registry'`` so the run report can group registry
    failures without each call site re-supplying it.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, stage="registry", run_id=run_id, cause=cause)


class RegistryLoadError(RegistryError):
    """Raised when a registry YAML file fails to load or validate.

    Carries the offending file path so user-facing output can point at
    the file directly. The underlying error (YAML parse failure or
    ``pydantic.ValidationError``) is preserved as ``cause`` and as
    ``__cause__`` via ``raise ... from``.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path,
        run_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, run_id=run_id, cause=cause)
        self.path = path
