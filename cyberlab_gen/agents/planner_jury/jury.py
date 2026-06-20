"""The Planner-Jury stage: review a draft LabManifest, emit a JuryVerdict.

Architectural source: ``agents.md §5.8`` (the Planner-Jury reviews fidelity to the AttackSpec, phase
decomposition, facet correctness; asymmetric calibration), ``pipeline.md §3.2.7`` (the Planner-Jury
stage). ADR 0078 (the **verify-only** tool set), ADR 0072 (the ``ToolUsingAgent`` contract).

The Planner-Jury is a typed-output agent (output type ``JuryVerdict`` — the **same shape** as the
Extractor-Jury, ``agents.md §5.8`` / ``pipeline.md §3.2.7``) invoked with the
``high_quality_reasoning`` capability (review is a judgment task). Its tool set is the
``ToolUsingAgent`` **verify-only** set (``external_lookup`` only, no ``propose_*``) — inherited via
the ``verify_only_tools`` hook (ADR 0078), the mechanical read/write split enforced by tool
availability, never prose. It reuses the Extractor-Jury's ``JuryVerdict`` rather than inventing a
bespoke verdict pair: ``§5.8`` and ``pipeline.md §3.2.7`` both say "same shape as Extractor-Jury",
and the four rubric dimensions (fidelity, completeness, provenance correctness, structural validity)
read naturally for the manifest (fidelity to the AttackSpec, coverage completeness, correctness of
the Planner's llm_inference provenance, structural validity of the manifest).

The LLM makes the judgment and returns the verdict; the **framework** — the plan-refinement
coordinator (:mod:`cyberlab_gen.framework.plan_orchestrator`) — maps the verdict to control flow
(``architecture.md §1.5``). This stage only produces the judgment.

Asymmetric calibration (``agents.md §5.8`` → ``§5.5``, ``eval.md §7.5``, recorded in
``CALIBRATION.md``): the 0.7 rubric floor is tuned *up* on observed false-approval, never *down* on
false-rejection — false-approval is costlier because a bad manifest cascades through every Generator.
The floor is a **placeholder** (``architecture.md §8.4``) exposed as a parameter so the eval harness
can drive it; the discipline forbids loosening. The *number* is locked by the architect's eval run
(Task 10 / ``CALIBRATION.md``), not here — Task 4 builds the asymmetric discipline, not the value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cyberlab_gen.agents.extractor_jury.schema import JuryVerdict
from cyberlab_gen.agents.tool_agent import ToolUsingAgent, verify_only_external_lookup_offered
from cyberlab_gen.providers.base import AgentLabel, CapabilityHint

if TYPE_CHECKING:
    from cyberlab_gen.external_data_sources import NvdClient
    from cyberlab_gen.providers.base import Provider
    from cyberlab_gen.providers.ranking import ProviderRegistry
    from cyberlab_gen.registries.merge import MergedRegistries
    from cyberlab_gen.schemas.attack_spec import AttackSpec
    from cyberlab_gen.schemas.manifest import LabManifest

PLANNER_JURY_AGENT_DIR = "planner_jury"

#: Default rubric floor (``agents.md §5.8`` → ``§5.5``, ``architecture.md §8.4`` placeholder). Its
#: own constant (not the Extractor-Jury's) so the architect can calibrate the two juries
#: independently per ``CALIBRATION.md``; mirrors the Extractor-Jury's 0.7 default for now (the
#: Task-4 brief: "mirror the Extractor-Jury 0.7 default").
DEFAULT_RUBRIC_FLOOR = 0.7

#: Tool-use loop depth for one jury pass (independent external_api verification).
DEFAULT_MAX_TOOL_ITERATIONS = 8


class PlannerJury(ToolUsingAgent):
    """Drives one Planner-Jury review of a draft LabManifest (``agents.md §5.8``).

    The six-step tool-loop sequence lives in :class:`ToolUsingAgent` (ADR 0072); this stage supplies
    the ``high_quality_reasoning`` capability, the ``JuryVerdict`` output schema, and the review user
    turn, then returns the verdict. It is **verify-only** (ADR 0078): the base wires the
    ``external_lookup`` read tool and withholds every ``propose_*`` write tool, so the read/write
    split is mechanical, not prose — the same default the Extractor-Jury uses.
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
            agent_label=AgentLabel.PLANNER_JURY,
            agent_dir=PLANNER_JURY_AGENT_DIR,
            max_tool_iterations=max_tool_iterations,
            nvd_client=nvd_client,
            # The jury reviews; it must not propose (no value-type / facet proposals). The §1.5
            # read/write split is enforced by tool availability (ADR 0078): external_lookup only.
            verify_only_tools=True,
        )
        self._rubric_floor = rubric_floor

    @property
    def rubric_floor(self) -> float:
        return self._rubric_floor

    async def review(self, *, manifest: LabManifest, attack_spec: AttackSpec) -> JuryVerdict:
        """Review ``manifest`` against ``attack_spec`` and return a validated ``JuryVerdict``.

        Asks the LLM for the verdict over the draft manifest + the enriched AttackSpec it must be
        faithful to (``agents.md §5.8`` inputs — the Planner's reasoning trace travels *in* the
        manifest's ``llm_inference`` provenance). The framework reads ``verdict`` to route control
        flow (``architecture.md §1.5``).
        """
        user_content = self._build_user_turn(manifest=manifest, attack_spec=attack_spec)
        response, _ = await self._emit(
            capability=CapabilityHint.HIGH_QUALITY_REASONING,
            output_schema=JuryVerdict,
            user_content=user_content,
            # Gate the dead verify-only external_lookup off unless there is verifiable work (ADR
            # 0105): without this the jury spirals the source catalog into a ToolLoopError.
            offer_external_lookup=verify_only_external_lookup_offered(
                nvd_client_wired=self._nvd_client is not None, spec=attack_spec
            ),
        )
        return response.output

    def _build_user_turn(self, *, manifest: LabManifest, attack_spec: AttackSpec) -> str:
        return (
            f"RUBRIC FLOOR: every dimension must score >= {self._rubric_floor}.\n\n"
            "DRAFT LABMANIFEST UNDER REVIEW (YAML — the Planner's structural decisions; its "
            "reasoning trace is the llm_inference provenance on the content fields):\n"
            f"{manifest.to_yaml()}\n\n"
            "ENRICHED ATTACKSPEC (YAML — the manifest must be faithful to this; do not repair it, "
            "flag incoherence for the Planner to route back):\n"
            f"{attack_spec.to_yaml()}"
        )


__all__ = [
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "DEFAULT_RUBRIC_FLOOR",
    "PLANNER_JURY_AGENT_DIR",
    "PlannerJury",
]
