"""AI judge for zh multi-style rewrite against official-style anchors.

This judge reads local rewrite outputs with `_source_text`, uses the local
official zh multi-style sample/profile as style reference, and writes per-row
JSONL plus aggregate JSON/Markdown reports.
"""
from __future__ import annotations

import argparse
import json
import re
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

OFFICIAL_RAW_PATH = REPO_ROOT / "data/official/zh/multi_style_raw.jsonl"
OFFICIAL_PROFILE_PATH = REPO_ROOT / "data/eval/zh/official_zh_multi_style_profile.json"
LOCAL_FULL_PATH = REPO_ROOT / "data/output/zh_rewrite_v4_0/multi_style_full.jsonl"
JSONL_OUT = REPO_ROOT / "data/eval/zh/eval_l1_rewrite_official_style_v4_0.jsonl"
SUMMARY_JSON_OUT = REPO_ROOT / "data/eval/zh/eval_l1_rewrite_official_style_v4_0_summary.json"
SUMMARY_MD_OUT = REPO_ROOT / "data/eval/zh/eval_l1_rewrite_official_style_v4_0_summary.md"

SCORE_FIELDS = [
    "faithfulness_score",
    "information_retention_score",
    "refinement_value_score",
    "official_style_closeness_score",
    "natural_prose_score",
    "low_template_score",
    "appropriate_length_score",
    "noise_handling_score",
    "hallucination_risk_score",
    "overall_score",
]
BOOL_FIELDS = [
    "too_markdown_or_template",
    "too_textbook_like",
    "too_conversational",
    "too_abstract_short",
    "adds_unseen_facts",
    "drops_key_information",
    "keeps_web_noise_unnecessarily",
    "keeps_brand_ad_noise",
    "unsupported_numeric_correction",
    "drops_key_technical_parameter",
]
TEXT_FIELDS = ["main_gap", "prompt_fix"]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no} invalid JSON: {e}") from e
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def display_path(path: Path) -> str:
    try:
        return rel(path)
    except ValueError:
        return str(path)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def jsonl_row_stats(path: Path) -> dict[str, Any]:
    rows = 0
    source_texts = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            source_text = row.get("_source_text")
            if source_text:
                source_texts.add(str(source_text))
    return {
        "path": display_path(path),
        "row_count": rows,
        "unique_source_docs": len(source_texts),
        "mtime": path.stat().st_mtime,
    }


def resolve_local_full_path(value: str) -> tuple[Path, dict[str, Any]]:
    if value != "auto":
        path = resolve_path(value)
        return path, {
            "mode": "explicit",
            "selected": display_path(path),
        }

    candidates = []
    for path in (REPO_ROOT / "data" / "output").glob("**/multi_style_full.jsonl"):
        if not path.is_file():
            continue
        try:
            stats = jsonl_row_stats(path)
        except OSError:
            continue
        if stats["row_count"] > 0:
            candidates.append({"path_obj": path, **stats})

    if not candidates:
        raise SystemExit("No local rewrite outputs found under data/output/**/multi_style_full.jsonl")

    candidates.sort(
        key=lambda item: (item["row_count"], item["unique_source_docs"], item["mtime"]),
        reverse=True,
    )
    selected = candidates[0]
    return selected["path_obj"], {
        "mode": "auto_largest_local_full",
        "selected": selected["path"],
        "candidates_top5": [
            {key: item[key] for key in ("path", "row_count", "unique_source_docs")}
            for item in candidates[:5]
        ],
    }


def count_unique_source_docs(rows: list[dict[str, Any]]) -> int:
    return len({str(row.get("_source_text")) for row in rows if row.get("_source_text")})


def output_tag_from_local_full(path: Path) -> str:
    tag = path.parent.name
    if tag.startswith("zh_rewrite_"):
        tag = tag[len("zh_rewrite_"):]
    elif tag == "zh":
        tag = "zh_current"
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag).strip("_")
    return tag or "rewrite"


