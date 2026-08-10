# Ultra-FineWeb-L3 精炼层 · 四条生产线综合封板报告

> 汇总日期：2026-08-10　｜　合成模型：Qwen3.6-27B（5090 本地 vLLM tp2，served id `qwen-math-classifier`，端点 `http://127.0.0.1:8003/v1`，`max_model_len=8192`）
> 铁律：本报告只引用文件里真实存在的数值，未编造或估算任何一个数字；负结果如实记录，找不到的写"未找到"。每个关键数字后标注来源文件。
> 数据来源：`qwen_L3/README.md`、四线封板报告/判官 summary/config/sample、记忆索引；采集自 2026-08-07 交接（`qwen_L3/HANDOFF_comprehensive_report.md`）。

---

## 0. 四线封板状态总览

来源：`qwen_L3/README.md`

| 线 | 目录 | 封板 prompt | 成功率 | 产物规模 | 真坏数据 |
|---|---|---|---|---|---|
| 中文 QA | `zh/qa` | `qa_gen_v3_30_forcetype_zh.txt`（强制题型 v3.30） | 0.99 | 99 篇 / 614 QA 对（n100 验证批） | 1 条 CoT 泄漏\* |
| 英文 QA | `en/qa` | `qa_gen_en_v1.txt`（v1-hardened） | 0.9912 | 453 篇 | 0 |
| 中文 rewrite | `zh/rewrite` | `rewrite_styles_v4_3_5_v5090_zh.yaml`（v4.3.5-5090） | 1.0 | 1808 条 | 0 |
| 英文 rewrite | `en/rewrite` | `rewrite_styles_v3_7_en.yaml`（v3.7） | 0.9487 | 759 条 | 0 |

\* **中文 QA 唯一坏数据**：n100 封板验证批（`qa_27b_v330_n100`）中 rec93（脏 OCR 种子·光合作用题）答案混入模型推理过程，该批**早于** L0 CoT 过滤规则加入故残留。规则已补入 `QA/src/common.py`（`_reasoning_leak_answer`/`_source_noise_block_answer`），`zh/qa/sample_5.jsonl` 取自重过滤后干净版，下一批生产自动净。其余 3 线零真坏数据（此前 grep 命中均为原文正常词的假阳性，已逐条核实）。来源：`qwen_L3/README.md` L16。

---

## 1. 整体架构

**共同管道**：种子（L2 `openbmb/Ultra-FineWeb`）→ 合成（Qwen3.6-27B）→ L0 后过滤 → L1 AI 判官评测。本项目**只复现 L3 精炼层**，不复现 L1 清洗、L2 分类器（来源：`AGENTS.md`）。

**两类线的管道差异：**

- **QA 线**：`content` = 原文 + 问答对（**原文在前**，硬不变量）；判官走 **pairwise 盲评**（本地产物 vs 官方同源 QA 并排打分，核心指标 `official_closeness`）。中文判官用 DMX `claude-sonnet-4-6`，英文判官用 `qwen3.6-35b-a3b`。L0 过滤重（`filter_direct_qa` 一整套坏模式拦截 + 选项修复）。
- **rewrite 线**：一篇原文 → 4 种风格改写正文（`encyclopedia`/`textbook`/`blog`/`abstract`），忠实原文不新增事实；判官评忠实度/信息保留/风格符合度/自足性等多维（判官 `qwen3.6-35b-a3b` 或本地 27b）。L0 过滤主要挡占位符/空 content/`<think>` 残留。

交付 schema 三线一致：`{uid, content, style}`（来源：`AGENTS.md` 不变量 1）。

---

## 2. 各线 prompt 版本演进 + 封板版本

### 2.1 中文 QA：v3.25 → v3.28 → **v3.30（封板）**
来源：记忆 `qa-v328-soft-optimization-win.md`、`qa-v330-forcetype-l0-win.md`；报告 `QA/reports/qa_v330_forcetype_l0_n50_result.md`

- **v3.26/v3.27（弃）**：栽在**题型硬配额**（强制单点题≤40%、每篇必须 N 种）→ 复现模板化。
- **v3.28（克制软优化）**：绝不硬配额，只做答案密度、题型自然多样、焦点锚点优先级、显式加"严禁题型硬配额"防护段。相对基线 closeness **2.63→2.80↑**、忠实保 5.0、答案均字数 24.1→27.5。
- **v3.29 骨架重构（弃）**：学官方 First/Then/Finally 骨架零删约束重组，closeness **2.80→2.60 反降**，假设证伪，保留 v3.28。来源：记忆 `qa-v329-skeleton-refactor-regression.md`。
- **v3.30 强制题型（封板）**：每篇在证据允许时 ≥1 判断题 + ≥1 选择题（A/B/C/D 单选）+ ≥1 原因/推算题，**忠实优先级高于题型配额**（无证据宁可少出，绝不编造）。**2026-08-07 用户拍板封板**，生产 config（`QA/config.yaml`、`QA/config.v5090.yaml`）`qa_prompt_zh` 已切 v3.30。

