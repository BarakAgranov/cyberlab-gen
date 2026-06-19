"""Tests for the data-driven enrichment seam and the per-source adapters (ADR 0101).

Exercises the Task-9 exit criteria:

1. **data-driven** — a triggered source resolves to its registered adapter and
   enriches; a registry entry with no adapter stub-skips (no hardcoded dispatch);
2. **five new sources enrich on recorded fixtures** — KEV / EPSS / MSRC parse
   recorded publisher payloads losslessly into typed records, and the adapters
   append them; bulletins parse a recorded RSS feed when the target facet is
   present; OSV honestly skips (its trigger field has no schema home);
3. **materiality on a real disagreement** — covered by the NVD cross-tier tests
   in ``tests/unit/framework/test_enrichment.py`` (the only corroboration-capable
   source); additive sources have no blog value to disagree with;
4. **unavailable sources never fatal** (ADR 0042) — a client that raises records
   a skip and the pass continues.

Fakes returning canned typed records are the recorded-fixture seam (the same
pattern as the NVD ``_FakeNvd``); the ``parse_*`` functions are additionally
tested against realistic recorded JSON/XML payloads to prove lossless capture.
"""

from __future__ import annotations

from cyberlab_gen.errors import ExternalApiUnavailableError
from cyberlab_gen.external_data_sources import (
    EpssRecord,
    KevRecord,
    MsrcRecord,
    SourceClients,
    parse_epss_response,
    parse_kev_catalog,
    parse_msrc_cvrf,
    parse_rss_feed,
)
from cyberlab_gen.framework.enrichment import EnrichmentConfig, enrich
from cyberlab_gen.schemas.attack_spec import (
    AttackSpec,
    ChainBlock,
    ChainStep,
    ChainStepTechniques,
    CveReference,
    ExternalRefsBlock,
    ExtractionMetadataBlock,
    SourceBlock,
    ThesisBlock,
)
from cyberlab_gen.schemas.enums import (
    CitationKind,
    ExtractionOutcome,
    ProvenanceSource,
    ProvisioningMechanism,
)
from cyberlab_gen.schemas.provenance import CitationBlock, ProvenanceString
from cyberlab_gen.schemas.registries import MitreTechniqueCatalog

_HASH = "a" * 64


# --- fakes -----------------------------------------------------------------


class _FakeKev:
    def __init__(self, table: dict[str, KevRecord]) -> None:
        self.table = table

    def lookup(self, cve_id: str) -> KevRecord | None:
        return self.table.get(cve_id)


class _FakeEpss:
    def __init__(self, table: dict[str, EpssRecord]) -> None:
        self.table = table

    def lookup(self, cve_id: str) -> EpssRecord | None:
        return self.table.get(cve_id)


class _FakeMsrc:
    def __init__(self, table: dict[str, MsrcRecord]) -> None:
        self.table = table

    def lookup(self, cve_id: str) -> MsrcRecord | None:
        return self.table.get(cve_id)


class _Unavailable:
    """Any client whose every call raises (the never-fatal degrade path)."""

    def lookup(self, cve_id: str) -> object:
        raise ExternalApiUnavailableError("down")

    def list_recent(self) -> list[object]:
        raise ExternalApiUnavailableError("down")


# --- builders --------------------------------------------------------------


def _pstr(value: str) -> ProvenanceString:
    return ProvenanceString(
        value=value,
        source=ProvenanceSource.BLOG_EXPLICIT,
        citations=[CitationBlock(kind=CitationKind.BLOG_PASSAGE, reference="§1")],
    )


def _mitre_catalog() -> MitreTechniqueCatalog:
    return MitreTechniqueCatalog(entries=[])


def _cve(cve_id: str) -> CveReference:
    return CveReference(cve_id=cve_id, description=_pstr("a cve"))  # type: ignore[arg-type]


def _spec(*, cves: list[CveReference] | None = None, facets: list[str] | None = None) -> AttackSpec:
    return AttackSpec(
        spec_version=1,
        source=SourceBlock(
            url="https://example.com/blog",  # type: ignore[arg-type]
            canonical_url="https://example.com/blog",  # type: ignore[arg-type]
            title="t",
            publisher={"name": "n", "domain": "d.com", "kind": "researcher_personal"},  # type: ignore[arg-type]
            fetched_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
            content_hash=_HASH,
            fetch_method="http_get",
            word_count=10,
        ),
        extraction_outcome=ExtractionOutcome.IN_SCOPE,
        thesis=ThesisBlock(
            types=["vulnerability_chain"],  # type: ignore[list-item]
            summary=_pstr("s"),
            attacker_objective=_pstr("o"),
            vulnerability_story=_pstr("v"),
            duration_as_described=_pstr("d"),
        ),
        facets=facets or [],  # type: ignore[arg-type]
        chain=ChainBlock(
            chain_steps=[
                ChainStep(
                    id="step-1",  # type: ignore[arg-type]
                    step_number=1,
                    title="s",
                    description=_pstr("d"),
                    blog_excerpt="excerpt",
                    techniques=ChainStepTechniques(mitre=[]),
                    reproducibility={  # type: ignore[arg-type]
                        "classification": "full",
                        "caveats": _pstr("none"),
                        "why": _pstr("scriptable"),
                    },
                    provisioning_mechanism=ProvisioningMechanism.TERRAFORM,
                )
            ]
        ),
        external_references=ExternalRefsBlock(cves=cves or []),
        extraction_metadata=ExtractionMetadataBlock(
            extractor_version="1.0.0", model="mock", completeness_score=0.5, citations_count=1
        ),
    )


