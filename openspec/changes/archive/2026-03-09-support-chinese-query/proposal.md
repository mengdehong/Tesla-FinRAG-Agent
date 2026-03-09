## Why

当前查询链路默认假设用户使用英文提问，而底层财报语料也主要是英文内容。这导致中文或中英混合问句在 query planner、关键词提取和 BM25 检索阶段经常无法正确识别期间、指标与叙事意图，最终即使语料存在也无法稳定命中。

## What Changes

- 为查询规划链路增加中英双语归一化能力，支持中文、英文和中英混合问句提取期间、财务指标、比较/排序/计算意图。
- 为多 period sub-query 增加独立的检索搜索串，使中文问句可以映射到英文财报证据而不是直接以中文原句检索英文文档。
- 增强词法检索 tokenizer，对中文 query 提供有效 token，并保留财务期间、表单类型和财务术语的检索信号。
- 让答案正文与 limitation 文案根据问句语言自适应输出，同时保留 citation excerpt 的源文语言。
- 增加中文回归测试与 benchmark 等价问句，验证 planner、retrieval 和 answer payload 的双语行为。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `grounded-financial-qa`: 结构化规划、混合检索和答案载荷需要支持中文与中英混合问句，并对输出语言进行自适应。

## Impact

- Affected code: `src/tesla_finrag/planning/query_planner.py`, `src/tesla_finrag/retrieval/lexical.py`, `src/tesla_finrag/retrieval/hybrid.py`, `src/tesla_finrag/models.py`, `src/tesla_finrag/answer/composer.py`
- Affected tests: planner intent、hybrid retrieval、composer intent routing、integration/e2e benchmark coverage
- No new external dependency is required; the change keeps the current local-first deterministic architecture
