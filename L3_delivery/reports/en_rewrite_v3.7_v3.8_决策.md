# 英文 rewrite v3.7 / v3.8 决策记录 - 2026-08-04

## 范围

这份记录只覆盖英文 L2 seed -> multi-style rewrite 合成路径。
不复现 L1 清洗，也不复现 L2 分类器。官方导出 schema 仍然是
`{uid, content, style}`，英文官方 `style` 仍为 `multi_style`。

## v3.7 人工审核结果

来源工作簿：
`outputs/en_manual_review_v3_7_n50_2026-08-03/en_rewrite_v3_7_n50_manual_review.xlsx`

工作簿中的人工审核汇总：

- Review rows: 200
- Pass: 198
- Check: 2
- Fail: 0
- Blank verdict: 0
- Average human score: 4.99
- Check rows: 69 and 178
- 人工结论：整体稳定，不需要为忠实性或 schema 再改 prompt；若要继续提质，可考虑补短列表类样本的风格区分。

v3.7 的 L0 / schema 汇总：

- Official rows: 200
- Official schema exact rate: 100.0%
- Style valid rate: 100.0%
- Nonempty rate: 100.0%
- Underscore field leak count: 0
- QA marker hit count: 0
- HTML artifact hit count: 0
- Encoding artifact hit count: 0
- Length warnings: 1

v3.7 合成资源消耗：

- Seed docs: 50
- Rewrite attempts / ok / failure: 200 / 200 / 0
- Rewrite success rate: 100.0%
- Total tokens: 1,177,298
- Prompt tokens: 373,458
- Completion tokens: 803,840
- Tokens per 100 rewrite: 588,649.0
- Wall seconds: 3,003.103
- Mean request seconds: 59.6544
- Max request seconds: 106.467
- CPU seconds: 2.5469
- API attempts: 200

v3.7 L1 judge 汇总：

- L1 success / failure: 200 / 0
- L1 overall mean: 4.98
- L1 faithfulness mean: 4.995
- L1 drops_key_information: 1
- L1 adds_unseen_facts: 0

## v3.8 状态

v3.8 只是在 v3.7 基础上加了一个很窄的系统护栏：

- 对 list / catalog / course outline / resource index 这类 source，保持每个条目的事实都挂在各自条目上。
- 不要把相邻条目合并，也不要把日期、slide 范围、module label、resource note 或 source-specific metadata 从一个条目挪到另一个条目，除非原文明确连着。

定向回归 n=2 的 L1 通过了，8/8 成功，且没有 boolean quality flag。
但 n50 跑出来后，blog 行出现了回归：

- Rewrite success / failure: 200 / 0
- L1 overall mean: 4.965
- L1 faithfulness mean: 4.975
- L1 adds_unseen_facts: 1
- L1 drops_key_information: 1
- 问题集中在 blog style
- v3.8 的人工审核工作簿目前还是空的：0 Pass / 0 Check / 0 Fail / 200 blank verdicts

v3.8 合成资源消耗：

- Total tokens: 1,190,817
- Prompt tokens: 389,458
- Completion tokens: 801,359
- Tokens per 100 rewrite: 595,408.5
- Wall seconds: 3,141.9906
- Mean request seconds: 62.3562
- Max request seconds: 151.444
- CPU seconds: 2.4531
- API attempts: 200

## 决策

英文 rewrite 当前冻结候选仍然是 v3.7。

v3.8 暂不封板。它确实补到了一个有用的 list/catalog 护栏，但 n50 的 L1
比 v3.7 更差，而且人工审核还没填，所以先作为实验候选保留。

## 下一步

整理 v3.7 封板包/总结时，优先使用：

- `src/prompts/rewrite_styles_v3_7_en.yaml`
- `outputs/en_rewrite_v3_7_l1_n50_enfilter3_2026-08-03`
- `outputs/en_manual_review_v3_7_n50_2026-08-03/en_rewrite_v3_7_n50_manual_review.xlsx`

如果后面还想保留 list/catalog 护栏，应该在 v3.7 之后新开版本再做定向回归，不要直接改 v3.7。