def fill_default_output_paths(args: argparse.Namespace, local_full_path: Path) -> None:
    tag = output_tag_from_local_full(local_full_path)
    if args.jsonl_out is None:
        args.jsonl_out = f"data/eval/zh/eval_l1_rewrite_official_style_{tag}.jsonl"
    if args.summary_json_out is None:
        args.summary_json_out = f"data/eval/zh/eval_l1_rewrite_official_style_{tag}_summary.json"
    if args.summary_md_out is None:
        args.summary_md_out = f"data/eval/zh/eval_l1_rewrite_official_style_{tag}_summary.md"


def clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n...[中间截断]...\n{tail}"


def select_official_anchors(records: list[dict[str, Any]], count: int = 8) -> list[dict[str, Any]]:
    if not records:
        return []
    ordered = sorted(records, key=lambda row: len(str(row.get("content", ""))))
    if count <= 1:
        positions = [0.50]
    else:
        positions = [0.03 + (0.94 * index / (count - 1)) for index in range(count)]
    anchors = []
    used = set()
    for pos in positions[:count]:
        idx = min(round((len(ordered) - 1) * pos), len(ordered) - 1)
        row = ordered[idx]
        uid = row.get("uid", idx)
        if uid in used:
            continue
        used.add(uid)
        anchors.append({
            "uid": str(row.get("uid", "")),
            "length": len(str(row.get("content", ""))),
            "content": clip(str(row.get("content", "")), 450),
        })
    return anchors


def load_profile_summary() -> dict[str, Any]:
    profile = json.loads(OFFICIAL_PROFILE_PATH.read_text(encoding="utf-8"))
    overview = profile.get("input_overview") or profile.get("inputs") or {}
    return {
        "sample_count": overview.get("sample_count"),
        "content_length": profile["content_length_distribution"],
        "structure": profile["structure_distribution"],
        "style_distribution": overview.get("style_distribution"),
        "profile_note": "官方 multi-style 只有统一 style=multi_style，不暴露 encyclopedia/textbook/blog/abstract 标签；四类只是本项目内部策略。",
    }


def build_judge_system(profile_summary: dict[str, Any], anchors: list[dict[str, Any]]) -> str:
    anchors_text = json.dumps(anchors, ensure_ascii=False, indent=2)
    profile_text = json.dumps(profile_summary, ensure_ascii=False, indent=2)
    return f"""你是 Ultra-FineWeb-L3 中文 multi-style 改写的严格裁判。

你会看到：
1. 本地 rewrite 的原文 seed_text。
2. 本地 rewrite 输出 local_rewrite。
3. 本地内部生成策略 internal_style，这只是工程内部标签，不是官方标签。

评分目标：
- 先判断 local_rewrite 是否忠实原文，是否真的提高学习价值。
- 再判断它是否接近官方 zh multi-style 成品，而不是是否“风格鲜明”。
- 官方 multi-style 不暴露四种风格标签，统一为 multi_style；不要要求输出像百科/教材/博客/摘要模板。

官方成品画像（程序化 profile）：
{profile_text}

官方代表样例（只作为风格锚点，不要求逐字模仿）：
{anchors_text}

官方风格要点：
- 更像自然改写后的正文，通常是连贯段落，不是 prompt 回答、Markdown 笔记或强模板。
- 可以保留必要列表/流程/数字，但不应过度标题化、项目符号化、定义-原理-推导化。
- 内容应忠实、信息关系更清楚、去掉明显重复和无正文意义的包装。
- 不鼓励新增事实、外部背景、虚构例子、抒情比喻、鸡汤化或过强口语化。
- 长度随原文信息密度变化，不用硬贴均值；但过短会丢信息，过长且啰嗦会扣分。
- 对品牌推广、厂家广告、咨询/购买/服务话术要严格；把广告语改得更自然仍算噪声残留。
- 原文中明显可疑的数字不能擅自纠错为看似合理的新数字，除非原文已有依据；否则算不忠实。
- 技术类文本中的关键公式、化学式、比例、参数、范围等应尽量保留，尤其百科/教材/摘要策略不能漏掉核心参数。

请输出一个 JSON 对象，不要 Markdown，不要代码块。所有分数均为 1-5 整数，5 最好。
字段必须为：
{{
  "faithfulness_score": 1,
  "information_retention_score": 1,
  "refinement_value_score": 1,
  "official_style_closeness_score": 1,
  "natural_prose_score": 1,
  "low_template_score": 1,
  "appropriate_length_score": 1,
  "noise_handling_score": 1,
  "hallucination_risk_score": 1,
  "overall_score": 1,
  "too_markdown_or_template": false,
  "too_textbook_like": false,
  "too_conversational": false,
  "too_abstract_short": false,
  "adds_unseen_facts": false,
  "drops_key_information": false,
  "keeps_web_noise_unnecessarily": false,
  "keeps_brand_ad_noise": false,
  "unsupported_numeric_correction": false,
  "drops_key_technical_parameter": false,
  "main_gap": "一句话说明最大问题",
  "prompt_fix": "一句话说明下一版 prompt 应怎么改"
}}
"""


