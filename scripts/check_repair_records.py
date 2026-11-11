"""Check canonical repair records; --sync renders CSV/table from backlog.json."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs" / "repairs"


def cell(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_table(rows):
    lines = [
        "# Complete repair backlog", "",
        "All 165 original records are retained. Evidence classification describes the reviewed baseline; workflow describes repair progress. Compound records can remain partially open after a batch ships. Full findings, acceptance criteria and provenance are in [backlog.json](backlog.json) and [backlog.csv](backlog.csv).",
        "", "| ID | Priority | Area | Finding | Workflow |", "|---|---|---|---|---|",
    ]
    fields = ("id", "priority", "area", "finding", "workflow")
    lines.extend("| " + " | ".join(cell(r[k]) for k in fields) + " |" for r in rows)
    return "\n".join(lines) + "\n"


def render_csv(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def check(sync=False):
    rows = json.loads((BASE / "backlog.json").read_text())
    baseline = json.loads((BASE / "baseline-ids.json").read_text())
    incidents = json.loads((BASE / "incidents.json").read_text())
    ids = [r["id"] for r in rows]
    errors = []
    if len(ids) != len(set(ids)):
        errors.append("duplicate backlog IDs")
    if baseline["count"] != 165 or len(set(baseline["ids"])) != 165:
        errors.append("original 165-record baseline changed")
    missing = set(baseline["ids"]) - set(ids)
    if missing:
        errors.append(f"original records removed: {sorted(missing)}")
    incident_ids = [r["id"] for r in incidents]
    if len(incident_ids) != len(set(incident_ids)) or set(incident_ids) & set(ids):
        errors.append("incident IDs must be unique and distinct from backlog IDs")

    # The owner-approved sequence is additive to the canonical findings.
    plan = json.loads((BASE / "execution-plan.json").read_text())
    steps = plan["steps"]
    expected_steps = [f"SEQ-{i:02}" for i in range(1, 12)]
    if [s["id"] for s in steps] != expected_steps:
        errors.append("execution sequence must retain SEQ-01 through SEQ-11 in order")
    if plan.get("active_step") not in expected_steps:
        errors.append("execution plan active step is invalid")
    for i, step in enumerate(steps, 1):
        if step.get("order") != i or step.get("depends_on") != ([] if i == 1 else [expected_steps[i-2]]):
            errors.append(f"{step['id']}: sequence/dependency mismatch")
        if not step.get("finding_ids") or set(step["finding_ids"]) - set(ids):
            errors.append(f"{step['id']}: unknown finding reference")
        evidence = step.get("evidence")
        if evidence and not (ROOT / evidence).is_file():
            errors.append(f"{step['id']}: missing batch evidence")

    releases = "\n".join(p.read_text() for p in (BASE / "releases").glob("*.md"))
    for r in rows + incidents:
        for key in ("id", "finding", "workflow"):
            if not r.get(key):
                errors.append(f"record missing {key}: {r.get('id')}")
        if r in rows and not r.get("acceptance"):
            errors.append(f"{r['id']}: missing acceptance criteria")
        evidence = r.get("current_evidence", r.get("evidence", ""))
        if evidence.startswith(("docs/", "trading/", "web/")):
            target = evidence.split("#")[0].split(":")[0]
            if not (ROOT / target).is_file():
                errors.append(f"{r['id']}: missing evidence file {target}")
        if "DEPLOYED" in r["workflow"]:
            for key in ("fix_commit", "deployment_revision"):
                sha = r.get(key, "")
                if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha not in releases:
                    errors.append(f"{r['id']}: {key} needs a full SHA in release evidence")
    if re.search(r"/Users/|/private/tmp/|[A-Z]:\\\\Users\\\\", json.dumps(rows + incidents)):
        errors.append("canonical records contain machine-specific evidence paths")

    if sync and not errors:
        (BASE / "BACKLOG.md").write_text(render_table(rows))
        with (BASE / "backlog.csv").open("w", newline="") as f:
            f.write(render_csv(rows))
    if (BASE / "BACKLOG.md").read_text() != render_table(rows):
        errors.append("BACKLOG.md differs from JSON; run with --sync")
    with (BASE / "backlog.csv").open(newline="") as f:
        actual = list(csv.DictReader(f))
    expected = list(csv.DictReader(io.StringIO(render_csv(rows))))
    if actual != expected:
        errors.append("backlog.csv differs from JSON; run with --sync")

    # Current navigation must resolve inside the checkout. Historical source
    # prose is retained as evidence, not treated as current navigation.
    navigation = [ROOT / "README.md", BASE / "README.md", BASE / "GOALS.md",
                  BASE / "WORKFLOW.md", BASE / "BACKLOG.md", BASE / "EXECUTION_PLAN.md"]
    navigation.extend((BASE / "releases").glob("*.md"))
    for page in navigation:
        for target in re.findall(r"\]\(([^)]+)\)", page.read_text()):
            if target.startswith(("https://", "http://", "#")):
                continue
            if not (page.parent / target.split("#")[0]).exists():
                errors.append(f"{page.relative_to(ROOT)}: broken link {target}")
    if errors:
        raise SystemExit("Repair record check failed:\n- " + "\n- ".join(errors))
    print(f"Repair records valid: {len(rows)} backlog records, {len(incidents)} separate incident(s); original 165 IDs retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync", action="store_true", help="render CSV and table from JSON before checking")
    check(parser.parse_args().sync)
