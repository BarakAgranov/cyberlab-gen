"""Run store — the system's on-disk memory of everything a run produces.

Architectural source: ``docs/architecture.md §2.3`` (the local-state layout:
``runs/`` for per-run working directories) and ``pipeline.md §3.6``/``§3.7``
(reports + run/checkpoint directories). Recorded in ADR 0039.

cyberlab-gen's pipeline produces *artifacts that are themselves the work product*
— an ``AttackSpec``, a jury verdict, an enrichment result (and, in later phases, a
lab manifest, IaC, attack scripts, detection rules). For a system whose purpose is
to evaluate and improve those artifacts, they must never be throwaway scratch: every
run's outputs are written to disk, complete **or** partial, on **every** exit path
(success, failure, cost-abort, Ctrl-C / SIGINT / SIGTERM, uncaught crash). It must be
structurally impossible to spend money running the pipeline and end with nothing to
read.

Design guarantees:

- **Always something to read.** :meth:`RunStore.start` creates the run directory and
  writes ``run.json`` (status ``running`` + lineage) *before* the first LLM call.
  Each artifact write rewrites ``run.json`` so it always reflects current state.
  A ``finally`` / signal handler calls :meth:`RunHandle.finalize` on every exit.
- **Never silently overwritten.** The run id is keyed by UTC timestamp + a slug, and
  a same-instant collision gets a numeric suffix, so two runs never share a directory.
- **Real vs. eval separation by location.** The store is constructed with a ``root``:
  real ``extract`` runs pass ``LocalState.runs_dir`` (``~/.cyberlab-gen/runs/``); eval
  runs pass the in-repo ``eval/reports/runs/`` directory. Same code, different pile.
- **Room for provenance/lineage (Layer 4).** :class:`RunLineage` carries the fields a
  future lineage system needs (model, prompt/extractor version, input hash, code
  version); they are populated best-effort now and left ``None`` where not yet known.

Writes are **best-effort**: an :class:`OSError` while persisting is logged and
swallowed so persistence can never mask the original error that is propagating.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from io import StringIO
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from cyberlab_gen.schemas.base import ArtifactModel

if TYPE_CHECKING:
    from pathlib import Path

    from cyberlab_gen.providers.cost_ledger import CostLedger

logger = logging.getLogger(__name__)

#: Canonical filenames inside a run directory. Centralised so inspectors and tests
#: agree on the layout.
RUN_RECORD_FILENAME = "run.json"
COST_FILENAME = "cost.yaml"
SPEC_FILENAME = "spec.yaml"
MANIFEST_FILENAME = (
    "manifest.yaml"  # the LabManifest a `plan` run produces (run-dir mirror of lab.yaml)
)
JURY_VERDICT_FILENAME = "jury-verdict.yaml"
ENRICHMENT_FILENAME = "enrichment.yaml"
RUN_LOG_FILENAME = "run.log"

_MAX_SLUG_LEN = 48


class RunKind(StrEnum):
    """Which entry point produced the run — real deliverable vs. measurement."""

    EXTRACT = "extract"
    PLAN = "plan"
    EVAL = "eval"


class RunStatus(StrEnum):
    """Terminal (or in-flight) classification of a run, written to ``run.json``.

    ``RUNNING`` is the initial state written at :meth:`RunStore.start`. Every other
    value is terminal; :meth:`RunHandle.finalize` records exactly one of them, and
    once a terminal status is set it is never downgraded back to ``RUNNING`` (so a
    stray signal handler firing after a clean finish cannot corrupt the record).
    """

    RUNNING = "running"
    SHIPPED = "shipped"
    SHIPPED_LOW_CONFIDENCE = "shipped_low_confidence"
    HALTED_VALIDATION = "halted_validation"
    HALTED_REJECT = "halted_reject"
    OUT_OF_SCOPE = "out_of_scope"
    ABORTED = "aborted"
    BUDGET_EXCEEDED = "budget_exceeded"
    FAILED = "failed"  # a classified pipeline failure (truncation, malformed, tool loop, ...)
    INTERRUPTED = "interrupted"
    CRASHED = "crashed"  # an unexpected, unclassified error


class RunLineage(ArtifactModel):
    """Provenance fields tracing a run to how it was made (ADR 0039, Layer 4).

    Designed-for, not fully built: future phases flesh out lineage capture. Fields are
    optional and populated best-effort as they become known (the resolved model and
    extractor version are only known after extraction, for example).
    """

    input_ref: str | None = None  # the URL (extract) or blog id (eval)
    input_hash: str | None = None  # content hash of the ingested input
    model: str | None = None  # the provider-resolved model id
    extractor_version: str | None = None
    prompt_version: str | None = None
    code_version: str | None = None  # e.g. a git short hash


class RunRecord(ArtifactModel):
    """The ``run.json`` record — a complete, inspectable account of one run.

    Detailed per-call cost lives in ``cost.yaml`` (a :class:`CostReportBlock`); the
    per-stage artifacts live in their own files. This record is the index: identity,
    status, timing, lineage, a cost summary, and the list of artifact files actually
    written (so an inspector can tell complete from partial at a glance).
    """

    run_id: str
    kind: RunKind
    label: str
    status: RunStatus
    halt_reason: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    lineage: RunLineage = Field(default_factory=RunLineage)
    total_cost_usd: Decimal | None = None
    num_llm_calls: int | None = None
    metrics: dict[str, float] = Field(default_factory=dict[str, float])
    artifacts: list[str] = Field(default_factory=list[str])


class RunHandle:
    """A live handle to one run's directory; writes artifacts and the run record.

    Constructed by :meth:`RunStore.start`. All writes are best-effort: an
    :class:`OSError` is logged at WARNING and swallowed so persistence never masks the
    error that is propagating out of the pipeline.
    """

    def __init__(self, directory: Path, record: RunRecord) -> None:
        self._directory = directory
        self._record = record
        self._finalized = False

    @classmethod
    def create(cls, directory: Path, record: RunRecord) -> RunHandle:
        """Build a handle and write its initial ``run.json`` immediately.

        The first flush happens here (inside the class) so the record exists on disk
        before any work begins, without the store reaching into a private method.
        """
        handle = cls(directory, record)
        handle._flush()
        return handle

    @property
    def directory(self) -> Path:
        """The run directory (created)."""
        return self._directory

    @property
    def record(self) -> RunRecord:
        """The current run record (mutated in place as the run progresses)."""
        return self._record

    def write_artifact(self, name: str, content: BaseModel | str) -> None:
        """Write one artifact into the run directory and re-flush ``run.json``.

        A ``BaseModel`` is serialised to YAML (or JSON when ``name`` ends ``.json``);
        a ``str`` is written verbatim. The filename is recorded in
        :attr:`RunRecord.artifacts` (deduplicated) so the record always lists what is
        actually on disk. Best-effort.
        """
        payload = content if isinstance(content, str) else _serialize(content, name)
        if self._write_text(name, payload):
            if name not in self._record.artifacts:
                self._record.artifacts.append(name)
            self._flush()

    def write_cost(self, ledger: CostLedger) -> None:
        """Persist the per-agent/per-model cost breakdown (``cost.yaml``).

        Surfaces the full :class:`CostReportBlock` the eval report otherwise discards,
        and folds the run-total + call count into the record summary. Best-effort.
        """
        block = ledger.to_report_block()
        self._record.total_cost_usd = block.total_usd
        self._record.num_llm_calls = len(block.entries)
        self.write_artifact(COST_FILENAME, block)

    def update_lineage(self, **fields: str | None) -> None:
        """Merge known lineage fields into the record and re-flush. Best-effort."""
        merged = self._record.lineage.model_copy(
            update={k: v for k, v in fields.items() if v is not None}
        )
        self._record.lineage = merged
        self._flush()

    def finalize(
        self,
        status: RunStatus,
        *,
        halt_reason: str | None = None,
        metrics: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Record the terminal status, end time and metrics. Idempotent.

        The first finalize wins: once a terminal status is recorded, later calls (e.g.
        a signal handler firing after the normal ``finally``) are no-ops. This keeps
        the run record honest about how the run actually ended.
        """
        if self._finalized:
            return
        self._finalized = True
        self._record.status = status
        self._record.halt_reason = halt_reason
        self._record.ended_at = now or datetime.now(UTC)
        if metrics:
            self._record.metrics = {**self._record.metrics, **metrics}
        self._flush()

    def _flush(self) -> None:
        """Rewrite ``run.json`` to reflect current state. Best-effort."""
        self._write_text(RUN_RECORD_FILENAME, self._record.model_dump_json(indent=2))

    def _write_text(self, name: str, text: str) -> bool:
        """Write ``text`` to ``<dir>/<name>``; return whether it succeeded."""
        try:
            (self._directory / name).write_text(text, encoding="utf-8")
        except OSError:
            logger.warning("run-store: failed to write %s in %s", name, self._directory)
            return False
        return True