def build_user_prompt(row: dict[str, Any]) -> str:
    return (
        f"uid: {row.get('uid', '')}\n"
        f"internal_style: {row.get('_internal_style', '')}\n\n"
        f"seed_text:\n<<<SEED\n{clip(str(row.get('_source_text', '')), 5000)}\nSEED\n\n"
        f"local_rewrite:\n<<<REWRITE\n{clip(str(row.get('content', '')), 3500)}\nREWRITE\n"
    )


def extract_judge_json(raw: str) -> dict[str, Any] | None:
    data = C.extract_json(raw)
    if data is not None:
        return data
    repaired = re.sub(r"”(\s*[,}])", r'"\1', raw or "")
    repaired = re.sub(r"([:{,\[]\s*)“", r'\1"', repaired)
    return C.extract_json(repaired)


def normalize_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    if isinstance(value, float) and 1 <= value <= 5:
        return max(1, min(5, int(value + 0.5)))
    return None


def normalize_judge_output(data: Any) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    if not isinstance(data, dict):
        return None, "judge_output_not_object", []

    normalized = dict(data)
    repair_notes = []
    for field in SCORE_FIELDS:
        score = normalize_score(data.get(field))
        if score is None:
            return None, f"invalid_score:{field}", repair_notes
        if score != data.get(field):
            repair_notes.append(f"{field}:{data.get(field)}->{score}")
        normalized[field] = score
    for field in BOOL_FIELDS:
        if not isinstance(data.get(field), bool):
            return None, f"invalid_bool:{field}", repair_notes
    for field in TEXT_FIELDS:
        if not isinstance(data.get(field), str) or not data[field].strip():
            return None, f"invalid_text:{field}", repair_notes
        normalized[field] = data[field].strip()
    return normalized, None, repair_notes


def valid_score(value: Any) -> bool:
    return normalize_score(value) is not None


def validate_output(data: Any) -> tuple[bool, str | None]:
    _, reason, _ = normalize_judge_output(data)
    return reason is None, reason


def judge_one(llm: C.LLM, model: str, system_prompt: str, row: dict[str, Any], temperature: float) -> dict[str, Any]:
    base = {
        "uid": str(row.get("uid", "")),
        "internal_style": str(row.get("_internal_style", "")),
        "content_length": len(str(row.get("content", ""))),
        "source_length": len(str(row.get("_source_text", ""))),
        "status": "failure",
    }
    try:
        raw, judge_perf = llm.chat_detailed(model, system_prompt, build_user_prompt(row), temperature)
        data = extract_judge_json(raw)
        data, reason, repair_notes = normalize_judge_output(data)
        if reason is not None:
            return {
                **base,
                "failure_reason": reason,
                "raw_judge_prefix": (raw or "")[:1000],
                "judge_perf": judge_perf,
            }
        assert isinstance(data, dict)
        result = {
            **base,
            "status": "success",
            "judge_perf": judge_perf,
            **{field: data[field] for field in SCORE_FIELDS},
            **{field: data[field] for field in BOOL_FIELDS},
            "main_gap": data["main_gap"].strip(),
            "prompt_fix": data["prompt_fix"].strip(),
        }
        if repair_notes:
            result["score_repair_notes"] = repair_notes
        return result
    except Exception as e:
        return {**base, "failure_reason": type(e).__name__, "failure_detail": str(e)[:1000]}


