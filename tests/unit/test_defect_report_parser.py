from backend.App.orchestration.domain.defect import parse_defect_report


def test_explicit_defect_report_tag():
    text = '<defect_report>{"defects":[{"id":"X1","severity":"P0","title":"crash"}]}</defect_report>\nVERDICT: NEEDS_WORK'
    report = parse_defect_report(text)
    assert len(report.defects) == 1
    assert report.has_blockers
    assert report.defects[0].id == "X1"


def test_fenced_json_with_real_defects_is_extracted():
    text = """### Review Summary

```json
{
  "defects": [
    {"id": "t1_eventbus_001", "title": "Crash", "severity": "P0", "category": "Stability"},
    {"id": "t1_eventbus_002", "title": "Type", "severity": "P1", "category": "Design"}
  ],
  "test_scenarios": [],
  "edge_cases": [],
  "regression_checks": []
}
```

VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 2
    assert report.has_blockers
    assert {d.id for d in report.defects} == {"t1_eventbus_001", "t1_eventbus_002"}


def test_fenced_json_unlabeled_with_defects_is_extracted():
    text = """```
{"defects":[{"id":"D1","severity":"P0","title":"x"}]}
```
VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 1
    assert report.defects[0].id == "D1"


def test_fenced_block_containing_defect_report_tag_is_rejected():
    text = """```json
<defect_report>{"defects":[{"id":"D1","severity":"P1"}]}</defect_report>
```
VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 0
    assert not report.has_blockers


def test_fenced_json_without_severity_is_rejected_as_documentation():
    text = """```json
{"defects":[{"id":"example","title":"What goes here"}]}
```
VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 0


def test_fenced_json_with_empty_defects_array_is_rejected():
    text = """```json
{"defects":[]}
```
VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 0


def test_explicit_tag_takes_precedence_over_fenced_json():
    text = """```json
{"defects":[{"id":"FENCED","severity":"P0","title":"a"}]}
```
<defect_report>{"defects":[{"id":"TAG","severity":"P1","title":"b"}]}</defect_report>
VERDICT: NEEDS_WORK"""
    report = parse_defect_report(text)
    assert len(report.defects) == 1
    assert report.defects[0].id == "TAG"


def test_no_defects_returns_empty_report():
    text = "VERDICT: OK\nLooks good."
    report = parse_defect_report(text)
    assert len(report.defects) == 0
    assert not report.has_blockers
