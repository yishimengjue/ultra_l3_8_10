# Ultra-FineWeb-L3 精炼层 · 四条线封板交付包（L3_delivery）

日期：2026-08-05
本包汇总 Ultra-FineWeb-L3 精炼层（QA 生成 + 多风格改写）四条线的**封板产物**，供上传服务器生产 / 公司代码仓库。
铁律：本包所有评测数字均来自真实评测输出文件，无编造。

---

## 一、四条线封板总览

| 线 | 封板 prompt | 接近官方 | 核心质量（判官 1-5，人工校准偏宽） |
|----|------------|---------|-----------------------------------|
| 中文 QA | `qa_gen_v3_35_trim_zh`（取代 v3.30） | closeness **2.847**（DMX 判官） | 忠实 4.85 / 可答 4.70 / 自足 3.76 / 覆盖 3.25 |
| 英文 QA | `qa_gen_en_v1`（hardened） | closeness **3.76**（35b 判官） | 忠实 5.0 / 可答 5.0 / 自足 4.99 / 覆盖 3.98 |
| 中文 rewrite | `rewrite_styles_v4.3.5-v5090` | style_closeness **4.62** | 忠实 4.87 / overall 4.74 / 信息保留 4.90 |
| 英文 rewrite | `rewrite_styles_v3.7` | 人工 **4.99**（198/200 pass） | 忠实 5.0 / overall 4.98 |

> **口径说明**：QA 与 rewrite 评分体系不同，不可直接比大小。**中文 QA v3.35 用 DMX `claude-sonnet-4-6` 判官（closeness 2.847）；其余三线用 `qwen3.6-35b-a3b` 判官——两 QA 线的 closeness 判官不同，横比仅供参考。** 35b 判官人工校准过、偏宽，读作"无红旗"。
>
> **中文 QA 封板演进（v3.30 → v3.35，均 DMX 同判官同种子配对）**：v3.30（强制题型，2.758）为 2026-08-07 首次封板；v3.32 加"事实题层次化"软提示提到 **2.847**（faith 4.73→4.83，部分修正"prompt 已到边际"旧论）；**v3.35** 在 v3.32 上删 5 行答案格式复述冗余，judge 全维度持平/微升、faith **4.847**（全场最高）、更精简，**2026-08-11 取代 v3.30 封板**。其后 v3.33/34/36 三轮（求短/为主/催深度题）均证伪，prompt 层确认到顶。

---

## 二、目录结构

