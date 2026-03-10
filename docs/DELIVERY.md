## 数据处理与索引：智能语义分块策略

针对SEC财报特征，系统采用**两阶段语义分块**策略，以文档自然结构为边界进行切分，摒弃固定长度截断。

1. **逻辑抽取（表文分离）**
   - **文本块（`SectionChunk`）**：按原文章节（如"Item 1"）划分归属，长章节以800 token为限按段首/句尾滑动切分，过滤目录页。保证语义高内聚，检索结果自带章节锚点。
   - **表格块（`TableChunk`）**：表格独立提取。去空行后转为管道符（`|`）分隔的Markdown格式。追踪原页码和章节上下文，解析Title，避免与叙述文本混淆。

2. **索引级安全切分**
   - **表头保留策略**：针对超长表格，严格按行（line-by-line）切片以防数据错位。截断后片段自动补充源表格的主表头，确保分块后的数据仍保持独立解释性。

该策略避免了财报数据上下文断裂和表格结构化丢失，提升了针对财务指标和年份等结构化数据的混合检索（BM25+Vector）准确率。

## 检索阶段：混合检索权重分配

系统在检索层结合了基于 **BM25 的词法检索 (Lexical Search)** 和基于 **Dense Embedding 的向量检索 (Vector Search)**，以确保兼顾关键词的精准匹配与上下文的语义理解。

在权重分配和结果融合策略上，系统做出了以下架构设计：

1. **摒弃线性加权 (Linear Interpolation)**：
   BM25 得分（通常远大于1，依赖词频和文档长度）与向量检索的余弦相似度分数（`0-1` 之间）量纲差异巨大。若采用传统的硬编码 `alpha * Vector + (1 - alpha) * BM25` 线性相加，极易导致某一通道主导最终排序，使混合检索失效。
2. **采用倒数排序融合 (Reciprocal Rank Fusion, RRF)**：
   系统在 `HybridRetrievalService` 接口中实现了 RRF 算法（平滑常数 `k=60`）。此算法通过评估文档在两个不同召回集中的**排名先后**，而非绝对得分（公式为 `Sum(1 / (k + rank))`），计算出最终融合分数。
3. **50/50 权重对等原则**：
   通过 RRF 融合，BM25（擅长精确命中特定财务术语如"Free Cash Flow"、"供应链挑战"，以及特定年份如"2022 Q3"）与 Vector Search（擅长长句泛化理解）被视为**同等重要**。二者权重实质上是对等的 (50/50平权)。任何在一个通道中排名极高（如 BM25 精准命中包含搜索关键字的表格）的块，或在两个通道中均命中且排名中上等的块，都能在终态 EvidenceBundle 中排名前列，保障复杂的多步推理阶段能够获取充分的支撑材料。

## Corpus Information

| Document Type | Coverage | Count |
| --- | --- | --- |
| 10-K (Annual) | 2021–2025 | 5 |
| 10-Q (Quarterly) | 2021 Q1–Q3, 2022 Q1–Q3, 2023 Q1–Q3, 2024 Q1–Q3, 2025 Q1–Q3 | 15 |
| XBRL Structured Facts | `companyfacts.json` (SEC API) | 1 |
| **Total source files** | | **21** |

**支持的数据范围：** 2021–2025 全年 10-K 年报 + 各年 Q1–Q3 季报（10-Q），共覆盖 5 个年度、15 份季报、1 份结构化 XBRL 事实文件。

## Benchmark Evaluation

### 题库概览

基准测试集包含 **19 道复杂财务问答题**（BQ-001 至 BQ-019），覆盖六类场景、中英双语：

| 题目 ID | 类别 | 难度 | 语言 | 说明 |
| --- | --- | --- | --- | --- |
| BQ-001 | cross_year | medium | EN | FY2022 vs FY2023 营收同比增长 |
| BQ-002 | calculation | medium | EN | FY2023 毛利率计算（GrossProfit/Revenues） |
| BQ-003 | text_plus_table | hard | EN | 2023 10-K 供应链风险 + FY2022/2023 营业成本 |
| BQ-004 | time_sequenced | hard | EN | FY2021–FY2024 研发费用趋势 |
| BQ-005 | multi_period | hard | EN | 2023 Q1–Q3 营业利润及最高营业利润率季度 |
| BQ-006 | balance_sheet | medium | EN | 2023 vs 2022 年末现金及现金等价物 |
| BQ-007 | calculation | hard | EN | FY2023 自由现金流逐步计算（OpCF − CapEx） |
| BQ-008 | cross_year | easy | EN | FY2023 总营收查询 |
| BQ-009 | calculation | easy | EN | FY2023 自由现金流查询 |
| BQ-010 | cross_year | medium | ZH | BQ-001 中文版 |
| BQ-011 | calculation | medium | ZH | BQ-002 中文版 |
| BQ-012 | text_plus_table | hard | ZH | BQ-003 中文版 |
| BQ-013 | time_sequenced | hard | ZH | BQ-004 中文版 |
| BQ-014 | multi_period | hard | ZH | BQ-005 中文版 |
| BQ-015 | balance_sheet | medium | ZH | BQ-006 中文版 |
| BQ-016 | balance_sheet | medium | EN | FY2024 年末应付账款 |
| BQ-017 | balance_sheet | hard | EN | 2024 年报公众持股市值（public float） |
| BQ-018 | cross_year | medium | EN | FY2023 vs FY2024 应收账款对比 |
| BQ-019 | text_plus_table | hard | EN | 2024 地缘政治风险叙述 + 年末应付账款 |

