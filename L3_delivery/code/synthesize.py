"""阶段 C：合成 pipeline。
读取 L2 种子切片 -> 对每篇跑 QA 生成 + 四种风格改写 -> 解析 -> 组装官方 schema -> 落盘。

用法（仓库根目录）：
    export SILICONFLOW_API_KEY=sk-xxx
    python src/synthesize.py
输出：
    data/output/qa_synthetic.jsonl            (官方 schema: uid, content, style)
    data/output/multi_style_synthetic.jsonl   (官方 schema)
    data/output/*_full.jsonl                  (含 _source_text，供评测用)
    data/output/run_stats.json                (成功率统计 = L0 的一部分)
"""
import sys
import uuid
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common as C
from filter_rewrite_seed_numeric_guard import numeric_guard_reasons


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    pos = 0
    for raw in text.splitlines(True):
        end = pos + len(raw)
        spans.append((pos, end, raw.strip()))
        pos = end
    if text and (not spans or spans[-1][1] < len(text)):
        spans.append((pos, len(text), text[pos:].strip()))
    return spans


def _split_sentences(text: str) -> list[str]:
    parts = [text]
    for sep in "。？！；;!?\n\r":
        parts = [y for x in parts for y in x.split(sep)]
    return [x.strip() for x in parts if x.strip()]


def _is_tail_context(text: str) -> bool:
    return bool(re.search(
        r"二维码|扫码|扫一扫|公众号|朋友圈|转发|在看|点赞|客服|添加微信|福利|"
        r"回复关键词|点击阅读|阅读原文|相关阅读|相关推荐|往期|推荐|赞赏|投稿|联系方式",
        text,
    ))


def _tail_marker_match(line: str) -> str | None:
    exact_or_context = (
        "全文到此为止", "点击阅读原文", "阅读原文", "长按二维码", "扫码关注",
        "扫描二维码", "关注公众号", "点亮在看", "转发到朋友圈", "相关阅读",
        "相关推荐", "往期推荐", "客服微信", "添加微信", "回复关键词",
        "相关链接", "往期精选", "更多阅读", "点个在看", "— END —",
    )
    for marker in exact_or_context:
        if marker == "阅读原文" and re.search(r"阅读原文[献档]", line):
            continue
        if marker in line:
            return marker

    if re.search(r"扫.{0,8}二维码|二维码.{0,8}关注|微信.{0,8}关注|关注.{0,8}公众号", line):
        return "二维码/关注"
    if re.search(r"点.{0,4}在看|在看.{0,6}转发|转发.{0,8}朋友圈", line):
        return "在看/转发"
    if re.fullmatch(r"(?:文末)?福利(?:推荐)?", line):
        return "福利"
    return None


def edit_seed_noise(text: str, min_chars: int) -> tuple[str, bool, str]:
    """Conservatively remove web wrappers while preserving broad page topics."""
    d = text.strip()
    if not d:
        return "", False, "empty"

    reasons = []
    lines = d.splitlines()
    kept_lines = []
    removed_head = []
    seen_body = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        in_head = i < 14 and sum(len(x) + 1 for x in lines[:i]) <= 900
        head_noise = False
        if in_head:
            head_noise = bool(
                re.search(r"点击上方.{0,8}(蓝字|蓝色字)?.{0,8}关注", stripped)
                or re.search(r"扫码关注|长按二维码|扫描二维码", stripped)
                or re.search(r"^回复[【\[].{1,20}[】\]]", stripped)
                or re.search(r"^回复关键词", stripped)
                or re.search(r"^备注[【\[].{1,20}[】\]].{0,12}(群|资料)", stripped)
                or re.search(r"^(首页|登录|注册|导航|菜单|快速链接|上一页|下一页)(\s*[>|｜|/].*)?$", stripped)
            )
            if re.search(r"点击[“\"']?接受[”\"']?加好友", stripped):
                head_noise = True
            if len(stripped) >= 45 and not _is_tail_context(stripped):
                seen_body = True
        if head_noise and (not seen_body or i < 8):
            removed_head.append(stripped[:24] or "blank")
            continue
        kept_lines.append(line)

    if removed_head:
        candidate = "\n".join(kept_lines).strip()
        if len(candidate) >= min_chars:
            d = candidate
            reasons.append("head:" + ",".join(removed_head[:3]))

    spans = _line_spans(d)
    best = None
    n = len(d)
    for start, end, stripped in spans:
        if not stripped:
            continue
        marker = _tail_marker_match(stripped)
        if not marker:
            continue
        tailish = start >= n * 0.45 or n - start <= 1400
        standalone = len(stripped) <= 90 or marker in {"全文到此为止", "— END —", "福利"}
        contextual = _is_tail_context(stripped)
        if not (tailish and (standalone or contextual)):
            continue
        cut_pos = start
        candidate = d[:cut_pos].strip()
        if len(candidate) < min_chars:
            continue
        best = (cut_pos, marker)
        break

    if best:
        cut_pos, marker = best
        d = d[:cut_pos].strip()
        reasons.append(f"tail:{marker}")

    changed = bool(reasons)
    return d, changed, ";".join(reasons) if reasons else "unchanged"