```
L3_delivery/
├── README.md                 # 本文件
├── requirements.txt          # Python 依赖（openai/pydantic/pyyaml/tqdm）
├── prompts/                  # 四条线封板 prompt（文件名后缀标注 SEALED）
│   ├── qa_gen_v3_35_trim_zh__ZH_QA_SEALED.txt   # 中文 QA 封板（取代 v3_30，后者保留作留痕）
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
│   ├── config_zh_qa__SEALED_v3.35.yaml    # 当前封板（取代 v3.30，后者保留作留痕）
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

> **中文 QA 数据卫生（v3.30 起引入，v3.35 沿用，均在本包 code 内）**：
> - `common.py` L0 后过滤：删露怯元话语答案（`reasoning_leak_answer`）、修复选项错位到答案字段的选择题、多选答案保护。
> - `synthesize.py` 种子层：`_ocr_garble_reason` 拦截整篇中文 OCR 断裂的垃圾源文（连续异常标点 ≥1.5/百字），合成前丢弃，500 条种子池命中 1 条零误伤。
> - 效果：CLEAN 生产产出四类脏数据（露怯 / 选项错位 / `<think>` / OCR 垃圾源文）全部归零。

---

## 四、生产运行方式（从零部署，手把手）

> 目标：拿到本包后，在一台**云服务器或本地机器**上跑通中文 QA 合成。其余三条线把 config 换掉即可，命令一致。

### 第 1 步 · 摆好目录（关键，别跳过）

代码通过「脚本所在目录的**上一级**」定位所有相对路径（`common.py: REPO_ROOT = 脚本目录的上一级`）。所以**必须把 `code/` 目录改名成 `src/`**，最终目录长这样：

```
你的工作目录/                     ← 在这里跑命令（cd 到这里）
├── src/                          ← 由本包 code/ 改名而来
│   ├── synthesize.py
│   ├── common.py
│   └── ...（其余 .py）
├── prompts/                      ← 本包 prompts/
├── configs/                      ← 本包 configs/
├── data/
│   └── seeds/
│       └── my_seed.jsonl         ← 你自己准备的种子（见第 3 步）
└── requirements.txt
```

```bash
# 从本包根目录执行
mv code src                       # ← 这一步漏了会 import 失败
mkdir -p data/seeds
```

### 第 2 步 · 装依赖（Python ≥ 3.9）

```bash
pip install -r requirements.txt
```

### 第 3 步 · 准备种子

种子是一个 jsonl，**每行一篇原文**，正文放在 `content`（或 `_source_text` / `text`）字段之一即可——`load_seed` 按 `_source_text → content → text` 顺序取第一个非空的。最小样例：

```jsonl
{"content": "第一篇原文正文……（≥500字符，否则被 min_chars 过滤）"}
{"content": "第二篇原文正文……"}
```

把它存成 `data/seeds/my_seed.jsonl`，再到 config 里把 `seed.local_path` 指向它、`n_docs` 设成你的篇数。

### 第 4 步 · 配 config（区分「云 API」和「本地 vLLM」两种）

打开 `configs/config_zh_qa__SEALED_v3.35.yaml`，改 `llm.base_url` 和种子路径：

**A. 云服务器 / 云 API（如阿里云百炼，OpenAI 兼容）**
```yaml
llm:
  base_url: "https://<你的云端OpenAI兼容地址>/v1"
  api_key_env: "DASHSCOPE_API_KEY"        # 从环境变量读 key
  synth_model: "qwen3.6-27b"
  extra_body: {chat_template_kwargs: {enable_thinking: false}}   # 关思维链，勿删
seed:
  local_path: "data/seeds/my_seed.jsonl"
  n_docs: 100
```
```bash
export DASHSCOPE_API_KEY=sk-你的真实key       # 换成真 key
```

**B. 本地 vLLM（如 5090，端口 8003）**
```yaml
llm:
  base_url: "http://127.0.0.1:8003/v1"
  synth_model: "qwen-math-classifier"      # vLLM 的 served-model-name
  extra_body: {chat_template_kwargs: {enable_thinking: false}}
