"""Shared interactive-interrupt machinery for the typed-artifact verbs.

Architectural source: ``pipeline.md §3.1.1`` (the four-option typed-artifact menu; structural
edit-revalidation), ``§3.2.5`` / ``§3.2.8`` (the per-proposal Accept/Edit menu). Built first for
the post-Extractor interrupt (``cli/extract``, ADR 0024); **generalized here at its second use** —
the post-Planner interrupt (``cli/plan``, ADR 0100) — so the artifact-agnostic machinery has one
home (the same "build at the second use" discipline as ADR 0089's tool-provider hook and ADR
0086's stamp dispatch, and the direction-neutral alternative to ``plan`` importing from
``extract``).

**Only genuinely artifact-agnostic pieces live here:** the menu enums, the four-option prompt
(parameterized by which agent the Feedback option re-runs), the configured YAML round-trip, the
structural edit-revalidation loop, and the per-proposal Accept/Edit loop. Anything AttackSpec- or
LabManifest-specific (serializers, summaries, the promotion/acceptance context) stays in the
owning verb module.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import typer
from ruamel.yaml import YAML

from cyberlab_gen.cli import output

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Per-run cap on auto-accepted proposals in ``--auto`` mode (placeholder 5,
#: ``implementation-plan.md §4.2``; revisited in Phase 4). ``--interactive`` has no cap — the user
#: acts on every proposal individually. Shared by both verbs' ``--auto`` promotion.
DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP = 5


# --- menu option enums -----------------------------------------------------


class ArtifactChoice(StrEnum):
    """The four-option menu for a typed artifact (``pipeline.md §3.1.1``)."""

    APPROVE = "approve"
    FEEDBACK = "feedback"
    EDIT = "edit"
    ABORT = "abort"


class ProposalChoice(StrEnum):
    """The per-proposal menu (``pipeline.md §3.2.5`` / ``§3.2.8``): Accept or Edit only.

    Rejecting a single proposal in isolation has no coherent semantics — the value exists in the
    artifact and the system requires typed values. A user who disagrees Edits, gives agent-level
    feedback at the artifact menu, or Aborts.
    """

    ACCEPT = "accept"
    EDIT = "edit"


#: Type of the editor callable: takes the text to edit, returns the edited text (or ``None`` if the
#: user made no change / aborted the editor). ``click.edit`` matches this; tests inject a fake.
type EditorFn = Callable[[str], str | None]


# --- YAML (de)serialization for the artifact + editor round-trip -----------


def yaml() -> YAML:
    """The configured round-trip YAML used for on-disk artifacts and the ``$EDITOR`` buffer."""
    y = YAML()
    y.default_flow_style = False
    y.width = 4096  # don't wrap long citation strings
    return y


def errors_as_comments(exc: Exception) -> str:
    """Render a validation/parse error as ``#``-prefixed editor comment lines."""
    lines = ["# STRUCTURAL VALIDATION FAILED — fix these and re-save:"]
    for raw_line in str(exc).splitlines():
        lines.append(f"# {raw_line}")
    return "\n".join(lines)


# --- the four-option artifact menu (``pipeline.md §3.1.1``) -----------------


def prompt_artifact_choice(*, rerun_agent: str) -> ArtifactChoice:
    """Render and read the four-option menu (``pipeline.md §3.1.1``).

    ``rerun_agent`` names the upstream agent the Feedback option re-runs (e.g. ``"Extractor"``,
    ``"Planner"``) — the one artifact-specific token in an otherwise generic menu.
    """
    output.print_info(
        f"\nChoose: [a]pprove  [f]eedback (re-run {rerun_agent})  [e]dit in $EDITOR  a[b]ort"
    )
    raw = typer.prompt("action", default="a").strip().lower()
    mapping = {
        "a": ArtifactChoice.APPROVE,
        "approve": ArtifactChoice.APPROVE,
        "f": ArtifactChoice.FEEDBACK,
        "feedback": ArtifactChoice.FEEDBACK,
        "e": ArtifactChoice.EDIT,
        "edit": ArtifactChoice.EDIT,
        "b": ArtifactChoice.ABORT,
        "abort": ArtifactChoice.ABORT,
    }
    choice = mapping.get(raw)
    if choice is None:
        output.print_error(f"unrecognized choice {raw!r}; treating as abort")
        return ArtifactChoice.ABORT
    return choice


# --- the structural edit-revalidation loop (``pipeline.md §3.1.1``) ---------


def edit_with_revalidation[T](
    current: T, *, to_text: Callable[[T], str], parse: Callable[[str], T], editor: EditorFn
) -> T:
    """Open ``current`` (rendered via ``to_text``) in ``$EDITOR``; reopen with error-comments on
    invalid edits.

    ``pipeline.md §3.1.1``: user edits are **structurally** re-validated only; a structurally invalid
    edit reopens the editor with the errors prepended as comments. The user may abort the editor
    (return ``None``/unchanged) to keep the original. Semantic correctness of edits is the user's
    responsibility. ``parse`` takes the edited text and returns the parsed object or raises.
    """
    text = to_text(current)
    while True:
        edited = editor(text)
        if edited is None or edited == text:
            return current  # no change / editor aborted → keep the original
        try:
            return parse(edited)
        except Exception as exc:
            # Broad by design: the user can paste arbitrary YAML into the editor, so any
            # parse/validation failure re-prompts with the errors inlined below. Logged at WARNING
            # with the traceback so a genuine bug surfaces in the run log, not as a silent user typo.
            logger.warning("edited artifact failed structural revalidation: %s", exc, exc_info=True)
            text = f"{errors_as_comments(exc)}\n{edited}"


# --- the per-proposal Accept/Edit menu (``pipeline.md §3.2.5`` / ``§3.2.8``) -


def default_proposal_choice_reader() -> str:
    return typer.prompt("proposal action ([a]ccept / [e]dit)", default="a")


def review_one_proposal[T](
    *,
    label: str,
    model: T,
    to_text: Callable[[T], str],
    parse: Callable[[str], T],
    editor: EditorFn,
    choice_reader: Callable[[], str] = default_proposal_choice_reader,
) -> T:
    """Run the Accept/Edit menu for one proposal; return the (possibly edited) one.

    ``choice_reader`` is injectable so the per-proposal menu can be tested without stdin; it defaults
    to a ``typer.prompt``. Edit reuses the same structural revalidation loop as the artifact edit.
    """
    output.print_info(f"\nProposal: {label}")
    raw = choice_reader().strip().lower()
    if raw in ("a", "accept"):
        return model
    return edit_with_revalidation(model, to_text=to_text, parse=parse, editor=editor)


__all__ = [
    "DEFAULT_AUTO_ACCEPT_PROPOSAL_CAP",
    "ArtifactChoice",
    "EditorFn",
    "ProposalChoice",
    "default_proposal_choice_reader",
    "edit_with_revalidation",
    "errors_as_comments",
    "prompt_artifact_choice",
    "review_one_proposal",
    "yaml",
]
