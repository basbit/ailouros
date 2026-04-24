from __future__ import annotations


def build_ring_pass_defect_context(open_defects: list[dict], pass_number: int) -> str:
    lines = [f"\n\n## Ring pass {pass_number} — unresolved defects from previous run\n"]
    for defect in open_defects[:10]:
        severity = str(defect.get("severity") or "?")
        description = str(defect.get("description") or "?")
        location = str(defect.get("location") or "")
        lines.append(f"- [{severity}] {description}" + (f" ({location})" if location else ""))
    lines.append(
        "\nAll above defects MUST be resolved in this pass before the pipeline can "
        "be considered complete. Reviewers: verify each defect is addressed."
    )
    return "\n".join(lines)
