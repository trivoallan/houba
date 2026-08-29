"""The `ArchiverPort` seam: both methods reach the real implementations.

Deliberately not a behaviour suite — `test_tree_walker.py` and `test_zip_writer.py` own
that. What is worth pinning here is that the delegation is wired at all, since a class
this thin is exactly the kind that gets stubbed out or half-implemented without anything
failing loudly.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from knock.adapters.local_archiver import LocalArchiver
from knock.domain.packaging import plan_archive
from knock.ports.archiver import ArchiverPort


def test_it_walks_and_writes_a_real_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[remote]\n\turl = https://t:s3cr3t@example.com/r\n")
    (root / "SKILL.md").write_text("# probe\n")

    # Annotated as the Protocol, so mypy checks the implementation structurally rather
    # than the test merely happening to call two methods that exist.
    archiver: ArchiverPort = LocalArchiver()
    destination = tmp_path / "out.zip"
    archiver.write_archive(root, plan_archive(archiver.walk(root)), destination)

    with zipfile.ZipFile(destination) as zf:
        # `.git/` absent proves `walk` reached `walk_tree` and not some inert stub.
        assert zf.namelist() == ["SKILL.md"]
        assert zf.read("SKILL.md") == b"# probe\n"