### 2.2 英文 QA：v1 → v2 → v3 → **v1-hardened（封板）**
来源：记忆 `en-qa-line-v1-baseline.md`、`en-qa-v2-soft-lever-regression.md`、`en-qa-v3-pipeline-win-prompt-regression.md`；报告 `en_qa/reports/en_qa_v1_hardened_sealing.md`

- **v1 基线**（2026-07-31 建成）：无硬配额，护栏 n=50 全通过，closeness **3.71** > 中文 3.18。
- **v2 软优化（弃）**：两杠杆软引导，closeness **3.71→3.57 反降**，保留 v1。
- **v3 分离两效应**：①**管道加固（采纳）**——新建 `src/qa_gate_en.py`、修 `common._looks_zh` 把含日文的英文文档误判中文的确定性 bug、强制 `Question:/Answer:` 门禁 + 内容级重试，closeness **3.707→3.760**、可解析 99→100/100；②**v3 prompt focus-pass（弃）**——对加固基线 3.760 跌到 **3.677**。
- **封板 = v1 prompt + 加固管道 = v1-hardened**。v2、v3 两次 prompt 迭代均回退，是"忠实饱和后 closeness 天花板 = 27B 模型、非 prompt"的第三次独立印证。

### 2.3 中文 rewrite：v4.3.5 → **v4.3.5-5090（封板）**；v4.3.6 骨架实验（弃）
来源：`qwen_L3/zh/rewrite/rewrite_styles_v4_3_5_v5090_zh.yaml` 文件头；记忆 `rewrite-5090-freeze-v5090.md`、`rewrite-5090-think-tag-parse-fix.md`、`rewrite-v436-skeleton-experiment.md`

- **v4.3.5-5090**：基于 v4.3.5，仅为本地弱指令模型做最小适配——v4.3.5 用尖括号占位符举例导致本地模型照抄，**实测 1812/1812 条 content 全成 `<改写后的正文>` 占位符**；本版把示例改为非占位符描述 + 显式禁尖括号/占位符，**四风格段与 v4.3.5 未改一字**。
- **配套修复思维链 bug**：served `qwen-math-classifier` 会把 `<think>…</think>` 塞进 `content`，`enable_thinking:false` 对该 served 模型不可靠；`rewrite/src/common.py` 新增 `_strip_reasoning()` 在解析入口剥掉 `</think>` 前内容。坏条率 **90%→0%**。
- **v4.3.6 骨架实验（弃）**：修复思维链 bug 后单变量干净对比，与基线 v5090 **打平**（official_style_closeness 基线 4.840 vs 骨架 4.830，Δ=−0.010，10 维度均在 ±0.04 噪声内）；初版"+0.196 正向"是 `run_rewrite_only_experiment.py` 漏传 extra_body 导致思维链吃满 max_tokens 截断正文的假象。**骨架不是质量杠杆**，保留 v5090 封板。

### 2.4 英文 rewrite：v3.6 → **v3.7（封板）**；v3.8（弃）
来源：`rewrite/reports/rewrite_freeze_summary_en_v3_7_2026-08-04.md`；`reports/en_rewrite_v3_7_v3_8_decision_2026-08-04.md`

- **v3.7（封板）**：相对 v3.6 增加对 population/institution 的 scope 护栏。
- **v3.8（弃）**：v3.7 + 一条很窄的 list/catalog/course-outline 护栏；定向回归 n=2 通过，但 n=50 全量后 blog 行回归——overall **4.965 < v3.7 的 4.98**、faithfulness **4.975 < 4.995**、`adds_unseen_facts` 出现 1 条。决策：冻结候选仍为 v3.7，list/catalog 护栏应在 v3.7 之后另开版本。

---

## 3. 评分逻辑

来源：`qwen_L3/README.md`；各线判官 summary

**判官模型：**
- 中文 QA：DMX `claude-sonnet-4-6`（pairwise 盲评）
- 英文 QA / 中文 rewrite n100 / 英文 rewrite：`qwen3.6-35b-a3b`
- 中文 rewrite n25 小样对比：本地 `qwen-math-classifier`（27b）