def _ocr_garble_reason(text: str) -> str | None:
    """检测整篇 OCR 严重损坏的中文源文（人读不通、只会污染训练集）。
    只用一条零误伤信号：中文 OCR 断裂特有的异常连续/孤立标点密度。
    （字母级乱码检测被放弃：无法与拉丁学名 Cirrhoscyllium、专名 Cybertruck 等可靠区分，
    易误删正常鱼类学/科技类文档；宁漏勿误删。）
    """
    d = text or ""
    total = max(len(d), 1)
    # "。、" "。。" "，，" "。 。" 这类连续/孤立标点，正常中文极罕见；
    # 数学填空题（"13。函数"）仅约 0.1/百字，安全阈值取 1.5。样例1 命中 2.14。
    weird_punct = len(re.findall(r"[。，、；]{2,}|[。，、；]\s+[。，、；]|[。，、；]。", d))
    if weird_punct / total * 100 >= 1.5:
        return "ocr_garble_punct"
    return None


def is_low_density_seed(text: str) -> tuple[bool, str]:
    """Drop only structurally low-information pages, not topic categories."""
    d = text.strip()
    if not d:
        return True, "empty"

    garble = _ocr_garble_reason(d)
    if garble:
        return True, garble

    lines = [x.strip() for x in d.splitlines() if x.strip()]
    sentences = _split_sentences(d)
    long_sentences = [x for x in sentences if len(x) >= 30]
    very_short_line_ratio = sum(1 for x in lines if len(x) < 12) / max(len(lines), 1)
    link_or_nav = [
        x for x in lines
        if re.search(r"https?://|www\.|上一篇|下一篇|相关阅读|点击文字即可阅读|^[-*•▶←]", x)
        or (" | " in x and len(x) < 80)
        or re.fullmatch(r"(首页|登录|注册|导航|菜单|快速链接|更多|返回|查看详情)", x)
    ]
    conversion_lines = [x for x in lines if _is_tail_context(x)]

    if len(lines) >= 8 and len(link_or_nav) / len(lines) > 0.7:
        return True, "pure_link_navigation"
    if len(lines) >= 6 and len(conversion_lines) / len(lines) > 0.6 and len(long_sentences) < 2:
        return True, "pure_marketing_conversion"
    if very_short_line_ratio > 0.75 and len(long_sentences) < 2 and len(d) < 1200:
        return True, "short_line_low_density"
    if len(sentences) >= 8:
        repeated = sum(1 for x in lines if lines.count(x) > 1 and len(x) >= 6)
        if repeated / max(len(lines), 1) > 0.45 and len(long_sentences) < 3:
            return True, "repetitive_low_density"
    return False, "ok"


def load_seed(cfg: dict) -> list[str]:
    s = cfg["seed"]
    lo, hi, n = s["min_chars"], s["max_chars"], s["n_docs"]
    lang = s.get("language", "en")

    def add_if_kept(txt: str, out: list[str]) -> bool:
        nonlocal truncated, numeric_rejected
        if not txt:
            return False
        d = txt.strip()
        if len(d) < lo:
            return False
        d = d[:hi]
        d, changed, _ = edit_seed_noise(d, lo)
        if changed:
            truncated += 1
        if len(d) < lo:
            return False
        numeric_reasons = numeric_guard_reasons(d)
        if numeric_reasons:
            numeric_rejected += 1
            return False
        low_density, _ = is_low_density_seed(d)
        if low_density:
            return False
        out.append(d)
        return True

    out = []
    scanned = 0
    candidates = 0
    truncated = 0
    numeric_rejected = 0
    if s["source"] == "local":
        for r in C.read_jsonl(Path(s["local_path"])):
            scanned += 1
            txt = r.get("_source_text") or r.get("content") or r.get("text") or ""
            if txt and len(txt.strip()) >= lo:
                candidates += 1
            add_if_kept(txt, out)
            if len(out) >= n:
                break
    else:
        from datasets import load_dataset

        data_files = f"data/ultrafineweb_{lang}/*.parquet"
        try:
            ds = load_dataset(s["hf_dataset"], data_files=data_files,
                              split=s["hf_split"], streaming=True)
        except FileNotFoundError:
            hf_path = f"hf://datasets/{s['hf_dataset']}/{data_files}"
            ds = load_dataset("parquet", data_files=hf_path,
                              split=s["hf_split"], streaming=True)

        max_scan = max(n * 50, 1000)
        for row in ds:
            scanned += 1
            txt = row.get("content") or row.get("text") or ""
            if txt and len(txt.strip()) >= lo:
                candidates += 1
            add_if_kept(txt, out)
            if len(out) >= n or scanned >= max_scan:
                break

    cfg["_filter_stats"] = {
        "filter_scanned": scanned,
        "filter_dropped": scanned - len(out),
        "filter_kept": len(out),
        "numeric_guard_rejected": numeric_rejected,
    }
    print(f"[filter] 过滤前{candidates}篇 截断{truncated}篇 保留{len(out)}篇")
    return out


