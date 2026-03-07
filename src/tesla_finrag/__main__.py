"""Minimal package entry point for the Tesla FinRAG workspace."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from tesla_finrag import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the lightweight package CLI used during workspace bootstrap."""
    parser = argparse.ArgumentParser(
        prog="tesla_finrag",
        description="Tesla FinRAG workspace bootstrap CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minimal package CLI."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