**QA 维度含义**（pairwise，本地 vs 官方同源）：
- `official_closeness`：与官方 QA 的整体接近度（核心指标）
- `faithfulness`：答案是否忠实原文、可逐句溯源
- `hallucination_risk`：是否引入原文没有的事实（越高越安全）
- `focus_similarity`：是否命中官方 QA 的信息焦点
- `coverage`：对原文关键信息的覆盖广度

**rewrite 维度含义**（多风格改写）：`faithfulness`（忠实）、`information_retention`（信息保留）、`refinement_value`（精炼价值）、`official_style_closeness`（官方风格接近度）、`natural_prose`（自然行文）、`low_template`（低模板化）、`noise_handling`（噪声处理）、`hallucination_risk`、`appropriate_length`、`overall`。

---

## 4. 各版本评分全列表（标来源）

### 4.1 中文 QA
来源：`QA/claude_model_test/judge_*_summary.json`（judge = claude-sonnet-4-6）；配对结论见 `QA/reports/qa_v330_forcetype_l0_n50_result.md`

**（a）n100 v3.30 vs v3.28 严格配对（54 条共同种子，2026-08-07 定论轮）：**

| 指标 | v3.28 | v3.30 | Δ |
|---|---|---|---|
| official_closeness | 2.370 | 2.667 | **+0.296** |
| faithfulness | 4.833 | 4.722 | −0.111 |
| hallucination_risk | 4.815 | 4.722 | −0.093 |
| focus_similarity | 2.741 | 2.852 | +0.111 |
| coverage | 3.111 | 3.093 | −0.019 |
| answerability | 4.759 | 4.630 | −0.130 |
| self_containment | 3.667 | 3.593 | −0.074 |

**（b）各 summary JSON 全局均值（n 为实际判成条数）：**

| 文件（judge=sonnet-4-6） | n | closeness | faith | hallu | focus | coverage |
|---|---|---|---|---|---|---|
| `judge_27b_v330_n100_summary.json` | 99 | 2.758 | 4.727 | 4.667 | 2.939 | 3.242 |
| `judge_27b_v328_n100_summary.json` | 83 | 2.458 | 4.831 | 4.783 | 2.735 | 3.169 |
| `judge_v330_n100_PREFILTER_summary.json` | 63 | 2.778 | 4.698 | 4.619 | 2.952 | 3.302 |
| `judge_v330_n100_POSTFILTER_summary.json` | 60 | 2.700 | 4.817 | 4.700 | 2.917 | 3.167 |

> **faithfulness −0.111 非真回归**：v3.30 全 15 条 faith<5 逐条定性——13 条=纯 style 意见（判官把风格差异计入忠实分）、1 条=idx44 边缘半条疑似过度推断、1 条=idx93 脏 OCR 露怯（已补 L0 规则拦掉）。真编造事实实质 ≤1 条，无成片编造。来源：报告 `qa_v330_forcetype_l0_n50_result.md`。

### 4.2 英文 QA
来源：`data/eval/en/eval_l1_pairwise_qa_en_*summary.json`（judge = qwen3.6-35b-a3b）；`en_qa/reports/en_qa_v1_hardened_sealing.md`

**prompt 演进对照（closeness）：** v1 基线 **3.707**（99/100） → v2 软优化 **3.57**（弃） → **v1-hardened 3.760**（n=100，100/100，采纳） → v3 focus-pass **3.677**（弃）。

**v1-hardened 封板权威指标（n=500 冻结判官复算，来源报告 2a）：**
- closeness **3.698**（95%CI [3.648, 3.748]，半宽 ±0.050）
- 可解析 500/500、hallucination_risk **5.0**、faithfulness **4.992**（仅 3 条 <5 = 0.6%，均噪声源）
- coverage **3.922**、answerability **4.996**、self_containment **4.940**

**三批构成 JSON（各批原始值）：**

| 文件 | n | closeness | faith | hallu | coverage | self_cont |
|---|---|---|---|---|---|---|
| `..._v1_hardened_summary.json` | 100 | 3.76 | 5.0 | 5.0 | 3.98 | 4.99 |
| `..._v1_hardened_ext100_summary.json` | 100 | 3.69 | 5.0 | 5.0 | 3.94 | 4.92 |
| `..._v1_hardened_ext300_summary.json` | 300 | 3.68 | 4.9867 | 5.0 | 3.8967 | 4.93 |

