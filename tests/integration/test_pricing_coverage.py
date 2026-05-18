"""Pricing-coverage smoke test — ``implementation-plan.md`` §3.4 check 5.

Every ``(provider, model)`` pair declared in the bundled
``model_rankings.yaml`` must have a corresponding row in the bundled
``pricing.yaml``. This is one of the two Phase-0 mechanical-consistency
guarantors (Task 4's registry-load test is the other); together they
prevent silent drift between configuration files.

Entries whose model string is the documented sentinel
``<pinned-in-release>`` (currently the OpenAI placeholders) are skipped:
the sentinel is the project's signal that an entry is intentionally
unfilled until release time. Treating it as missing pricing would force
us to either fill in fake OpenAI prices or remove the placeholder
entries entirely — both worse than the skip.
"""

from cyberlab_gen.providers import load_model_rankings, load_pricing_table

_RELEASE_PIN_SENTINEL = "<pinned-in-release>"


def test_every_ranked_model_has_pricing() -> None:
    rankings = load_model_rankings()
    table = load_pricing_table()
    missing: list[tuple[str, str]] = []
    for entries in rankings.by_capability.values():
        for entry in entries:
            if entry.model == _RELEASE_PIN_SENTINEL:
                continue
            try:
                table.lookup(entry.provider, entry.model)
            except KeyError:
                missing.append((entry.provider, entry.model))
    assert not missing, f"rankings reference (provider, model) pairs not in pricing.yaml: {missing}"
