## Context

仓库当前的 grounded QA pipeline 已经具备规则化 query planner、BM25 + vector hybrid retrieval、显式 calculation 和 traceable answer payload，但这些能力主要围绕英文问句构建。现状里有两个直接瓶颈：

1. `query_planner.py` 的 period、metric、intent、narrative cue 规则几乎都只识别英文表达。
2. `retrieval/lexical.py` 的 tokenizer 只保留 `[a-z0-9]`，中文 query 在词法检索通道中会退化为极少甚至空 token。

财报语料仍然以英文为主，因此“支持中文查询”本质上不是把语料翻译成中文，而是要让中文问句被稳定归一化为适合英文财报检索与结构化计算的中间表示。

## Goals / Non-Goals

**Goals:**

- 支持简体中文、英文和中英混合问句进入同一条 grounded-financial-qa 链路。
- 让 planner 能从中文问句中提取期间、指标、计算意图、叙事线索，并构造适合英文语料检索的规范化搜索文本。
- 让 lexical retrieval 在中文 query 下保持有效，不再因为 tokenizer 丢失全部中文 token 而失效。
- 让答案正文和 limitation 文案按问句语言自适应输出，减少产品体验割裂。
- 保持当前 typed service 架构和本地可测性，不引入 provider 依赖作为主路径。

**Non-Goals:**

- 不对财报语料进行机器翻译，也不新增中文语料索引。
- 不引入基于 LLM 的 query translation 作为主实现路径。
- 不修改 ingestion pipeline 或要求重建当前 processed corpus。
- 不覆盖繁体中文或更泛化的多语种支持。

## Decisions

### 1. 使用规则归一化而不是“先翻译再检索”

采用双语 alias、期间模式和意图 cue 扩展 planner，把中文问句解析为已有 `QueryPlan` 所需的结构化字段，并生成英文 `normalized_query` / `search_text` 用于检索。

Rationale:

- 现有系统已经是显式规划与显式计算架构，规则扩展能直接复用现有 typed contracts。
- 不引入模型翻译可以避免 provider 可用性、延迟和不可测的 prompt 漂移问题。
- 对财务指标、期间和风险主题这类高价值 query，规则映射的可解释性和可调试性更适合当前项目目标。

Alternatives considered:

- 先把中文 query 翻译成英文，再走现有链路。放弃原因是它会让 planner、retrieval 和 tests 都依赖外部模型质量，不符合本仓库当前的 deterministic 设计方向。

### 2. 在 `QueryPlan` / `SubQuery` 中显式区分原始问句与检索搜索串

为 `QueryPlan` 增加 `query_language` 与 `normalized_query`，为 `SubQuery` 增加 `search_text`。`original_query` 和 `SubQuery.text` 保留原始/可读表达，新的字段只服务于检索与调试。

Rationale:

- 中文问句的原文保留对 UI、debug 和 answer language adaptation 仍然重要。
- 检索层需要面向英文语料的规范化搜索串，不能继续直接复用原始中文问句。
- 将二者分开可以避免后续 retrieval debug 无法解释“展示的是中文，实际搜的是英文”的问题。

Alternatives considered:

- 直接覆写 `original_query` 或 `SubQuery.text` 为英文。放弃原因是会损失用户输入与调试上下文，不利于 answer composer 做语言自适应。

### 3. 对 lexical lane 增加 CJK-aware tokenizer，但仍以英文归一化检索为主

词法 tokenizer 会同时支持英文 token、数字 token 和中文片段 token。对于中文连续片段，输出原片段和 2 字滑窗 token；同时 hybrid retrieval 优先消费 `normalized_query` / `search_text`，确保中文问句仍能对英文 chunk 保持高命中率。

Rationale:

- 仅靠 tokenizer 不能解决“中文 query 检索英文语料”的核心问题，因此仍需 planner 归一化。
- 仅靠归一化也不够，因为 mixed-language query、中文 debug query 和未来的中文元数据仍需要 tokenizer 能工作。
- 两层一起做，能同时修复 planner 和 BM25 lane 的退化问题。

Alternatives considered:

- 只改 planner，不改 tokenizer。放弃原因是 lexical lane 仍然对中文原 query 和部分 mixed query 不稳。
- 只改 tokenizer，不做 query normalization。放弃原因是中文词法 token 仍无法有效匹配英文财报正文。

### 4. 答案语言自适应只作用于模板文案，不翻译证据

answer composer 将根据 `query_language` 选择中文或英文的模板前缀、结果标签和 limitation 文案；citation excerpt、table text 与 narrative excerpt 保持财报原文，不做翻译。

Rationale:

- 这样可以满足中文使用体验，同时保持 grounded evidence 的可核查性。
- 自动翻译 excerpt 会引入新的准确性风险，也会模糊 citation 与原文的对应关系。

Alternatives considered:

- 全量翻译 answer 和引用。放弃原因是超出当前 change 范围，且会引入新的模型依赖与可追溯性问题。

## Risks / Trade-offs

- [Risk] 中文 alias 覆盖不全，首版仍可能漏掉少量财务表达。
  Mitigation: 先覆盖 benchmark 和产品文档中最常见的财务指标、期间表达和风险主题，并通过 tests 固化。

- [Risk] 某些中文术语存在歧义，例如“利润”可能指 gross profit、operating income 或 net income。
  Mitigation: 优先使用更具体的 alias；对泛化词保持保守，不在没有上下文时做激进映射。

- [Risk] mixed-language query 可能同时触发中文和英文 cue，导致 keywords 冗余。
  Mitigation: 在归一化阶段做去重，并优先保留 concept label、period label 和 narrative cue 的 canonical form。

- [Risk] CJK tokenizer 增加 token 数量，可能略微影响 BM25 性能。
  Mitigation: 只对 query 和 chunk 中的连续中文片段做轻量切分，不引入重量级分词依赖。

## Migration Plan

1. 先扩展 `models.py` 的 query language / normalized search contracts。
2. 实现 planner 的中文 period / metric / intent / narrative 归一化。
3. 更新 retrieval 层消费新搜索串并补充 tokenizer。
4. 调整 answer composer 的语言自适应模板。
5. 增加回归测试与中文 benchmark 等价问句。

本变更无数据迁移要求，也不依赖重建 LanceDB 索引。若实现出现回归，可回滚到当前英文优先链路，因为所有改动都局限在 query-time planning、retrieval 和 answer composition。

## Open Questions

- 当前 change 默认 `mixed` 问句输出中文模板文案；除非后续产品反馈要求不同，本次实现不再增加用户级配置项。
