"""The Extractor-Jury stage: review an AttackSpec, emit a JuryVerdict.

Architectural source: ``agents.md §5.5``, ``pipeline.md §3.2.3``, ADR 0021.

The jury is a typed-output agent (output type ``JuryVerdict``) invoked via Task
2's call surface with the ``high_quality_reasoning`` capability (review is a
judgment task, not long-context extraction). It has the *same tool inventory* as
the Extractor so it can independently verify ``external_api`` responses
(``agents.md §5.5``).

The orchestrator-owned grounding stack (``validation.md §6.10.2``,
:mod:`cyberlab_gen.validators.grounding_validator`) computes the mechanical
findings (provenance-structure, search-before-claim, CVE) once; the jury
**consumes** that findings set as prompt grounding and does *not* re-derive it
(``agents.md §5.5``, ADR 0051/0060) — mirroring how the Critic reads the Validator
report "without re-checking" (``§5.14``). The LLM then makes the fidelity /
semantic judgment and returns the verdict. The framework — not the jury — maps the
verdict to control flow (``architecture.md §1.5``); this stage only produces the
judgment.

Asymmetric calibration (``agents.md §5.5``, ``eval.md §7.5``, recorded in
``CALIBRATION.md``): the 0.7 rubric floor is tuned *up* on observed
false-approval, never *down* on false-rejection. This stage exposes the floor as
a parameter so the eval harness can drive it, but the discipline forbids loosening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
from cyberlab_gen.agents.tool_agent import ToolUsingAgent
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint

if TYPE_CHECKING:
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.validators.grounding_validator import GroundingFinding

EXTRACTOR_JURY_AGENT_DIR = "extractor_jury"

#: Default rubric floor (``agents.md §5.5``, ``architecture.md §8.4`` placeholder).
DEFAULT_RUBRIC_FLOOR = 0.7

#: Tool-use loop depth for one jury pass (independent external_api verification).
DEFAULT_MAX_TOOL_ITERATIONS = 8


class ExtractorJury(ToolUsingAgent):
    """Drives one Extractor-Jury review of an AttackSpec (``agents.md §5.5``).

    The six-step tool-loop sequence lives in :class:`ToolUsingAgent` (ADR 0072); this stage supplies
    the ``high_quality_reasoning`` capability, the ``JuryVerdict`` output schema, and the review
    user turn, then returns the verdict. It shares the Extractor's tool inventory (so it can
    independently verify ``external_api`` responses), which the base wires.
    """

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: MergedRegistries,
        nvd_client: NvdClient | None = None,
        rubric_floor: float = DEFAULT_RUBRIC_FLOOR,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        if not 0.0 <= rubric_floor <= 1.0:
            raise ValueError("rubric_floor must be in [0, 1]")
        super().__init__(
            provider=provider,
            registry=registry,
            registries=registries,
            agent_label=AgentLabel.EXTRACTOR_JURY,
            agent_dir=EXTRACTOR_JURY_AGENT_DIR,
            max_tool_iterations=max_tool_iterations,
            nvd_client=nvd_client,
        )
        self._rubric_floor = rubric_floor

    @property
    def rubric_floor(self) -> float:
        return self._rubric_floor

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        grounding_findings: list[GroundingFinding] | None = None,
    ) -> JuryVerdict:
        """Review ``spec`` against ``blog_content`` and the orchestrator's grounding findings.

        ``grounding_findings`` is the orchestrator-owned mechanical-validator stack's findings
        set (``validation.md §6.10.2``, ADR 0051/0060); the jury **consumes** it as prompt
        grounding and does not re-derive it. Asks the LLM for the verdict and returns a
        validated ``JuryVerdict``; the framework reads ``verdict`` to route control flow.
        """
        user_content = self._build_user_turn(
            spec=spec, blog_content=blog_content, findings=grounding_findings or []
        )
        response, _ = await self._emit(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            output_schema=JuryVerdict,
            user_content=user_content,
        )
        return response.output

    def _build_user_turn(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        findings: list[GroundingFinding],
    ) -> str:
        if findings:
            findings_text = "\n".join(
                f"- {f.location} ({f.code.value}): {f.detail}" for f in findings
            )
        else:
            findings_text = "(none — every provenance envelope is structurally grounded)"
        return (
            f"RUBRIC FLOOR: every dimension must score >= {self._rubric_floor}.\n\n"
            "MECHANICAL GROUNDING FINDINGS (framework-computed by the orchestrator's stack; "
            "treat as ground truth for structure, then make your own fidelity judgment):\n"
            f"{findings_text}\n\n"
            "ATTACKSPEC UNDER REVIEW (YAML):\n"
            f"{spec.to_yaml()}\n\n"
            "ORIGINAL BLOG CONTENT (verbatim, for fidelity checks):\n"
            f"{blog_content}"
        )


__all__ = [
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "DEFAULT_RUBRIC_FLOOR",
    "EXTRACTOR_JURY_AGENT_DIR",
    "ExtractorJury",
]
