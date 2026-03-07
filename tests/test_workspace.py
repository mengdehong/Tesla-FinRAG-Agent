"""Workspace bootstrap tests."""

from __future__ import annotations

import subprocess
import sys


def test_package_entrypoint_reports_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tesla_finrag", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "tesla_finrag 0.1.0" in result.stdout


def test_package_entrypoint_help_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tesla_finrag"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Tesla FinRAG workspace bootstrap CLI." in result.stdout