def _token_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def summarize_judge_perf(results: list[dict[str, Any]]) -> dict[str, Any]:
    perf_rows = [row.get("judge_perf") for row in results if isinstance(row.get("judge_perf"), dict)]
    usage_rows = [row.get("usage") or {} for row in perf_rows]
    usage_available_rows = [
        usage for usage in usage_rows
        if usage.get("total_tokens") is not None
    ]
    elapsed_values = [
        float(row["elapsed_seconds"])
        for row in perf_rows
        if isinstance(row.get("elapsed_seconds"), (int, float))
    ]
    process_cpu_values = [
        float(row["process_cpu_seconds"])
        for row in perf_rows
        if isinstance(row.get("process_cpu_seconds"), (int, float))
    ]
    api_attempts = sum(int(row.get("api_attempts") or 0) for row in perf_rows)
    prompt_tokens = sum(_token_value(usage, "prompt_tokens") for usage in usage_available_rows)
    completion_tokens = sum(_token_value(usage, "completion_tokens") for usage in usage_available_rows)
    total_tokens = sum(_token_value(usage, "total_tokens") for usage in usage_available_rows)
    return {
        "rows_with_perf": len(perf_rows),
        "api_attempts_total": api_attempts,
        "api_attempts_per_100_judge": round(api_attempts / max(len(results), 1) * 100, 4),
        "token_usage_available_rows": len(usage_available_rows),
        "token_usage_missing_rows": len(perf_rows) - len(usage_available_rows),
        "prompt_tokens_total": prompt_tokens if usage_available_rows else None,
        "completion_tokens_total": completion_tokens if usage_available_rows else None,
        "total_tokens_total": total_tokens if usage_available_rows else None,
        "total_tokens_per_judge": round(total_tokens / max(len(usage_available_rows), 1), 4) if usage_available_rows else None,
        "total_tokens_per_100_judge": round(total_tokens / max(len(usage_available_rows), 1) * 100, 4) if usage_available_rows else None,
        "request_elapsed_seconds_mean": round(statistics.fmean(elapsed_values), 4) if elapsed_values else None,
        "request_elapsed_seconds_min": round(min(elapsed_values), 4) if elapsed_values else None,
        "request_elapsed_seconds_max": round(max(elapsed_values), 4) if elapsed_values else None,
        "process_cpu_seconds_sum": round(sum(process_cpu_values), 4),
    }


