from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.infrastructure.verifiers.invariant_verifier import InvariantVerifier

pytestmark = pytest.mark.integration

_SPEC_BODY_PASSING = """\
## Invariants

```yaml {dsl=invariants}
- name: result_is_int
  predicate: isinstance(result, int)
```
"""

_SPEC_BODY_FAILING = """\
## Invariants

```yaml {dsl=invariants}
- name: result_always_none
  predicate: result is None
```
"""

_SPEC_BODY_NO_INVARIANTS = """\
## Purpose

No invariants block here.
"""


def _write_fixture(tmp_path: Path, fn_name: str, body: str) -> Path:
    module_file = tmp_path / f"{fn_name}.py"
    module_file.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.mark.slow
def test_passing_invariant_returns_no_findings(tmp_path: Path):
    fixture_dir = _write_fixture(
        tmp_path,
        "positive_fixture",
        "def positive_fixture(result):\n    return result\n",
    )
    verifier = InvariantVerifier(
        spec_id="spec_pass",
        spec_body=_SPEC_BODY_PASSING,
        fixture_module="positive_fixture",
        timeout=60,
        extra_pythonpath=(str(fixture_dir),),
    )
    findings = verifier.verify(tmp_path, ())
    assert findings == ()


@pytest.mark.slow
def test_failing_invariant_returns_error_finding(tmp_path: Path):
    fixture_dir = _write_fixture(
        tmp_path,
        "positive_fixture",
        "def positive_fixture(result):\n    return result\n",
    )
    verifier = InvariantVerifier(
        spec_id="spec_fail",
        spec_body=_SPEC_BODY_FAILING,
        fixture_module="positive_fixture",
        timeout=60,
        extra_pythonpath=(str(fixture_dir),),
    )
    findings = verifier.verify(tmp_path, ())
    assert len(findings) >= 1
    assert findings[0].severity == "error"
    assert findings[0].verifier_kind == "invariants"


@pytest.mark.slow
def test_finding_message_contains_spec_id(tmp_path: Path):
    fixture_dir = _write_fixture(
        tmp_path,
        "positive_fixture",
        "def positive_fixture(result):\n    return result\n",
    )
    verifier = InvariantVerifier(
        spec_id="my_spec_id",
        spec_body=_SPEC_BODY_FAILING,
        fixture_module="positive_fixture",
        timeout=60,
        extra_pythonpath=(str(fixture_dir),),
    )
    findings = verifier.verify(tmp_path, ())
    assert any("my_spec_id" in f.file_path for f in findings)


@pytest.mark.slow
def test_no_invariants_block_returns_no_findings(tmp_path: Path):
    verifier = InvariantVerifier(
        spec_id="spec_no_block",
        spec_body=_SPEC_BODY_NO_INVARIANTS,
        fixture_module="irrelevant_fixture",
        timeout=60,
    )
    findings = verifier.verify(tmp_path, ())
    assert findings == ()


@pytest.mark.slow
def test_multiple_invariants_all_passing(tmp_path: Path):
    spec_body = """\
## Invariants

```yaml {dsl=invariants}
- name: result_is_int
  predicate: isinstance(result, int)
- name: result_equals_self
  predicate: result == result
```
"""
    fixture_dir = _write_fixture(
        tmp_path,
        "int_fixture",
        "def int_fixture(result):\n    return result\n",
    )
    verifier = InvariantVerifier(
        spec_id="spec_multi",
        spec_body=spec_body,
        fixture_module="int_fixture",
        timeout=60,
        extra_pythonpath=(str(fixture_dir),),
    )
    findings = verifier.verify(tmp_path, ())
    assert findings == ()
