# Phase C: Fact Resolution & Table Fallback + Composite Partial Answer

## 背景

Phase A+B 完成后，benchmark 达到 8/9 (88.89%)，仅 BQ-003 仍然失败。

BQ-003 是一个 **复合问题** (text + table)：
> "What supply chain risk factors did Tesla mention in its 2023 10-K, and how did cost of automotive revenue change between FY2022 and FY2023?"

## 根因分析

### 失败链
1. Planner 将 "cost of automotive revenue" 映射为 `us-gaap:CostOfGoodsAndServicesSold`
2. XBRL fact store 中该 concept 有 **0 条记录**
3. Linker 报告 `missing_periods: [2022-12-31, 2023-12-31]`
4. Composer 的 evidence sufficiency check 检测到 `limitation_reasons` 非空
5. 整个答案短路为 `INSUFFICIENT_EVIDENCE`
6. Narrative lane（supply chain risk factors）的 7 个 section chunks 被完全丢弃

### 架构缺陷
1. **无 table fallback**: XBRL facts 缺失时，不尝试从 table chunks 提取数值
2. **无 partial answer**: 复合问题中任一 lane 缺失导致整体失败
3. **Retrieval 过窄**: sub-query 文本为 `"CostOfGoodsAndServicesSold for FY2022"`，对表格匹配效果差

## 设计方案

### 1. 复合问题检测 (Planner)
- 检测 "and" 连接的 narrative + numeric 双 lane
- 设置 `answer_shape = COMPOSITE`
- 为 narrative lane 生成独立的 sub-query

### 2. Table Fallback (Linker)
- 当 XBRL facts 缺失时，扫描 table chunks 尝试提取匹配的数值
- 创建 table-backed FactRecord（带 provenance 标记）
- 优先级链: XBRL fact > table-extracted fact > limitation

### 3. Composite Partial Answer (Composer)
- 复合问题不因单一 lane 失败而整体短路
- 返回 narrative 回答 + numeric limitation（如有缺失）
- Status 为 OK（当 narrative lane 成功时），附带 limitation 注释

### 4. 语义保护
- "cost of automotive revenue" ≠ total cost of revenue
- 不做无根据的概念替换

## 不做的事
- 不修改 XBRL ingestion pipeline
- 不引入 LLM 做表格解析
- 不放松 benchmark assertions（除非有实质能力提升后的合理调整）
