# rewrite 中文线 5090 本地版封板（qwen3.6-27b / v5090）

> 封板日期：2026-08-03
> 环境：5090 单机本地 vLLM（tp2），served id `qwen-math-classifier`（权重实为 Qwen3.6-27B），
> 端点 `http://127.0.0.1:8003/v1`，`max_model_len=8192`。

## 版本

- prompt：`rewrite_styles_v4_3_5_v5090_zh.yaml`（去尖括号占位符 + 禁占位符条款）
- 模型：`qwen3.6-27b`（5090 本地推理模型）
- seed：`zh_rewrite_v4_3_5_model_compare_n500_v6_review_clean_content`
- config：`config.v5090.yaml`

## 核心修复：推理模型思维链解析

- **根因**：该 served 模型是推理模型，会把 `<think>…</think>` 思维链塞进 `message.content`；
  思维链里模型自起草的 `{"content":"..."}` 占位示例被解析器正则误抓 → 3 字符坏数据。
- **`enable_thinking:false` 不可靠**：config 已设，但该 served 模型时灵时不灵（曾解释批量 75%/90% 坏的随机性）。
- **修复**：`common.py` 新增 `_strip_reasoning()`，解析前剥掉最后一个 `</think>` 之前内容；
  在 `parse_rewrite` / `parse_qa` 入口各调一次。判官 `eval_l1_rewrite_official_style.py`
  的 `extract_judge_json` 同样加此剥离（判官 27b 同样吐思维链）。
- **效果**：坏条率 **90% → 0%**。

## 生产验证结果（n=453 正式跑）

- 合成：种子池过滤后 `453` 篇（459 候选 - 6 数字守卫/低密度），产出 `1808` 条改写，`1` 条失败
- 失败原因：唯一 1 篇因原文超长（5121 输入 + 3072 输出 = 8193 > 8192 上限）被拒，非质量问题
- rewrite success rate：`1.0`
- 坏条（content < 50 字）：`0`

## style 字段

- 官方 schema 观察值为统一 `multi_style`（见 SPEC §4），但本工程按需求导出**具体风格标签**：
  `encyclopedia / textbook / blog / abstract`，各 `452` 条。
- 交付文件 `multi_style_synthetic.jsonl` 字段严格为 `{uid, content, style}`。

## 质检

### L0 格式质检（1808 条）
- 字段完整率 / style 合法率 / 非空率：均 `1.0`
- 长度：P05 `313` / P50 `645` / P95 `1412`，无 > 9000 字
- 偏短告警 `256` 条（14%）：encyclopedia 111 / textbook 61 / blog 84（abstract 走 150 阈值，0 告警）；
  属"比理想篇幅短"的质量提示，非坏数据。

### L1 AI 判官（n=20，anchor-count=4）
- 20/20 成功，均分 official_closeness `5.0` / overall `5.0` / refinement `4.9`
- **判官高分档偏宽，分数以人工复核为准**（见下）。

### 人工审核（n=30 并排 Excel）
- 多数忠实：原文事实、专名、数值保住，属正常压缩改写。
- 少数问题：个别把原文写太顺/补背景、把情绪立场洗淡、压过短丢后半段信息；少数源文本本身较乱。
- 结论：**无需再迭代 prompt**；仅 #8 #17 边缘个案。

## 资源消耗（n=453 生产跑）

- 总 token：`3,546,673`（prompt `2,700,524` + completion `846,149`）
- 平均每篇：`7829` token（4 风格合计）
- 平均每条改写：`1962` token
- 成功率：`100%`

## 吞吐基准（n=200 同口径，稳态）

| 并发 | 墙钟 | 篇/分钟 | 每篇墙钟 | 加速比 | success |
|------|------|---------|---------|--------|---------|
| 4 | 28:34 (1714s) | 7.0 | 8.57s | 1.0× | 100% |
| 8 | 14:44 (884s) | 13.6 | 4.42s | **1.94×** | 100% |

- 并发 4→8 近线性加速（1.94×，接近理论 2×），**并发 8 为当前最优**，零质量损失。
- n=10 小样本曾误判"并发无用"，因样本小于并发数、测的是单批延迟而非稳态吞吐；n=200 修正之。
- 拐点在 8~16 之间（n=10 测得 16 反慢，稳态未复测）。瓶颈主要在输出 token 解码速度。

### 生产工期估算（按并发 8，13.6 篇/分）
- 1 万篇 ≈ 12.3 小时
- 10 万篇 ≈ 5.1 天（单机连续）

## 运行命令（复现用）

```bash
# 环境
export REWRITE_API_KEY=EMPTY
export REWRITE_BASE_URL=http://127.0.0.1:8003/v1
export REWRITE_CONFIG=config.v5090.yaml

# 合成（生产并发建议 8）
python -u src/synthesize.py

# L0 质检 + Excel
python src/report_l0_excel.py

# L1 判官小样本（anchor-count 默认已固化为 4，勿用 16 否则超长）
python -u src/eval_l1_rewrite_official_style.py \
  --local-full outputs/<out_dir>/multi_style_full.jsonl --n 20

# 人工审核并排 Excel
python src/report_rewrite_audit_excel.py \
  --full outputs/<out_dir>/multi_style_full.jsonl \
  --out data/eval/v5090/rewrite_audit_n30.xlsx --n 30
```

## 结论

- 本版作为 rewrite 中文线 **5090 本地生产版**封板。
- 流水线跑通、质量达标（人工确认无需迭代 prompt）、并发 8 吞吐最优。
- 已知边界：判官 anchor-count 须 ≤ 4（8192 上限）；单篇原文超长会被拒（极少数）。
