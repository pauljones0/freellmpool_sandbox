"""Keep maintained catalog claims and Pages targets tied to this fork."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from scripts.catalog_counts import catalog_counts
from scripts.check_docs import check_docs

ROOT = Path(__file__).resolve().parents[1]
PAGES_BASE = "https://pauljones0.github.io/freellmpool_sandbox/"


@pytest.mark.parametrize("claim", ["22 provider groups", "22 cataloged provider groups"])
def test_count_gate_rejects_stale_provider_group_claims(tmp_path: Path, claim: str) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.html").write_text(f"freellmpool catalogs {claim}.")
    checker = runpy.run_path(str(ROOT / "scripts/check-counts"))
    errors = checker["_check_public_drift"](tmp_path, catalog_counts(ROOT))
    assert len(errors) == 1
    assert "provider count drift" in errors[0]


def test_count_gate_keeps_historical_audit_facts(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "MODEL_ACTIVITY_AUDIT_2026-07-14.md").write_text("22 provider groups.")
    checker = runpy.run_path(str(ROOT / "scripts/check-counts"))
    assert checker["_check_public_drift"](tmp_path, catalog_counts(ROOT)) == []


def test_count_gate_checks_metadata_even_when_keyless_copy_is_adjacent(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.html").write_text(
        '<title>freellmpool — keyless-capable gateway</title>\n'
        '<meta property="og:description" content="Pool cataloged free-tier routes across '
        '15 LLM providers behind one endpoint. Free, open-source, keyless start when available.">'
    )
    checker = runpy.run_path(str(ROOT / "scripts/check-counts"))
    errors = checker["_check_public_drift"](tmp_path, catalog_counts(ROOT))
    assert len(errors) == 1
    assert "15 LLM providers" in errors[0]


def test_count_gate_preserves_dated_comparison_and_checks_other_legacy_sections(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "legacy-0.13-guide.md").write_text(
        "## How it compares\n\nComparison snapshot: 2026-07-19.\n\n22 chat providers.\n"
        "## Catalog\n\n22 provider groups.\n"
    )
    checker = runpy.run_path(str(ROOT / "scripts/check-counts"))
    errors = checker["_check_public_drift"](tmp_path, catalog_counts(ROOT))
    assert len(errors) == 1
    assert "22 provider groups" in errors[0]


@pytest.mark.parametrize("filename", ["FAQ.md", "README.es.md", ".env.example", "docker-compose.yml"])
def test_retired_providers_have_no_active_setup_guidance(filename: str) -> None:
    content = (ROOT / filename).read_text().casefold()
    assert "aion" not in content
    assert "modelscope" not in content


def test_landing_page_supported_provider_list_excludes_retired_names() -> None:
    content = (ROOT / "docs/index.html").read_text().casefold()
    assert "aion labs" not in content
    assert "modelscope api inference" not in content


def test_docs_checker_resolves_fork_links_and_rejects_missing_targets(tmp_path: Path) -> None:
    (tmp_path / "404.html").write_text('<a href="/freellmpool_sandbox/">Home</a>')
    (tmp_path / "index.html").write_text(f'<a href="{PAGES_BASE}missing.html">Missing</a>')
    (tmp_path / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{PAGES_BASE}</loc></url></urlset>"
    )
    assert check_docs(tmp_path) == ["index.html: missing internal target missing.html"]


def test_current_site_metadata_targets_configured_fork() -> None:
    for path in (ROOT / "docs").glob("*.html"):
        content = path.read_text()
        assert "https://0xzr.github.io/freellmpool/" not in content, path.name
    assert PAGES_BASE in (ROOT / "docs/robots.txt").read_text()
    assert PAGES_BASE in (ROOT / "docs/sitemap.xml").read_text()
