from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ast_soleaux.worker import JsonLineWorker


def worker(command: str, cwd: Path, *, max_response_bytes: int = 1024) -> JsonLineWorker:
    return JsonLineWorker(
        command=(sys.executable, "-c", command),
        cwd=str(cwd),
        environment=dict(os.environ),
        max_response_bytes=max_response_bytes,
    )


def test_worker_close_falls_back_and_reaps_when_group_signal_is_denied(tmp_path: Path) -> None:
    instance = worker("import time; print('ready', flush=True); time.sleep(30)", tmp_path)
    instance.start()
    process = instance._process
    assert process is not None
    response = instance._responses.get(timeout=2)
    assert isinstance(response, bytes) and response == b"ready"

    try:
        with patch(
            "ast_soleaux.worker._signal_process_group",
            side_effect=PermissionError(1, "Operation not permitted"),
        ) as signal_group:
            instance.close()
        signal_group.assert_called_once()
        assert process.poll() is not None
        assert instance._process is None
        assert instance._reader is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_worker_close_allows_clean_exit_after_stdin_eof(tmp_path: Path) -> None:
    instance = worker("import sys; print('ready', flush=True); sys.stdin.read()", tmp_path)
    instance.start()
    process = instance._process
    assert process is not None
    response = instance._responses.get(timeout=2)
    assert isinstance(response, bytes) and response == b"ready"

    try:
        with patch("ast_soleaux.worker._signal_process_group") as signal_group:
            instance.close()
        signal_group.assert_not_called()
        assert process.poll() == 0
        assert instance._process is None
        assert instance._reader is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_worker_terminates_on_response_overflow_before_newline(tmp_path: Path) -> None:
    instance = worker(
        "import sys, time; sys.stdin.readline(); sys.stdout.write('x' * 1024); sys.stdout.flush(); time.sleep(30)",
        tmp_path,
        max_response_bytes=128,
    )
    instance.start()
    process = instance._process
    assert process is not None

    with pytest.raises(RuntimeError, match="worker response exceeds 128 bytes"):
        instance.request({}, timeout=2)

    assert process.poll() is not None
    assert instance._process is None
    assert instance._reader is None
