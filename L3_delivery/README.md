# Ultra-FineWeb-L3 精炼层 · 四条线封板交付包（L3_delivery）

日期：2026-08-05
本包汇总 Ultra-FineWeb-L3 精炼层（QA 生成 + 多风格改写）四条线的**封板产物**，供上传服务器生产 / 公司代码仓库。
铁律：本包所有评测数字均来自真实评测输出文件，无编造。

---

## 一、四条线封板总览

| 线 | 封板 prompt | 接近官方 | 核心质量（判官 1-5，人工校准偏宽） |
|----|------------|---------|-----------------------------------|
| 中文 QA | `qa_gen_v3_30_forcetype_zh` | closeness **2.76**（DMX 判官） | 忠实 4.73 / 可答 4.60 / 自足 3.65 / 覆盖 3.24 |
| 英文 QA | `qa_gen_en_v1`（hardened） | closeness **3.76**（35b 判官） | 忠实 5.0 / 可答 5.0 / 自足 4.99 / 覆盖 3.98 |
| 中文 rewrite | `rewrite_styles_v4.3.5-v5090` | style_closeness **4.62** | 忠实 4.87 / overall 4.74 / 信息保留 4.90 |
| 英文 rewrite | `rewrite_styles_v3.7` | 人工 **4.99**（198/200 pass） | 忠实 5.0 / overall 4.98 |

> **口径说明**：QA 与 rewrite 评分体系不同，不可直接比大小。**中文 QA v3.30 用 DMX `claude-sonnet-4-6` 判官（closeness 2.76）；其余三线用 `qwen3.6-35b-a3b` 判官——两 QA 线的 closeness 判官不同，横比仅供参考。** 35b 判官人工校准过、偏宽，读作"无红旗"。中文 QA 相对旧 v3.28 的真实提升取同判官（DMX）同种子配对：closeness 2.49→2.76（+0.27）。

---

## 二、目录结构

```
L3_delivery/
├── README.md                 # 本文件
├── prompts/                  # 四条线封板 prompt（文件名后缀标注 SEALED）
│   ├── qa_gen_v3_30_forcetype_zh__ZH_QA_SEALED.txt
│   ├── qa_gen_en_v1__EN_QA_SEALED.txt
│   ├── rewrite_styles_v4_3_5_v5090_zh__ZH_REWRITE_SEALED.yaml
│   └── rewrite_styles_v3_7_en__EN_REWRITE_SEALED.yaml
├── code/                     # 完整可跑核心代码（common.py 为修复版，见下方⚠）
│   ├── common.py             # LLM 客户端 + schema + 解析（含 extra_body 关思维链）
│   ├── synthesize.py         # 合成主程序（QA + rewrite）
│   ├── eval_l0.py            # L0 结构/schema 质检
│   ├── eval_l1_judge.py      # L1 单文档质量判官
│   ├── eval_l1_pairwise_qa.py        # QA 官方 pairwise 判官逻辑
│   ├── run_pairwise_official500.py   # QA 官方同源 pairwise 判官（可参数化）
│   ├── eval_l1_rewrite_official_style.py  # rewrite official_style 判官
│   ├── run_rewrite_only_experiment.py     # rewrite-only 实验/生产脚本（含 extra_body 修复）
│   ├── extract_seed_from_l3.py / _en.py   # 种子/QA 对切分
│   └── filter_rewrite_seed_numeric_guard.py  # 种子数值护栏（synthesize 依赖）
├── configs/                  # 四条线封板生产 config（paths 指向 prompts/）
│   ├── config_zh_qa__SEALED_v3.30.yaml
│   ├── config_en_qa__SEALED_v1hardened.yaml
│   ├── config_zh_rewrite__SEALED_v5090.yaml
│   └── config_en_rewrite__SEALED_v3.7.yaml
├── reports/                  # 封板结论 + 吞吐报告
│   ├── 00_四线最终质量评分总览.md
│   ├── 01_四线吞吐总报告.md
│   └── （各线封板/决策报告）
├── eval_summaries/           # 四条线评测 summary json（真实数字）
├── samples/
│   ├── L3_四线封板样例汇总.xlsx   # 总览 sheet + 4 线各 30 条样例（原文+产出）
│   └── build_samples_xlsx.py     # 生成脚本
└── official_ref/             # 官方参考数据（评测依赖：zh/en profile + anchors + QA ref）
```

---

## 三、⚠ 关键：思维链 bug 修复（务必了解）

本包 `code/common.py` 是**修复版**，包含 `extra_body` 透传逻辑（`chat_template_kwargs:{enable_thinking:false}`）。

