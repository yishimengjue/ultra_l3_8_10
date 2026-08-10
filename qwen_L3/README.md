# qwen_L3 — Ultra-FineWeb-L3 精炼层 4 条生产线封板汇总

> 汇总日期：2026-08-07　｜　合成模型：Qwen3.6-27B（served id `qwen-math-classifier`，5090 本地 vLLM）
> 判官：DMX claude-sonnet-4-6（pairwise 盲评）／中文 rewrite 另用 qwen3.6-35b-a3b
> 本目录为 4 条线的封板快照，每线含：`config.yaml`（生产配置）、prompt、封板报告、`sample_5.jsonl`（前 5 条产物样例）。

## 4 条线封板状态（全部真实运行结果，未估算）

| 线 | 目录 | 封板 prompt | 成功率 | 产物规模 | 真坏数据 |
|---|---|---|---|---|---|
| QA 中文 | `zh/qa` | `qa_gen_v3_30_forcetype_zh.txt`（强制题型 v3.30） | 0.99 | 99 篇 / 614 QA 对(n100 验证批) | 1 条 CoT 泄漏* |
| QA 英文 | `en/qa` | `qa_gen_en_v1.txt`（v1 hardened） | 0.9912 | 453 篇 | 0 |
| rewrite 中文 | `zh/rewrite` | `rewrite_styles_v4_3_5_v5090_zh.yaml`（v4.3.5-5090） | 1.0 | 1808 条 | 0 |
| rewrite 英文 | `en/rewrite` | `rewrite_styles_v3_7_en.yaml`（v3.7） | 0.9487 | 759 条 | 0 |

\* **QA 中文 1 条 CoT 泄漏说明**：n100 封板验证批（`qa_27b_v330_n100`，08-07 10:09 跑）中 rec93（脏 OCR 种子·光合作用题）答案混入模型推理过程。该批**早于** L0 CoT 过滤规则加入，故残留。规则已补入 `QA/src/common.py`（`_reasoning_leak_answer`/`_source_noise_block_answer`，服务器 md5 已同步 `843967729f...`），本目录 `zh/qa/sample_5.jsonl` 取自**重过滤后干净版**，下一批生产自动净。其余 3 线零真坏数据（此前 grep 命中均为原文正常词的假阳性，已逐条核实）。

## 核心结论

- **QA 中文 v3.30**：强制题型 + L0 过滤。相对 v3.28 基线，n100 官方同源严格配对 closeness +0.25~0.30；faithfulness 经逐条 + 人工抽样双重核实，真编造≈0（差距是判官把风格差异计入忠实分）。已于 2026-08-07 用户拍板封板，服务器生产 config 已切 v3.30。
- **QA 英文 v1**：hardened 管道，n500 成功率 99.12%，closeness 3.71 > 中文，护栏全通过。
- **rewrite 中文 v4.3.5-5090**：5090 本地版封板，四风格具体标注、判官 anchor≤4、并发甜点已定，成功率 100%。
- **rewrite 英文 v3.7**：成功率 94.87%，零坏数据。

## 说明

- 本目录是**封板快照**，非全量产物。全量 qa_full/multi_style_full 产物在服务器 `/root/QA/outputs/` 与 `/root/rewrite/outputs/` 对应目录。
- 过滤逻辑单一事实来源：`QA/src/common.py`（服务器实际运行、功能超集）；根 `src/common.py` 为其转发垫片。
- 各线 `config.yaml` 中的 `qa_prompt_zh/en`、`rewrite_prompt_zh/en` 字段指明该线实际加载的 prompt。
