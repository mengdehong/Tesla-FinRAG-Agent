"""Package entry point for the Tesla FinRAG CLI.

Provides the ``ask`` subcommand for demo Q&A and workspace info.

Usage::

    python -m tesla_finrag ask --question "What was Tesla's 2023 revenue?"
    python -m tesla_finrag ask --question "..." --provider openai-compatible --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from tesla_finrag import __version__


def _format_answer_summary(answer: object) -> str:
    """Render the default CLI output with answer, citations, and calc trace."""
    from tesla_finrag.models import AnswerPayload

    assert isinstance(answer, AnswerPayload)

    lines = [
        f"Status: {answer.status.value}",
        f"Confidence: {answer.confidence:.0%}",
        "",
        "Answer:",
        answer.answer_text,
    ]

    lines.extend(["", "Citations:"])
    if answer.citations:
        for citation in answer.citations[:3]:
            excerpt = citation.excerpt.replace("\n", " ").strip()
            lines.append(
                f"- {citation.filing_type.value} period ending {citation.period_end}: {excerpt}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "Calculation Trace:"])
    if answer.calculation_trace:
        lines.extend(f"- {step}" for step in answer.calculation_trace)
    else:
        lines.append("- None")

    return "\n".join(lines)


def _run_ask(args: argparse.Namespace) -> int:
    """Execute the ``ask`` subcommand."""
    from tesla_finrag.evaluation.workbench import ProviderMode, get_workbench_pipeline
    from tesla_finrag.provider import ProviderError
    from tesla_finrag.runtime import ProcessedCorpusError

    try:
        provider_mode = ProviderMode(args.provider)
    except ValueError:
        print(
            f"Error: unknown provider '{args.provider}'. "
            f"Choose from: {', '.join(m.value for m in ProviderMode)}",
            file=sys.stderr,
        )
        return 1

    try:
        # Clear the cache so provider_mode takes effect
        get_workbench_pipeline.cache_clear()
        pipeline = get_workbench_pipeline(provider_mode=provider_mode)
    except ProcessedCorpusError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        plan, bundle, answer = pipeline.run(args.question)
    except ProviderError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = answer.model_dump(mode="json")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(_format_answer_summary(answer))

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the package CLI with subcommands."""
    parser = argparse.ArgumentParser(
        prog="tesla_finrag",
        description="Tesla FinRAG workspace CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ── ask subcommand ────────────────────────────────────────────────────────
    ask_parser = subparsers.add_parser(
        "ask",
        help="Ask a question against the processed corpus.",
    )
    ask_parser.add_argument(
        "--question",
        "-q",
        required=True,
        help="The financial question to answer.",
    )
    ask_parser.add_argument(
        "--provider",
        "-p",
        default="local",
        choices=[m.value for m in _get_provider_modes()],
        help="Provider mode: 'local' (default) or 'openai-compatible'.",
    )
    ask_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output the full AnswerPayload as JSON.",
    )

    return parser


def _get_provider_modes() -> list:
    """Lazy import to avoid circular deps at parse time."""
    from tesla_finrag.evaluation.workbench import ProviderMode

    return list(ProviderMode)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the package CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return _run_ask(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