- **背景**：5090 本地模型（Qwen3.6-27B）是**推理模型**，默认吐 `<think>` 思维链。若不关，思维链会吃满 `max_tokens`，导致改写正文被截断成残句。
- **修复点**：
  1. `common.py` 的 LLM 类读取并透传 `extra_body`（旧版本不读，是死配置）。
  2. `run_rewrite_only_experiment.py` 的 `build_kwargs()` 补传 `extra_body`（此前漏传，导致 rewrite 本地实验被思维链污染 73% 样本）。
- **上传服务器时**：确保用本包这两个文件覆盖旧版；`config` 的 `extra_body` 段必须保留。
- **验证方法**：合成后检查输出 `_finish_reason` 应为 `stop`、`_completion_tokens` 为几百而非撞 `max_tokens`、content 无 `<think>` 残留。

> **中文 QA v3.30 追加的数据卫生（已在本包 code 内）**：
> - `common.py` L0 后过滤：删露怯元话语答案（`reasoning_leak_answer`）、修复选项错位到答案字段的选择题、多选答案保护。
> - `synthesize.py` 种子层：`_ocr_garble_reason` 拦截整篇中文 OCR 断裂的垃圾源文（连续异常标点 ≥1.5/百字），合成前丢弃，500 条种子池命中 1 条零误伤。
> - 效果：CLEAN 生产产出四类脏数据（露怯 / 选项错位 / `<think>` / OCR 垃圾源文）全部归零。

---

## 四、生产运行方式（每条线）

从含 `prompts/`、`code/`、`configs/`、`data/seeds/` 的目录根运行（需先把 `code/` 目录改名为 `src/`、准备种子）：

**⚠ 部署前两个必查项（均由交付冒烟实测发现）：**
1. **config 必须同时含 qa + rewrite 两个 prompt 路径（同语言）**：`synthesize.py` 无条件加载两者，即使 `do_qa=false` 或 `do_rewrite=false`。本包 4 个 config 已补全，勿删。
2. **种子必须有 `content`（或 `text`）字段**：`load_seed` 只读 `content`/`text`，**不读 `_source_text`**。若你的种子正文在 `_source_text` 字段（如 official 种子），需先转换：
   ```python
   # 每行 {"_source_text": "..."} → {"content": "...", "_source_text": "..."}
   ```
   否则合成会"取到 0 篇文档"、成功率 0。
3. **config 用环境变量切换**：`REWRITE_CONFIG=configs/config_zh_qa__SEALED_v3.30.yaml python src/synthesize.py`。

```bash
# 环境变量
export REWRITE_API_KEY=EMPTY REWRITE_BASE_URL=http://127.0.0.1:8003/v1

# 合成（REWRITE_CONFIG 指定 config，否则默认 config.yaml）
REWRITE_CONFIG=configs/config_zh_qa__SEALED_v3.30.yaml python src/synthesize.py

# 评测
python eval_l0.py                                    # L0 结构质检
python run_pairwise_official500.py --local <out>/qa_full.jsonl --n 30 --concurrency 2   # QA 官方对比（判官必须 2 并发）
python eval_l1_rewrite_official_style.py --local-full <out>/multi_style_full.jsonl      # rewrite official_style
```

**吞吐甜点（5090 实测，见吞吐报告）**：QA 并发 16（中文 v3.30 c16 实测 **85.7 篇/min**、英文 62 篇/min）；rewrite 并发 8（zh 13.6 篇/min、en 10.33 篇/min）。判官必须 2 并发（高并发下 8192 显存竞争会截断 JSON）；rewrite 判官 anchor 默认 4（16 个会撑爆 8192 输入）。

---

## 五、吞吐（5090 本地 vLLM 实测，墙钟）

| 语言 | 线 | 封板版本 | 并发 | 墙钟吞吐 | 成功率 | 坏数据 |
|------|-----|---------|------|---------|--------|--------|
| 中文 | QA | v3.30 | 16 | **85.7 篇/分** | 0.985 | 0 |
| 英文 | QA | v1-hardened | 16 | **62.0 篇/分** | 0.991 | 0 |
| 中文 | rewrite | v4.3.5-v5090 | 8 | **13.6 篇/分**（54.3 调用/分） | 1.0 | 0 |
| 英文 | rewrite | v3.7 | 8 | **10.33 篇/分**（+prefix cache 11.31） | 0.95 | 0 |

- **并发甜点**：QA token 省、拐点靠后（c16 最优）；rewrite 每篇 4 次调用、token 重、拐点靠前（c8 最优）。两类任务生产并发不通用：**QA=16、rewrite=8**。
- 注：`run_stats.elapsed_seconds` 是并发累加非墙钟，真实墙钟取 tqdm。详见 `reports/01_四线吞吐总报告.md`。

---

## 六、样例展示（四条线各一例，均取自封板产出，未改动）

