from __future__ import annotations

from backend.App.repomap.domain.repo_map import RepoMap, RepoMapEntry, render_text


def _entry(path: str, rank: float, sigs: tuple[str, ...] = ("def f  # L1",)) -> RepoMapEntry:
    return RepoMapEntry(file_path=path, signatures=sigs, rank=rank)


def test_empty_repo_map_returns_honest_message():
    result = render_text(RepoMap(entries=()), max_tokens=1000)
    assert "no source files" in result


def test_ordering_by_rank_descending():
    entries = (
        _entry("low.py", 0.1),
        _entry("high.py", 0.9),
        _entry("mid.py", 0.5),
    )
    result = render_text(RepoMap(entries=entries), max_tokens=10000)
    assert result.index("high.py") < result.index("mid.py") < result.index("low.py")


def test_token_budget_respected():
    sigs = tuple(f"def func_{i}  # L{i}" for i in range(100))
    entry = _entry("big.py", 1.0, sigs)
    result = render_text(RepoMap(entries=(entry,)), max_tokens=20)
    rendered_sigs = [line.strip() for line in result.splitlines() if line.strip().startswith("def")]
    assert len(rendered_sigs) < len(sigs)


def test_tiny_budget_returns_budget_message():
    entry = _entry("x.py", 1.0, ("def very_long_function_name  # L1",))
    result = render_text(RepoMap(entries=(entry,)), max_tokens=1)
    assert "budget" in result or "no source" in result


def test_custom_token_counter_used():
    called = []

    def counter(text: str) -> int:
        called.append(text)
        return len(text)

    entry = _entry("a.py", 0.5)
    render_text(RepoMap(entries=(entry,)), max_tokens=10000, token_counter=counter)
    assert called


def test_multiple_files_all_appear_within_budget():
    entries = tuple(_entry(f"file{i}.py", float(i)) for i in range(5))
    result = render_text(RepoMap(entries=entries), max_tokens=100000)
    for i in range(5):
        assert f"file{i}.py" in result