def mean(values: list[int]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def median(values: list[int]) -> float | int | None:
    if not values:
        return None
    return statistics.median(values)


def compact_snippet(text: str, limit: int = 260) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def active_flags(row: dict[str, Any]) -> list[str]:
    return [field for field in BOOL_FIELDS if row.get(field) is True]


def is_actionable_fix(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    return not any(marker in text for marker in ("无需修改", "当前 prompt 已", "当前prompt已"))


def sample_detail(row: dict[str, Any], local_by_uid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    local = local_by_uid.get(str(row.get("uid", "")), {})
    return {
        "uid": str(row.get("uid", "")),
        "internal_style": str(row.get("internal_style", "")),
        "scores": {
            "official_style_closeness_score": row.get("official_style_closeness_score"),
            "overall_score": row.get("overall_score"),
            "faithfulness_score": row.get("faithfulness_score"),
            "noise_handling_score": row.get("noise_handling_score"),
            "low_template_score": row.get("low_template_score"),
            "hallucination_risk_score": row.get("hallucination_risk_score"),
        },
        "flags": active_flags(row),
        "main_gap": str(row.get("main_gap", "")).strip(),
        "prompt_fix": str(row.get("prompt_fix", "")).strip(),
        "source_head": compact_snippet(local.get("_source_text"), 220),
        "rewrite_head": compact_snippet(local.get("content"), 260),
    }


def collect_weakness_samples(
    results: list[dict[str, Any]],
    local_rows: list[dict[str, Any]],
    limit: int = 12,
) -> dict[str, Any]:
    successes = [row for row in results if row.get("status") == "success"]
    local_by_uid = {str(row.get("uid", "")): row for row in local_rows}

    low_score_rows = sorted(
        successes,
        key=lambda row: (
            int(row.get("official_style_closeness_score", 99)),
            int(row.get("overall_score", 99)),
            int(row.get("noise_handling_score", 99)),
            int(row.get("low_template_score", 99)),
            str(row.get("uid", "")),
        ),
    )[:limit]

    flag_examples: dict[str, list[dict[str, Any]]] = {}
    for field in BOOL_FIELDS:
        rows = [row for row in successes if row.get(field) is True]
        rows = sorted(
            rows,
            key=lambda row: (
                int(row.get("official_style_closeness_score", 99)),
                int(row.get("overall_score", 99)),
                str(row.get("uid", "")),
            ),
        )[:5]
        flag_examples[field] = [sample_detail(row, local_by_uid) for row in rows]

    actionable_fixes = [
        {"fix": fix, "count": count}
        for fix, count in Counter(
            str(row.get("prompt_fix", "")).strip()
            for row in successes
            if is_actionable_fix(row.get("prompt_fix", ""))
        ).most_common(20)
    ]

    return {
        "low_score_samples": [sample_detail(row, local_by_uid) for row in low_score_rows],
        "flag_examples": flag_examples,
        "top_actionable_prompt_fix": actionable_fixes,
    }


def summarize(results: list[dict[str, Any]], local_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    successes = [row for row in results if row.get("status") == "success"]
    failures = [row for row in results if row.get("status") != "success"]
    score_summary = {}
    for field in SCORE_FIELDS:
        values = [int(row[field]) for row in successes]
        score_summary[field] = {
            "mean": mean(values),
            "median": median(values),
            "distribution": dict(sorted(Counter(values).items())),
        }
    bool_counts = {
        field: sum(1 for row in successes if row.get(field) is True)
        for field in BOOL_FIELDS
    }
    by_style: dict[str, Any] = {}
    for style in sorted({str(row.get("internal_style", "")) for row in successes}):
        rows = [row for row in successes if row.get("internal_style") == style]
        by_style[style] = {
            "n": len(rows),
            "official_style_closeness_score_mean": mean([int(row["official_style_closeness_score"]) for row in rows]),
            "overall_score_mean": mean([int(row["overall_score"]) for row in rows]),
            "refinement_value_score_mean": mean([int(row["refinement_value_score"]) for row in rows]),
            "faithfulness_score_mean": mean([int(row["faithfulness_score"]) for row in rows]),
            "bool_counts": {
                field: sum(1 for row in rows if row.get(field) is True)
                for field in BOOL_FIELDS
            },
        }
    summary = {
        "total": len(results),
        "success": len(successes),
        "failure": len(failures),
        "score_summary": score_summary,
        "boolean_counts": bool_counts,
        "by_internal_style": by_style,
        "failure_samples": failures[:10],
        "top_prompt_fix": [
            {"fix": fix, "count": count}
            for fix, count in Counter(row.get("prompt_fix", "") for row in successes).most_common(20)
        ],
    }
    if local_rows is not None:
        summary["weakness_analysis"] = collect_weakness_samples(results, local_rows)
    return summary


def build_markdown(summary: dict[str, Any], args: argparse.Namespace) -> str:
    inputs = summary.get("inputs", {})
    lines = [
        "# zh Rewrite Official-style AI Judge",
        "",
        "AI judge using official zh multi-style outputs/profile as style reference. Official data has only `style=multi_style`; internal styles are local generation strategies.",
        "",
        "## Inputs",
        "",
        f"- local_full: `{inputs.get('local_full', args.local_full)}`",
        f"- local_full_arg: `{inputs.get('local_full_arg', args.local_full)}`",
        f"- local_total_rows_available: `{inputs.get('local_total_rows_available')}`",
        f"- local_unique_source_docs_available: `{inputs.get('local_unique_source_docs_available')}`",
        f"- judged_rows: `{inputs.get('judged_rows')}`",
        f"- n_limit: `{inputs.get('n_limit')}`",
        f"- official_raw: `{rel(OFFICIAL_RAW_PATH)}`",
        f"- official_profile: `{rel(OFFICIAL_PROFILE_PATH)}`",
        f"- official_reference_count: `{inputs.get('official_reference_count')}`",
        f"- official_anchor_count: `{inputs.get('official_anchor_count')}`",
        f"- jsonl_output: `{args.jsonl_out}`",
        f"- summary_json_output: `{args.summary_json_out}`",
        f"- summary_md_output: `{args.summary_md_out}`",
        "",
        "## Run Status",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| total | {summary['total']} |",
        f"| success | {summary['success']} |",
        f"| failure | {summary['failure']} |",
        "",
        "## Score Summary",
        "",
        "| score | mean | median | distribution |",
        "| --- | ---: | ---: | --- |",
    ]
    for field in SCORE_FIELDS:
        item = summary["score_summary"][field]
        lines.append(f"| {field} | {item['mean']} | {item['median']} | {item['distribution']} |")
    lines.extend([
        "",
        "## Boolean Flags",
        "",
        "| flag | count |",
        "| --- | ---: |",
    ])
    for field, count in summary["boolean_counts"].items():
        lines.append(f"| {field} | {count} |")
    lines.extend([
        "",
        "## By Internal Style",
        "",
        "| internal_style | n | official_closeness_mean | overall_mean | refinement_mean | faithfulness_mean |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for style, item in summary["by_internal_style"].items():
        lines.append(
            f"| {style} | {item['n']} | {item['official_style_closeness_score_mean']} | "
            f"{item['overall_score_mean']} | {item['refinement_value_score_mean']} | {item['faithfulness_score_mean']} |"
        )
    lines.extend([
        "",
        "## Actionable Prompt Fix",
        "",
        "| count | fix |",
        "| ---: | --- |",
    ])
    weakness = summary.get("weakness_analysis", {})
    actionable = weakness.get("top_actionable_prompt_fix") or []
    if actionable:
        for item in actionable[:20]:
            lines.append(f"| {item['count']} | {item['fix'].replace('|', '/')} |")
    else:
        lines.append("| 0 | No actionable prompt fix found by judge. |")
    lines.extend([
        "",
        "## Low-score Samples",
        "",
        "| uid | style | scores | flags | main_gap | prompt_fix | source_head | rewrite_head |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for item in weakness.get("low_score_samples", [])[:12]:
        scores = item.get("scores", {})
        score_text = (
            f"official={scores.get('official_style_closeness_score')}, "
            f"overall={scores.get('overall_score')}, "
            f"noise={scores.get('noise_handling_score')}, "
            f"template={scores.get('low_template_score')}, "
            f"hallucination={scores.get('hallucination_risk_score')}"
        )
        lines.append(
            "| "
            + " | ".join([
                item.get("uid", ""),
                item.get("internal_style", ""),
                score_text,
                ", ".join(item.get("flags", [])),
                item.get("main_gap", "").replace("|", "/"),
                item.get("prompt_fix", "").replace("|", "/"),
                item.get("source_head", "").replace("|", "/"),
                item.get("rewrite_head", "").replace("|", "/"),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Flag Examples",
        "",
    ])
    flag_examples = weakness.get("flag_examples") or {}
    for flag in BOOL_FIELDS:
        examples = flag_examples.get(flag) or []
        if not examples:
            continue
        lines.extend([
            f"### {flag}",
            "",
            "| uid | style | official | overall | main_gap | prompt_fix | rewrite_head |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ])
        for item in examples[:5]:
            scores = item.get("scores", {})
            lines.append(
                "| "
                + " | ".join([
                    item.get("uid", ""),
                    item.get("internal_style", ""),
                    str(scores.get("official_style_closeness_score")),
                    str(scores.get("overall_score")),
                    item.get("main_gap", "").replace("|", "/"),
                    item.get("prompt_fix", "").replace("|", "/"),
                    item.get("rewrite_head", "").replace("|", "/"),
                ])
                + " |"
            )
        lines.append("")
    lines.extend([
        "",
        "## Top Prompt Fix",
        "",
        "| count | fix |",
        "| ---: | --- |",
    ])
    for item in summary["top_prompt_fix"][:20]:
        lines.append(f"| {item['count']} | {item['fix'].replace('|', '/')} |")
    if summary["failure_samples"]:
        lines.extend(["", "## Failure Samples", "", "| uid | reason | detail |", "| --- | --- | --- |"])
        for row in summary["failure_samples"]:
            lines.append(f"| {row.get('uid', '')} | {row.get('failure_reason', '')} | {row.get('failure_detail', '')} |")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local-full",
        default="auto",
        help="Path to multi_style_full.jsonl. Use 'auto' to judge the largest local rewrite output under data/output.",
    )
    parser.add_argument("--jsonl-out", default=None)
    parser.add_argument("--summary-json-out", default=None)
    parser.add_argument("--summary-md-out", default=None)
    parser.add_argument("--n", type=int, default=None, help="Limit judged rows; omit to judge all available rows.")
    parser.add_argument("--anchor-count", type=int, default=16)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = C.load_config()
    local_full_path, local_selection = resolve_local_full_path(args.local_full)
    fill_default_output_paths(args, local_full_path)

    local_rows_all = read_jsonl(local_full_path)
    local_total_rows_available = len(local_rows_all)
    local_unique_source_docs_available = count_unique_source_docs(local_rows_all)
    local_rows = local_rows_all
    if args.n is not None:
        if args.n <= 0:
            raise SystemExit("--n must be positive; omit --n to judge all available rows.")
        local_rows = local_rows[: args.n]

    official_rows = read_jsonl(OFFICIAL_RAW_PATH)
    anchors = select_official_anchors(official_rows, args.anchor_count)
    profile_summary = load_profile_summary()
    system_prompt = build_judge_system(profile_summary, anchors)
    llm = C.LLM(cfg)
    model = cfg["llm"]["judge_model"]
    temperature = cfg["llm"].get("judge_temperature", 0.0)
    workers = max(1, int(cfg.get("synthesis", {}).get("concurrency", 1)))

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(judge_one, llm, model, system_prompt, row, temperature): index
            for index, row in enumerate(local_rows)
        }
        completed: dict[int, dict[str, Any]] = {}
        for future in tqdm(as_completed(futures), total=len(futures), desc="rewrite judge"):
            completed[futures[future]] = future.result()
        results = [completed[index] for index in range(len(local_rows))]

    summary = summarize(results, local_rows)
    summary["judge_performance_summary"] = summarize_judge_perf(results)
    summary["inputs"] = {
        "local_full_arg": args.local_full,
        "local_full": display_path(local_full_path),
        "local_selection": local_selection,
        "local_total_rows_available": local_total_rows_available,
        "local_unique_source_docs_available": local_unique_source_docs_available,
        "judged_rows": len(local_rows),
        "judged_unique_source_docs": count_unique_source_docs(local_rows),
        "n_limit": args.n,
        "official_raw": rel(OFFICIAL_RAW_PATH),
        "official_profile": rel(OFFICIAL_PROFILE_PATH),
        "official_reference_count": len(official_rows),
        "judge_model": model,
        "official_anchor_count": len(anchors),
    }
    summary["outputs"] = {
        "jsonl": args.jsonl_out,
        "summary_json": args.summary_json_out,
        "summary_md": args.summary_md_out,
    }
    write_jsonl(REPO_ROOT / args.jsonl_out, results)
    write_json(REPO_ROOT / args.summary_json_out, summary)
    write_text(REPO_ROOT / args.summary_md_out, build_markdown(summary, args))
    print(json.dumps({
        "local_full": summary["inputs"]["local_full"],
        "local_total_rows_available": summary["inputs"]["local_total_rows_available"],
        "local_unique_source_docs_available": summary["inputs"]["local_unique_source_docs_available"],
        "total": summary["total"],
        "success": summary["success"],
        "failure": summary["failure"],
        "official_style_closeness_score_mean": summary["score_summary"]["official_style_closeness_score"]["mean"],
        "overall_score_mean": summary["score_summary"]["overall_score"]["mean"],
        "refinement_value_score_mean": summary["score_summary"]["refinement_value_score"]["mean"],
        "jsonl_output": args.jsonl_out,
        "summary_json_output": args.summary_json_out,
        "summary_md_output": args.summary_md_out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