英文 closeness **3.70 > 中文 3.18/2.76**（口径不同：英文判官 qwen35b、中文判官 sonnet-4-6，不可直接横比）。

### 4.3 中文 rewrite
来源：`rewrite/data/eval/zh/*_summary.json`；`rewrite/reports/rewrite_freeze_summary_v5090_qwen3_6_27b_2026-08-03.md`

**封板主评（`eval_l1_rewrite_official_style_qwen3_6_27b_v4_3_5_chunks_merged_n100_summary.json`，judge=qwen3.6-35b-a3b，anchor=16，success 99/failure 1/total 100）：**

| 维度 | mean | 维度 | mean |
|---|---|---|---|
| faithfulness | 4.8687 | low_template | 4.9192 |
| information_retention | 4.899 | noise_handling | 4.8788 |
| refinement_value | 4.6566 | hallucination_risk | 4.9798 |
| official_style_closeness | 4.6162 | appropriate_length | 4.6667 |
| natural_prose | 4.7273 | **overall** | **4.7374** |

分风格 overall（n=25 each）：abstract 4.72 / blog 4.84 / encyclopedia 4.75 / textbook 4.64。boolean 违规：adds_unseen_facts **0**、drops_key_information **0**、drops_key_technical_parameter **0**。

- 封板报告独立跑（n=20，本地 27b 判官，anchor=4）：official_closeness / overall 均 **5.0**、refinement 4.9（报告自注"判官高分档偏宽，以人工复核为准"）。

> ⚠️ **v4.3.6 骨架实验的干净结论（Δ≈0）以记忆 `rewrite-v436-skeleton-experiment.md` + `rewrite/reports/rewrite_v4_3_6_skeleton_result.md` 为准**。`rewrite/data/eval/zh/judge_rw_baseline_v5090_n25_summary.json`（closeness 2.6869）与 `judge_rw_v436_skeleton_n25_summary.json`（closeness 2.8384）这两份落盘 n25 summary 判官口径不同（本地 27b anchor=4）且含大量空/污染样本，**不代表骨架实验的干净结论，报告不引用其绝对分**。

### 4.4 英文 rewrite
来源：`en_rewrite/outputs/en_rewrite_v3_7_l1_n50_enfilter3_2026-08-03/l1_rewrite_en_quality_summary.json`（judge=qwen3.6-35b-a3b，200/200 success）；封板报告 `rewrite_freeze_summary_en_v3_7_2026-08-04.md`

| 维度 | mean | 维度 | mean |
|---|---|---|---|
| overall_score | **4.98** | natural_english | 4.99 |
| faithfulness | 4.995 | low_template | 4.995 |
| information_retention | 4.99 | noise_handling | 5.0 |
| style_strategy_fit | 4.945 | refinement_value | 4.95 |

分风格 overall（n=50 each）：abstract 5.0 / blog 5.0 / encyclopedia 4.98 / textbook 4.94。boolean 违规：adds_unseen_facts **0**、drops_key_information **1**（落在 textbook）、其余全 0。

> 注：`rewrite/data/eval/en/` 目录不存在；英文 rewrite 判官 summary 实际位于 `en_rewrite/outputs/.../` 与 `L3_delivery/eval_summaries/en_rewrite_v3.7_L1_quality_summary.json`（同值拷贝）。

---

## 5. 服务器吞吐（真实墙钟）

> ⚠️ 坑：`run_stats.json` 的 `elapsed_seconds` 是并发累加**非墙钟**，真实吞吐取日志 tqdm 末行 `[MM:SS]`。来源：`qwen_L3/HANDOFF_comprehensive_report.md` L34。

| 线 | 并发 | 真实墙钟 | 吞吐 | 来源 |
|---|---|---|---|---|
| 中文 QA v3.30 n100 | c16 | **1:36** | 100 篇 / 1:36 | `/root/QA/log_v330_n100.log` tqdm 末行（记忆 `qa-v330-forcetype-l0-win.md`） |
| 中文 rewrite n200 | c8 | 14:44（884s） | **13.6 篇/分**（每篇 4.42s，加速比 1.94×，success 100%） | `rewrite_freeze_summary_v5090_qwen3_6_27b_2026-08-03.md` |
| 中文 rewrite n200 | c4 | 28:34（1714s） | 7.0 篇/分（每篇 8.57s，1.0×） | 同上 |
| 英文 rewrite n200 | c8 | 19:22 | **10.33 篇/分**（每篇 5.81s，加速比 1.67×，success 94.63%） | `en_rewrite_5090_throughput_summary_2026-08-04.md` |
| 英文 rewrite n200 | c4 | 32:25 | 6.17 篇/分（每篇 9.73s，success 95.13%） | 同上 |
| 英文 rewrite n200（开 prefix caching） | c8 | 17:41 | 11.31 篇/分（+9.5%，prefix_cache_hits 63.2 万 ~37% token 命中） | 同上 |

