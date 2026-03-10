#!/usr/bin/env python
"""
PDF 处理链路诊断脚本

测试单一 PDF 文件的完整处理流程，并验证：
1. PDF 原始文本提取（pdfplumber + PyMuPDF fallback）
2. Narrative chunk 切分
3. LanceDB 已存储的向量
4. 向量检索是否真实有效
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
import urllib.request
import uuid
import warnings
from pathlib import Path
from uuid import UUID

# 抑制 pdfminer 的 FontBBox 报错
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# 确保能找到项目包
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ─── 颜色和格式化 ──────────────────────────────────────────────────────────
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


def get_disp_len(s: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in s)


def section(title: str) -> None:
    pad_len = max(0, 72 - 5 - get_disp_len(title))
    print(f"\n{Colors.OKBLUE}{Colors.BOLD}─── {title} {'─' * pad_len}{Colors.ENDC}")


def ok(msg: str) -> None:
    print(f"  {Colors.OKGREEN}✔{Colors.ENDC} {msg}")


def warn(msg: str) -> None:
    print(f"  {Colors.WARNING}⚠{Colors.ENDC} {msg}")


def err(msg: str) -> None:
    print(f"  {Colors.FAIL}✖{Colors.ENDC} {msg}")


def info(msg: str) -> None:
    print(f"  {Colors.OKCYAN}ℹ{Colors.ENDC} {msg}")


def dim(msg: str) -> str:
    return f"{Colors.DIM}{msg}{Colors.ENDC}"


# ─── 辅助函数 ──────────────────────────────────────────────────────────────
def _default_serializer(obj):
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def generate_doc_id(pdf_path: Path) -> UUID:
    """根据文件名生成确定的 UUID"""
    return uuid.uuid5(uuid.NAMESPACE_DNS, pdf_path.stem)


# ─── 主诊断流程 ────────────────────────────────────────────────────────────
def run_diagnostics(
    pdf_path: Path,
    query: str,
    doc_id: UUID | None = None,
    lancedb_dir: Path | None = None,
    out_dir: Path | None = None,
) -> None:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

    # 路径解析
    orig_path = pdf_path
    if not pdf_path.is_absolute():
        pdf_path = (PROJECT_ROOT / pdf_path).resolve()

    lancedb_dir = lancedb_dir or PROJECT_ROOT / "data/processed/lancedb"
    out_dir = out_dir or PROJECT_ROOT / "data/processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 允许自动生成或传入
    doc_id = doc_id or generate_doc_id(pdf_path)
    # 兼容之前硬编码的 UUID，如果文件名是 Tesla_2021_全年_10-K，可以用之前写死的 UUID 方便对比
    # 不过这里就不特别写死，让用户也可以通过 --doc-id 传

    print(f"\n{Colors.BOLD}🚀 启动 PDF 诊断流水线{Colors.ENDC}")
    info(f"PDF 目标: {pdf_path}")
    info(f"Doc ID  : {doc_id}")
    info(f"测试查询: {query!r}")

    # 1. 检查 PDF
    section("1. PDF 文件检查")
    if not pdf_path.exists():
        err(f"PDF 不存在: {pdf_path}")
        msg = (
            "可以通过参数传入具体文件路径，"
            "如：uv run python scripts/diagnose_pdf_pipeline.py data/raw/xxx.pdf"
        )
        print(f"  {dim(msg)}")
        sys.exit(1)

    size_mb = pdf_path.stat().st_size / 1024 / 1024
    ok(f"文件存在: {pdf_path.name} ({size_mb:.2f} MB)")

    # 2. 文本解析
    section("2. PDF 文本解析 (analyze_filing_pdf)")
    try:
        from tesla_finrag.ingestion.analysis import analyze_filing_pdf
    except ImportError as e:
        err(f"无法导入项目包: {e}")
        sys.exit(1)

    t0 = time.monotonic()
    analysis = analyze_filing_pdf(pdf_path)
    elapsed = time.monotonic() - t0

    total_pages = len(analysis.pages)
    fallback_pages = analysis.fallback_count
    failed_pages = len(analysis.failed_pages)
    total_chars = sum(len(p.text) for p in analysis.pages)

    ok(f"解析耗时: {elapsed:.2f}s")
    ok(f"总页数  : {total_pages}")
    ok(f"总字符数: {total_chars:,} (~{total_chars // 4:,} tokens)")

    if fallback_pages:
        warn(f"PyMuPDF fallback 页数: {fallback_pages}")
    else:
        ok("无 fallback 页（pdfplumber 全部成功）")

    if failed_pages:
        err(f"解析失败页数: {failed_pages}")
    else:
        ok("无解析失败页")

    output_json = out_dir / f"{pdf_path.stem}_parsed.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(
            dataclasses.asdict(analysis),
            f,
            default=_default_serializer,
            ensure_ascii=False,
            indent=2,
        )
    ok(f"解析结果已保存至: {output_json}")

    print(f"\n  {dim('前 2 页文本预览 (各前 150 字符):')}")
    for page in analysis.pages[:2]:
        preview = page.text.replace("\n", " ")[:150]
        print(f"  {Colors.DIM}📄 [Page {page.page_number}]{Colors.ENDC} {preview}...")

    # 3. Narrative Chunk 切分
    section("3. Narrative Chunk 切分")
    from tesla_finrag.ingestion.narrative import narrative_chunks_from_analysis

    chunks = narrative_chunks_from_analysis(analysis, doc_id)
    section_titles = list(dict.fromkeys(c.section_title for c in chunks))

    ok(f"生成 Chunk 数: {len(chunks)}")
    ok(f"涵盖 Section 数: {len(section_titles)}")
    avg_tokens = sum(c.token_count for c in chunks) // max(len(chunks), 1)
    ok(f"平均 Chunk token 数: {avg_tokens}")

    print(f"\n  {dim('检测到的 Sections 示例 (最多排 5 个):')}")
    for t in section_titles[:5]:
        print(f"    • {t}")
    if len(section_titles) > 5:
        print(f"    • ... (共 {len(section_titles)} 个)")

    print(f"\n  {dim('第一个 Chunk 预览:')}")
    if chunks:
        c = chunks[0]
        print(f"  {Colors.OKCYAN}Section : {c.section_title}{Colors.ENDC}")
        print(f"  {Colors.OKCYAN}Tokens  : {c.token_count}{Colors.ENDC}")
        print(f"  {dim(c.text[:200].replace(chr(10), ' ') + '...')}")

    # 4. LanceDB 已存向量检查
    section("4. LanceDB 存储验证")
    # 为了兼容如果尚未入过库，我们需要捕获相关缺失可能
    table = None
    try:
        import lancedb

        # 忽略由于版本过新抛出的 table_names 报废警告，保持底层兼容原逻辑
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            db = lancedb.connect(str(lancedb_dir))
            table_names = db.table_names()

        if "chunks" not in table_names:
            warn("LanceDB 路径存在, 但不存在 'chunks' 表。这是全新的库吗？")
        else:
            table = db.open_table("chunks")
            total_rows = table.count_rows()

            info(f"LanceDB 路径: {lancedb_dir}")
            ok(f"全局总行数  : {total_rows:,}")

            df = table.to_pandas()
            doc_rows = df[df["doc_id"] == str(doc_id)]

            if len(doc_rows) > 0:
                ok(f"当前文档 ({pdf_path.stem}) 的向量行数: {len(doc_rows)}")

                sample_vec = doc_rows.iloc[0]["vector"]
                vec_dim = len(sample_vec)
                vec_norm = sum(x**2 for x in sample_vec) ** 0.5
                nonzero = sum(1 for x in sample_vec if x != 0.0)

                info(
                    f"向量维度: {vec_dim} | L2 范数: {vec_norm:.4f} | 非零分量: {nonzero}/{vec_dim}"
                )

                section_count = (doc_rows["kind"] == "section").sum()
                table_count = (doc_rows["kind"] == "table").sum()
                ok(f"类型分布: Section {section_count} 行, Table {table_count} 行")
            else:
                warn(f"未找到 doc_id={doc_id} 的向量记录！")
                info(
                    "提示: 之前的数据可能使用了硬编码 UUID(ce42cb0c-6daa...)。"
                    "可以指定 --doc-id 参数查询，"
                    "或先执行 `uv run python -m tesla_finrag ingest` 重新入库。"
                )
    except Exception as e:
        err(f"LanceDB 检查失败: {e}")

    # 5. 向量检索链路验证
    section("5. 向量检索验证 (真实查询)")
    from tesla_finrag.retrieval.lancedb_store import LanceDBRetrievalStore

    store = LanceDBRetrievalStore(lancedb_dir)
    try:
        info(f"检索器加载完成，索引库记录数: {store.chunk_count}")
    except Exception:
        warn("检索器无法获取 chunk 数，索引可能为空或尚未建立。")

    ollama_ok = False
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=2)
        ollama_ok = True
        ok("Ollama 嵌入服务在线 (localhost:11434)")
    except Exception:
        warn("Ollama 服务不可用，无法执行实时文本 Embedding")

    if ollama_ok and table is not None and table.count_rows() > 0:
        from openai import OpenAI

        client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1", timeout=30)
        try:
            resp = client.embeddings.create(input=[query], model="nomic-embed-text")
            q_vec = resp.data[0].embedding
            ok(f"Query 成功向量化 (维度: {len(q_vec)})")

            # 使用真实 query 进行搜索，限定在当前文档下搜索（或者也可以全库搜）
            # 不限定 doc_id 试试全库搜会不会更好，因为用户可能就是想验证泛型查询
            # 也可以提示指定了 doc_id 的局部结果

            results = store.search(q_vec, top_k=5)
            # 如果想限定只查这一篇文档，加入 kwarg: filters=f"doc_id = '{doc_id}'"
            # 但要看看 retrieval 怎么实现的
            ok(f"全局搜索 Query [{query}]，Top-5 命中:")

            for i, (chunk, score) in enumerate(results, 1):
                text = getattr(chunk, "text", None) or getattr(chunk, "raw_text", "")
                preview = text[:150].replace(chr(10), " ")

                # 若命中当前文档加高亮
                is_current = str(chunk.doc_id) == str(doc_id)
                mark = f"{Colors.OKGREEN}★ {Colors.ENDC}" if is_current else "  "

                print(
                    f"\n{mark}{Colors.HEADER}[T-{i}] Score: {score:.4f} | "
                    f"Doc: {str(chunk.doc_id)[:8]} | Section: {chunk.section_title!r}{Colors.ENDC}"
                )
                print(f"    {dim(preview + '...')}")

        except Exception as e:
            err(f"检索流程出错/LanceDB 未就绪: {e}")
    else:
        warn("跳过实时 Query 测试 (Ollama 不存在或表为空)。")
        if (
            table is not None
            and doc_rows is not locals().get("doc_rows", None)
            and len(doc_rows) > 0
        ):
            warn("尝试使用库中的样本向量进行【自查询】测试...")
            q_vec = list(doc_rows.iloc[0]["vector"])
            results = store.search(q_vec, top_k=3, doc_ids=[doc_id])
            ok(f"自查询命中 {len(results)} 条 (Top-1 应该是它自身)")
            for i, (chunk, score) in enumerate(results, 1):
                text = getattr(chunk, "text", None) or getattr(chunk, "raw_text", "")
                preview = text[:100].replace(chr(10), " ")
                print(
                    f"\n  {Colors.HEADER}[T-{i}] Score: {score:.4f} | "
                    f"Section: {chunk.section_title!r}{Colors.ENDC}"
                )
                print(f"  {dim(preview + '...')}")

    print(f"\n{Colors.OKGREEN}{Colors.BOLD}🎉 诊断流程执行完毕！{Colors.ENDC}\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tesla FinRAG PDF 诊断脚本 - 用于测试 PDF 解析与检索链路",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "pdf_path",
        nargs="?",
        default="data/raw/Tesla_2021_全年_10-K.pdf",
        help="待诊断的 PDF 文件路径",
    )

    parser.add_argument(
        "-q", "--query", default="Tesla 2021 annual revenue", help="用于测试向量检索的查询字符串"
    )

    parser.add_argument(
        "--doc-id",
        type=UUID,
        help=(
            "指定检索时核对的文档 UUID (默认: 基于文件名的生成的UUID，"
            "若要沿用旧数据请传入已库里存在的 UUID)"
        ),
    )

    parser.add_argument(
        "--lancedb-dir", type=Path, help="LanceDB 数据目录路径 (默认: data/processed/lancedb)"
    )

    parser.add_argument("--out-dir", type=Path, help="解析结果暂存目录 (默认: data/processed)")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        run_diagnostics(
            pdf_path=Path(args.pdf_path),
            query=args.query,
            doc_id=args.doc_id,
            lancedb_dir=args.lancedb_dir,
            out_dir=args.out_dir,
        )
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}用户中断诊断流程。{Colors.ENDC}")
        sys.exit(130)
