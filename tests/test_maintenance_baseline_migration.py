"""Trusted historical artifacts retain review work without reviving retired providers."""

from __future__ import annotations

import base64
import copy
import io
import json
import zipfile

import pytest

from freellmpool import maintenance
from freellmpool.provider_registry import REGISTRY_PATH, load_registry
from scripts import fetch_maintenance_baseline as fetch

REVISION = "a" * 40
NOW = "2026-09-25T12:00:00+00:00"
REPOSITORY = "owner/project"


def historical_document():
    document = json.loads(REGISTRY_PATH.read_text())
    document["tombstones"] = []
    for provider in ("aion", "modelscope"):
        document["providers"].append({"id": provider, "evidence": [
            {"id": "terms", "url": f"https://{provider}.example/terms"}],
            "discovery": {"url": f"https://{provider}.example/models"}})
    return document


def empty_baseline():
    return {"schema": 1, "visibility": "public", "source_revision": REVISION,
            "checked_at": NOW, "providers": {}, "pending_changes": [],
            "incidents": [], "proposals": []}


def proposal(provider, url):
    return {"provider": provider, "rule_id": "tokens", "model_id": "model",
            "metric": "tokens", "window_seconds": 60, "old_capacity": 10,
            "new_capacity": 20, "source_url": url, "source_sha256": "b" * 64}


def archive(document):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(fetch.BASELINE_FILE, json.dumps(document))
    return output.getvalue()


def test_retirement_prunes_all_collections_but_preserves_active_review_and_age():
    historical = historical_document()
    document = empty_baseline()
    active = maintenance._finding("groq", "model_added", "pending/model", before=False, after=True)
    document["pending_changes"].append(active)
    document["providers"]["groq"] = {"checked_at": NOW, "models": {"old/model": {"input": "0"}}}
    for provider in ("aion", "modelscope"):
        url = f"https://{provider}.example/terms"
        document["providers"][provider] = {"checked_at": NOW, "models": {"model": {"input": "0"}}}
        document["pending_changes"].append(maintenance._finding(provider, "limit_changed", "tokens/model",
                                                              before=10, after=20, source_url=url))
        document["incidents"].append(maintenance._finding(provider, "source_check_failed", "terms", source_url=url))
        document["proposals"].append(proposal(provider, url))
    original = copy.deepcopy(document)
    result = maintenance.migrate_public_baseline(document, historical)
    assert result == {**empty_baseline(), "providers": {"groq": original["providers"]["groq"]},
                      "pending_changes": [active]}
    assert document == original
    assert maintenance.validate_public_baseline(result) == result


def test_unchanged_active_records_are_preserved():
    document = empty_baseline()
    url = load_registry()["groq"]["evidence"][0]["url"]
    document["pending_changes"] = [maintenance._finding("groq", "limit_changed", "tokens/model",
                                                       before=10, after=20, source_url=url)]
    document["proposals"] = [proposal("groq", url)]
    assert maintenance.migrate_public_baseline(document, historical_document()) == document


@pytest.mark.parametrize("collection", ["providers", "pending_changes", "incidents", "proposals"])
def test_unknown_retirement_is_rejected_in_each_collection(collection):
    historical = historical_document()
    historical["providers"].append({"id": "unreviewed", "evidence": [], "discovery": {}})
    document = empty_baseline()
    if collection == "providers":
        document[collection]["unreviewed"] = {"checked_at": NOW, "models": {}}
    elif collection == "proposals":
        historical["providers"][-1]["evidence"] = [{"id": "terms", "url": "https://unknown.example/terms"}]
        document[collection].append(proposal("unreviewed", "https://unknown.example/terms"))
    else:
        code = "model_added" if collection == "pending_changes" else "catalog_failed"
        document[collection].append(maintenance._finding("unreviewed", code, "model"))
    with pytest.raises(ValueError, match="removed provider"):
        maintenance.migrate_public_baseline(document, historical)


@pytest.mark.parametrize("collection", ["providers", "pending_changes", "incidents", "proposals"])
def test_malformed_retired_records_are_validated_before_pruning(collection):
    document = empty_baseline()
    if collection == "providers":
        document[collection]["aion"] = {"checked_at": NOW, "models": {"../invalid": {}}}
    elif collection == "proposals":
        document[collection].append({**proposal("aion", "https://aion.example/terms"), "source_sha256": "bad"})
    else:
        code = "model_added" if collection == "pending_changes" else "catalog_failed"
        document[collection].append({**maintenance._finding("aion", code, "model"), "fingerprint": "bad"})
    with pytest.raises(ValueError):
        maintenance.migrate_public_baseline(document, historical_document())


def changed_source():
    historical = historical_document()
    groq = next(provider for provider in historical["providers"] if provider["id"] == "groq")
    source = groq["evidence"][0]
    source["url"] = "https://old.example/terms"
    return historical, source