### 最新 Baseline 结果

**Run ID:** `d6360262e3ce`  
**时间戳:** `2026-03-10T01:59:29Z`  
**Run 文件:** `data/evaluation/runs/run_20260310_015929_d6360262e3ce.json`

| 指标 | 数值 |
| --- | --- |
| 总题数 | 19 |
| 通过 (Pass) | **19** |
| 失败 (Fail) | 0 |
| 错误 (Error) | 0 |
| 通过率 | **100%** |
| 平均延迟 | **2,305 ms** |
| 评测模型 | `qwen2.5:1.5b` (Ollama 本地) |
| 嵌入模型 | `nomic-embed-text` (Ollama 本地) |

### 按类别统计

| 类别 | 题数 | 通过 | 通过率 | 典型场景 |
| --- | --- | --- | --- | --- |
| cross_year | 4 | 4 | 100% | 跨年营收/指标对比、同比增长率计算 |
| calculation | 4 | 4 | 100% | 毛利率、自由现金流分步计算 |
| text_plus_table | 3 | 3 | 100% | 风险叙述段落 + 对应财务表格关联 |
| time_sequenced | 2 | 2 | 100% | 多年费用趋势（FY2021–FY2024） |
| multi_period | 2 | 2 | 100% | 同年多季度营业利润排名 |
| balance_sheet | 4 | 4 | 100% | 资产负债表项目查询与对比 |
| **合计** | **19** | **19** | **100%** | |

### 按难度统计

| 难度 | 题数 | 通过 | 通过率 |
| --- | --- | --- | --- |
| easy | 2 | 2 | 100% |
| medium | 8 | 8 | 100% |
| hard | 9 | 9 | 100% |
| **合计** | **19** | **19** | **100%** |

### 各题延迟明细

| 题目 ID | 延迟 (ms) | 结果 |
| --- | --- | --- |
| BQ-001 | 7,206 | PASS |
| BQ-002 | 1,195 | PASS |
| BQ-003 | 5,676 | PASS |
| BQ-004 | 1,492 | PASS |
| BQ-005 | 1,836 | PASS |
| BQ-006 | 1,340 | PASS |
| BQ-007 | 1,130 | PASS |
| BQ-008 | 1,127 | PASS |
| BQ-009 | 1,263 | PASS |
| BQ-010 | 1,612 | PASS |
| BQ-011 | 1,265 | PASS |
| BQ-012 | 5,961 | PASS |
| BQ-013 | 2,020 | PASS |
| BQ-014 | 2,087 | PASS |
| BQ-015 | 1,417 | PASS |
| BQ-016 | 1,113 | PASS |
| BQ-017 | 981 | PASS |
| BQ-018 | 1,355 | PASS |
| BQ-019 | 3,714 | PASS |

> BQ-001/BQ-003/BQ-012/BQ-019 延迟较高，原因是这些题涉及文本+表格混合检索（`hybrid_reasoning`），需要额外的 narrative 章节扫描轮次。

## 失败案例分析（已全部修复）

系统已记录并修复 **6 个典型失败案例**（见 `data/evaluation/failure_analyses.json`）：

| 案例 ID | 关联题目 | 失败表象 | 根本原因 | 严重程度 | 状态 |
| --- | --- | --- | --- | --- | --- |
| FA-001 | BQ-002 | 毛利率返回 5.48 而非 18.2% | Calculator 除法顺序错误（Revenue/GrossProfit） | critical | resolved |
| FA-002 | BQ-003 | INSUFFICIENT\_EVIDENCE；未查文本通道 | 遇 XBRL 缺失时未回退到 PDF chunks | major | resolved |
| FA-003 | BQ-004 | INSUFFICIENT\_EVIDENCE（研发费用趋势） | `ResearchAndDevelopmentExpense` 未在 XBRL 概念白名单中 | major | resolved |
| FA-004 | BQ-005 | 营业利润率返回 0.00 | Planner 创建了 `custom:OperatingMarginPercent` 派生概念而非逐季拆解查询 | critical | resolved |
| FA-005 | BQ-006 | INSUFFICIENT\_EVIDENCE（现金余额） | `CashAndCashEquivalentsAtCarryingValue` 未被 XBRL 摄入索引 | major | resolved |
| FA-006 | BQ-007 | FCF 数值正确但无分步展示 | 直接返回预计算聚合值，未执行组件拆解 | major | resolved |

## 验证命令

```bash
# 安装依赖
uv sync

# 重新执行完整 benchmark（约 2–3 分钟，需 Ollama 在线）
uv run python -m tesla_finrag.evaluation
```
