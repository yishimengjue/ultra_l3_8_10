"""AI pairwise QA judge for frozen zh official/local QA records.

Reads only local frozen evaluation inputs, calls the configured judge model, and
writes per-sample JSONL plus JSON/Markdown summaries.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import common as C  # noqa: E402
from extract_seed_from_l3 import SplitFailure, split_content_with_reason  # noqa: E402

OFFICIAL_PATH = REPO_ROOT / "data/eval/zh/pair_eval_100.jsonl"
OFFICIAL_RAW_PATH = REPO_ROOT / "data/official/zh/qa_raw.jsonl"
OFFICIAL_REFERENCE_PATH = REPO_ROOT / "data/eval/zh/official_qa_reference_anchors.json"
LOCAL_RECORDS_PATH = REPO_ROOT / "data/eval/zh/baseline_qa_v2_1_records.jsonl"
JSONL_OUT = REPO_ROOT / "data/eval/zh/eval_l1_pairwise_qa_v2_1.jsonl"
SUMMARY_JSON_OUT = REPO_ROOT / "data/eval/zh/eval_l1_pairwise_qa_v2_1_summary.json"
SUMMARY_MD_OUT = REPO_ROOT / "data/eval/zh/eval_l1_pairwise_qa_v2_1_summary.md"
VERSION_LABEL = "v2.1"

SCORE_FIELDS = [
    "faithfulness_score",
    "answerability_score",
    "hallucination_risk_score",
    "self_containment_score",
    "coverage_score",
    "official_closeness_score",
    "pair_count_similarity",
    "question_style_similarity",
    "answer_style_similarity",
    "focus_similarity",
]
QUALITY_SCORE_FIELDS = [
    "faithfulness_score",
    "answerability_score",
    "hallucination_risk_score",
    "self_containment_score",
    "coverage_score",
]
CLOSENESS_SCORE_FIELDS = [
    "official_closeness_score",
    "pair_count_similarity",
    "question_style_similarity",
    "answer_style_similarity",
    "focus_similarity",
]
BOOL_FIELDS = [
    "local_too_verbose",
    "local_too_textbook_like",
    "local_over_explains",
    "local_noise_question",
    "local_omits_official_focus",
]
TEXT_FIELDS = [
    "main_gap",
    "unlike_official_questions",
    "question_style_issue",
    "answer_style_issue",
    "coverage_issue",
    "actionable_prompt_fix",
]

JUDGE_SYSTEM = """你是严格的数据质量与官方风格接近度裁判。你会看到同一个 seed_text 的官方 QA、本地生成 QA，以及一组不含当前 uid 的官方中文 QA 参考锚点。

请同时评估两个方面：
A. 本地 QA 质量（是否忠实、可答、自足、覆盖核心信息）。
B. 本地 QA 与官方 Ultra-FineWeb-L3 QA 的接近度（不是谁更像标准考试答案，而是谁更像官方样本）。

官方风格锚点的用途：
- 先从参考锚点中归纳官方常见题型、问题长度、答案长度、焦点分布与噪声处理方式。
- 再比较当前本地 QA，不要只盯当前官方 QA 的逐题重合。
- 参考锚点只用于风格校准，不要求逐字模仿。

重点诊断：
- 哪些本地问题不像官方：是太教材化、太概念化、太抽象，还是太碎片事实抽取。
- 哪些答案不像官方：是过长、过度解释，还是过短导致必要依据/限定丢失。
- 覆盖是否漏掉主旨、结尾、数据、人物/机构、对比/因果/条件，或反而问了网页噪声。
- 给出一条可直接改 prompt 的建议。

