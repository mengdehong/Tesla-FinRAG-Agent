"""Package entry point for the Tesla FinRAG CLI.

Provides the ``ask`` and ``ingest`` subcommands.

Usage::

    python -m tesla_finrag ingest
    python -m tesla_finrag ask --question "What was Tesla's 2023 revenue?"
    python -m tesla_finrag ask --question "..." --provider openai-compatible --json
"""

from __future__ import annotations

import argparse
import json
import os
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


def _run_ingest(args: argparse.Namespace) -> int:
    """Execute the ``ingest`` subcommand."""
    from pathlib import Path

    from tesla_finrag.ingestion.pipeline import run_pipeline
    from tesla_finrag.logging_config import configure_cli_logging

    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    workers = _resolve_ingest_workers(args.workers)

    configure_cli_logging()
    print(f"Running ingestion: {raw_dir} -> {output_dir} (workers={workers})")

    try:
        summary = run_pipeline(raw_dir=raw_dir, output_dir=output_dir, workers=workers)
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    failed_filings = summary.get("failed_filings", 0)
    completion_label = (
        "Ingestion Complete" if failed_filings == 0 else "Ingestion Complete With Warnings"
    )

    print("\n" + "=" * 60)
    print(completion_label)
    print("=" * 60)
    print(f"Output location:    {output_dir.resolve()}")
    print(f"Filings written:    {summary.get('filings', 0)}")
    print(f"Section chunks:     {summary.get('section_chunks', 0)}")
    print(f"Table chunks:       {summary.get('table_chunks', 0)}")
    print(f"Fact records:       {summary.get('fact_records', 0)}")
    print(f"Manifest available: {summary.get('manifest_available', 0)}")
    print(f"Manifest gaps:      {summary.get('manifest_gaps', 0)}")
    print(f"Failed filings:     {failed_filings}")
    print(f"Elapsed seconds:    {summary.get('elapsed_seconds', 0)}")

    gap_details = summary.get("gap_details", [])
    if gap_details:
        print("\nGap Details:")
        for gap in gap_details:
            fy = gap.get("fiscal_year", "?")
            fq = gap.get("fiscal_quarter", "")
            ft = gap.get("filing_type", "")
            status = gap.get("status", "")
            notes = gap.get("notes", "")
            quarter_info = f" Q{fq}" if fq else ""
            print(f"  - FY{fy}{quarter_info} {ft} ({status}){': ' + notes if notes else ''}")

    failed_details = summary.get("failed_details", [])
    if failed_details:
        print("\nFailed Filings:")
        for failure in failed_details:
            print(
                f"  - {failure.get('period_key', '?')} "
                f"({failure.get('elapsed_seconds', 0)}s): {failure.get('error', 'unknown error')}"
            )

    print("=" * 60)
    return 0


def _resolve_ingest_workers(requested_workers: int) -> int:
    """Resolve the CLI worker count, using auto-parallelism by default."""
    if requested_workers > 0:
        return requested_workers
    return max(1, min(4, os.cpu_count() or 1))


def _run_ask(args: argparse.Namespace) -> int:
    """Execute the ``ask`` subcommand."""
    from tesla_finrag.evaluation.workbench import ProviderMode, get_workbench_pipeline
    from tesla_finrag.guidance import format_corpus_guidance
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
        print(format_corpus_guidance(exc), file=sys.stderr)
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

    # ── ingest subcommand ─────────────────────────────────────────────────────
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Run the ingestion pipeline to produce data/processed/.",
    )
    ingest_parser.add_argument(
        "--raw-dir",
        default="data/raw",
        help="Path to raw filing inputs (default: data/raw).",
    )
    ingest_parser.add_argument(
        "--output-dir",
        default="data/processed",
        help="Path for processed output (default: data/processed).",
    )
    ingest_parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of filing workers (default: auto, capped at 4).",
    )

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

    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "ask":
        return _run_ask(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
