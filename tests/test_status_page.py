"""G18: live free-tier status page publisher (snapshot + staleness + history)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freellmpool.healthcheck import HealthRow
from freellmpool.status_page import (
    HISTORY_LIMIT,
    append_history,
    assert_no_key_material,
    build_snapshot,
    publish_status,
    render_status_html,
    validate_published,
)

ROWS = [
    HealthRow("groq/llama-3", "ok", 412.0, "12 tok"),
    HealthRow("gemini/flash", "rate_limited", None, "429 quota exceeded"),
]

HOSTILE_SECRET = "sk-live-abcdefghijklmnop1234"


def _snapshot() -> dict:
    return build_snapshot(ROWS, generated_at="2026-09-19T23:00:00Z", version="0.13.0")


def test_build_snapshot_projects_only_safe_fields() -> None:
    snap = _snapshot()
    assert snap["schema"] == 1
    assert snap["generated_at"] == "2026-09-19T23:00:00Z"
    assert snap["rows"] == [
        {"target": "groq/llama-3", "status": "ok", "latency_ms": 412.0, "note": "12 tok"},
        {"target": "gemini/flash", "status": "rate_limited", "latency_ms": None,
         "note": "429 quota exceeded"},
    ]
    assert set(snap) == {"schema", "generated_at", "freellmpool", "rows"}


def test_append_history_caps_at_limit_newest_last() -> None:
    history: list = []
    for i in range(HISTORY_LIMIT + 5):
        snap = build_snapshot([], generated_at=f"2026-09-{10 + i:02d}T00:00:00Z",
                              version="0.13.0")
        history = append_history(history, snap)
    assert len(history) == HISTORY_LIMIT
    assert history[-1]["generated_at"] == "2026-09-26T00:00:00Z"
    assert history[0]["generated_at"] == "2026-09-15T00:00:00Z"


def test_render_escapes_html_and_labels_staleness() -> None:
    rows = [HealthRow("<b>evil</b>", "ok", 1.0, "<script>alert(1)</script>")]
    snap = build_snapshot(rows, generated_at="2026-09-19T23:00:00Z", version="0.13.0")
    html = render_status_html(snap, [snap])
    assert "<script>alert(1)</script>" not in html and "<b>evil" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "2026-09-19T23:00:00Z" in html
    assert "stale" in html.lower() or "old" in html.lower()
    assert "1/1" in html  # ok/total summary


def test_render_empty_rows_is_honest_not_green() -> None:
    snap = build_snapshot([], generated_at="2026-09-19T23:00:00Z", version="0.13.0")
    html = render_status_html(snap, [])
    assert "No configured providers" in html
    assert "0/0" in html


def test_secret_scanner_passes_clean_and_redacted_text() -> None:
    assert_no_key_material("ok 12 tok, latency 412 ms")
    assert_no_key_material("note with [REDACTED_SECRET] marker is safe")


@pytest.mark.parametrize("secret", [
    "sk-live-abcdefghijklmnop1234",
    "sk-proj-abcdefghijklmnop1234",
    "ghp_abcdefghijklmnop1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "AIzaSyA-abcdefghijklmnopqrstuvw",
    "api_key=supersecretvalue123",
])
def test_secret_scanner_rejects_key_material(secret: str) -> None:
    with pytest.raises(ValueError, match="key material"):
        assert_no_key_material(f"probe note leaked {secret} oops")


def test_publish_redacts_hostile_notes_and_merges_history(tmp_path: Path) -> None:
    hostile = [HealthRow("evil/p", "error", None, f"failed: key {HOSTILE_SECRET} denied")]
    page, history_file = publish_status(tmp_path, hostile,
                                        generated_at="2026-09-19T23:00:00Z",
                                        version="0.13.0")
    assert page.name == "free-tier-status.html"
    assert history_file.name == "status-history.json"
    for path in (page, history_file):
        text = path.read_text()
        assert HOSTILE_SECRET not in text
        assert "REDACTED" in text
    assert validate_published(tmp_path) == []
    # Second publish appends history rather than replacing it.
    publish_status(tmp_path, ROWS, generated_at="2026-09-20T00:00:00Z", version="0.13.0")
    history = json.loads(history_file.read_text())
    assert [h["generated_at"] for h in history] == ["2026-09-19T23:00:00Z",
                                                   "2026-09-20T00:00:00Z"]


def test_cli_publish_from_rows_file_and_check(tmp_path: Path, capsys) -> None:
    from freellmpool.cli import main

    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([
        {"target": "groq/llama-3", "status": "ok", "latency_ms": 100.0, "note": "8 tok"},
    ]))
    docs_dir = tmp_path / "docs"
    assert main(["status-page", "publish", "--docs-dir", str(docs_dir),
                 "--rows-file", str(rows_file)]) == 0
    out = capsys.readouterr().out
    assert "1/1 ok" in out
    assert (docs_dir / "free-tier-status.html").is_file()
    assert main(["status-page", "check", "--docs-dir", str(docs_dir)]) == 0
    assert "valid" in capsys.readouterr().out


def test_cli_check_fails_on_missing_files(tmp_path: Path) -> None:
    from freellmpool.cli import main

    assert main(["status-page", "check", "--docs-dir", str(tmp_path)]) == 1


def test_publish_syncs_sitemap_lastmod_to_snapshot_date(tmp_path: Path) -> None:
    from freellmpool.status_page import sync_sitemap_lastmod
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url><loc>https://0xzr.github.io/freellmpool/free-tier-status.html</loc>'
        "<lastmod>2026-09-19</lastmod><priority>0.8</priority></url>\n"
        '  <url><loc>https://0xzr.github.io/freellmpool/other.html</loc>'
        "<lastmod>2026-08-01</lastmod><priority>0.5</priority></url>\n"
        "</urlset>\n")
    publish_status(tmp_path, ROWS, generated_at="2026-09-25T15:30:00Z", version="0.13.0")
    text = sitemap.read_text()
    assert "<lastmod>2026-09-25</lastmod>" in text
    assert "<lastmod>2026-08-01</lastmod>" in text  # other entries untouched
    page = (tmp_path / "free-tier-status.html").read_text()
    assert "2026-09-25" in page  # sitemap date stays visible on the page
    # Missing sitemap is a no-op for fresh docs dirs.
    (tmp_path / "sitemap.xml").unlink()
    assert sync_sitemap_lastmod(tmp_path, "2026-09-26T00:00:00Z") is False


def test_validate_published_detects_shape_and_secret_problems(tmp_path: Path) -> None:
    assert validate_published(tmp_path)  # missing files
    (tmp_path / "free-tier-status.html").write_text("<html>no stamp</html>")
    (tmp_path / "status-history.json").write_text(json.dumps([{"schema": 999}]))
    errors = validate_published(tmp_path)
    assert any("generated_at" in e for e in errors)
    assert any("schema" in e for e in errors)
    (tmp_path / "status-history.json").write_text(
        json.dumps([{"schema": 1, "generated_at": "t", "note": HOSTILE_SECRET}]))
    assert any("key material" in e for e in validate_published(tmp_path))


def test_render_reports_attempted_and_skipped_separately_and_fork_urls() -> None:
    rows = [*ROWS, HealthRow("llm7", "skipped", None, "evidence expired")]
    snap = build_snapshot(rows, generated_at="2026-09-29T00:00:00Z", version="0.14.6")
    rendered = render_status_html(snap, [snap])
    assert "1/2" in rendered
    assert "1 skipped" in rendered
    assert "Responding/attempted" in rendered
    assert "https://pauljones0.github.io/freellmpool_sandbox/free-tier-status.html" in rendered
    assert "https://github.com/pauljones0/freellmpool_sandbox" in rendered
    assert "0xzr.github.io" not in rendered
    assert "every configured" not in rendered


def test_all_skipped_is_unobserved_not_zero_responding_outage() -> None:
    snap = build_snapshot([HealthRow("llm7", "skipped", None, "evidence expired")],
                          generated_at="2026-09-29T00:00:00Z", version="0.14.6")
    rendered = render_status_html(snap, [snap])
    assert "0/0" in rendered
    assert "1 skipped" in rendered
    assert "No live requests were attempted" in rendered


@pytest.mark.parametrize("evidence_status,complete,evidence_fresh,timeout", [
    ("unchanged", True, False, 2),
    ("review_required", True, False, 2),
    ("check_failed", True, False, 2),
    ("unchanged", False, False, 2),
    ("review_required", True, True, 2),
    ("models_changed", True, True, 2),
    ("unchanged", True, False, float("inf")),
    ("unchanged", True, False, float("nan")),
])
def test_public_refresh_uses_isolated_paths_and_real_evidence_admission(
    tmp_path: Path, monkeypatch, evidence_status: str, complete: bool, evidence_fresh: bool, timeout: float,
) -> None:
    import copy
    import time
    from datetime import UTC, datetime, timedelta

    from freellmpool import discovery, managed, provider_registry, status_page
    from freellmpool.client import HTTPResult
    from freellmpool.managed import ManagedPool

    registry = provider_registry.load_registry()
    anonymous = copy.deepcopy(registry["llm7"])
    now = datetime.now(UTC)
    for source in anonymous["evidence"]:
        source["checked_at"] = (now - timedelta(days=1 if evidence_fresh else 8)).isoformat()
        source["expires_at"] = (now + timedelta(days=1 if evidence_fresh else -1)).isoformat()
    events = []
    paths = []
    environments = []
    calls = []
    private_config = tmp_path / "private.toml"
    private_config.write_text('[keys]\nLLM7_API_KEY="secret-from-config"\n')
    private_quota = tmp_path / "private-quota.json"
    private_quota.write_text("private sentinel")
    monkeypatch.setenv("LLM7_API_KEY", "secret-from-env")
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(private_config))
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(private_config))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(private_quota))
    monkeypatch.setenv("FREELLMPOOL_LEGACY_ROUTER", "1")
    monkeypatch.setenv("FREELLMPOOL_DISCOVERY_BUDGET_SECONDS", "inf")
    monkeypatch.setenv("FREELLMPOOL_QUOTA_FLUSH_EVERY", "999999")
    monkeypatch.setenv("FREELLMPOOL_QUOTA_FLUSH_INTERVAL", "inf")
    monkeypatch.setenv("FREELLMPOOL_STATS_FLUSH_EVERY", "999999")
    monkeypatch.setenv("FREELLMPOOL_STATS_FLUSH_INTERVAL", "inf")

    def load_registry(env=None, *, renew_evidence=True):
        result = {"llm7": copy.deepcopy(anonymous), "groq": copy.deepcopy(registry["groq"])}
        if env is not None and renew_evidence:
            provider_registry._apply_renewals(result, env)
        return result

    monkeypatch.setattr(provider_registry, "load_registry", load_registry)
    monkeypatch.setattr(discovery, "load_registry", load_registry)

    def refresh_catalog(env, provider_ids, *, public_only, path, deadline):
        events.append("catalog")
        environments.append(dict(env))
        paths.append(Path(path))
        assert provider_ids == ["llm7"] and public_only is True
        assert 0 < deadline - time.monotonic() <= 40
        assert Path(path) == discovery.default_discovery_path(env)
        snapshot = {"schema": 1, "generation": "test", "updated_at": now.isoformat(),
                    "providers": {"llm7": {"status": "ok", "complete": complete,
                    "checked_at": now.isoformat(), "models": [{"id": "codestral-latest",
                    "modalities": ["chat"], "pricing": {"input": 0, "output": 0}}]}}}
        Path(path).write_text(json.dumps(snapshot))
        return snapshot

    def check_sources(provider_ids, *, registry, time_budget_seconds):
        events.append("evidence")
        assert provider_ids == ["llm7"]
        assert time_budget_seconds == 120
        return {"checked_at": now.isoformat(), "sources": [
            {"url": source["url"], "status": "error" if evidence_status == "check_failed" else "ok",
             "checked_at": now.isoformat(),
             "sha256": source["source_hash"]["sha256"]
             if evidence_status == "unchanged" or (evidence_status == "models_changed" and source["id"] != "models")
             else "0" * 64}
            for source in anonymous["evidence"]]}

    original_init = ManagedPool.__init__
    original_flush = ManagedPool.flush

    def init(pool, *args, **kwargs):
        events.append("pool")
        assert events[:2] == ["catalog", "evidence"]
        assert kwargs["providers"] == [] and kwargs["accounts"] == {}
        original_init(pool, *args, **kwargs)
        paths.extend([pool.ledger.path, pool.quota.path, pool._stats_store.path,
                      pool.conformance.path, pool.route_health.path])
        assert "LLM7_API_KEY" not in pool.env
        assert pool._stats_store.flush_every == 1 and pool._stats_store.flush_interval == 1
        assert pool.quota.flush_every == 1 and pool.quota.flush_interval == 1
        assert Path(pool.env["FREELLMPOOL_EVIDENCE_FILE"]).is_file()
        assert Path(pool.env["FREELLMPOOL_CONFIG_FILE"]) != private_config

        def post(url, headers, body, timeout):
            calls.append(body)
            assert "Authorization" not in headers
            assert body["max_tokens"] == 512  # existing managed probe reservation floor
            assert 0 < timeout <= 30
            return HTTPResult(200, {"choices": [{"message": {"content": "OK"}}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, "")

        pool._post = post

    def flush(pool):
        events.append("flush")
        assert all(path.parent.exists() for path in paths)
        original_flush(pool)

    monkeypatch.setattr(discovery, "refresh_catalog", refresh_catalog)
    monkeypatch.setattr(discovery, "check_public_sources", check_sources)
    monkeypatch.setattr(ManagedPool, "__init__", init)
    monkeypatch.setattr(ManagedPool, "flush", flush)
    monkeypatch.setattr(managed, "StatsStore",
                        lambda: pytest.fail("explicit stats store must prevent reading global private stats"))
    rows = status_page.collect_public_rows(timeout=timeout)
    assert events == ["catalog", "evidence", "pool", "flush"]
    assert len(rows) == 1
    expected_probe = evidence_status == "unchanged" and complete
    assert rows[0].status == ("ok" if expected_probe else "skipped")
    assert bool(calls) == expected_probe
    assert all(not path.exists() for path in paths)
    assert all("LLM7_API_KEY" not in env for env in environments)
    assert private_quota.read_text() == "private sentinel"
    assert private_config.read_text().endswith('"secret-from-config"\n')


@pytest.mark.parametrize("providers", [["unknown"], ["groq"], [""]])
def test_public_refresh_invalid_filter_rejected_before_network_or_temp_state(monkeypatch, providers) -> None:
    from freellmpool import discovery, status_page

    def forbidden(*args, **kwargs):
        pytest.fail("invalid filter must not start public refresh or create state")

    monkeypatch.setattr(discovery, "refresh_catalog", forbidden)
    monkeypatch.setattr(discovery, "refresh_evidence", forbidden)
    monkeypatch.setattr(status_page.tempfile, "TemporaryDirectory", forbidden)
    with pytest.raises(ValueError, match="provider"):
        status_page.collect_public_rows(providers=providers)


def test_cli_rows_file_bypasses_public_refresh_and_filter(tmp_path: Path, monkeypatch, capsys) -> None:
    from freellmpool import status_page
    from freellmpool.cli import main

    monkeypatch.setattr(status_page, "collect_public_rows", lambda **kwargs: pytest.fail("unexpected refresh"))
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps([{"target": "llm7", "status": "skipped", "note": "expired"}]))
    assert main(["status-page", "publish", "--refresh-public", "--providers", "unknown",
                 "--rows-file", str(rows_file), "--docs-dir", str(tmp_path / "docs")]) == 0
    output = capsys.readouterr().out
    assert "0/0 ok" in output and "1 skipped" in output


def test_cli_public_filter_error_does_not_publish(tmp_path: Path) -> None:
    from freellmpool.cli import main

    docs = tmp_path / "docs"
    assert main(["status-page", "publish", "--refresh-public", "--providers", "groq",
                 "--docs-dir", str(docs)]) == 2
    assert not docs.exists()


def test_cli_public_refresh_dispatches_options_and_prints_attempts(tmp_path: Path, monkeypatch, capsys) -> None:
    from freellmpool import status_page
    from freellmpool.cli import main

    captured = []

    def collect(**kwargs):
        captured.append(kwargs)
        return [*ROWS, HealthRow("llm7", "skipped", None, "expired")]

    monkeypatch.setattr(status_page, "collect_public_rows", collect)
    assert main(["status-page", "publish", "--refresh-public", "--providers", "llm7",
                 "--model", "codestral-latest", "--timeout", "2", "--docs-dir", str(tmp_path)]) == 0
    assert captured == [{"model": "codestral-latest", "providers": ["llm7"], "timeout": 2}]
    assert "1/2 ok among attempted probes, 1 skipped" in capsys.readouterr().out


def test_public_refresh_no_anonymous_providers_does_not_create_state(monkeypatch) -> None:
    from freellmpool import provider_registry, status_page

    monkeypatch.setattr(provider_registry, "load_registry", lambda: {})
    monkeypatch.setattr(status_page.tempfile, "TemporaryDirectory",
                        lambda **kwargs: pytest.fail("empty selection must not create state"))
    assert status_page.collect_public_rows() == []


def test_public_refresh_normalizes_filter_and_flushes_before_cleanup_on_error(monkeypatch) -> None:
    from freellmpool import discovery, status_page
    from freellmpool.managed import ManagedPool

    captured = []

    def refresh_catalog(env, providers, **kwargs):
        assert providers == ["llm7"]
        captured.append(Path(env["FREELLMPOOL_ALLOWANCE_FILE"]).parent)
        return {}

    def refresh_evidence(env, providers, **kwargs):
        assert providers == ["llm7"]
        return {}

    def collect(pool, **kwargs):
        assert [row["id"] for row in pool.snapshot().providers] == ["llm7"]
        raise RuntimeError("probe interruption")

    original_flush = ManagedPool.flush
    flushed = []

    def flush(pool):
        assert captured[0].exists()
        flushed.append(True)
        original_flush(pool)

    monkeypatch.setattr(discovery, "refresh_catalog", refresh_catalog)
    monkeypatch.setattr(discovery, "refresh_evidence", refresh_evidence)
    monkeypatch.setattr(status_page, "collect_live_rows", collect)
    monkeypatch.setattr(ManagedPool, "flush", flush)
    with pytest.raises(RuntimeError, match="probe interruption"):
        status_page.collect_public_rows(providers=[" LLM7 ", "llm7"])
    assert flushed == [True]
    assert not captured[0].exists()


def test_public_ovh_health_probe_normalizes_speech_first_catalog_before_model_selection(monkeypatch) -> None:
    from datetime import UTC, datetime

    from freellmpool import discovery, provider_registry, status_page
    from freellmpool.client import HTTPResult
    from freellmpool.managed import ManagedPool

    now = datetime.now(UTC).isoformat()
    calls = []
    registry = provider_registry.load_registry()

    def refresh_catalog(env, providers, *, path, **kwargs):
        assert providers == ["ovh"]
        models = discovery.normalize_models("ovh", {"data": [
            {"id": "nvr-tts-es-es", "owned_by": "NVIDIA Riva", "context_length": 0,
             "max_completion_tokens": 0, "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "Qwen3.6-27B", "context_length": 262144, "max_completion_tokens": 262144,
             "pricing": {"prompt": "0.00000047", "completion": "0.00000319"}},
        ]})
        snapshot = {"schema": 1, "generation": "ovh-modality-test", "updated_at": now,
                    "providers": {"ovh": {"status": "ok", "complete": True,
                                          "checked_at": now, "models": models}}}
        Path(path).write_text(json.dumps(snapshot))
        return snapshot

    def check_sources(providers, **kwargs):
        assert providers == ["ovh"]
        return {"checked_at": now, "sources": [
            {"url": source["url"], "status": "ok", "checked_at": now,
             "sha256": source["source_hash"]["sha256"]}
            for source in registry["ovh"]["evidence"]]}

    original_init = ManagedPool.__init__

    def init(pool, *args, **kwargs):
        original_init(pool, *args, **kwargs)

        def post(url, headers, body, timeout):
            calls.append(body["model"])
            assert "Authorization" not in headers
            if body["model"].startswith("nvr-tts-"):
                return HTTPResult(404, {"error": {"message": "speech endpoint is not chat"}}, "")
            return HTTPResult(200, {"choices": [{"message": {"content": "OK"}}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, "")

        pool._post = post

    monkeypatch.setattr(discovery, "refresh_catalog", refresh_catalog)
    monkeypatch.setattr(discovery, "check_public_sources", check_sources)
    monkeypatch.setattr(ManagedPool, "__init__", init)
    rows = status_page.collect_public_rows(providers=["ovh"], timeout=2)
    assert len(rows) == 1 and rows[0].status == "ok"
    assert rows[0].target == "ovh/Qwen3.6-27B"
    assert calls == ["Qwen3.6-27B"]
