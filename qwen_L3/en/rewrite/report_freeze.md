# 英文 rewrite 封板摘要 v3.7 - 2026-08-04

## 版本

- 范围：只做英文 L2 seed -> multi-style rewrite
- Prompt：`src/prompts/rewrite_styles_v3_7_en.yaml`
- Prompt sha256：`7ac73fd3fcff7d55818d14cd4d0145defebb75499de160662c7e9f34a9337340`
- Seed JSONL：`data/seeds/en_ultrafineweb_l2_seed_n250.jsonl`
- Seed filter：`en-soft`
- 输出目录：`outputs/en_rewrite_v3_7_l1_n50_enfilter3_2026-08-03`
- 人工审核工作簿：
  `outputs/en_manual_review_v3_7_n50_2026-08-03/en_rewrite_v3_7_n50_manual_review.xlsx`
- L1 judge model（来自汇总文件记录）：`qwen3.6-35b-a3b`
- 合成模型没有写进 `run_stats.json`，这里不做推断。

## 生产运行

- Seed scanned: 103
- Seed kept: 50
- Seed dropped by quality filter: 53
- Rewrite attempts: 200
- Rewrite OK: 200
- Rewrite failures: 0
- Rewrite success rate: 100.0%

## 官方输出 schema

`multi_style_synthetic.jsonl` 是官方导出，只包含：

```json
{"uid": "...", "content": "...", "style": "multi_style"}
```

L0 结果：

- Official rows: 200
- Official schema exact rate: 100.0%
- Style valid rate: 100.0%
- Nonempty rate: 100.0%
- Underscore field leak count: 0
- QA marker hit count: 0
- Internal style marker hit count: 0
- HTML artifact hit count: 0
- Encoding artifact hit count: 0
- Length warnings: 1

## 人工审核

v3.7 工作簿中的人工结果：

- Review rows: 200
- Pass: 198
- Check: 2
- Fail: 0
- Blank verdict: 0
- Average human score: 4.99
- Check rows: 69 and 178

人工结论：

- 忠实性总体稳定
- 没有 Fail
- 1 行存在事实关联问题
- 1 行的 textbook 风格重组偏弱
- static-hit 行已人工复核为误报
- L0 没有发现 QA marker、HTML artifact 或 encoding artifact

## L1 judge

- L1 judged rows: 200
- L1 success: 200
- L1 failure: 0
- Overall score mean: 4.98
- Faithfulness score mean: 4.995
- Style strategy fit mean: 4.945
- Adds unseen facts: 0
- Drops key information: 1
- Internal style leaks: 0
- QA marker leaks: 0
- Web noise: 0
- Markdown/template flags: 0
- Unsupported numeric changes: 0

## 资源消耗

合成资源消耗：

- Total tokens: 1,177,298
- Prompt tokens: 373,458
- Completion tokens: 803,840
- Average tokens per rewrite: 5,886.49
- Tokens per 100 rewrite: 588,649.0
- Wall seconds: 3,003.103
- Mean request seconds: 59.6544
- Max request seconds: 106.467
- CPU seconds: 2.5469
- API attempts: 200

L1 judge 资源消耗：

- Total tokens: 736,805
- Prompt tokens: 295,475
- Completion tokens: 441,330
- Average tokens per judge: 3,684.025
- Tokens per 100 judge: 368,402.5
- Mean request seconds: 17.1108
- Max request seconds: 27.3613
- API attempts: 200

## 决策

英文 rewrite 当前冻结候选定为 v3.7。

v3.8 暂不封板。它补了 list/catalog 护栏，但 n50 的 L1 出现 blog 回归
（`adds_unseen_facts: 1`、`drops_key_information: 1`），而且人工审核表还是空的。
