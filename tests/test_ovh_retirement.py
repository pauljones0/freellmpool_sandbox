"""Owner-directed OVH retirement cannot be reversed by stale local state."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from freellmpool import account_observations, discovery, provider_registry
from freellmpool.config import Model, Provider, load_catalog, load_embedders
from freellmpool.errors import AllProvidersExhausted
from freellmpool.managed import ManagedPool
from freellmpool.policy_updates import IncompatiblePolicy, validate_document
from freellmpool.quota import QuotaStore


def test_packaged_ovh_support_is_removed_from_both_modalities_and_metadata():
    registry = provider_registry.load_registry()
    assert "ovh" not in registry
    assert "ovh" not in {provider.id for provider in load_catalog()}
    assert "ovh" not in {provider.id for provider in load_embedders()}
    policies = json.loads(Path("src/freellmpool/data_policies.json").read_text())
    assert "ovh" not in policies["policies"]
    assert "ovh" not in account_observations._UNSUPPORTED


def test_owner_retirement_tombstone_explains_observed_limit_without_global_claim():
    packaged = json.loads(provider_registry.REGISTRY_PATH.read_text())
    row = next((row for row in packaged["tombstones"] if row["id"] == "ovh"), None)
    assert row is not None
    assert "owner" in row["reason"].lower()
    assert "429" in row["reason"]
    assert "withdraw" not in row["reason"].lower()
    assert "dead" not in row["reason"].lower()


def test_ovh_reintroduction_is_rejected_by_both_registry_and_policy_channel(tmp_path, monkeypatch):
    packaged = json.loads(provider_registry.REGISTRY_PATH.read_text())
    stale = copy.deepcopy(packaged)
    stale["providers"] = [row for row in stale["providers"] if row["id"] != "ovh"]
    stale["providers"].append({**copy.deepcopy(packaged["providers"][0]), "id": "ovh"})
    path = tmp_path / "stale-registry.json"
    path.write_text(json.dumps(stale))
    monkeypatch.setattr(provider_registry, "REGISTRY_PATH", path)
    with pytest.raises(ValueError, match="removed provider"):
        provider_registry.load_registry()
    with pytest.raises(IncompatiblePolicy, match="identities"):
        validate_document(stale, packaged)


@pytest.mark.parametrize("operation", ["chat", "embedding"])
def test_stale_ovh_discovery_and_operator_catalog_cannot_make_transport_calls(tmp_path, operation):
    transport = []

    def forbidden(*args, **kwargs):
        transport.append(args)
        pytest.fail("retired provider must not reach transport")

    env = {"XDG_STATE_HOME": str(tmp_path), "FREELLMPOOL_CONFIG_FILE": str(tmp_path / "config.toml"),
           "FREELLMPOOL_CONFIG": str(tmp_path / "providers.toml"),
           "FREELLMPOOL_ALLOWANCE_FILE": str(tmp_path / "allowances.sqlite3"),
           "FREELLMPOOL_CONFORMANCE_FILE": str(tmp_path / "conformance.json"),
           "FREELLMPOOL_HEALTH_FILE": str(tmp_path / "health.json")}
    env_path = Path(env["FREELLMPOOL_CONFIG"])
    env_path.write_text('[[provider]]\nid="ovh"\nlabel="retired"\nadapter="openai"\n'
                        'base_url="https://example.invalid/v1"\n'
                        'models=[{name="stale/model",rpd=0}]\n')
    provider = Provider("ovh", "retired", "openai", "https://example.invalid/v1", (Model("stale/model"),), auth="none")
    snapshot = {"schema": 1, "providers": {"ovh": {"status": "ok", "complete": True,
                "checked_at": datetime.now(UTC).isoformat(), "models": [{"id": "stale/model",
                "modalities": [operation], "pricing": {"input": "0", "output": "0"}}]}}}
    pool = ManagedPool(providers=[provider], discovery=snapshot, accounts={}, env=env, post=forbidden,
                       quota=QuotaStore(path=tmp_path / "quota.json", flush_every=1, flush_interval=1),
                       stats_store=None)
    try:
        assert "ovh" not in {row["id"] for row in pool.snapshot().providers}
        assert "ovh" not in {route.provider.id for route in pool.snapshot().routes}
        with pytest.raises(AllProvidersExhausted):
            if operation == "chat":
                pool.chat([{"role": "user", "content": "hello"}], providers=["ovh"], model="stale/model", timeout=0.1)
            else:
                pool.embed("hello", providers=["ovh"], model="stale/model", timeout=0.1)
        assert transport == []
    finally:
        pool.flush()


def test_retired_discovery_selection_rejects_ovh_before_any_network(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_client", lambda: pytest.fail("retired discovery must not request"))
    with pytest.raises(ValueError, match="absent or removed"):
        discovery.refresh_catalog({}, ["ovh"], public_only=True, path=tmp_path / "discovery.json")
    assert not (tmp_path / "discovery.json").exists()