- **并发甜点**：中文 QA c16、中文 rewrite c8（1.94×）、英文 rewrite c8（1.67×）。
- 中文 rewrite 工期估算（c8，13.6 篇/分）：1 万篇 ≈ 12.3 小时、10 万篇 ≈ 5.1 天。来源：封板报告。
- 中文 rewrite 资源（n=453）：总 token 3,546,673；平均每篇 7829 token、每条改写 1962 token。来源：封板报告。
- **英文 QA 吞吐**：封板报告未给独立墙钟；config `qwen_L3/en/qa/config.yaml` 记录 concurrency 16（5090 甜点），产量 453 篇 / 成功率 0.9912。真实墙钟数字**未找到**（未在已读文件中）。

---

## 6. 样例展示（原文 + 合成数据）

### 6.1 中文 QA（`qwen_L3/zh/qa/sample_5.jsonl`，REC0）
- uid `e217962f-7c7a-4f78-bd0c-dee1a067507a`，style `qa`，content_len 832
- **原文（在前）**：《中国文化知识 100 题》选择题库（16–20 题带 ABCD 选项+答案）
- **合成问答对**（空行分隔在原文后）：含选择题（"美"字含义 → 答"羊大即为美"）、判断题（"错误。原文指出墨子的主要思想是'兼爱'…"）
- 印证不变量：`content` = 原文 + 问答对，原文在前，style 恒为 `qa`。

### 6.2 英文 QA（`qwen_L3/en/qa/sample_5.jsonl`，第 1 条）
- uid `e625e747-8f08-47ef-a74b-83d54dc406f9`，style `qa`
- **原文（在前）**：专利表格（US8261340 等）
- **合成问答**（5 对）：如 `Question: Which organization is listed as the assignee for patent US8261340? Answer: Citrix Systems, Inc.`

### 6.3 中文 rewrite 官方对照（`qwen_L3/zh/rewrite/sample_5.jsonl`，同源风筝课 4 风格）
- 同一源文档（风筝课）产出 4 种风格，字段 `_internal_style` 标具体风格：
  - encyclopedia：客观说明——"受疫情蔓延导致户外活动减少的影响，教师通过增加课堂丰富性…超过九成学生一次性完成了折叠任务。"
  - textbook：学习型说明——"…通过播放教学影片并配合多次操作练习，九成以上学生能够一次性完成风筝骨架的组装…"

> ⚠️ **字段口径注意**：`sample_5.jsonl` 里交付字段 `style` 恒为 `"multi_style"`，具体风格放在内部字段 `_internal_style`（`encyclopedia/textbook/blog/abstract`）；中英 rewrite 两线一致。封板报告文字曾表述"导出具体风格标签"，与实际 sample 字段不一致，此处如实记录以样例文件为准。

---

## 7. 线间差异：中文 QA 的「强约束 + 后过滤」机制

中文 QA 是四线里唯一同时上了**强约束（强制题型）**与**重后过滤（L0）**的线。

**（a）强制题型（prompt 层）**：每篇在证据允许时 ≥1 判断 + ≥1 选择（A/B/C/D 单选）+ ≥1 原因/推算题，**忠实优先级 > 题型配额**（无证据宁可少出，绝不为凑题型编造）。来源：`QA/src/prompts/qa_gen_v3_30_forcetype_zh.txt` L55-59。

**（b）L0 后过滤（`QA/src/common.py` `filter_direct_qa`，唯一事实来源，根 `src/common.py` 为转发垫片）**，命中的坏模式：
- `_reasoning_leak_answer`：拦答案裸推理泄漏（"根据常识/结合上下文推断/OCR 错误…"，保守判定 ≥2 次命中或答案 ≥80 字）
- `_source_noise_block_answer`：拦"热心网友/试题分析/解析…考查"源噪声块
- `_QA_META_DISCOURSE_ANSWER_RE`：拦"露怯"元话语（"让我重新/作为出题人/应改为问…"）
- `_repair_misplaced_choice_options` / `_orphan_choice_block_in_answer`：选项块错位修复或删除
- `_unsupported_ocr_numeric` / `_unsupported_visual_inference` / `_unsupported_subject_shift` 等专项规则
- n100 实际命中（来源报告 L114-116）：v330 removed 40 / adjusted 61（含 choice_answer_label_removed 61、choice_missing_options 17、unsupported_ocr_numeric 8、meta_discourse 2）

