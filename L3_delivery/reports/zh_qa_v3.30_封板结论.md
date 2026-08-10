# 中文 QA v3.30 封板结论（强制题型 + L0 加固 + OCR 种子过滤）

封板日期：2026-08-07（prompt 拍板）｜ 数据卫生补强：2026-08-10
判官：DMX `claude-sonnet-4-6`（盲评 pairwise）｜ 合成模型：`qwen3.6-27b`（未变）
铁律遵守：数字均真实运行所得，无编造。

> **判官口径说明**：本版全部成绩用 DMX `claude-sonnet-4-6`。旧 v3.28 封板成绩 closeness 2.80 用的是
> 冻结判官 `qwen3.6-35b-a3b`，**两判官口径不同，2.76 与 2.80 不可直接比大小**。v3.30 相对 v3.28 的
> 真实提升取**同判官（DMX）同种子配对**：见下表。

## 一、v3.30 相对 v3.28 的提升（99 篇官方同源严格配对，DMX 判官全覆盖）

| 指标 | v3.28 | v3.30 | Δ |
|---|---|---|---|
| **official_closeness** | 2.485 | **2.758** | **+0.273** |
| question_style_similarity | 2.552 | 2.716 | +0.164 |
| focus_similarity | 2.747 | 2.939 | +0.192 |
| coverage | 3.172 | 3.242 | +0.070 |
| faithfulness | 4.848 | 4.727 | −0.121 |
| hallucination_risk | 4.778 | 4.667 | −0.111 |

- **题型多样 +0.963、答案质量 +0.407**（无参考质量判官，thru30 同种子配对）。
- **faithfulness −0.121 定性**：逐条核查 99 篇 faith<5 的 23 篇，判官备注**几乎全是"纯事实抽取/不像官方
  风格/判断题不像官方"这类 style 意见，真编造≈0**；即判官把强制题型的风格偏离计入了 faith，非质量回退。
  同批"过滤前 vs 过滤后"对照显示 L0 过滤本身抬升 faith +0.042。

## 二、v3.30 做了什么（相对 v3.28）

**变量1 · prompt**（`qa_gen_v3_30_forcetype_zh.txt`，不改 v3.28）：**强制题型**——每篇在证据允许时应含
至少 1 判断题 + 1 选择题 + 1 原因/推算题，但**忠实优先级高于题型配额**（信息不足就少出、不编造）。
把纯事实抽取从 78% 降到 63%、选择题从 1% 提到 12%。

**变量2 · L0 代码后处理**（`common.py`，逐对审查删坏数据，从不补造）：
- 露怯过滤 `reasoning_leak_answer`：删答案里混入的出题自我纠结/元话语。
- 选项错位修复 `_repair_misplaced_choice_options`：把错位到答案的选项块搬回题干（修复非删）。
- 多选保护：`C、D` 类答案不被误截。

**变量3 · 种子层 OCR 过滤**（`synthesize.py`，2026-08-10 补）：`_ocr_garble_reason` 拦截整篇中文 OCR
断裂的垃圾源文（连续异常标点密度 ≥1.5/百字），合成前丢弃。500 条种子池命中 1 条零误伤。

## 三、坏数据（100 条 CLEAN 生产产出）

用最终版代码重跑，四类脏数据**全部归零**：
- 种子层丢弃 1 篇（OCR 垃圾源文），`filter_scanned:100 → kept:99`，success_rate 1.0。
- 存活 617 对：露怯残留 0、选项错位残留 0、`<think>` 残留 0、OCR 垃圾源文残留 0。

## 四、吞吐（5090 本地 c16）

**85.7 篇/min**（`log_thru200_v330` 实测墙钟 2:20/200 篇），0 think 残留，success 0.985。
（注：`run_stats.elapsed_seconds` 是并发累加非墙钟，真实墙钟取 tqdm）。

## 五、结论

1. **v3.30 全面优于 v3.28**：closeness +0.273（大样本配对）、题型多样 +0.963、答案质量 +0.407、
   坏数据归零、吞吐反升。faithfulness 名义 −0.12 经核查为判官 style 计入、真编造≈0。
2. **closeness 瓶颈是模型天花板，非 prompt**（三重证明）：v3.26/v3.27 放开展开度→教材化回归；
   v3.31 去标签+改句式→指令全执行仍不涨分；同 prompt 下 opus-4-8 合成 closeness 3.0 vs 27b 2.76。
   要再动 closeness 只能换更强合成模型，prompt 已到边际。
3. **v3.30 为封板版**，生产 config 已切；数据卫生补强后产出干净、可进训练集。

## 产物
- prompt：`prompts/qa_gen_v3_30_forcetype_zh__ZH_QA_SEALED.txt`
- config：`configs/config_zh_qa__SEALED_v3.30.yaml`
- 代码：`code/common.py`（L0 全加固）+ `code/synthesize.py`（OCR 种子过滤）
- 判官摘要：`eval_summaries/zh_qa_v3.30_pairwise_summary.json`（DMX，n=99）
- CLEAN 交付数据 + 人工可读 Excel + 四方对照样例：见 `qwen_L3/zh/qa/`