def test_moved_evidence_url_is_omitted_without_rewriting_hashes_or_identity():
    historical, source = changed_source()
    document = empty_baseline()
    finding = maintenance._finding("groq", "source_changed", source["id"],
                                   before="b" * 64, after="c" * 64, source_url=source["url"])
    document["pending_changes"] = [finding]
    result = maintenance.migrate_public_baseline(document, historical)
    assert result["pending_changes"] == [{key: value for key, value in finding.items() if key != "source_url"}]
    assert result["checked_at"] == NOW
    assert result["source_revision"] == REVISION
    assert document["pending_changes"][0] == finding


def test_changed_proposal_url_cannot_relabel_original_source_hash():
    historical, source = changed_source()
    document = empty_baseline()
    document["proposals"] = [proposal("groq", source["url"])]
    with pytest.raises(ValueError, match="proposal source"):
        maintenance.migrate_public_baseline(document, historical)


@pytest.mark.parametrize("code,subject", [("source_changed", "missing"), ("limit_changed", "tokens/model")])
def test_unexplained_obsolete_url_is_rejected(code, subject):
    historical, source = changed_source()
    document = empty_baseline()
    document["pending_changes"] = [maintenance._finding("groq", code, subject,
                                                       before=10, after=20, source_url=source["url"])]
    with pytest.raises(ValueError, match="source identity"):
        maintenance.migrate_public_baseline(document, historical)


@pytest.mark.parametrize("mutation", ["schema", "duplicate_provider", "duplicate_evidence", "unsafe_url", "unsafe_id"])
def test_historical_allowlist_schema_is_narrow_and_rejects_malformed_content(mutation):
    historical = historical_document()
    if mutation == "schema":
        historical["schema"] = True
    elif mutation == "duplicate_provider":
        historical["providers"].append(copy.deepcopy(historical["providers"][0]))
    elif mutation == "duplicate_evidence":
        historical["providers"][0]["evidence"].append(copy.deepcopy(historical["providers"][0]["evidence"][0]))
    elif mutation == "unsafe_id":
        historical["providers"][0]["id"] = "../provider"
    else:
        historical["providers"][0]["evidence"][0]["url"] = "https://user:secret@example.com/terms"
    with pytest.raises(ValueError):
        maintenance.migrate_public_baseline(empty_baseline(), historical)


class ArtifactAPI(fetch.GitHubAPI):
    def __init__(self, document=None, historical=None):
        super().__init__(REPOSITORY, "test-token")
        self.document = document or empty_baseline()
        self.historical = historical or historical_document()
        self.calls = []

    def json(self, method, path, payload=None):
        self.calls.append(path)
        prefix = f"/repos/{self.repository}"
        if path == prefix:
            return {"default_branch": "main"}
        if "/workflows/" in path:
            return {"workflow_runs": [{"id": 3, "event": "schedule", "conclusion": "success",
                    "head_branch": "main", "path": ".github/workflows/" + fetch.WORKFLOW_FILE,
                    "head_sha": REVISION, "head_repository": {"full_name": self.repository}}]}
        if "/artifacts?" in path:
            return {"artifacts": [{"id": 9, "name": fetch.ARTIFACT_NAME,
                    "size_in_bytes": 1000, "expired": False,
                    "workflow_run": {"id": 3, "head_sha": REVISION}}]}
        assert path == prefix + "/contents/src/freellmpool/provider_registry.json?ref=" + REVISION
        content = json.dumps(self.historical).encode()
        return {"type": "file", "path": "src/freellmpool/provider_registry.json", "encoding": "base64",
                "size": len(content), "content": base64.b64encode(content).decode()}

    def download_artifact(self, artifact_id):
        assert artifact_id == 9
        return archive(self.document)


def test_default_fetch_uses_commit_pinned_registry_and_migrates_retired_provider():
    document = empty_baseline()
    document["providers"]["aion"] = {"checked_at": NOW, "models": {}}
    api = ArtifactAPI(document)
    assert fetch.fetch_baseline(api, current_run_id=10) == empty_baseline()
    assert api.calls[-1].endswith("provider_registry.json?ref=" + REVISION)


def test_raw_revision_mismatch_is_rejected_before_historical_registry_fetch():
    api = ArtifactAPI({**empty_baseline(), "source_revision": "b" * 40})
    with pytest.raises(ValueError, match="trusted workflow revision"):
        fetch.fetch_baseline(api, current_run_id=10)
    assert not any("/contents/" in path for path in api.calls)


def test_injected_validator_keeps_existing_fetch_seam_without_registry_request():
    api = ArtifactAPI()
    assert fetch.fetch_baseline(api, current_run_id=10, validator=lambda value: value) == empty_baseline()
    assert not any("/contents/" in path for path in api.calls)


@pytest.mark.parametrize("field,value", [("encoding", "utf8"), ("type", "symlink"),
    ("path", "elsewhere.json"), ("size", True), ("size", fetch.MAX_BYTES + 1),
    ("size", 1), ("content", "not base64!")])
def test_historical_contents_response_is_bounded_and_exact(field, value):
    api = ArtifactAPI()
    original = api.json

    def response(method, path, payload=None):
        result = original(method, path, payload)
        if "/contents/" in path:
            result[field] = value
        return result

    api.json = response
    with pytest.raises(ValueError):
        fetch.fetch_baseline(api, current_run_id=10)