重要规则：
- 不要把“本地更好看/更详细”当作更接近官方。
- 官方 QA 不是完美答案，只是接近度参考。
- 如果本地答案比官方长很多、解释性更强，应在 answer_style_similarity 中扣分。
- 如果本地问题更教材化、概念化、抽象化，应在 question_style_similarity 中扣分。
- 如果本地只做机械事实抽取、漏掉官方常见的判断/选择/原因/比较/语境题，也应在 question_style_similarity 和 focus_similarity 中扣分。
- 如果本地围绕广告、扫码、关注、福利、客服、转发、朋友圈等网页噪声出题，local_noise_question=true。
- 如果本地忠实但风格不像官方，质量分可以高，official_closeness_score 应低。
- 所有分数都是 1-5 的整数；hallucination_risk_score 中 5 表示无明显幻觉风险。
- pair_count_similarity 是“组数接近度”的 1-5 分，不是输出组数或数量差；即使组数差为 7，也必须映射到 1-5。

只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释 JSON 之外的内容。格式必须为：
{
  "faithfulness_score": 1,
  "answerability_score": 1,
  "hallucination_risk_score": 1,
  "self_containment_score": 1,
  "coverage_score": 1,
  "official_closeness_score": 1,
  "pair_count_similarity": 1,  // 1-5整数，表示组数接近度，不是实际组数
  "question_style_similarity": 1,
  "answer_style_similarity": 1,
  "focus_similarity": 1,
  "main_gap": "一句话说明最大差距",
  "unlike_official_questions": "指出最不像官方的问题类型或题号；没有则写none",
  "question_style_issue": "too_textbook_like / too_fact_extraction / too_abstract / too_template_like / none 中选一项并简述",
  "answer_style_issue": "too_long / too_short_missing_evidence / over_explains / too_fragmented / none 中选一项并简述",
  "coverage_issue": "missing_main_idea / missing_ending / missing_data / missing_people / missing_comparison / asks_noise / none 中选一项并简述",
  "local_too_verbose": false,
  "local_too_textbook_like": false,
  "local_over_explains": false,
  "local_noise_question": false,
  "local_omits_official_focus": false,
  "actionable_prompt_fix": "一句话说明 prompt 可改方向"
}"""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no} is not valid JSON: {e}") from e
    return rows


def read_uid_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return [line.strip() for line in stripped.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    raise ValueError(f"{path} must be a JSON list or newline-separated uid file")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def configure_version(version: str) -> None:
    if not version.startswith("v"):
        raise ValueError("version must look like v2_1 or v2_2")
    global LOCAL_RECORDS_PATH, JSONL_OUT, SUMMARY_JSON_OUT, SUMMARY_MD_OUT, VERSION_LABEL
    base = REPO_ROOT / "data/eval/zh"
    LOCAL_RECORDS_PATH = base / f"baseline_qa_{version}_records.jsonl"
    JSONL_OUT = base / f"eval_l1_pairwise_qa_{version}.jsonl"
    SUMMARY_JSON_OUT = base / f"eval_l1_pairwise_qa_{version}_summary.json"
    SUMMARY_MD_OUT = base / f"eval_l1_pairwise_qa_{version}_summary.md"
    VERSION_LABEL = version.replace("_", ".")


def normalize_pair(pair: dict[str, Any]) -> dict[str, str]:
    return {
        "question": str(pair.get("question", "")).strip(),
        "answer": str(pair.get("answer", "")).strip(),
    }


def load_reference_anchors() -> list[dict[str, Any]]:
    if not OFFICIAL_REFERENCE_PATH.exists():
        return []
    data = json.loads(OFFICIAL_REFERENCE_PATH.read_text(encoding="utf-8"))
    anchors = data.get("anchors", []) if isinstance(data, dict) else []
    if not isinstance(anchors, list):
        return []
    return [anchor for anchor in anchors if isinstance(anchor, dict)]


def reference_examples_for_uid(anchors: list[dict[str, Any]], uid: str) -> list[dict[str, Any]]:
    return [anchor for anchor in anchors if anchor.get("uid") != uid]


def parse_generated_pairs(record: dict[str, Any]) -> tuple[str, list[dict[str, str]], str | None]:
    generated = record.get("generated_record") or {}
    content = str(generated.get("content") or "")
    result = split_content_with_reason(content)
    if isinstance(result, SplitFailure):
        return content, [], result.reason
    pairs = [{"question": q.strip(), "answer": a.strip()} for q, a in result.pairs]
    return content, pairs, None


def build_user_prompt(row: dict[str, Any]) -> str:
    official_pairs = json.dumps(row["official_qa_pairs"], ensure_ascii=False, indent=2)
    generated_pairs = json.dumps(row["generated_qa_pairs"], ensure_ascii=False, indent=2)
    reference_examples = json.dumps(row.get("reference_examples", []), ensure_ascii=False, indent=2)
    return (
        f"uid: {row['uid']}\n\n"
        f"seed_text:\n<<<SEED\n{row['seed_text']}\nSEED\n\n"
        f"official_qa_pairs:\n{official_pairs}\n\n"
        f"generated_local_qa_pairs:\n{generated_pairs}\n\n"
        f"official_pair_count: {row['official_pair_count']}\n"
        f"generated_pair_count: {row['generated_pair_count']}\n\n"
        f"reference_official_examples_excluding_current_uid:\n{reference_examples}\n"
    )


def valid_score(value: Any) -> bool:
    return isinstance(value, int) and 1 <= value <= 5


def validate_judge_output(data: Any) -> tuple[bool, str | None]:
    if not isinstance(data, dict):
        return False, "judge_output_not_object"
    for field in SCORE_FIELDS:
        if not valid_score(data.get(field)):
            return False, f"invalid_score:{field}"
    for field in BOOL_FIELDS:
        if not isinstance(data.get(field), bool):
            return False, f"invalid_bool:{field}"
    for field in TEXT_FIELDS:
        if not isinstance(data.get(field), str) or not data[field].strip():
            return False, f"invalid_text:{field}"
    return True, None


def judge_one(llm: C.LLM, model: str, row: dict[str, Any], temperature: float) -> dict[str, Any]:
    base = {
        "uid": row["uid"],
        "status": "failure",
        "official_pair_count": row["official_pair_count"],
        "generated_pair_count": row["generated_pair_count"],
    }
    if row.get("generated_parse_failure"):
        return {**base, "failure_reason": f"generated_parse_failure:{row['generated_parse_failure']}"}

    try:
        raw = llm.chat(model, JUDGE_SYSTEM, build_user_prompt(row), temperature)
        data = C.extract_json(raw)
        ok, reason = validate_judge_output(data)
        if not ok:
            return {
                **base,
                "failure_reason": reason,
                "raw_judge_prefix": (raw or "")[:1000],
            }
        assert isinstance(data, dict)
        return {
            **base,
            "status": "success",
            **{field: data[field] for field in SCORE_FIELDS},
            **{field: data[field] for field in BOOL_FIELDS},
            **{field: data[field].strip() for field in TEXT_FIELDS},
        }
    except Exception as e:  # keep the run resumable and do not fabricate scores
        return {**base, "failure_reason": type(e).__name__, "failure_detail": str(e)[:1000]}


def build_eval_rows(n: int | None = None, uids: list[str] | None = None) -> list[dict[str, Any]]:
    official_rows = read_jsonl(OFFICIAL_PATH)
    local_rows = read_jsonl(LOCAL_RECORDS_PATH)
    local_by_uid = {row["uid"]: row for row in local_rows}
    if uids:
        official_by_uid = {row["uid"]: row for row in official_rows}
        missing = [uid for uid in uids if uid not in official_by_uid]
        if missing:
            raise ValueError(f"uids not found in official eval rows: {missing[:5]}")
        official_rows = [official_by_uid[uid] for uid in uids]
    anchors = load_reference_anchors()
    rows = []
    for official in official_rows:
        uid = official["uid"]
        local = local_by_uid.get(uid)
        reference_examples = reference_examples_for_uid(anchors, uid)
        if not local:
            rows.append({
                "uid": uid,
                "seed_text": official.get("seed_text", ""),
                "official_qa_pairs": [normalize_pair(pair) for pair in official.get("qa_pairs", [])],
                "generated_qa_pairs": [],
                "official_pair_count": len(official.get("qa_pairs", [])),
                "generated_pair_count": 0,
                "reference_examples": reference_examples,
                "generated_parse_failure": "missing_local_record",
            })
            continue
        _, generated_pairs, parse_failure = parse_generated_pairs(local)
        rows.append({
            "uid": uid,
            "seed_text": str(official.get("seed_text") or local.get("seed_text") or ""),
            "official_qa_pairs": [normalize_pair(pair) for pair in official.get("qa_pairs", [])],
            "generated_qa_pairs": generated_pairs,
            "official_pair_count": len(official.get("qa_pairs", [])),
            "generated_pair_count": len(generated_pairs),
            "reference_examples": reference_examples,
            "generated_parse_failure": parse_failure,
        })
    return rows[:n] if n is not None else rows


def median(values: list[int]) -> float | int | None:
    if not values:
        return None
    return statistics.median(values)


def mean(values: list[int]) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values), 4)


def distribution(values: list[int]) -> dict[str, int]:
    counter = Counter(values)
    return {str(key): counter[key] for key in sorted(counter)}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [record for record in records if record.get("status") == "success"]
    failures = [record for record in records if record.get("status") != "success"]
    score_summary = {}
    for field in SCORE_FIELDS:
        values = [record[field] for record in successes if isinstance(record.get(field), int)]
        score_summary[field] = {
            "mean": mean(values),
            "median": median(values),
            "distribution": distribution(values),
        }

    bool_counts = {field: sum(1 for record in successes if record.get(field) is True) for field in BOOL_FIELDS}
    fix_counts = Counter(record.get("actionable_prompt_fix", "").strip() for record in successes)
    fix_counts.pop("", None)

    return {
        "inputs": {
            "official_path": rel(OFFICIAL_PATH),
            "official_reference_path": rel(OFFICIAL_REFERENCE_PATH),
            "official_reference_anchor_count": len(load_reference_anchors()),
            "local_records_path": rel(LOCAL_RECORDS_PATH),
        },
        "outputs": {
            "jsonl_path": rel(JSONL_OUT),
            "summary_json_path": rel(SUMMARY_JSON_OUT),
            "summary_markdown_path": rel(SUMMARY_MD_OUT),
        },
        "total": len(records),
        "success": len(successes),
        "failure": len(failures),
        "score_summary": score_summary,
        "quality_score_means": {field: score_summary[field]["mean"] for field in QUALITY_SCORE_FIELDS},
        "closeness_score_means": {field: score_summary[field]["mean"] for field in CLOSENESS_SCORE_FIELDS},
        "official_closeness_score_distribution": score_summary["official_closeness_score"]["distribution"],
        "boolean_counts": bool_counts,
        "top_actionable_prompt_fix": [
            {"fix": fix, "count": count} for fix, count in fix_counts.most_common(10)
        ],
        "failure_samples": [
            {
                "uid": record.get("uid"),
                "failure_reason": record.get("failure_reason"),
                "failure_detail": record.get("failure_detail"),
            }
            for record in failures[:20]
        ],
    }


def build_markdown(summary: dict[str, Any]) -> str:
    score_summary = summary["score_summary"]
    bool_counts = summary["boolean_counts"]
    lines = [
        f"# zh QA {VERSION_LABEL} AI Pairwise Judge Summary",
        "",
        "Semantic LLM judge comparison between the frozen official paired QA set and the local QA baseline. This report is aggregated only from per-sample judge JSONL output.",
        "",
        "## Inputs and outputs",
        "",
        f"- official_input: `{summary['inputs']['official_path']}`",
        f"- local_records_input: `{summary['inputs']['local_records_path']}`",
        f"- jsonl_output: `{summary['outputs']['jsonl_path']}`",
        f"- summary_json_output: `{summary['outputs']['summary_json_path']}`",
        f"- summary_markdown_output: `{summary['outputs']['summary_markdown_path']}`",
        "",
        "## Run status",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| total | {summary['total']} |",
        f"| success | {summary['success']} |",
        f"| failure | {summary['failure']} |",
        "",
        "## Score mean/median",
        "",
        "| score | mean | median |",
        "| --- | ---: | ---: |",
    ]
    for field in SCORE_FIELDS:
        stats = score_summary[field]
        lines.append(f"| {field} | {stats['mean']} | {stats['median']} |")

    lines.extend([
        "",
        "## official_closeness_score distribution",
        "",
        "| score | count |",
        "| ---: | ---: |",
    ])
    closeness_dist = summary["official_closeness_score_distribution"]
    lines.extend(f"| {score} | {count} |" for score, count in closeness_dist.items())

    lines.extend([
        "",
        "## Boolean flags",
        "",
        "| flag | count |",
        "| --- | ---: |",
    ])
    for field in BOOL_FIELDS:
        lines.append(f"| {field} | {bool_counts[field]} |")

    lines.extend([
        "",
        "## Top actionable_prompt_fix",
        "",
        "| count | fix |",
        "| ---: | --- |",
    ])
    if summary["top_actionable_prompt_fix"]:
        for item in summary["top_actionable_prompt_fix"]:
            fix = item["fix"].replace("|", "\\|")
            lines.append(f"| {item['count']} | {fix} |")
    else:
        lines.append("| 0 | none |")

    lines.extend([
        "",
        "## Failure samples",
        "",
        "| uid | reason | detail |",
        "| --- | --- | --- |",
    ])
    if summary["failure_samples"]:
        for sample in summary["failure_samples"]:
            detail = str(sample.get("failure_detail") or "").replace("|", "\\|")
            lines.append(f"| `{sample['uid']}` | {sample.get('failure_reason')} | {detail} |")
    else:
        lines.append("| none | none | none |")

    return "\n".join(lines).rstrip() + "\n"


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cfg = C.load_config()
    llm = C.LLM(cfg)
    model = args.model or cfg["llm"]["judge_model"]
    temperature = cfg["llm"].get("judge_temperature", 0.0)
    concurrency = args.concurrency or cfg["synthesis"].get("concurrency", 4)
    uids = read_uid_file(REPO_ROOT / args.uids_file) if args.uids_file else None
    rows = build_eval_rows(args.n, uids)

    records = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(judge_one, llm, model, row, temperature): row for row in rows}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="pairwise qa judge"):
            records.append(fut.result())

    records.sort(key=lambda record: rows.index(next(row for row in rows if row["uid"] == record["uid"])))
    write_jsonl(JSONL_OUT, records)
    summary = summarize(records)
    summary["judge_model"] = model
    write_json(SUMMARY_JSON_OUT, summary)
    write_text(SUMMARY_MD_OUT, build_markdown(summary))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None, help="只评测前 n 条；默认全部 100 条")
    ap.add_argument("--model", default=None, help="覆盖 config.yaml 中的 judge_model")
    ap.add_argument("--concurrency", type=int, default=None, help="覆盖 config.yaml 中的并发数")
    ap.add_argument("--version", default="v2_1", help="Input/output version suffix, e.g. v2_1 or v2_2")
    ap.add_argument("--uids-file", default=None, help="只评测指定 UID 文件中的样本，支持 JSON list 或逐行 UID")
    args = ap.parse_args()
    configure_version(args.version)

    summary = run_eval(args)
    print(json.dumps({
        "total": summary["total"],
        "success": summary["success"],
        "failure": summary["failure"],
        "official_closeness_score_mean": summary["score_summary"]["official_closeness_score"]["mean"],
        "official_closeness_score_median": summary["score_summary"]["official_closeness_score"]["median"],
        "quality_score_means": summary["quality_score_means"],
        "boolean_counts": summary["boolean_counts"],
        "jsonl_output": rel(JSONL_OUT),
        "summary_json_output": rel(SUMMARY_JSON_OUT),
        "summary_markdown_output": rel(SUMMARY_MD_OUT),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