```
```bash
export REWRITE_API_KEY=EMPTY REWRITE_BASE_URL=http://127.0.0.1:8003/v1
```

> ⚠ **两个必查项（漏了合成必失败）：**
> 1. config 必须**同时**含 qa + rewrite 两个 prompt 路径（同语言）——`synthesize.py` 无条件加载两者，即使 `do_rewrite=false`。本包 config 已配好，勿删。
> 2. `extra_body` 关思维链那段必须保留——27b 是推理模型，不关会把 `<think>` 塞进正文、JSON 解析失败。

### 第 5 步 · 跑合成

```bash
# REWRITE_CONFIG 指定用哪个 config（相对当前目录）
REWRITE_CONFIG=configs/config_zh_qa__SEALED_v3.35.yaml python src/synthesize.py
```
产出落在 config 的 `out_dir`（默认 `outputs/zh_qa_prod/`）：
- `qa_full.jsonl` — 完整产出（含原文+QA，人工审用）
- `qa_synthetic.jsonl` — 官方 schema `{uid, content, style}`（交付/训练用）
- `run_stats.json` — 成功率、token、耗时

**验证成功**：看 `run_stats.json` 的 `qa_success_rate` 应接近 1.0；`qa_full.jsonl` 里 content 无 `<think>` 残留。

**控制合成规模（改 config 的 `seed` / `synthesis` 段）**：
| 参数 | 作用 | 建议 |
|---|---|---|
| `seed.n_docs` | **合成多少篇**（从种子文件取前 N 篇符合条件的） | 先设 3-5 跑通，再放大到几百/几千 |
| `seed.min_chars` | 原文太短则丢弃（默认 500） | 太短的原文出不了好 QA，一般不用改 |
| `seed.max_chars` | 原文超长则截断（默认 8000） | 受模型 max_model_len 限制，本地 4096 上下文要调小 |
| `synthesis.concurrency` | 并发数（几篇同时合成） | 云 API 建议 4-8；本地 5090 甜点 16 |
| `synthesis.qa_min_pairs` / `qa_max_pairs` | 每篇出几组 QA（默认 3-8） | 一般不改 |

> 例：想合成 500 篇、云 API 并发 8 —— 把 `n_docs: 500`、`concurrency: 8`，种子文件里至少要有 500 篇达到 `min_chars` 的原文。`n_docs` 超过种子实际篇数时，以种子实际数为准。

### 第 6 步 · 评测（可选）

```bash
# 评测脚本也要用 REWRITE_CONFIG 指定同一个 config（否则默认找 config.yaml 会报错）
REWRITE_CONFIG=configs/config_zh_qa__SEALED_v3.35.yaml python src/eval_l0.py     # L0 结构/schema 质检
python src/run_pairwise_official500.py --local outputs/zh_qa_prod/qa_full.jsonl --n 30 --concurrency 2   # 官方对比（判官必须 2 并发）
```

**吞吐甜点（5090 实测，见吞吐报告）**：QA 并发 16（中文 v3.30 c16 实测 **85.7 篇/min**、英文 62 篇/min）；rewrite 并发 8（zh 13.6 篇/min、en 10.33 篇/min）。判官必须 2 并发（高并发下 8192 显存竞争会截断 JSON）；rewrite 判官 anchor 默认 4（16 个会撑爆 8192 输入）。
> 注：85.7 篇/min 为 v3.30 在 5090 实测；v3.35 已在 5090/8003 同部署复验（2026-08-12，n=100）——成功率 100%、0 思维链泄漏、同部署配对 closeness 净升 +0.040（vs v3.30），5090 本地生产可用。

---

## 五、吞吐（5090 本地 vLLM 实测，墙钟）

| 语言 | 线 | 封板版本 | 并发 | 墙钟吞吐 | 成功率 | 坏数据 |
|------|-----|---------|------|---------|--------|--------|
| 中文 | QA | v3.30→v3.35 | 16 | **85.7 篇/分**（v3.30 实测；v3.35 已5090复验、成功率100%/0泄漏） | 1.0 | 0 |
| 英文 | QA | v1-hardened | 16 | **62.0 篇/分** | 0.991 | 0 |
| 中文 | rewrite | v4.3.5-v5090 | 8 | **13.6 篇/分**（54.3 调用/分） | 1.0 | 0 |
| 英文 | rewrite | v3.7 | 8 | **10.33 篇/分**（+prefix cache 11.31） | 0.95 | 0 |

- **并发甜点**：QA token 省、拐点靠后（c16 最优）；rewrite 每篇 4 次调用、token 重、拐点靠前（c8 最优）。两类任务生产并发不通用：**QA=16、rewrite=8**。
- 注：`run_stats.elapsed_seconds` 是并发累加非墙钟，真实墙钟取 tqdm。详见 `reports/01_四线吞吐总报告.md`。

---

## 六、样例展示（四条线各一例，均取自封板产出，未改动）

### 6.1 中文 QA（判官 closeness=3 / 忠实=5）
脑卒中科普原文，展示「强制题型」效果：判断/选择/原因/事实题俱全，全部忠实可原文定位。（此样例取自 v3.30 产出；v3.35 沿用同一套强制题型策略，题型结构一致。v3.35 与官方/v3.32 的逐篇四方对照见 `samples/` 下对比表。）

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

> 更多样例：四线各 30 条汇总见 `samples/L3_四线封板样例汇总.xlsx`；**中文 QA 封板 v3.35 与官方/v3.32 四方对照（原文+官方+v3.32+v3.35）见 `samples/对比样例_v332_vs_v335.xlsx`**；早期 v3.30 四方对照见 `qwen_L3/zh/qa/compare_*.md`。

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
