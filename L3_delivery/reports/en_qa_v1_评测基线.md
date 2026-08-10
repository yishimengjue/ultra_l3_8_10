# English QA v1 — Baseline Evaluation (A1 + D2)

> Scope: the **evaluation** of the en QA v1 baseline slice — official-en alignment
> set, absolute-quality guardrail judge, and pairwise closeness vs official en.
> Builds on `en_qa_v1_build_and_consumption.md` (the v1 build). All numbers are real
> judge outputs; the judge is `qwen3.6-35b-a3b` at temperature 0 (same as the zh
> line). Everything runs through independent en files; the zh sealed line and
> `config.yaml` are untouched.

---

## 0. Headline

- **Absolute-quality guardrails (n=50): ALL PASS.** faithfulness 5.0,
  hallucination_risk 5.0, noise_question 0, success_rate 1.0.
- **Official closeness = 3.71 (median 4)**, over a same-source paired set of 100.
  For reference the sealed **zh line closeness is 3.18** — English v1, on its first
  attempt, scores **higher than the sealed Chinese line**.
- Judge manually spot-checked 10/10 directionally correct (see §4).

## 1. Official en alignment set (A1)

- Source: `openbmb/Ultra-FineWeb-L3`, config `data/ultrafineweb_en_l3/qa`
  (columns `uid/content/style`, `style="qa"`), pulled via the same pyarrow-free
  range-read+fastparquet path as the seeds → `data/official/en/qa_raw.jsonl`
  (2000 rows).
- **Confirmed QA separator markers: `Question:` / `Answer:`** — identical to what
  `assemble_qa_content` emits for English, so the en splitter
  (`src/extract_seed_from_l3_en.py`) parses official content cleanly: **1999/2000
  split OK** (1 benign `seed_too_short`).
- Official en pair-count distribution: mode 8 (1518/1999), range 3–8 — matches
  SPEC §2; my v1's 6.46 avg sits inside it.
- Frozen alignment set: `data/eval/en/pair_eval_100.jsonl` (seed 714, 100 rows,
  sha256 `b410f884a1c46624…`) + 24 disjoint style anchors
  (`official_qa_reference_anchors.json`, seed 715).
- **Notable**: official en content itself contains "the text states / as described
  in the text" phrasing — i.e. the self-reference the v1 build report flagged is
  partly **official data behaviour**, not purely a v1 defect.

## 2. Absolute-quality judge (D2, guardrails)

`scripts/eval_l1_judge_en.py` over `outputs/qa_en_v1_n200/qa_full.jsonl`, n=50,
one rubric pass yielding all four guardrail dimensions. 50/50 judged OK.

| dimension | mean | note |
| --- | ---: | --- |
| faithfulness | **5.0** | guardrail ≥4.9 ✅ |
| hallucination_risk | **5.0** | guardrail ≥4.95 ✅ (5 = safest) |
| noise_question (count true) | **0** | guardrail ==0 ✅ |
| success_rate | **1.0** | guardrail ==1.0 ✅ (from run_stats) |
| answerability | 5.0 | |
| coverage | 4.78 | |
| self_containment | 4.9 | 45/50 perfect, 5 at score 4 |

Guardrail verdict: **all_pass = true**. The judge scored self_containment 4.9 —
higher than the build report's crude regex (2.8% question-leak) implied, because
the "the document states" framing in true/false items is mostly stylistic, not a
real external dependency (and official data does the same).

Artifacts: `outputs/qa_en_v1_n200/l1_abs_quality_en_summary.json` + per-sample
`l1_abs_quality_en.jsonl`.

## 3. Pairwise closeness judge (D2, closeness)

`scripts/eval_l1_pairwise_qa_en.py`: same-source local QA (generated from the 100
official seed_texts, `data/eval/en/baseline_qa_en_v1_records.jsonl`, 100/100 synth
OK) vs frozen official en QA, calibrated by the 24 style anchors. 99/100 judged
(1 abstained: one local record had no `Question:` marker — the judge correctly did
**not** fabricate a score).

| score | mean | median |
| --- | ---: | ---: |
| **official_closeness_score** | **3.7071** | **4** |
| pair_count_similarity | 4.13 | |
| answer_style_similarity | 3.87 | |
| focus_similarity | 3.81 | |
| question_style_similarity | 3.80 | |

closeness distribution: `{2: 3, 3: 26, 4: 67, 5: 3}` — 70% at 4–5.

Quality dims held under the pairwise view too: faithfulness 5.0,
hallucination_risk 5.0, answerability 4.99, self_containment 4.94, coverage 3.93.

### Diagnostic flags (what v1 is missing)

| flag | count/100 | reading |
| --- | ---: | --- |
| local_omits_official_focus | **57** | dominant gap — v1 misses some official focus points (coverage 3.93) |
| local_over_explains | 26 | answers longer/more explanatory than official |
| local_too_verbose | 12 | same direction |
| local_too_textbook_like | **6** | **low** — the no-hard-quota discipline worked (avoided zh v3.26's textbook trap) |
| local_noise_question | **0** | noise guard holds |

## 4. Judge human calibration (en, fresh)

The zh judge calibration does **not** transfer (plan §1.2), so the en judge was
re-calibrated: 10 samples spread across closeness 2→5, manually read against
seed + official + local QA. **10/10 directionally correct.** The judge flags
`too_template_like` / `too_fact_extraction` on genuinely repetitive low cases
(e.g. a "What does [Author] state" pattern → closeness 2) and rewards
format-matching + conciseness on high cases (a QA that truly mirrors official
format → closeness 5). No obvious high-band leniency in this sample; consistent
with the zh judge's calibrated behaviour.

## 5. Verdict — is the v1 gap prompt-fixable or model-bound?

closeness 3.71 already **beats the sealed zh 3.18**, so v1 is a strong baseline,
not a problem case. The gap to official (closeness 5) is diagnostically **coverage
/ focus + answer conciseness**, not textbook-regression:
- `local_omits_official_focus` = 57 and `over_explains`/`too_verbose` = 26/12 point
  at **prompt-addressable** levers (cover more official focus points; tighten answer
  length toward official's ultra-concise style).
- `too_textbook_like` = 6 and closeness median already 4 suggest the question-type
  mix is close; the zh lesson (don't chase closeness with hard quotas) still applies
  — a v2 should nudge coverage/conciseness softly, then re-measure, not add quotas.

This is a **data-driven v2 hypothesis**, not a committed change. Recommended next:
one v2 prompt tweak on the two levers above, re-run this exact eval, compare
closeness — same methodology as the zh line.

## 6. Positioning

The en QA line now has the **full evaluation harness** the zh line has (alignment
set + absolute guardrail judge + pairwise closeness + fresh human calibration),
and a **passing v1 baseline** (guardrails all-pass, closeness 3.71 > zh 3.18). What
remains is optional quality iteration (v2), not infrastructure.
