"""Hard-filter rewrite seeds with obvious numeric corruption.

This script is intentionally narrow: it only rejects source texts that contain
clear descending numeric ranges or truncated numeric ranges, such as:
- "4800～50年"
- "0.22~0.2%"

It does not try to fix the text, and it does not call any LLM.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

DATE_LIKE_RE = re.compile(
    r"""
    (?:
        \d{4}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日? |
        \d{4}年\d{1,2}月\d{1,2}日? |
        \d{1,2}月\d{1,2}日
    )
    """,
    re.VERBOSE,
)

RANGE_RE = re.compile(
    r"(?<!\d)"
    r"(?P<left>\d{1,6}(?:\.\d+)?)"
    r"\s*(?P<sep>~|～|—|–|-|至|到)"
    r"\s*(?P<right>\d{1,6}(?:\.\d+)?)"
    r"(?!\d)"
)

STANDARD_CODE_CONTEXT_RE = re.compile(
    r"(?:GB/T|GB|ISO|ASTM|DIN|EN|JIS|HG/T|HG|QB/T|QB|JB/T|JB|YS/T|YS|DL/T|DL|SL/T|SL|SY/T|SY|YY/T|YY|JT/T|JT|NY/T|NY|MT/T|MT)\s*$",
    re.IGNORECASE,
)

DATE_OR_TIME_RANGE_CONTEXT_RE = re.compile(
    r"(?:"
    r"\d{1,2}:\d{2}\s*[-–—~]\s*\d{1,2}:\d{2}|"
    r"\d{1,2}\.\d{1,2}\s*[-–—~]\s*\d{1,2}\.\d{1,2}|"
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*[-–—~]\s*\d{4}[./-]\d{1,2}[./-]\d{1,2}"
    r")"
)


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid JSON: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def source_text(row: dict[str, Any]) -> str:
    return str(row.get("_source_text") or row.get("content") or row.get("text") or "").strip()


def compact(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def mask_date_like(text: str) -> str:
    return DATE_LIKE_RE.sub(" ", text)


def looks_like_standard_code(text: str, start: int) -> bool:
    prefix = text[max(0, start - 24):start]
    return bool(STANDARD_CODE_CONTEXT_RE.search(prefix))


def looks_like_date_or_time_range(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 16):min(len(text), end + 16)]
    return bool(DATE_OR_TIME_RANGE_CONTEXT_RE.search(context))


def looks_like_decimal_measurement_range(left_text: str, right_text: str) -> bool:
    return "." in left_text or "." in right_text


def numeric_guard_reasons(text: str) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    scan_text = mask_date_like(text)
    for match in RANGE_RE.finditer(scan_text):
        left_text = match.group("left")
        right_text = match.group("right")
        if looks_like_standard_code(scan_text, match.start()):
            continue
        if (
            looks_like_date_or_time_range(scan_text, match.start(), match.end())
            and not looks_like_decimal_measurement_range(left_text, right_text)
        ):
            continue
        try:
            left_value = float(left_text)
            right_value = float(right_text)
        except ValueError:
            continue
        if right_value >= left_value:
            continue
        raw = match.group(0)
        if raw in {"1-2", "2-3", "3-4", "4-5"}:
            continue
        reasons.append({
            "label": "descending_numeric_range",
            "match": raw,
            "left": left_text,
            "right": right_text,
        })
    return reasons


def filter_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in rows:
        text = source_text(row)
        reasons = numeric_guard_reasons(text)
        decision = {
            "doc_id": str(row.get("doc_id") or row.get("uid") or ""),
            "source_preview": compact(text),
            "reasons": reasons,
            "decision": "reject" if reasons else "keep",
        }
        if reasons:
            rejected.append({
                **row,
                "numeric_guard_reject_reasons": reasons,
            })
        else:
            kept.append(row)
        decisions.append(decision)
    return kept, rejected, decisions


def build_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# rewrite seed numeric hard guard report",
        "",
        "## Overview",
        "",
        "- 这个步骤只负责拦截明显的脏数值种子，不改 prompt，不跑 LLM。",
        "- 目标是把上游已经坏掉的数值样本先挡住，避免后面 rewrite 继续“修数值”。",
        "",
        "## Inputs",
        "",
        f"- seed_jsonl: `{summary['inputs']['seed_jsonl']}`",
        f"- clean_out: `{summary['outputs']['clean_out']}`",
        f"- rejected_out: `{summary['outputs']['rejected_out']}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| seed_total | {summary['seed_total']} |",
        f"| kept | {summary['kept']} |",
        f"| rejected | {summary['rejected']} |",
        "",
        "## Reject reasons",
        "",
        "| reason | count |",
        "| --- | ---: |",
    ]
    for reason, count in summary["reject_reason_counts"].items():
        lines.append(f"| {reason} | {count} |")
    lines.extend([
        "",
        "## Examples",
        "",
        "| doc_id | reason | preview |",
        "| --- | --- | --- |",
    ])
    for item in summary["rejected_examples"]:
        reason_text = ", ".join(reason["label"] for reason in item["reasons"])
        lines.append(
            f"| {item['doc_id']} | {reason_text} | {item['source_preview'].replace('|', '｜')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-jsonl", required=True)
    parser.add_argument("--clean-out", required=True)
    parser.add_argument("--rejected-out", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--md-out", required=True)
    parser.add_argument("--example-limit", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seed_path = resolve(args.seed_jsonl)
    clean_out = resolve(args.clean_out)
    rejected_out = resolve(args.rejected_out)
    json_out = resolve(args.json_out)
    md_out = resolve(args.md_out)

    seed_rows = read_jsonl(seed_path)
    kept, rejected, decisions = filter_rows(seed_rows)
    reject_reason_counts = Counter(reason["label"] for item in decisions for reason in item["reasons"])

    write_jsonl(clean_out, kept)
    write_jsonl(rejected_out, rejected)

    summary = {
        "inputs": {
            "seed_jsonl": rel(seed_path),
        },
        "outputs": {
            "clean_out": rel(clean_out),
            "rejected_out": rel(rejected_out),
            "json_out": rel(json_out),
            "md_out": rel(md_out),
        },
        "seed_total": len(seed_rows),
        "kept": len(kept),
        "rejected": len(rejected),
        "reject_reason_counts": dict(reject_reason_counts.most_common()),
        "rejected_examples": [
            item for item in decisions if item["decision"] == "reject"
        ][: args.example_limit],
    }
    write_json(json_out, summary)
    write_text(md_out, build_markdown(summary))

    print(json.dumps({
        "seed_total": len(seed_rows),
        "kept": len(kept),
        "rejected": len(rejected),
        "reject_reason_counts": summary["reject_reason_counts"],
        "clean_out": rel(clean_out),
        "rejected_out": rel(rejected_out),
        "json_out": rel(json_out),
        "md_out": rel(md_out),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
