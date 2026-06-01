"""The Extractor-Jury stage: review an AttackSpec, emit a JuryVerdict.

Architectural source: ``agents.md §5.5``, ``pipeline.md §3.2.3``, ADR 0021.

The jury is a typed-output agent (output type ``JuryVerdict``) invoked via Task
2's call surface with the ``high_quality_reasoning`` capability (review is a
judgment task, not long-context extraction). It has the *same tool inventory* as
the Extractor so it can independently verify ``external_api`` responses
(``agents.md §5.5``).

The framework runs the mechanical provenance-structure verifier
(``verification.verify_provenance``) before the LLM call and feeds the findings
into the jury prompt as grounding; the LLM then makes the fidelity / semantic
judgment and returns the verdict. The framework — not the jury — maps the
verdict to control flow (``architecture.md §1.5``); this stage only produces the
judgment.

Asymmetric calibration (``agents.md §5.5``, ``eval.md §7.5``, recorded in
``CALIBRATION.md``): the 0.7 rubric floor is tuned *up* on observed
false-approval, never *down* on false-rejection. This stage exposes the floor as
a parameter so the eval harness can drive it, but the discipline forbids loosening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.agents.call_surface import AgentRunner
from cyberlab_gen.agents.extractor.tools import (
    ExtractorToolExecutor,
    extractor_tool_definitions,
)
from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
from cyberlab_gen.agents.extractor_jury.verification import (
    ProvenanceFinding,
    verify_provenance,
)
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint

if TYPE_CHECKING:
    from cyberlab_gen.agents.extractor.tools import ExternalLookupRecord
    from cyberlab_gen.framework.enrichment import NvdClient
    from cyberlab_gen.providers.base import Provider
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.schemas.attack_spec import AttackSpec

EXTRACTOR_JURY_AGENT_DIR = "extractor_jury"

#: Default rubric floor (``agents.md §5.5``, ``architecture.md §8.4`` placeholder).
DEFAULT_RUBRIC_FLOOR = 0.7

#: Tool-use loop depth for one jury pass (independent external_api verification).
DEFAULT_MAX_TOOL_ITERATIONS = 8


class ExtractorJury:
    """Drives one Extractor-Jury review of an AttackSpec (``agents.md §5.5``)."""

    def __init__(
        self,
        *,
        provider: Provider,
        registry: ProviderRegistry,
        registries: object,  # MergedRegistries; loose to avoid a runtime import here
        nvd_client: NvdClient | None = None,
        rubric_floor: float = DEFAULT_RUBRIC_FLOOR,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        if not 0.0 <= rubric_floor <= 1.0:
            raise ValueError("rubric_floor must be in [0, 1]")
        self._runner = AgentRunner(
            agent_label=AgentLabel.EXTRACTOR_JURY,
            agent_dir=EXTRACTOR_JURY_AGENT_DIR,
            provider=provider,
            registry=registry,
        )
        self._registries = registries
        self._nvd_client = nvd_client
        self._rubric_floor = rubric_floor
        self._max_tool_iterations = max_tool_iterations

    @property
    def rubric_floor(self) -> float:
        return self._rubric_floor

    async def review(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        lookups: list[ExternalLookupRecord] | None = None,
    ) -> JuryVerdict:
        """Review ``spec`` against ``blog_content`` and the Extractor's tool trace.

        Runs the mechanical provenance verifier first, then asks the LLM for the
        verdict with the findings supplied as grounding. Returns a validated
        ``JuryVerdict``; the framework reads ``verdict`` to route control flow.
        """
        from cyberlab_gen.registries.merge import MergedRegistries

        if not isinstance(self._registries, MergedRegistries):  # pragma: no cover - guard
            raise TypeError("ExtractorJury.registries must be a MergedRegistries")

        findings = verify_provenance(spec, lookups)
        executor = ExtractorToolExecutor(registries=self._registries, nvd_client=self._nvd_client)
        user_content = self._build_user_turn(
            spec=spec, blog_content=blog_content, findings=findings
        )
        messages = self._runner.build_messages(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            user_content=user_content,
        )
        response = await self._runner.run_with_tools(
            messages,
            output_schema=JuryVerdict,
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            tools=extractor_tool_definitions(),
            tool_executor=executor,
            max_iterations=self._max_tool_iterations,
        )
        return response.output

    def _build_user_turn(
        self,
        *,
        spec: AttackSpec,
        blog_content: str,
        findings: list[ProvenanceFinding],
    ) -> str:
        if findings:
            findings_text = "\n".join(
                f"- {f.field_path} ({f.source}): {f.detail}" for f in findings
            )
        else:
            findings_text = "(none — every provenance envelope is structurally grounded)"
        return (
            f"RUBRIC FLOOR: every dimension must score >= {self._rubric_floor}.\n\n"
            "MECHANICAL PROVENANCE FINDINGS (framework-computed; treat as ground truth "
            "for structure, then make your own fidelity judgment):\n"
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
