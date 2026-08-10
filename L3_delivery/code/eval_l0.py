"""阶段 E · L0 格式质检（成功率 + schema 合规 + 长度/分布 warning）。
用法：python src/eval_l0.py
读配置中的 out_dir/*.jsonl，输出 eval_dir/l0_report.json 并打印。
"""
import sys
import argparse
import json
import re
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

VALID_STYLES = {"qa", "multi_style"}
MIN_CONTENT_LENGTH_BY_STYLE = {"qa": 500, "multi_style": 490}
MIN_CONTENT_LENGTH_BY_INTERNAL_STYLE = {
    "encyclopedia": 490,
    "textbook": 490,
    "blog": 490,
    "abstract": 150,
}
DEFAULT_MIN_CONTENT_LENGTH = 500
QA_MARKERS = ("Question:", "Answer:", "问题：", "答案：")
INTERNAL_STYLE_MARKERS = (
    "encyclopedia style",
    "textbook style",
    "blog style",
    "abstract style",
    "_internal_style",
)
ENCODING_ARTIFACT_RE = re.compile(
    r"(?:\ufffd|\u9225|\u00e2[\u0080-\u009f\u20ac]|"
    r"\u00c3[\u0080-\u00bf]|\u00c2[\u0080-\u00bf])"
)
HTML_ARTIFACT_RE = re.compile(
    r"<(?:/?(?:p|div|br|h[1-6]|a|script|style))\b|"
    r"&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+|#x[0-9A-Fa-f]+);",
    re.IGNORECASE,
)


def min_content_length(style: str | None, internal_style: str | None = None) -> int:
    if internal_style:
        return MIN_CONTENT_LENGTH_BY_INTERNAL_STYLE.get(internal_style, DEFAULT_MIN_CONTENT_LENGTH)
    return MIN_CONTENT_LENGTH_BY_STYLE.get(style, DEFAULT_MIN_CONTENT_LENGTH)


def check_records(records: list[dict], length_records: list[dict] | None = None) -> dict:
    n = len(records)
    field_ok = exact_schema = style_ok = nonempty = 0
    underscore_leaks = qa_marker_hits = internal_style_marker_hits = 0
    encoding_artifact_hits = html_artifact_hits = 0
    style_dist = Counter()
    for r in records:
        field_ok += all(k in r for k in ("uid", "content", "style"))
        exact_schema += set(r) == {"uid", "content", "style"}
        style = r.get("style")
        style_ok += style in VALID_STYLES
        c = r.get("content", "")
        nonempty += bool(c and c.strip())
        underscore_leaks += any(key.startswith("_") for key in r)
        qa_marker_hits += sum(marker in c for marker in QA_MARKERS)
        internal_style_marker_hits += sum(marker in c.lower() for marker in INTERNAL_STYLE_MARKERS)
        encoding_artifact_hits += bool(ENCODING_ARTIFACT_RE.search(c))
        html_artifact_hits += bool(HTML_ARTIFACT_RE.search(c))
        style_dist[style] += 1

    length_records = length_records or records
    lengths = []
    len_below_min = 0
    len_warning_by_style = Counter()
    len_warning_by_internal_style = Counter()
    internal_style_dist = Counter()
    for r in length_records:
        style = r.get("style")
        internal_style = r.get("_internal_style")
        length = len(r.get("content", ""))
        lengths.append(length)
        if internal_style:
            internal_style_dist[internal_style] += 1
        if length < min_content_length(style, internal_style):
            len_below_min += 1
            len_warning_by_style[style] += 1
            if internal_style:
                len_warning_by_internal_style[internal_style] += 1
    lengths.sort()

    def pct(p):
        return lengths[int(p * (len(lengths) - 1))] if lengths else 0

    return {
        "n": n,
        "field_complete_rate": round(field_ok / max(n, 1), 4),
        "official_schema_exact_rate": round(exact_schema / max(n, 1), 4),
        "style_valid_rate": round(style_ok / max(n, 1), 4),
        "nonempty_rate": round(nonempty / max(n, 1), 4),
        "underscore_field_leak_count": underscore_leaks,
        "qa_marker_hit_count": qa_marker_hits,
        "internal_style_marker_hit_count": internal_style_marker_hits,
        "encoding_artifact_hit_count": encoding_artifact_hits,
        "html_artifact_hit_count": html_artifact_hits,
        "len_p05": pct(0.05), "len_p50": pct(0.50), "len_p95": pct(0.95),
        "min_content_length_by_style": MIN_CONTENT_LENGTH_BY_STYLE,
        "min_content_length_by_internal_style": MIN_CONTENT_LENGTH_BY_INTERNAL_STYLE,
        "len_warning_below_min_content_length": len_below_min,
        "len_warning_by_style": dict(len_warning_by_style),
        "len_warning_by_internal_style": dict(len_warning_by_internal_style),
        "len_above_9000": sum(l > 9000 for l in lengths),
        "style_distribution": dict(style_dist),
        "internal_style_distribution": dict(internal_style_dist),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=None, help="Override synthesis output directory")
    parser.add_argument("--eval-dir", default=None, help="Override evaluation directory")
    args = parser.parse_args()
    cfg = C.load_config()
    out = Path(args.out_dir or cfg["paths"]["out_dir"])
    eval_dir = Path(args.eval_dir or cfg["paths"]["eval_dir"])
    report = {}
    # 合成阶段记录的成功率
    stats_path = out / "run_stats.json"
    if stats_path.exists():
        report["synthesis_success"] = json.loads(stats_path.read_text(encoding="utf-8"))
    # schema/长度检查；长度 warning 优先使用 *_full.jsonl 以保留内部风格。
    for name, fn, full_fn in [("qa", "qa_synthetic.jsonl", "qa_full.jsonl"),
                              ("multi_style", "multi_style_synthetic.jsonl", "multi_style_full.jsonl")]:
        p = out / fn
        full_p = out / full_fn
        if p.exists():
            records = C.read_jsonl(p)
            length_records = C.read_jsonl(full_p) if full_p.exists() else records
            report[name] = check_records(records, length_records)
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "l0_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