**（c）过滤前后效果对比**（来源：`judge_v330_n100_PREFILTER/POSTFILTER_summary.json`）：

| 指标 | PREFILTER (n=63) | POSTFILTER (n=60) | Δ |
|---|---|---|---|
| official_closeness | 2.778 | 2.700 | −0.078 |
| faithfulness | 4.698 | 4.817 | **+0.119** |
| hallucination_risk | 4.619 | 4.700 | +0.081 |
| coverage | 3.302 | 3.167 | −0.135 |
| 坏数据 | 有 | **0** | 归零 |

**结论**：过滤对 closeness/coverage 中性（Δ 在 ±0.13 噪声内），faithfulness/hallucination 微升约 +0.08~0.12，**坏数据归零**——即以近乎零质量代价换取产物洁净。

---

## 8. 中文 QA 问题样例（真实短板，非编造）

来源：报告 `qa_v330_forcetype_l0_n50_result.md`；记忆 `qa-v330-forcetype-l0-win.md`；`QA/reports/qa_v330_n100_fourway_review.csv`

- **原版本问法不自然/机械抽取**：**ID79**"为出题而出题、语言生硬"——v330 该篇呈单点短答（Q1"中国帮助非洲人民抗击了什么疫情？"→"埃博拉疫情。"；Q2"…哪个国家首都严重缺水？"→"马尔代夫。"），对照官方同源 QA 含判断/选择/推算题更丰富。属**风格短板非坏数据**，印证"天花板是模型非 prompt"。
- **判官假阳性 4 条**（人工核查实为忠实）：ID16Q5"2015 年"（原文"15 年"）、ID14Q5 镜子引句为原文原句、ID56Q6 腰围公式(尺+7)×2.54、ID79 埃博拉/马尔代夫组（英文原文可定位）。来源：报告 L158-164。
- **唯一真实缺陷**：ID93 Q2 CoT 元推理泄漏进答案（脏 OCR 种子），已"先修再封"（补 L0 规则）。21 条人工抽样**零事实编造**。
- **"官方合成数据自身有问题"的正面样例**：交接要求项，但报告与记忆中**未找到指名官方数据缺陷的具体条目**（报告表述集中在"v3.30 更贴官方焦点"）——如实记为**未找到**。

> 数据说明：`qa_v330_n100_fourway_review.csv` 中文字段存在双重编码损坏（乱码不可逆），ID79 定性结论取自报告与记忆明文，未从损坏 CSV 臆测。

---

## 9. 关键事实速查（均已核实）

来源：`qwen_L3/HANDOFF_comprehensive_report.md` L108-115、`qwen_L3/README.md`

- **四线成功率**：中文 QA 0.99、英文 QA 0.9912、中文 rewrite 1.0、英文 rewrite 0.9487
- **四线真坏数据**：仅中文 QA 1 条 CoT 泄漏（脏 OCR 种子 rec93，已被 L0 新规则修复、服务器全量重过滤回传归零）；其余 3 线 = 0
- 中文 QA v3.30 已于 **2026-08-07 用户拍板封板**，服务器生产 config 已切 v3.30
- L0 过滤单一事实来源 `QA/src/common.py`，根 `src/common.py` 为转发垫片（两者不再分叉）
- 中文 QA v330 n100 真实墙钟：100 篇 / **1:36**（c16）

---

## 附：报告内已标注的口径冲突（如实记录，未擅自统一）

1. **判官口径不可横比**：中文 QA closeness（sonnet-4-6）≈2.7、英文 QA closeness（qwen35b）≈3.7、rewrite closeness（qwen35b anchor=16 vs 本地 27b anchor=4）差异大，均因判官模型/anchor 不同，**跨线绝对分不可直接比较**。
2. **rewrite `sample_5.jsonl` 的 `style` 字段恒为 `multi_style`**，与部分封板报告"导出具体风格标签"表述不一致；具体风格实际在 `_internal_style`。
3. **英文 QA config 模型标注**（本地 `qwen-math-classifier`/Qwen3.6-27B）与封板报告（阿里云 qwen3.6-27b + 判官 qwen3.6-35b-a3b）口径不同，已并列未合并。
