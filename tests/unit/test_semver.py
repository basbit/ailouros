from __future__ import annotations

import pytest

from backend.App.plugins.domain.semver import SemverError, SemverRange


def r(raw: str) -> SemverRange:
    return SemverRange.parse(raw)


def test_exact_match():
    assert r("1.2.3").matches("1.2.3")
    assert not r("1.2.3").matches("1.2.4")


def test_eq_operator():
    assert r("=1.2.3").matches("1.2.3")
    assert not r("=1.2.3").matches("1.3.0")


def test_gte():
    assert r(">=1.0.0").matches("1.0.0")
    assert r(">=1.0.0").matches("2.0.0")
    assert not r(">=1.0.0").matches("0.9.0")


def test_gt():
    assert r(">1.0.0").matches("1.0.1")
    assert not r(">1.0.0").matches("1.0.0")


def test_lt():
    assert r("<2.0.0").matches("1.9.9")
    assert not r("<2.0.0").matches("2.0.0")


def test_lte():
    assert r("<=2.0.0").matches("2.0.0")
    assert r("<=2.0.0").matches("1.9.9")
    assert not r("<=2.0.0").matches("2.0.1")


def test_neq():
    assert r("!=1.0.0").matches("1.0.1")
    assert not r("!=1.0.0").matches("1.0.0")


def test_caret_major():
    assert r("^1.2.3").matches("1.2.3")
    assert r("^1.2.3").matches("1.9.0")
    assert not r("^1.2.3").matches("2.0.0")
    assert not r("^1.2.3").matches("1.2.2")


def test_caret_zero_major():
    assert r("^0.3.0").matches("0.3.0")
    assert r("^0.3.0").matches("0.3.9")
    assert not r("^0.3.0").matches("0.4.0")


def test_caret_zero_minor():
    assert r("^0.0.3").matches("0.0.3")
    assert not r("^0.0.3").matches("0.0.4")


def test_tilde():
    assert r("~1.2.3").matches("1.2.3")
    assert r("~1.2.3").matches("1.2.9")
    assert not r("~1.2.3").matches("1.3.0")


def test_comma_range_and():
    assert r(">=0.3.0,<0.5.0").matches("0.4.0")
    assert r(">=0.3.0,<0.5.0").matches("0.3.0")
    assert not r(">=0.3.0,<0.5.0").matches("0.5.0")
    assert not r(">=0.3.0,<0.5.0").matches("0.2.9")


def test_empty_range_matches_all():
    assert r("").matches("1.0.0")
    assert r("").matches("99.0.0")


def test_version_with_prerelease_suffix():
    assert r(">=1.0.0").matches("1.0.0-alpha")


def test_invalid_version_raises():
    with pytest.raises(SemverError):
        r(">=1.0.0").matches("not_a_version")


def test_str_representation():
    assert str(r(">=1.0.0,<2.0.0")) == ">=1.0.0,<2.0.0"