### 6.1 中文 QA（v3.30，判官 closeness=3 / 忠实=5）
脑卒中科普原文，展示「强制题型」效果：判断/选择/原因/事实题俱全，全部忠实可原文定位。

**原文（节选）**：看 1 张脸是否对称、有无眼斜嘴歪；2 看手臂是否单侧无力；0 代表聆听有无言语不清。如果有，就要怀疑是不是中风……脑出血恢复极为缓慢……预防工作显得格外重要——高血压患者一定要遵医嘱按时服药……饮食上低盐、低脂……

| 题型 | 问题 | 答案 |
|------|------|------|
| 判断题 | 判断以下说法是否正确：发现有人眼斜嘴歪或单侧手臂无力时，应立即拨打120并怀疑可能是中风。 | 正确。文档指出看脸是否对称、有无眼斜嘴歪，看手臂是否单侧无力，有这些症状就要怀疑中风并立刻拨打120。 |
| 选择题 | 下列哪项不属于文档提到的脑出血预防措施？A. 多食粗纤维食物 B. 戒烟戒酒 C. 换季注意保暖防暑 D. 每日高强度运动 | D。文档提到多食粗纤维、戒烟戒酒、换季保暖防暑等，未提每日高强度运动，反而要避免过度劳累。 |
| 原因题 | 为什么文档强调脑出血的预防工作显得格外重要？ | 因为脑出血对脑神经造成损害、恢复极为缓慢且严重时无法再生，救治漫长痛苦、花费巨大，对家庭和社会都是沉重负担。 |

### 6.2 英文 QA（v1-hardened，忠实/无幻觉近满分）
科技论文原文，问答自足、答案可逐句定位。

**Source（节选）**：The membrane used in Guerra's experiment, known as a mixed ionic-electronic conduction (MIEC) membrane, is developmentally at its "very first steps" in being coupled with plasma…

| Question | Answer |
|------|------|
| What is the technical name of the membrane used in Guerra's experiment? | Mixed ionic-electronic conduction (MIEC) membrane |
| How does the paper describe the developmental stage of coupling the MIEC membrane with plasma? | It is at its 'very first steps' |
| What challenges to the use of MIEC membranes are listed by the researchers? | Stability in CO2 and the possibility of back reactions that can lessen the membrane's effectiveness |

### 6.3 中文 rewrite（v4.3.5-v5090，风格=encyclopedia）
去网页口语、重组为客观连贯正文；0 编造、0 漏关键信息。

**原文（节选）**：因为疫情的蔓延，户外活动次数的骤减，老师们只好绞尽脑汁……当我把整只白白、素面的风筝发下去……我先播放如何折叠风筝的教学影片给小朋友看，大声强调，仔细说明，木棍要重叠……

**改写（encyclopedia 风格）**：受疫情蔓延导致户外活动减少的影响，教师通过增加课堂丰富性，组织学生制作并绘制可翱翔天空的风筝。在分发空白素面风筝后，教师担心学生因无法正确收合支撑木棍而导致风筝无法飞行，因此播放折叠教学影片，强调木棍需重叠摆放，并指导学生进行多次操作练习……

### 6.4 英文 rewrite（v3.7，风格=blog）
清洗网页碎片、保留全部事实，重组为自然博客文体；忠实近满分。

**Source（节选）**：The EPA's Multispecies Care Survey is featured in a new online exhibition organized for Broto's Art-Climate-Science annual conference which provides an online c…

**Rewrite（blog 风格）**：The EPA's Multispecies Care Survey is currently featured in a new online exhibition titled "Agency." The showcase was organized for Broto's Art-Climate-Science annual conference, which serves as an online c…

> 更多样例：四线各 30 条汇总见 `samples/L3_四线封板样例汇总.xlsx`；中文 QA 四方对照（原文+官方+v3.30+v3.28/v3.31）见 `qwen_L3/zh/qa/compare_*.md`；中文 QA 全量人工可读表见 `qwen_L3/zh/qa/qa_zh_v330_交付数据_人工可读.xlsx`。

---

## 七、schema 不变量（官方对齐）

- 官方导出 schema：`{uid, content, style}`。
- QA：`style="qa"`，`content` = 原文 + 问答对（**原文在前**）。
- rewrite：官方 `style="multi_style"`（本工程内部 `_internal_style` 导出具体风格 encyclopedia/textbook/blog/abstract）。
- 忠实性铁律：不得引入原文没有的事实、数值、人物、机构、因果、结论。

---

## 八、样例表说明（samples/L3_四线封板样例汇总.xlsx）

- **总览 sheet**：四条线封板 prompt + closeness + 质量分对照。
- **4 个线 sheet**：每线 30 条样例，含 `原文(source)` 与 `产出(content)` 并列；rewrite 样例跨 4 风格均匀分布。
- 样例取自各线封板产出目录，未经改动。
