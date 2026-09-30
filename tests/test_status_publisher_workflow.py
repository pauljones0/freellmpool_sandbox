"""Exercise the scheduled publication shell against a real disposable remote."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from freellmpool.healthcheck import HealthRow
from freellmpool.status_page import publish_status

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/status-publisher.yml"
ARTIFACTS = {"docs/free-tier-status.html", "docs/status-history.json", "docs/sitemap.xml"}


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit_script() -> str:
    body = WORKFLOW.read_text().split("      - name: Commit refreshed snapshot\n", 1)[1]
    return textwrap.dedent(body.split("        run: |\n", 1)[1])


def test_workflow_refreshes_anonymous_public_state_before_publication() -> None:
    assert "freellmpool status-page publish --refresh-public" in WORKFLOW.read_text()


@pytest.mark.parametrize("change", ["snapshot", "sitemap-only", "none"])
def test_workflow_commits_every_generated_artifact_and_rebases_cleanly(tmp_path: Path, change: str) -> None:
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    competitor = tmp_path / "competitor"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "clone", str(remote), str(local))
    for path in (local,):
        git(path, "config", "user.name", "test publisher")
        git(path, "config", "user.email", "publisher@example.invalid")
    docs = local / "docs"
    docs.mkdir()
    (docs / "sitemap.xml").write_text(
        '<urlset><url><loc>https://pauljones0.github.io/freellmpool_sandbox/free-tier-status.html</loc>'
        "<lastmod>2026-09-28</lastmod></url></urlset>\n")
    rows = [HealthRow("llm7/codestral-latest", "ok", 1, "responded")]
    publish_status(docs, rows, generated_at="2026-09-28T00:00:00Z", version="0.14.6")
    git(local, "add", "docs")
    git(local, "commit", "-m", "initial publication")
    git(local, "push", "origin", "main")
    git(tmp_path, "clone", str(remote), str(competitor))
    git(competitor, "config", "user.name", "other maintainer")
    git(competitor, "config", "user.email", "maintainer@example.invalid")
    (competitor / "README.md").write_text("concurrent independent update\n")
    git(competitor, "add", "README.md")
    git(competitor, "commit", "-m", "concurrent update")
    git(competitor, "push", "origin", "main")
    before = git(local, "rev-parse", "HEAD")
    if change == "snapshot":
        publish_status(docs, rows, generated_at="2026-09-29T00:00:00Z", version="0.14.6")
    elif change == "sitemap-only":
        (docs / "sitemap.xml").write_text((docs / "sitemap.xml").read_text().replace("2026-09-28", "2026-09-29"))
    result = subprocess.run(["bash", "-c", commit_script()], cwd=local,
                            env={**os.environ, "GITHUB_REF_NAME": "main"},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(local, "status", "--porcelain") == ""
    if change == "none":
        assert git(local, "rev-parse", "HEAD") == before
        assert "No status changes to commit." in result.stdout
    else:
        assert git(local, "rev-parse", "HEAD") == git(remote, "rev-parse", "main")
        assert (local / "README.md").read_text() == "concurrent independent update\n"
        changed = set(git(local, "show", "--pretty=", "--name-only", "HEAD").splitlines())
        assert changed == (ARTIFACTS if change == "snapshot" else {"docs/sitemap.xml"})
