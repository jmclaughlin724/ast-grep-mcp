from __future__ import annotations

from pathlib import Path

import pytest

from ast_soleaux import mutation
from ast_soleaux.mutation import MutationService, PlannedWrite


def service() -> MutationService:
    return MutationService(max_file_bytes=1024, max_total_bytes=4096)


def test_in_place_write_is_atomic_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    source.write_text("const x={a:1}\n", encoding="utf-8")

    first = service().apply(
        project=tmp_path,
        writes=[PlannedWrite(source=source, target=source, content="const x = { a: 1 };\n")],
        conflict_policy="overwrite",
        allow_source_overwrite=True,
    )
    second = service().apply(
        project=tmp_path,
        writes=[PlannedWrite(source=source, target=source, content="const x = { a: 1 };\n")],
        conflict_policy="overwrite",
        allow_source_overwrite=True,
    )

    assert source.read_text(encoding="utf-8") == "const x = { a: 1 };\n"
    assert first.applied[0].changed is True
    assert second.applied[0].changed is False


def test_output_conflict_policies_are_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    target = tmp_path / "dist" / "source.js"
    source.write_text("const x: number = 1\n", encoding="utf-8")
    target.parent.mkdir()
    target.write_text("existing\n", encoding="utf-8")
    write = PlannedWrite(source=source, target=target, content="const x = 1;\n")

    with pytest.raises(FileExistsError):
        service().apply(project=tmp_path, writes=[write], conflict_policy="error", allow_source_overwrite=False)

    skipped = service().apply(project=tmp_path, writes=[write], conflict_policy="skip", allow_source_overwrite=False)
    assert skipped.skipped == ("dist/source.js",)
    assert target.read_text(encoding="utf-8") == "existing\n"

    overwritten = service().apply(project=tmp_path, writes=[write], conflict_policy="overwrite", allow_source_overwrite=False)
    assert overwritten.applied[0].overwritten is True
    assert target.read_text(encoding="utf-8") == "const x = 1;\n"


def test_source_overwrite_requires_explicit_capability(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    source.write_text("source\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Source overwrite is not enabled"):
        service().apply(
            project=tmp_path,
            writes=[PlannedWrite(source=source, target=source, content="changed\n")],
            conflict_policy="overwrite",
            allow_source_overwrite=False,
        )


def test_failed_batch_removes_targets_created_before_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "dist" / "first.js"
    second = tmp_path / "dist" / "second.js"
    original_replace = mutation.os.replace

    def fail_second_staged_replace(source: str | Path, target: str | Path) -> None:
        if Path(source).suffix == ".tmp" and Path(target) == second:
            raise OSError("second replacement failed")
        original_replace(source, target)

    monkeypatch.setattr(mutation.os, "replace", fail_second_staged_replace)
    with pytest.raises(OSError, match="second replacement failed"):
        service().apply(
            project=tmp_path,
            writes=[
                PlannedWrite(source=None, target=first, content="first\n"),
                PlannedWrite(source=None, target=second, content="second\n"),
            ],
            conflict_policy="error",
            allow_source_overwrite=False,
        )

    assert first.exists() is False
    assert second.exists() is False
    assert list((tmp_path / "dist").glob(".*.tmp")) == []
