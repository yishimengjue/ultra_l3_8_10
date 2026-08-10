# -*- coding: utf-8 -*-
"""官方 vs 本地 QA pairwise 判官（500篇官方同源）。
复用 src/eval_l1_pairwise_qa.py 的判官逻辑(JUDGE_SYSTEM/prompt/校验)，
但喂本对齐好的行，并把参考锚点上限设为 4（躲 8192 上限，坑3）。
判官走本地 config(经 SSH 隧道打到服务器 vLLM)。
"""
import sys, json, argparse, statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm

ROOT = Path(".")
sys.path.insert(0, str(ROOT / "src"))
import common as C
import eval_l1_pairwise_qa as PW  # 复用判官逻辑
from extract_seed_from_l3 import split_content_with_reason, SplitFailure

ANCHOR_CAP = 0  # 坑3：全量22锚点会让 prompt ~15k>8192

def split_local_pairs(content, source):
    res = split_content_with_reason(content)
    if isinstance(res, SplitFailure):
        return []
    return [{"question": q.strip(), "answer": a.strip()} for q, a in res.pairs]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--local", default="QA/outputs/qa_official500_c16/qa_full.jsonl",
                    help="本地 QA full jsonl（含 _source_text）")
    ap.add_argument("--out", default="data/eval/zh/pairwise_official500_2026-08-03.jsonl",
                    help="判官明细输出 jsonl")
    args = ap.parse_args()

    local = [json.loads(l) for l in open(args.local, encoding="utf-8")]
    offref = [json.loads(l) for l in open("data/eval/zh/official_ref_500_2026-08-03.jsonl", encoding="utf-8")]
    off_by_seed = {r["seed_text"]: r for r in offref}

    anchors = PW.load_reference_anchors()[:ANCHOR_CAP]  # 截断锚点

    rows = []
    for lr in local:
        src = lr.get("_source_text", "")
        off = off_by_seed.get(src)
        if not off:
            continue
        gen_pairs = split_local_pairs(lr["content"], src)
        rows.append({
            "uid": off["uid"],
            "seed_text": off["seed_text"],
            "official_qa_pairs": [PW.normalize_pair(p) for p in off["qa_pairs"]],
            "generated_qa_pairs": gen_pairs,
            "official_pair_count": len(off["qa_pairs"]),
            "generated_pair_count": len(gen_pairs),
            "reference_examples": anchors,
            "generated_parse_failure": None if gen_pairs else "local_split_failed",
        })
    if args.n:
        rows = rows[:args.n]
    print(f"对齐行: {len(rows)} | 每条锚点数: {len(anchors)}")

    cfg = C.load_config("config.judge_tunnel.yaml")
    llm = C.LLM(cfg)
    model = cfg["llm"]["judge_model"]
    temp = cfg["llm"].get("judge_temperature", 0.0)

    records = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(PW.judge_one, llm, model, r, temp): r for r in rows}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="pairwise judge"):
            records.append(fut.result())

    out = ROOT / args.out
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    succ = [r for r in records if r.get("status") == "success"]
    fail = [r for r in records if r.get("status") != "success"]
    def m(field):
        vals = [r[field] for r in succ if isinstance(r.get(field), int)]
        return round(statistics.fmean(vals), 3) if vals else None
    summary = {
        "total": len(records), "success": len(succ), "failure": len(fail),
        "quality": {k: m(k) for k in PW.QUALITY_SCORE_FIELDS},
        "closeness": {k: m(k) for k in PW.CLOSENESS_SCORE_FIELDS},
        "closeness_dist": dict(sorted(Counter(r["official_closeness_score"] for r in succ if isinstance(r.get("official_closeness_score"),int)).items())),
        "bool_flags": {k: sum(1 for r in succ if r.get(k) is True) for k in PW.BOOL_FIELDS},
        "top_fixes": Counter(r.get("actionable_prompt_fix","").strip() for r in succ if r.get("actionable_prompt_fix")).most_common(5),
        "fail_reasons": Counter(r.get("failure_reason") for r in fail).most_common(5),
    }
    sout = ROOT / (args.out.replace(".jsonl", "_summary.json"))
    sout.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("jsonl:", out)

if __name__ == "__main__":
    main()
