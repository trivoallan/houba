from __future__ import annotations

from pathlib import Path

import pytest

from knock.errors import SourceError, SourcePathError
from tests.fakes.source import FakeSourcePort


def test_resolve_returns_the_seeded_revision() -> None:
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "a" * 40})
    assert fake.resolve("https://x/y.git", "v1") == "a" * 40


def test_resolve_journals_its_calls() -> None:
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "a" * 40})
    fake.resolve("https://x/y.git", "v1")
    assert fake.resolved == [("https://x/y.git", "v1")]


def test_resolve_raises_on_an_unknown_ref() -> None:
    fake = FakeSourcePort()
    with pytest.raises(SourceError):
        fake.resolve("https://x/y.git", "nope")


def test_fetch_journals_separately_from_resolve(tmp_path: Path) -> None:
    # The journals must stay independent: a planner test asserts "resolved but never
    # fetched" to prove convergence, and that claim is worthless if fetch also
    # appends to `resolved`.
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "a" * 40})
    fetched = fake.fetch("https://x/y.git", "v1", tmp_path)
    assert fetched.revision == "a" * 40
    assert fake.fetched == [("https://x/y.git", "v1")]
    assert fake.resolved == []


def test_fetch_materialises_the_tree_and_agrees_with_resolve(tmp_path: Path) -> None:
    # resolve and fetch must return the same revision, or a planner could skip on one
    # value and stamp another.
    fake = FakeSourcePort(
        revisions={("https://x/y.git", "v1"): "b" * 40}, tree={"SKILL.md": "# probe\n"}
    )
    fetched = fake.fetch("https://x/y.git", "v1", tmp_path)
    assert fetched.revision == fake.resolve("https://x/y.git", "v1")
    assert (fetched.root / "SKILL.md").read_text() == "# probe\n"


def test_journals_cannot_be_seeded_by_the_caller() -> None:
    # They are the evidence for the convergence claim; a pre-loadable journal is not
    # evidence. Every other fake here keeps seed data private and journals public.
    with pytest.raises(TypeError):
        FakeSourcePort(resolved=[("https://x/y.git", "v1")])  # type: ignore[call-arg]


@pytest.mark.parametrize("path", ["/etc", "../outside", "packages/../.."])
def test_fetch_refuses_a_path_escaping_the_tree(tmp_path: Path, path: str) -> None:
    # Mirrors GitAdapter._subdir: a fake that accepts every path lets a test pass
    # where the real adapter exits 1.
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "b" * 40}, tree={"a/SKILL.md": "x"})
    with pytest.raises(SourcePathError, match="escapes"):
        fake.fetch("https://x/y.git", "v1", tmp_path, path=path)


def test_fetch_refuses_a_path_absent_from_the_tree(tmp_path: Path) -> None:
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "b" * 40}, tree={"a/SKILL.md": "x"})
    with pytest.raises(SourcePathError, match="not found"):
        fake.fetch("https://x/y.git", "v1", tmp_path, path="nope")


def test_fetch_reroots_onto_a_seeded_subdirectory(tmp_path: Path) -> None:
    fake = FakeSourcePort(
        revisions={("https://x/y.git", "v1"): "b" * 40}, tree={"a/SKILL.md": "# inner\n"}
    )
    fetched = fake.fetch("https://x/y.git", "v1", tmp_path, path="a")
    # Re-rooted, not nested: the subtree's own file sits directly under the new root,
    # exactly as it does after GitAdapter clones and then roots at `workdir / path`.
    assert fetched.root == tmp_path / "a"
    assert (fetched.root / "SKILL.md").read_text() == "# inner\n"


def test_fetch_materialises_nothing_when_the_tree_is_unseeded(tmp_path: Path) -> None:
    # The empty default is what lets a test assert that nothing extra was written.
    # A fake that silently produced a file would mask exactly that bug.
    fake = FakeSourcePort(revisions={("https://x/y.git", "v1"): "b" * 40})
    fetched = fake.fetch("https://x/y.git", "v1", tmp_path)
    assert list(fetched.root.iterdir()) == []