class RunStore:
    """Creates per-run directories under a fixed ``root``.

    Real ``extract`` runs and eval runs use distinct roots (the real-vs-eval
    separation is *where* artifacts live, ADR 0039): ``LocalState.runs_dir`` vs.
    ``eval/reports/runs/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def start(
        self,
        *,
        kind: RunKind,
        label: str,
        id_hint: str | None = None,
        lineage: RunLineage | None = None,
        now: datetime | None = None,
    ) -> RunHandle:
        """Create a fresh, never-overwriting run directory and write ``run.json``.

        Args:
            kind: which entry point produced the run.
            label: the raw human reference (URL for ``extract``, blog id for eval),
                stored verbatim in the record.
            id_hint: slug source for the directory name when it should differ from
                ``label`` (eval passes ``"<blog_id>-run<index>"``); defaults to
                ``label``.
            lineage: known provenance at start (input ref/hash, code version).
            now: injectable timestamp (tests); defaults to ``datetime.now(UTC)``.
        """
        started = now or datetime.now(UTC)
        slug_source = id_hint if id_hint is not None else label
        directory, run_id = self._reserve_dir(slug_source, started)
        record = RunRecord(
            run_id=run_id,
            kind=kind,
            label=label,
            status=RunStatus.RUNNING,
            started_at=started,
            lineage=lineage or RunLineage(),
        )
        return RunHandle.create(directory, record)

    def _reserve_dir(self, slug_source: str, started: datetime) -> tuple[Path, str]:
        """Pick and create a unique run directory; return ``(path, run_id)``."""
        stamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        base = f"{stamp}-{_slugify(slug_source)}" if slug_source else stamp
        run_id = base
        directory = self._root / run_id
        suffix = 2
        while directory.exists():
            run_id = f"{base}-{suffix}"
            directory = self._root / run_id
            suffix += 1
        directory.mkdir(parents=True, exist_ok=True)
        return directory, run_id


def _slugify(text: str) -> str:
    """Reduce a URL or label to a filesystem-safe, readable slug.

    For a URL, keep the host and the last path segment (the readable bit); for any
    text, lowercase, replace runs of non-alphanumerics with ``-``, and truncate.
    """
    candidate = text.strip()
    parsed = urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc
        last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        candidate = f"{host}-{last}" if last else host
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", candidate).strip("-").lower()
    return slug[:_MAX_SLUG_LEN].rstrip("-")


def _serialize(model: BaseModel, name: str) -> str:
    """Serialise a model to JSON (``.json`` names) or block-style YAML (default)."""
    if name.endswith(".json"):
        return model.model_dump_json(indent=2)
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(model.model_dump(mode="json", by_alias=True), stream)
    return stream.getvalue()


__all__ = [
    "COST_FILENAME",
    "ENRICHMENT_FILENAME",
    "JURY_VERDICT_FILENAME",
    "RUN_LOG_FILENAME",
    "RUN_RECORD_FILENAME",
    "SPEC_FILENAME",
    "RunHandle",
    "RunKind",
    "RunLineage",
    "RunRecord",
    "RunStatus",
    "RunStore",
]