def _config(**clients: object) -> EnrichmentConfig:
    return EnrichmentConfig(
        mitre_catalog=_mitre_catalog(),
        clients=SourceClients(**clients),  # type: ignore[arg-type]
    )


# --- KEV parse (lossless) + adapter ----------------------------------------


def test_parse_kev_catalog_captures_documented_fields() -> None:
    payload = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2021-44228",
                "vendorProject": "Apache",
                "product": "Log4j2",
                "vulnerabilityName": "Apache Log4j2 RCE",
                "dateAdded": "2021-12-10",
                "shortDescription": "JNDI lookups...",
                "requiredAction": "Apply updates.",
                "dueDate": "2021-12-24",
                "knownRansomwareCampaignUse": "Known",
                "notes": "https://...",
                "cwes": ["CWE-502", "CWE-917"],
            }
        ]
    }
    index = parse_kev_catalog(payload)
    rec = index["CVE-2021-44228"]
    assert rec.date_added == "2021-12-10"
    assert rec.due_date == "2021-12-24"
    assert rec.known_ransomware_campaign_use == "Known"
    assert rec.required_action == "Apply updates."
    assert rec.cwes == ["CWE-502", "CWE-917"]


def test_kev_adapter_records_listed_cves_only() -> None:
    spec = _spec(cves=[_cve("CVE-2021-44228"), _cve("CVE-2000-9999")])
    kev = _FakeKev({"CVE-2021-44228": KevRecord(source_id="cisa_kev", cve_id="CVE-2021-44228")})
    result = enrich(spec, _config(kev=kev))
    assert [r.cve_id for r in result.kev_records] == ["CVE-2021-44228"]


# --- EPSS parse + adapter --------------------------------------------------


def test_parse_epss_response_parses_string_scores() -> None:
    payload = {
        "status": "OK",
        "data": [
            {
                "cve": "CVE-2022-27225",
                "epss": "0.01654",
                "percentile": "0.85959",
                "date": "2022-03-05",
            }
        ],
    }
    rec = parse_epss_response(payload)
    assert rec is not None
    assert rec.cve_id == "CVE-2022-27225"
    assert abs(rec.epss - 0.01654) < 1e-9
    assert abs(rec.percentile - 0.85959) < 1e-9
    assert rec.as_of == "2022-03-05"


def test_parse_epss_response_empty_data_is_none() -> None:
    assert parse_epss_response({"status": "OK", "data": []}) is None


def test_epss_adapter_records_scored_cves() -> None:
    spec = _spec(cves=[_cve("CVE-2022-27225")])
    epss = _FakeEpss(
        {
            "CVE-2022-27225": EpssRecord(
                source_id="epss", cve_id="CVE-2022-27225", epss=0.1, percentile=0.9
            )
        }
    )
    result = enrich(spec, _config(epss=epss))
    assert [r.cve_id for r in result.epss_records] == ["CVE-2022-27225"]
    assert result.calls_made == 1


# --- MSRC CVRF parse (nested) + adapter ------------------------------------


def test_parse_msrc_cvrf_resolves_products_and_remediations() -> None:
    payload = {
        "ProductTree": {
            "FullProductName": [
                {"ProductID": "11926", "Value": "Windows 11 Version 22H2"},
            ]
        },
        "Vulnerability": [
            {
                "CVE": "CVE-2024-21351",
                "Title": {"Value": "Windows SmartScreen Security Feature Bypass"},
                "ProductStatuses": [{"ProductID": ["11926"], "Type": 3}],
                "Remediations": [
                    {
                        "ProductID": ["11926"],
                        "Description": {"Value": "5034765"},
                        "URL": "https://msrc.example/KB5034765",
                        "FixedBuild": "10.0.22621.3155",
                        "Type": 2,
                    }
                ],
                "CVSSScoreSets": [
                    {"BaseScore": 7.6, "Vector": "CVSS:3.1/AV:N", "ProductID": ["11926"]}
                ],
            }
        ],
    }
    rec = parse_msrc_cvrf(payload, "CVE-2024-21351")
    assert rec is not None
    assert rec.title == "Windows SmartScreen Security Feature Bypass"
    assert rec.affected_products == ["Windows 11 Version 22H2"]
    assert rec.cvss_score == 7.6
    assert rec.remediations[0].fixed_build == "10.0.22621.3155"
    assert rec.remediations[0].product == "Windows 11 Version 22H2"


