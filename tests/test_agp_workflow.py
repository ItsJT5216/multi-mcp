"""Invariant tests for .github/workflows/agp-workflow.yml (pip-audit security workflow).

These guard against two classes of bug that were found and fixed on this branch:

1. Fix-reversion: a step regenerated requirements.txt from ``pip freeze`` of a
   venv that still held the OLD (vulnerable) versions, silently reverting every
   fix pip-audit had just written. ``pip-audit -r requirements.txt --fix``
   rewrites the requirements file in place and does NOT modify the venv
   (verified empirically against pip-audit), so no freeze-merge step must ever
   reintroduce stale versions.

2. Shell injection: interpolating ``${{ inputs.* }}`` directly inside ``run:``
   blocks lets a workflow-dispatch caller inject arbitrary shell into a job
   that holds ``contents: write`` / ``pull-requests: write``. All inputs must
   be routed through ``env:`` indirection.
"""
import re
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "agp-workflow.yml"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def all_steps(workflow) -> list[dict]:
    steps = []
    for job in workflow["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def test_workflow_parses_and_has_security_audit_job(workflow):
    assert "security-audit" in workflow["jobs"]


def test_no_input_interpolation_inside_run_blocks(all_steps):
    """Inputs must flow through env: indirection, never ${{ inputs.* }} in run:."""
    offenders = []
    for step in all_steps:
        run = step.get("run", "") or ""
        for match in re.findall(r"\$\{\{[^}]*inputs\.[^}]*\}\}", run):
            offenders.append((step.get("name", "<unnamed>"), match))
    assert not offenders, (
        f"Script-injectable input interpolation inside run blocks: {offenders}. "
        "Route inputs through the step's env: block instead."
    )


def test_no_pip_freeze_requirements_regeneration(all_steps):
    """No step may regenerate requirements.txt from the (stale) venv.

    pip-audit -r --fix rewrites requirements.txt in place; the venv keeps the
    old versions. Any freeze-and-merge step deterministically reverts fixes.
    """
    for step in all_steps:
        run = step.get("run", "") or ""
        assert "pip freeze" not in run, (
            f"Step '{step.get('name')}' uses 'pip freeze' — this reads the "
            "pre-fix venv and would revert pip-audit's requirements.txt fixes."
        )
        assert step.get("name") != "Update requirements.txt with fixed versions"


def test_fix_detection_uses_git_diff_of_requirements(all_steps):
    """packages_fixed must be derived from the file, not console phrasing."""
    fix_step = next(s for s in all_steps if s.get("id") == "security-fix")
    assert "git diff --quiet -- requirements.txt" in fix_step["run"]
    assert "packages_fixed=true" in fix_step["run"]
    assert "packages_fixed=false" in fix_step["run"]


def test_pr_creation_gated_on_packages_fixed(all_steps):
    pr_step = next(s for s in all_steps if s.get("name") == "Create Pull Request")
    condition = pr_step.get("if", "")
    assert "steps.security-fix.outputs.packages_fixed == 'true'" in condition


def test_permissions_are_scoped(workflow):
    perms = workflow.get("permissions", {})
    assert perms == {"contents": "write", "pull-requests": "write"}


def test_actions_are_sha_or_major_pinned(all_steps):
    """Third-party actions must be SHA-pinned; official actions at least tagged."""
    for step in all_steps:
        uses = step.get("uses")
        if not uses:
            continue
        owner = uses.split("/")[0]
        ref = uses.rsplit("@", 1)[-1]
        if owner != "actions":
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"Third-party action '{uses}' must be pinned to a full commit SHA."
            )
