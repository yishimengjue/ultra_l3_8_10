"""阶段 E · L1 LLM-as-judge 合成质量评测。
从配置中的 out_dir/*_full.jsonl 抽样，用裁判模型按 rubric 打分，聚合。
用法：python src/eval_l1_judge.py --n 50
输出：eval_dir/l1_qa.jsonl, l1_rewrite.jsonl, l1_summary.json
"""
import sys
import json
import random
import argparse
from pathlib import Path
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

QA_SYS = (
    "You are a strict data-quality judge. You are given a SOURCE document and a set "
    "of generated question-answer pairs. Score them per the rubric. Output ONLY JSON: "
    '{"scores": {"answerability": int, "faithfulness": int, "self_containment": int, '
    '"coverage": int}, "reason": "..."}  (each score 1-5)'
)
RW_SYS = (
    "You are a strict data-quality judge. You are given a SOURCE document and a REWRITE "
    "with a claimed target style. First GUESS the style from the rewrite alone, then score. "
    "Output ONLY JSON: {\"scores\": {\"faithfulness\": int, \"style_adherence\": int, "
    "\"info_retention\": int, \"redundancy\": int}, \"guessed_style\": \"...\", \"reason\": \"...\"}"
)


def judge_qa(llm, model, rec):
    user = (f"SOURCE:\n\"\"\"\n{rec['_source_text']}\n\"\"\"\n\n"
            f"GENERATED (original text prepended to Q&A):\n\"\"\"\n{rec['content']}\n\"\"\"")
    raw = llm.chat(model, QA_SYS, user, 0.0)
    d = C.extract_json(raw)
    return d


def judge_rewrite(llm, model, rec):
    target_style = rec.get("_internal_style", rec["style"])
    user = (f"SOURCE:\n\"\"\"\n{rec['_source_text']}\n\"\"\"\n\n"
            f"TARGET STYLE (claimed): {target_style}\n\n"
            f"REWRITE:\n\"\"\"\n{rec['content']}\n\"\"\"")
    raw = llm.chat(model, RW_SYS, user, 0.0)
    d = C.extract_json(raw)
    return d


def run(llm, model, records, judge_fn, concurrency):
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(judge_fn, llm, model, r): r for r in records}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="judge"):
            r = futs[fut]
            try:
                d = fut.result()
            except Exception as e:
                d = None
            if d and "scores" in d:
                row = {"uid": r["uid"], "style": r["style"], **d}
                if "_internal_style" in r:
                    row["internal_style"] = r["_internal_style"]
                results.append(row)
    return results


def aggregate(results, style_match_check=False):
    dim_scores = defaultdict(list)
    style_hit = 0
    for r in results:
        for k, v in r.get("scores", {}).items():
            if isinstance(v, (int, float)):
                dim_scores[k].append(v)
        target_style = r.get("internal_style", r["style"])
        if style_match_check and r.get("guessed_style", "").lower() == target_style.lower():
            style_hit += 1
    summary = {k: round(mean(v), 3) for k, v in dim_scores.items() if v}
    summary["n_judged"] = len(results)
    if style_match_check and results:
        summary["style_blind_match_rate"] = round(style_hit / len(results), 3)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50, help="每类抽样条数")
    args = ap.parse_args()

    cfg = C.load_config()
    llm = C.LLM(cfg)
    model = cfg["llm"]["judge_model"]
    conc = cfg["synthesis"]["concurrency"]
    out = Path(cfg["paths"]["out_dir"])
    eval_dir = Path(cfg["paths"]["eval_dir"])
    eval_dir.mkdir(parents=True, exist_ok=True)
    random.seed(0)

    summary = {}

    qa_path = out / "qa_full.jsonl"
    if qa_path.exists():
        recs = C.read_jsonl(qa_path)
        sample = random.sample(recs, min(args.n, len(recs)))
        res = run(llm, model, sample, judge_qa, conc)
        C.write_jsonl(res, eval_dir / "l1_qa.jsonl")
        summary["qa"] = aggregate(res)

    ms_path = out / "multi_style_full.jsonl"
    if ms_path.exists():
        recs = C.read_jsonl(ms_path)
        sample = random.sample(recs, min(args.n, len(recs)))
        res = run(llm, model, sample, judge_rewrite, conc)
        C.write_jsonl(res, eval_dir / "l1_rewrite.jsonl")
        summary["rewrite"] = aggregate(res, style_match_check=True)

    (eval_dir / "l1_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