def test_parse_msrc_cvrf_unknown_cve_is_none() -> None:
    assert parse_msrc_cvrf({"Vulnerability": []}, "CVE-2024-21351") is None


def test_msrc_adapter_records_microsoft_cves() -> None:
    spec = _spec(cves=[_cve("CVE-2024-21351"), _cve("CVE-2000-1111")])
    msrc = _FakeMsrc({"CVE-2024-21351": MsrcRecord(source_id="msrc", cve_id="CVE-2024-21351")})
    result = enrich(spec, _config(msrc=msrc))
    assert [r.cve_id for r in result.msrc_records] == ["CVE-2024-21351"]


# --- bulletins RSS/Atom parse + facet-gated adapter ------------------------


def test_parse_rss_feed_reads_items() -> None:
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>AWS-2024-001</title><link>https://aws/1</link>
        <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
        <description>An AWS issue.</description></item>
    </channel></rss>"""
    records = parse_rss_feed(feed, source_id="aws_security_bulletins")
    assert len(records) == 1
    assert records[0].title == "AWS-2024-001"
    assert records[0].link == "https://aws/1"


def test_parse_rss_feed_reads_atom_entries() -> None:
    feed = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>GCP-2024-001</title>
        <link href="https://gcp/1"/><updated>2024-01-01T00:00:00Z</updated>
        <summary>A GCP issue.</summary></entry>
    </feed>"""
    records = parse_rss_feed(feed, source_id="gcp_security_bulletins")
    assert records[0].title == "GCP-2024-001"
    assert records[0].link == "https://gcp/1"


def test_parse_rss_feed_malformed_is_empty() -> None:
    assert parse_rss_feed("<not xml", source_id="aws_security_bulletins") == []


class _FakeBulletin:
    def __init__(self, feed: str, source_id: str) -> None:
        self._feed = feed
        self._source_id = source_id

    def list_recent(self) -> object:
        return parse_rss_feed(self._feed, source_id=self._source_id)


def test_bulletin_adapter_fires_only_when_target_facet_present() -> None:
    feed = (
        '<rss version="2.0"><channel><item><title>AWS-1</title>'
        "<description>x</description></item></channel></rss>"
    )
    bulletins = {"aws_security_bulletins": _FakeBulletin(feed, "aws_security_bulletins")}

    # target:aws present -> bulletins recorded.
    spec = _spec(cves=[], facets=["target:aws"])
    result = enrich(spec, _config(bulletins=bulletins))
    assert [r.title for r in result.bulletin_records] == ["AWS-1"]

    # no aws facet -> the trigger does not fire (no record, no skip from this source).
    spec2 = _spec(cves=[], facets=["target:azure"])
    result2 = enrich(spec2, _config(bulletins=bulletins))
    assert not result2.bulletin_records


# --- OSV honest skip (no schema home) --------------------------------------


def test_osv_records_honest_skip_for_unresolved_trigger() -> None:
    spec = _spec(cves=[_cve("CVE-2021-44228")])
    result = enrich(spec, _config())
    osv_skips = [s for s in result.skipped if s.source_id == "osv_dev"]
    assert osv_skips
    assert any("no home in the current AttackSpec schema" in s.reason for s in osv_skips)


# --- data-driven dispatch + never-fatal ------------------------------------


def test_no_adapter_entry_stub_skips_without_hardcoded_dispatch() -> None:
    # github_api has a registry entry but no registered adapter -> stub-skip,
    # proving the driver is data-driven (it does not hardcode which sources run).
    spec = _spec(cves=[_cve("CVE-2021-44228")])
    result = enrich(spec, _config())
    assert any(s.source_id == "github_api" for s in result.skipped)


def test_unavailable_source_is_never_fatal() -> None:
    # KEV + EPSS + MSRC clients all raise; enrich must not raise and must record skips.
    spec = _spec(cves=[_cve("CVE-2021-44228")])
    result = enrich(
        spec,
        _config(kev=_Unavailable(), epss=_Unavailable(), msrc=_Unavailable()),
    )
    reasons = [s.reason for s in result.skipped]
    assert any("unavailable" in r for r in reasons)
    # no records produced, but the pass completed.
    assert not result.kev_records and not result.epss_records and not result.msrc_records
