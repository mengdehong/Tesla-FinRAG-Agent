#!/usr/bin/env python3
# ./scripts/download_tesla_reports.py --out-dir data/raw
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TESLA_CIK = "0001318605"
SEC_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{TESLA_CIK}.json"
TESLA_IR_PDF_URL = "https://ir.tesla.com/_flysystem/s3/sec/{acc}/{base}-gen.pdf"
SEC_ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/1318605/{acc}/{doc}"


@dataclass
class Filing:
    form: str
    filing_date: str
    report_date: str
    accession_number: str
    primary_document: str

    @property
    def accession_nodash(self) -> str:
        return self.accession_number.replace("-", "")


class DownloadError(RuntimeError):
    pass


def http_get_bytes(url: str, user_agent: str, timeout: int = 45, retries: int = 3) -> bytes:
    last_err: Optional[Exception] = None
    for i in range(1, retries + 1):
        req = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "*/*",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = e
            if i < retries:
                time.sleep(min(1.5 * i, 5))
            continue
    raise DownloadError(f"GET failed after {retries} tries: {url} -> {last_err}")


def http_download_file(url: str, user_agent: str, out_path: Path, timeout: int = 60, retries: int = 3) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for i in range(1, retries + 1):
        req = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/pdf,*/*",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                content_type = (resp.headers.get("Content-Type") or "").lower()
                data = resp.read()
                if len(data) < 1024:
                    raise DownloadError(f"Too small response ({len(data)} bytes): {url}")
                if "pdf" not in content_type and not data.startswith(b"%PDF"):
                    raise DownloadError(f"Not a PDF response ({content_type}): {url}")
                tmp = out_path.with_suffix(out_path.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(out_path)
                return True
        except (HTTPError, URLError, TimeoutError, DownloadError) as e:
            last_err = e
            if i < retries:
                time.sleep(min(1.5 * i, 5))
            continue
    print(f"[WARN] PDF download failed: {url} ({last_err})")
    return False


def parse_filings(submissions: dict, start_year: int, end_year: int) -> list[Filing]:
    recent = submissions["filings"]["recent"]
    n = len(recent["form"])
    out: list[Filing] = []
    lo = f"{start_year}-01-01"
    hi = f"{end_year}-12-31"

    for i in range(n):
        form = recent["form"][i]
        if form not in {"10-K", "10-Q"}:
            continue
        report_date = recent["reportDate"][i]
        if not report_date or report_date < lo or report_date > hi:
            continue
        out.append(
            Filing(
                form=form,
                filing_date=recent["filingDate"][i],
                report_date=report_date,
                accession_number=recent["accessionNumber"][i],
                primary_document=recent["primaryDocument"][i],
            )
        )

    out.sort(key=lambda x: x.report_date)
    return out


def period_label(form: str, report_date: str) -> str:
    if form == "10-K":
        return "全年"
    mmdd = report_date[5:10]
    return {
        "03-31": "Q1",
        "06-30": "Q2",
        "09-30": "Q3",
    }.get(mmdd, "Q?")


def output_filename(company: str, filing: Filing) -> str:
    year = filing.report_date[:4]
    period = period_label(filing.form, filing.report_date)
    return f"{company}_{year}_{period}_{filing.form}.pdf"


def ir_pdf_url(filing: Filing) -> str:
    base = filing.primary_document.rsplit(".", 1)[0]
    return TESLA_IR_PDF_URL.format(acc=filing.accession_nodash, base=base)


def sec_doc_url(filing: Filing) -> str:
    return SEC_ARCHIVE_DOC_URL.format(acc=filing.accession_nodash, doc=filing.primary_document)


def chrome_print_html_to_pdf(chrome_bin: str, html_path: Path, out_pdf: Path) -> None:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    user_data_dir = Path(tempfile.mkdtemp(prefix="chrome-codex-"))
    try:
        cmd = [
            chrome_bin,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--user-data-dir={user_data_dir}",
            f"--crash-dumps-dir={user_data_dir}",
            f"--print-to-pdf={out_pdf}",
            html_path.resolve().as_uri(),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)


def fallback_via_sec_html_and_print(
    filing: Filing,
    out_pdf: Path,
    user_agent: str,
    chrome_bin: str,
) -> bool:
    try:
        html = http_get_bytes(sec_doc_url(filing), user_agent=user_agent, timeout=60, retries=3)
    except DownloadError as e:
        print(f"[WARN] SEC HTML fetch failed for fallback: {e}")
        return False

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tf:
        tf.write(html)
        html_path = Path(tf.name)

    try:
        chrome_print_html_to_pdf(chrome_bin=chrome_bin, html_path=html_path, out_pdf=out_pdf)
        if not out_pdf.exists() or out_pdf.stat().st_size < 50_000:
            print(f"[WARN] Fallback PDF looks too small: {out_pdf}")
            return False
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[WARN] Chrome print fallback failed: {e}")
        return False
    finally:
        html_path.unlink(missing_ok=True)


def find_chrome(preferred: Optional[str]) -> Optional[str]:
    if preferred:
        p = Path(preferred)
        return str(p) if p.exists() else None
    for c in ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser"]:
        p = shutil.which(c)
        if p:
            return p
    return None


def iter_target_filings(filings: Iterable[Filing], start_year: int, end_year: int) -> Iterable[Filing]:
    for f in filings:
        y = int(f.report_date[:4])
        if start_year <= y <= end_year:
            yield f


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Tesla 10-K/10-Q PDFs (2021-2025) reproducibly.")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--out-dir", default="raw")
    parser.add_argument("--company", default="Tesla")
    parser.add_argument(
        "--user-agent",
        default="TeslaDownloader/1.0 (contact: opendata@example.com)",
        help="Use a descriptive SEC-compliant User-Agent with contact.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-print-fallback", action="store_true", default=True)
    parser.add_argument("--no-print-fallback", action="store_true")
    parser.add_argument("--chrome-bin", default="")
    args = parser.parse_args()

    allow_print_fallback = args.allow_print_fallback and not args.no_print_fallback
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    submissions = json.loads(http_get_bytes(SEC_SUBMISSIONS_URL, user_agent=args.user_agent).decode("utf-8"))
    filings = list(iter_target_filings(parse_filings(submissions, args.start_year, args.end_year), args.start_year, args.end_year))

    if not filings:
        print("No target filings found.")
        return 1

    chrome_bin = find_chrome(args.chrome_bin or None)
    if allow_print_fallback and not chrome_bin:
        print("[WARN] Print fallback enabled but no Chrome/Chromium binary found.")

    ok = 0
    failed: list[tuple[Filing, str]] = []

    for filing in filings:
        out_name = output_filename(args.company, filing)
        out_pdf = out_dir / out_name

        if out_pdf.exists() and not args.overwrite:
            print(f"[SKIP] {out_pdf}")
            ok += 1
            continue

        url = ir_pdf_url(filing)
        print(f"[TRY ] {filing.report_date} {filing.form} -> {out_name}")

        if http_download_file(url, user_agent=args.user_agent, out_path=out_pdf):
            print(f"[ OK ] IR PDF {out_pdf}")
            ok += 1
            continue

        used_fallback = False
        if allow_print_fallback and chrome_bin:
            print(f"[FALL] print fallback from SEC HTML for {filing.accession_number}")
            used_fallback = fallback_via_sec_html_and_print(
                filing=filing,
                out_pdf=out_pdf,
                user_agent=args.user_agent,
                chrome_bin=chrome_bin,
            )

        if used_fallback:
            print(f"[ OK ] Fallback PDF {out_pdf}")
            ok += 1
        else:
            failed.append((filing, url))
            print(f"[FAIL] {out_name}")

    print("\n=== Summary ===")
    print(f"Success: {ok}")
    print(f"Failed : {len(failed)}")
    if failed:
        for filing, url in failed:
            print(f"- {filing.report_date} {filing.form} {filing.accession_number} -> {url}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
