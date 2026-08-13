# 交接 Prompt：Ultra-FineWeb-L3 四条生产线综合封板报告

> 本文件是给新 Claude Code 会话的任务交接。所有文件路径均已核实存在（2026-08-07）。
> 直接把下方「任务」整段复制给新会话即可。

---

## 任务

为 Ultra-FineWeb-L3 精炼层的 **4 条生产线** 写一份综合封板报告，精简且全面。

### 项目背景
本项目复现 Ultra-FineWeb-L3 精炼层，合成模型 Qwen3.6-27B（5090 本地 vLLM，served id `qwen-math-classifier`）。已封板 4 条线：
- 中文 QA、英文 QA、中文 rewrite（多风格改写）、英文 rewrite。
- 封板快照已汇总在仓库根 `qwen_L3/{zh,en}/{qa,rewrite}/`，每线含 config+prompt+report+sample_5.jsonl。

### 铁律（项目 AGENTS.md，务必遵守）
- **绝不编造/估算任何评测或吞吐数字**；只报告文件里真实存在的值，负结果如实写，找不到写"未找到"。
- 先读 `AGENTS.md` + `qwen_L3/README.md` + 记忆索引，了解全貌再动手。
- 建议先用并行子代理（每线一个 general-purpose agent）分头搜集数据，再汇总，避免主上下文爆炸。

### 报告需包含
1. **整体架构**：种子(L2 Ultra-FineWeb)→合成→L0 后过滤→判官评测；QA 线与 rewrite 线的管道差异。
2. **各线 prompt 版本演进 + 封板版本**：
   - 中文QA：v3.25→v3.28→v3.30(强制题型)，**封 v3.30**
   - 英文QA：**封 v1-hardened**
   - 中文rewrite：v4.3.5→v4.3.5-5090（v4.3.6 骨架实验回归被弃），**封 v5090 版**
   - 英文rewrite：**封 v3.7**（v3.8 决策被弃）
3. **评分逻辑**：判官模型（QA 走 DMX claude-sonnet-4-6 pairwise；中文 rewrite 部分用 qwen3.6-35b-a3b）、各维度含义。QA 维度：official_closeness / faithfulness / hallucination_risk / focus_similarity / coverage；rewrite 维度不同（评忠实度/风格符合度/自足性等）。
4. **不同版本的所有评分**（全列表，标来源）。重点：
   - 中文QA v3.30 vs v3.28 n100 严格配对(54篇)：closeness 2.370→2.667(+0.296)、faithfulness 4.833→4.722、halluc。
   - 中文QA **过滤前 vs 过滤后**(48篇配对)：closeness 2.812→2.771、faithfulness 4.729→4.792、halluc +0.062。
   - 英文QA closeness、rewrite 各版评分。