def synth_one(llm, cfg, prompts, doc: str, idx: int) -> dict:
    """对单篇文档跑所有合成任务，返回 {'qa': record|None, 'rewrites': {style: record|None}, 'failures': list}."""
    m = cfg["llm"]["synth_model"]
    temp = cfg["llm"]["temperature"]
    syn = cfg["synthesis"]
    result = {
        "qa": None,
        "rewrites": {},
        "failures": [],
        "metrics": {
            "api_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "elapsed_seconds": 0.0,
            "cpu_seconds": 0.0,
            "retry_sleeps_seconds": 0.0,
        },
    }

    def add_metrics(meta: dict) -> None:
        result["metrics"]["api_calls"] += int(meta.get("api_attempts") or 0)
        result["metrics"]["elapsed_seconds"] += float(meta.get("elapsed_seconds") or 0.0)
        result["metrics"]["cpu_seconds"] += float(meta.get("process_cpu_seconds") or 0.0)
        result["metrics"]["retry_sleeps_seconds"] += float(meta.get("retry_sleeps_seconds") or 0.0)
        usage = meta.get("usage") or {}
        result["metrics"]["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        result["metrics"]["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        result["metrics"]["total_tokens"] += int(usage.get("total_tokens") or 0)

    if syn["do_qa"]:
        user = prompts["qa"].replace("{document}", doc) \
                            .replace("{min_pairs}", str(syn["qa_min_pairs"])) \
                            .replace("{max_pairs}", str(syn["qa_max_pairs"]))
        raw, meta = llm.chat_detailed(m, "You output only JSON.", user, temp)
        add_metrics(meta)
        qa = C.parse_qa(raw)
        if qa and qa.qa_pairs:
            filtered_qa, removed, adjusted = C.filter_direct_qa(qa, doc)
            if not filtered_qa or not filtered_qa.qa_pairs:
                result["failures"].append({
                    "idx": idx,
                    "task": "qa",
                    "_internal_style": None,
                    "raw_prefix": raw[:500],
                    "reason": "post_filter_removed_all_pairs",
                    "_qa_prefilter_pairs": [
                        {"question": p.question, "answer": p.answer} for p in qa.qa_pairs
                    ],
                })
            else:
                # v3.28 L0 后处理：pair 数超上限时截断到 max（保留靠前的高显著题），
                # 低于下限属如实失败，不补造。只做上限截断这一克制动作。
                max_pairs = int(syn.get("qa_max_pairs", 8))
                if len(filtered_qa.qa_pairs) > max_pairs:
                    removed = list(removed) + [f"over_max_trimmed:{len(filtered_qa.qa_pairs)}->{max_pairs}"]
                    filtered_qa = C.QAOutput(qa_pairs=filtered_qa.qa_pairs[:max_pairs])
                qa_payload = {
                    "uid": str(uuid.uuid4()),
                    "content": C.assemble_qa_content(doc, filtered_qa),
                    "style": "qa",
                    "_source_text": doc,
                }
                if removed:
                    qa_payload["_qa_filter_removed"] = removed
                if adjusted:
                    qa_payload["_qa_filter_adjusted"] = adjusted
                # 过滤前快照：保存删/改前的原始 QA 对，供过滤前后逐题对比（仅用于评审导出，不进最终 schema）
                qa_payload["_qa_prefilter_pairs"] = [
                    {"question": p.question, "answer": p.answer} for p in qa.qa_pairs
                ]
                result["qa"] = qa_payload
        else:
            result["failures"].append({
                "idx": idx,
                "task": "qa",
                "_internal_style": None,
                "raw_prefix": raw[:500],
                "reason": "parse_qa returned empty result or no qa_pairs",
            })

    if syn["do_rewrite"]:
        sys_prompt = prompts["rewrite"]["_system"]
        for style in syn["rewrite_styles"]:
            tmpl = prompts["rewrite"].get(style)
            if not tmpl:
                continue
            user = tmpl.replace("{document}", doc)
            raw, meta = llm.chat_detailed(m, sys_prompt, user, temp)
            add_metrics(meta)
            rw = C.parse_rewrite(raw)
            if rw and rw.content.strip():
                result["rewrites"][style] = {
                    "uid": str(uuid.uuid4()),
                    "content": rw.content.strip(),
                    "style": "multi_style",
                    "_internal_style": style,
                    "_source_text": doc,
                }
            else:
                result["failures"].append({
                    "idx": idx,
                    "task": "rewrite",
                    "_internal_style": style,
                    "raw_prefix": raw[:500],
                    "reason": "parse_rewrite returned empty result or blank content",
                })
    return result


def main():
    cfg = C.load_config()
    llm = C.LLM(cfg)
    paths = cfg["paths"]
    lang = cfg["seed"].get("language", "en")
    if lang == "zh":
        prompts = {
            "qa": C.load_text(paths["qa_prompt_zh"]),
            "rewrite": C.load_yaml(paths["rewrite_prompt_zh"]),
        }
    else:
        prompts = {
            "qa": C.load_text(paths["qa_prompt_en"]),
            "rewrite": C.load_yaml(paths["rewrite_prompt_en"]),
        }

    docs = load_seed(cfg)
    print(f"[seed] 取到 {len(docs)} 篇文档")

    qa_full, ms_full, failures = [], [], []
    stats = {"docs": len(docs), "qa_attempt": 0, "qa_ok": 0,
             "rewrite_attempt": 0, "rewrite_ok": 0,
             "qa_filter_removed": 0, "qa_filter_adjusted": 0,
             "api_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "total_tokens": 0, "elapsed_seconds": 0.0, "cpu_seconds": 0.0,
             "retry_sleeps_seconds": 0.0}

    with ThreadPoolExecutor(max_workers=cfg["synthesis"]["concurrency"]) as ex:
        futs = {ex.submit(synth_one, llm, cfg, prompts, d, i): i for i, d in enumerate(docs)}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="synth"):
            idx = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                print("  [warn] doc failed:", e)
                failures.append({
                    "idx": idx,
                    "task": "doc",
                    "_internal_style": None,
                    "raw_prefix": "",
                    "reason": str(e),
                })
                continue
            metrics = r.get("metrics") or {}
            stats["api_calls"] += int(metrics.get("api_calls") or 0)
            stats["prompt_tokens"] += int(metrics.get("prompt_tokens") or 0)
            stats["completion_tokens"] += int(metrics.get("completion_tokens") or 0)
            stats["total_tokens"] += int(metrics.get("total_tokens") or 0)
            stats["elapsed_seconds"] += float(metrics.get("elapsed_seconds") or 0.0)
            stats["cpu_seconds"] += float(metrics.get("cpu_seconds") or 0.0)
            stats["retry_sleeps_seconds"] += float(metrics.get("retry_sleeps_seconds") or 0.0)
            failures.extend(r.get("failures", []))
            if cfg["synthesis"]["do_qa"]:
                stats["qa_attempt"] += 1
                if r["qa"]:
                    stats["qa_ok"] += 1
                    qa_full.append(r["qa"])
                    stats["qa_filter_removed"] += len(r["qa"].get("_qa_filter_removed") or [])
                    stats["qa_filter_adjusted"] += len(r["qa"].get("_qa_filter_adjusted") or [])
            if cfg["synthesis"]["do_rewrite"]:
                for style in cfg["synthesis"]["rewrite_styles"]:
                    stats["rewrite_attempt"] += 1
                    if r["rewrites"].get(style):
                        stats["rewrite_ok"] += 1
                        ms_full.append(r["rewrites"][style])

    out = Path(cfg["paths"]["out_dir"])
    # 官方 schema
    C.write_jsonl([C.official_record(x) for x in qa_full], out / "qa_synthetic.jsonl")
    C.write_jsonl([C.official_record(x) for x in ms_full], out / "multi_style_synthetic.jsonl")
    # 带原文（供评测）
    C.write_jsonl(qa_full, out / "qa_full.jsonl")
    C.write_jsonl(ms_full, out / "multi_style_full.jsonl")
    C.write_jsonl(failures, out / "failures.jsonl")
    # 成功率
    stats.update(cfg.get("_filter_stats", {}))
    stats["qa_success_rate"] = round(stats["qa_ok"] / max(stats["qa_attempt"], 1), 4)
    stats["rewrite_success_rate"] = round(stats["rewrite_ok"] / max(stats["rewrite_attempt"], 1), 4)
    stats["avg_tokens_per_doc"] = round(stats["total_tokens"] / max(stats["docs"], 1), 2)
    stats["avg_tokens_per_successful_rewrite"] = round(stats["total_tokens"] / max(stats["rewrite_ok"], 1), 2)
    stats["avg_elapsed_seconds_per_doc"] = round(stats["elapsed_seconds"] / max(stats["docs"], 1), 4)
    stats["avg_cpu_seconds_per_doc"] = round(stats["cpu_seconds"] / max(stats["docs"], 1), 4)
    (out / "run_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[done]", json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
