"""Current examples and assets must not advertise a retired provider."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
CURRENT_SURFACES = (
    "README.es.md", "FAQ.md", ".env.example", "docs/ACCOUNTS.md",
    "docs/account-observations.md", "docs/provider-registry.md", "docs/RAG_QUICKSTART.md",
    "docs/LITELLM_MIGRATION.md", "docs/legacy-0.13-guide.md",
    "docs/index.html", "docs/free-llm-api-providers-list.html",
    "docs/free-openai-api-alternatives.html", "docs/free-embedding-leaderboard.html",
    "integrations/opencode-tui/README.md", "assets/demo.svg", "assets/tokenmax-results.svg",
)


@pytest.mark.parametrize("filename", CURRENT_SURFACES)
def test_current_examples_do_not_advertise_ovh(filename: str) -> None:
    assert "ovh" not in (ROOT / filename).read_text().casefold()


def test_rag_requires_reviewed_authenticated_embedding_setup_and_has_no_keyless_script() -> None:
    text = (ROOT / "docs/RAG_QUICKSTART.md").read_text()
    assert "freellmpool setup --provider mistral" in text
    assert "account evidence" in text
    assert 'EMBED_MODEL = "mistral/mistral-embed"' in text
    assert "No eligible embedding route" in text
    assert "rag_container_test.sh" not in text
    assert not (ROOT / "scripts/rag_container_test.sh").exists()


def test_visible_asset_provider_and_route_counts_match_retirement_catalog() -> None:
    root = ElementTree.fromstring((ROOT / "assets/tokenmax-results.svg").read_text())
    texts = {row.attrib.get("x"): row.text for row in root.findall("{http://www.w3.org/2000/svg}text")
             if "metric" in row.attrib.get("class", "").split()}
    assert texts["104"] == "117"
    assert texts["474"] == "12"
    social = (ROOT / "assets/social-preview.svg").read_text()
    assert ">12 cataloged</text>" in social
    assert ">117 chat routes</text>" in social


def test_source_version_and_plugin_counts_match_retirement_release() -> None:
    assert "source version 0.14.7" in (ROOT / "README.md").read_text()
    for filename in ("plugins/llm-freellmpool/README.md", "plugins/llm-freellmpool/pyproject.toml"):
        assert "12 cataloged providers" in (ROOT / filename).read_text()


def test_current_account_observation_docs_count_only_active_registry_records() -> None:
    account_text = (ROOT / "docs/account-observations.md").read_text()
    assert "registry contains 13 provider records" in account_text
    assert "registry contains 16 provider records" not in account_text


def test_api_coverage_docs_explain_remaining_embedding_requirements() -> None:
    coverage_text = (ROOT / "docs/api-coverage.md").read_text()
    assert "two reviewed embedding routes" in coverage_text
    assert "credentials and current reviewed account evidence" in coverage_text
    assert "three reviewed embedding routes" not in coverage_text