5. **服务器吞吐**（真实墙钟）。⚠️坑：`run_stats.json` 的 `elapsed_seconds` 是并发累加**非墙钟**，真实吞吐看日志 tqdm 的 `[MM:SS]`。已验证：中文QA v330 n100 = 100篇/**1:36**(c16)。其余从记忆和服务器日志取。
6. **2 个样例展示**（原文+合成数据；有官方对照则并列）：从各线 sample_5.jsonl 和 fourway_review 取。
7. **线间差异**：重点写中文QA的「**强约束(强制题型) + 后过滤(L0)**」机制，及**过滤前后效果对比**（过滤对分数中性 Δ≤0.13、faith/halluc 微升约 +0.06、坏数据归零）。
8. **中文QA 问题样例**（真实短板，非编造）：
   - **官方合成数据的问题**：官方 QA 有跨段/含原文外计算条件的例子（判官核查发现，不能把官方当绝对金标）。
   - **原版本 QA 问法不自然/机械抽取样例**：如 ID79「为出题而出题」、v3.28 纯事实抽取。

---

## 数据来源地图（所有路径已核实存在，均相对仓库根 `D:\ultra-fineweb-l3-repro\ultra-fineweb-l3-repro`）

### 全局
- 项目规则：`AGENTS.md`
- 4 线封板总表：`qwen_L3/README.md`
- 记忆目录：`C:\Users\20360\.claude\projects\D--ultra-fineweb-l3-repro-ultra-fineweb-l3-repro\memory\`
  - 索引：`MEMORY.md`
  - 中文QA：`qa-v330-forcetype-l0-win.md`、`qa-v328-soft-optimization-win.md`
  - 英文QA：`en-qa-line-v1-baseline.md`、`en-qa-v2-soft-lever-regression.md`、`en-qa-v3-pipeline-win-prompt-regression.md`
  - rewrite：`rewrite-5090-freeze-v5090.md`、`rewrite-5090-think-tag-parse-fix.md`、`rewrite-v436-skeleton-experiment.md`、`en-rewrite-5090-throughput.md`

### 中文 QA 线
- 封板 prompt：`QA/src/prompts/qa_gen_v3_30_forcetype_zh.txt`（也在 `qwen_L3/zh/qa/`）
- 主报告（含 n100 配对 + 过滤前后 + 人工核查全过程）：`QA/reports/qa_v330_forcetype_l0_n50_result.md`
- 判官 summary（4 个，真实评分源）：
  - `QA/claude_model_test/judge_27b_v330_n100_summary.json`
  - `QA/claude_model_test/judge_27b_v328_n100_summary.json`
  - `QA/claude_model_test/judge_v330_n100_PREFILTER_summary.json`
  - `QA/claude_model_test/judge_v330_n100_POSTFILTER_summary.json`
- 人工逐条核查（官方问题样例 + 不自然样例来源）：
  - `outputs/fourway_review_n100_codex_2026-08-07/fourway_review_n100_codex逐条核查.xlsx`（用 openpyxl read_only 读，注意有重名 table，需 read_only 模式绕过）
  - `QA/reports/qa_v330_n100_fourway_review.csv`
- 样例：`qwen_L3/zh/qa/sample_5.jsonl`（重过滤后干净版）
- L0 过滤逻辑（唯一事实来源）：`QA/src/common.py` 的 `filter_direct_qa` + 相关正则（`_reasoning_leak_answer` 等）；根 `src/common.py` 是转发垫片
- 生产 config：`QA/config.yaml`（阿里云端点）、`QA/config.v5090.yaml`（5090 本地）

### 英文 QA 线
- 封板 prompt：`qwen_L3/en/qa/qa_gen_en_v1.txt`（⚠️ 本地 `QA/src/prompts/` 下**没有**，只在 qwen_L3 和服务器）
- 报告：`en_qa/reports/en_qa_v1_hardened_sealing.md`、`en_qa/reports/en_qa_v1_hardened_content_samples.md`、`L3_delivery/reports/en_qa_v1_评测基线.md`
- 判官 summary：`data/eval/en/eval_l1_pairwise_qa_en_v1_hardened_summary.json`（及同目录 v1/v2/v3/ext100/ext300 各 summary）
- 样例：`qwen_L3/en/qa/sample_5.jsonl`
- config：`qwen_L3/en/qa/config.yaml`

### 中文 rewrite 线
- 封板 prompt：`qwen_L3/zh/rewrite/rewrite_styles_v4_3_5_v5090_zh.yaml`（也在 `rewrite/src/prompts/`）
- 封板报告：`rewrite/reports/rewrite_freeze_summary_v5090_qwen3_6_27b_2026-08-03.md`
- 判官 summary：`rewrite/data/eval/zh/` 下 —
  - `judge_rw_baseline_v5090_n25_summary.json`、`judge_rw_v436_skeleton_n25_summary.json`（v4.3.6 骨架实验对比，记忆说 Δ≈0）
  - `eval_l1_rewrite_official_style_qwen3_6_27b_v4_3_5_chunks_merged_n100_summary.json`
- 样例：`qwen_L3/zh/rewrite/sample_5.jsonl`
- config：`qwen_L3/zh/rewrite/config.yaml`（对应服务器 `config.v5090.yaml`）

### 英文 rewrite 线
- 封板 prompt：`en_rewrite/src/prompts/rewrite_styles_v3_7_en.yaml`（⚠️ 在 `en_rewrite/` 下，**不在** `rewrite/src/prompts/`；也在 `qwen_L3/en/rewrite/`）
- 封板报告：`rewrite/reports/rewrite_freeze_summary_en_v3_7_2026-08-04.md`
- 吞吐/决策报告：`en_rewrite/reports/en_rewrite_5090_throughput_summary_2026-08-04.md`、`reports/en_rewrite_v3_7_v3_8_decision_2026-08-04.md`
- 样例：`qwen_L3/en/rewrite/sample_5.jsonl`
- config：`qwen_L3/en/rewrite/config.yaml`（对应服务器 `en_thru200_c8`）

---

## 服务器（取吞吐真实墙钟用，可选）
- SSH（Windows 原生 openssh）：
  `/c/Windows/System32/OpenSSH/ssh.exe -i "$USERPROFILE/.ssh/server_5090_key" -p 3295 -o StrictHostKeyChecking=no -o ConnectTimeout=45 root@219.146.211.36`
- 需代理时（VPN 端口 7897）：git 走 `-c http.proxy=http://127.0.0.1:7897`；SSH 本身不需代理。
- 4 线服务器输出目录（已核实）：
  - 中文QA：`/root/QA/outputs/qa_27b_v330_n100`
  - 英文QA：`/root/QA/outputs/qa_en_official500_c16`
  - 中文rewrite：`/root/rewrite/outputs/rewrite_v4_3_5_qwen3_6_27b_v5090_n500`
  - 英文rewrite：`/root/rewrite/outputs/en_thru200_c8`
- 吞吐日志：`/root/QA/log_v330_n100.log`（tqdm 末行取墙钟）、各 `outputs/*/run_stats.json`、`/root/rewrite/run_500.log`
- ⚠️ 坑：8003 是共享 GPU，不响应不要擅自重启；非交互 shell 不激活 conda，python 用 `/root/miniconda3/bin/python`。

---

## 已核查的关键事实（均已验证，可直接引用）
- **4 线成功率**：中文QA 0.99、英文QA 0.9912、中文rewrite 1.0、英文rewrite 0.9487
- **4 线真坏数据**：仅中文QA 1 条 CoT 泄漏（脏 OCR 种子 rec93），已被 L0 新规则 `_reasoning_leak_answer` 修复；服务器全量已重过滤回传、泄漏归零。其余 3 线 = 0（此前 grep 命中均为原文正常词的假阳性，已逐条核实）
- 中文QA v3.30 已于 **2026-08-07 用户拍板封板**；服务器生产 config（`/root/QA/config.yaml` + `config.v5090.yaml`）已切 v3.30
- L0 过滤单一事实来源 `QA/src/common.py`；根 `src/common.py` 为转发垫片（两者不再分叉）
- 中文QA v330 n100 真实墙钟：100 篇 / **1:36**（c16，来源 `/root/QA/log_v330_n100.log` tqdm 末行）

---

## 交付
写到 `qwen_L3/COMPREHENSIVE_REPORT.md`。中文，精简全面，**每个关键数字标注来源文件**。
