"""Run a rewrite-only experiment without editing config.yaml or running QA.

Default seed source is the existing `multi_style_full.jsonl` so prompt versions
can be compared on the same source documents.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import BadRequestError
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import common as C  # noqa: E402
from synthesize import load_seed  # noqa: E402


class MeteredLLMError(RuntimeError):
    def __init__(self, message: str, metrics: dict[str, Any]):
        super().__init__(message)
        self.metrics = metrics


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def process_rss_bytes() -> int | None:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if ok:
            return int(counters.WorkingSetSize)
        return None

    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * 1024 if sys.platform != "darwin" else value
    except Exception:
        return None


class ResourceSampler:
    def __init__(self, interval_seconds: float = 1.0):
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_perf = 0.0

    def start(self) -> None:
        self._start_perf = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1)
        self._record()

    def _record(self) -> None:
        self.samples.append({
            "elapsed_seconds": round(time.perf_counter() - self._start_perf, 4),
            "process_cpu_seconds": round(time.process_time(), 4),
            "rss_bytes": process_rss_bytes(),
        })

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._record()

    def summary(self) -> dict[str, Any]:
        rss_values = [item["rss_bytes"] for item in self.samples if item.get("rss_bytes") is not None]
        return {
            "sample_count": len(self.samples),
            "rss_bytes_max": max(rss_values) if rss_values else None,
            "rss_bytes_last": rss_values[-1] if rss_values else None,
        }


def usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif isinstance(usage, dict):
        data = dict(usage)
    else:
        data = {
            key: getattr(usage, key)
            for key in [
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "input_tokens",
                "output_tokens",
            ]
            if hasattr(usage, key)
        }
    return {
        key: value
        for key, value in data.items()
        if isinstance(value, (int, float, str, bool, type(None), list, dict))
    }


def finish_reason(resp: Any) -> str | None:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return None
    return getattr(choices[0], "finish_reason", None)


def chat_with_metrics(
    llm: C.LLM,
    model: str,
    system: str,
    user: str,
    temperature: float,
) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    start_cpu = time.process_time()
    api_attempts = 0
    retry_sleeps_seconds = 0
    omit_temperature = False
    last_err: Exception | None = None

    def build_kwargs() -> dict[str, Any]:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": llm.max_tokens,
            "extra_headers": llm.extra_headers or None,
        }
        # 关键：透传 extra_body（含 chat_template_kwargs:{enable_thinking:false}）。
        # 漏传会让本地推理模型思维链不关，吃满 max_tokens 导致正文被截断（见 rewrite_v4_3_6_skeleton_result.md）。
        if getattr(llm, "extra_body", None):
            kwargs["extra_body"] = llm.extra_body
        if not omit_temperature:
            kwargs["temperature"] = temperature
        return kwargs

    for attempt in range(llm.max_retries):
        try:
            api_attempts += 1
            resp = llm.client.chat.completions.create(**build_kwargs())
            metrics = {
                "api_attempts": api_attempts,
                "elapsed_seconds": round(time.perf_counter() - start, 4),
                "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
                "retry_sleeps_seconds": retry_sleeps_seconds,
                "temperature_omitted": omit_temperature,
                "finish_reason": finish_reason(resp),
                "usage": usage_to_dict(getattr(resp, "usage", None)),
            }
            return resp.choices[0].message.content or "", metrics
        except BadRequestError as exc:
            if (
                getattr(exc, "status_code", None) == 400
                and "temperature" in str(exc).lower()
                and not omit_temperature
            ):
                omit_temperature = True
                try:
                    api_attempts += 1
                    resp = llm.client.chat.completions.create(**build_kwargs())
                    metrics = {
                        "api_attempts": api_attempts,
                        "elapsed_seconds": round(time.perf_counter() - start, 4),
                        "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
                        "retry_sleeps_seconds": retry_sleeps_seconds,
                        "temperature_omitted": omit_temperature,
                        "finish_reason": finish_reason(resp),
                        "usage": usage_to_dict(getattr(resp, "usage", None)),
                    }
                    return resp.choices[0].message.content or "", metrics
                except Exception as retry_err:
                    last_err = retry_err
            else:
                last_err = exc
            sleep_seconds = 2 ** attempt
            retry_sleeps_seconds += sleep_seconds
            time.sleep(sleep_seconds)
        except Exception as exc:
            last_err = exc
            sleep_seconds = 2 ** attempt
            retry_sleeps_seconds += sleep_seconds
            time.sleep(sleep_seconds)

    metrics = {
        "api_attempts": api_attempts,
        "elapsed_seconds": round(time.perf_counter() - start, 4),
        "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
        "retry_sleeps_seconds": retry_sleeps_seconds,
        "temperature_omitted": omit_temperature,
        "usage": {},
    }
    raise MeteredLLMError(f"LLM 调用失败（{llm.max_retries} 次）: {last_err}", metrics)


def token_field(usage: dict[str, Any], primary: str, fallback: str) -> int:
    value = usage.get(primary, usage.get(fallback, 0))
    return int(value) if isinstance(value, (int, float)) else 0


def normalize_call_perf(
    perf: dict[str, Any] | None,
    *,
    doc_index: int,
    internal_style: str,
    input_chars: int,
    output_chars: int,
    ok: bool,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Flatten provider usage into a stable, reviewable per-call row."""
    perf = perf or {}
    usage = perf.get("usage") or {}
    row = {
        **perf,
        "api_attempts": int(perf.get("api_attempts") or 0),
        "elapsed_seconds": float(perf.get("elapsed_seconds") or 0.0),
        "process_cpu_seconds": float(perf.get("process_cpu_seconds") or 0.0),
        "retry_sleeps_seconds": float(perf.get("retry_sleeps_seconds") or 0.0),
        "finish_reason": perf.get("finish_reason"),
        "prompt_tokens": token_field(usage, "prompt_tokens", "input_tokens"),
        "completion_tokens": token_field(usage, "completion_tokens", "output_tokens"),
        "total_tokens": int(
            usage.get("total_tokens")
            or token_field(usage, "prompt_tokens", "input_tokens")
            + token_field(usage, "completion_tokens", "output_tokens")
        ),
        "input_chars": int(input_chars),
        "output_chars": int(output_chars),
        "doc_index": doc_index,
        "internal_style": internal_style,
        "ok": bool(ok),
    }
    if failure_reason:
        row["failure_reason"] = failure_reason
    return row


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[int(p * (len(ordered) - 1))]


def length_summary(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "max": None, "mean": None, "p05": None, "p50": None, "p95": None}
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 4),
        "p05": percentile(values, 0.05),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
    }


def build_performance_summary(
    perf_rows: list[dict[str, Any]],
    docs: list[str],
    prompts: dict[str, str],
    styles: list[str],
    wall_seconds: float,
    process_cpu_seconds: float,
    sampler_summary: dict[str, Any],
) -> dict[str, Any]:
    usage_rows = [row.get("usage") or {} for row in perf_rows]
    usage_available_rows = [
        usage for usage in usage_rows
        if (
            usage.get("total_tokens") is not None
            or usage.get("prompt_tokens") is not None
            or usage.get("input_tokens") is not None
        )
    ]
    prompt_tokens = sum(token_field(usage, "prompt_tokens", "input_tokens") for usage in usage_available_rows)
    completion_tokens = sum(token_field(usage, "completion_tokens", "output_tokens") for usage in usage_available_rows)
    total_tokens = sum(
        int(usage.get("total_tokens") or token_field(usage, "prompt_tokens", "input_tokens") + token_field(usage, "completion_tokens", "output_tokens"))
        for usage in usage_available_rows
    )
    input_chars_by_style = {
        style: sum(
            int(row.get("input_chars") or 0)
            for row in perf_rows
            if row.get("internal_style") == style
        )
        for style in styles
    }
    output_chars = sum(int(row.get("output_chars") or 0) for row in perf_rows if row.get("ok"))
    success_rows = [row for row in perf_rows if row.get("ok")]
    elapsed_values = [float(row["elapsed_seconds"]) for row in perf_rows if isinstance(row.get("elapsed_seconds"), (int, float))]
    api_attempts = sum(int(row.get("api_attempts") or 0) for row in perf_rows)
    total_jobs = len(perf_rows)
    output_lengths_by_style = {
        style: length_summary([
            int(row.get("output_chars") or 0)
            for row in success_rows
            if row.get("internal_style") == style
        ])
        for style in styles
    }
    cpu_percent_of_one_core = process_cpu_seconds / wall_seconds * 100 if wall_seconds > 0 else None
    cpu_percent_all_cores = (
        process_cpu_seconds / wall_seconds / max(os.cpu_count() or 1, 1) * 100
        if wall_seconds > 0 else None
    )
    return {
        "wall_seconds": round(wall_seconds, 4),
        "process_cpu_seconds": round(process_cpu_seconds, 4),
        "process_cpu_percent_of_one_core": round(cpu_percent_of_one_core, 4) if cpu_percent_of_one_core is not None else None,
        "process_cpu_percent_all_cores": round(cpu_percent_all_cores, 4) if cpu_percent_all_cores is not None else None,
        "cpu_count": os.cpu_count(),
        **sampler_summary,
        "docs": len(docs),
        "seed_docs": len(docs),
        "rewrite_attempt": total_jobs,
        "rewrite_ok": len(success_rows),
        "rewrite_failure": total_jobs - len(success_rows),
        "rewrite_success_rate": round(len(success_rows) / max(total_jobs, 1), 4),
        "api_attempts_total": api_attempts,
        "api_attempts": api_attempts,
        "api_attempts_per_100_rewrite": round(api_attempts / max(total_jobs, 1) * 100, 4),
        "request_elapsed_seconds_mean": round(sum(elapsed_values) / len(elapsed_values), 4) if elapsed_values else None,
        "request_elapsed_seconds_min": round(min(elapsed_values), 4) if elapsed_values else None,
        "request_elapsed_seconds_max": round(max(elapsed_values), 4) if elapsed_values else None,
        "input_chars_total": sum(input_chars_by_style.values()),
        "input_chars_by_style": input_chars_by_style,
        "output_chars_total": output_chars,
        "input_chars_per_rewrite": round(sum(input_chars_by_style.values()) / max(total_jobs, 1), 4),
        "output_chars_per_rewrite": round(output_chars / max(len(success_rows), 1), 4),
        "input_chars_per_100_rewrite": round(sum(input_chars_by_style.values()) / max(total_jobs, 1) * 100, 4),
        "output_chars_per_100_rewrite": round(output_chars / max(len(success_rows), 1) * 100, 4),
        "output_length_by_internal_style": output_lengths_by_style,
        "token_usage_available_rows": len(usage_available_rows),
        "token_usage_missing_rows": total_jobs - len(usage_available_rows),
        "prompt_tokens_total": prompt_tokens if usage_available_rows else None,
        "completion_tokens_total": completion_tokens if usage_available_rows else None,
        "total_tokens_total": total_tokens if usage_available_rows else None,
        "prompt_tokens": prompt_tokens if usage_available_rows else None,
        "completion_tokens": completion_tokens if usage_available_rows else None,
        "total_tokens": total_tokens if usage_available_rows else None,
        "avg_tokens_per_rewrite": round(total_tokens / total_jobs, 4)
        if usage_available_rows and len(usage_available_rows) == total_jobs else None,
        "avg_tokens_per_metered_rewrite": round(total_tokens / max(len(usage_available_rows), 1), 4)
        if usage_available_rows else None,
        "total_tokens_per_rewrite": round(total_tokens / total_jobs, 4)
        if usage_available_rows and len(usage_available_rows) == total_jobs else None,
        "tokens_per_100_rewrite": round(total_tokens / total_jobs * 100, 4)
        if usage_available_rows and len(usage_available_rows) == total_jobs else None,
        "total_tokens_per_100_rewrite": round(total_tokens / total_jobs * 100, 4)
        if usage_available_rows and len(usage_available_rows) == total_jobs else None,
        "tokens_per_100_metered_rewrite": round(total_tokens / max(len(usage_available_rows), 1) * 100, 4)
        if usage_available_rows else None,
    }


EN_WEB_WRAPPER_RE = re.compile(
    r"\b(?:subscribe|newsletter|read more|related posts?|advertisement|sponsored|"
    r"comments?|share|tweet|follow us|contact us|privacy policy|terms of use)\b",
    re.IGNORECASE,
)
EN_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
EN_HTML_RE = re.compile(
    r"<(?:/?(?:p|div|br|h[1-6]|a|script|style))\b|"
    r"&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+|#x[0-9A-Fa-f]+);",
    re.IGNORECASE,
)
EN_ENCODING_ARTIFACT_RE = re.compile(
    r"(?:\ufffd|\u9225|\u00e2[\u0080-\u009f\u20ac]|"
    r"\u00c3[\u0080-\u00bf]|\u00c2[\u0080-\u00bf])"
)
EN_MOJIBAKE_RE = re.compile(
    r"[\u5e90\u6845\u8133\u87fa-\u87ff\u9220-\u923f\u9500-\u953f]"
)
EN_NON_LATIN_SCRIPT_RE = re.compile(
    r"[\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u3040-\u30ff\u3400-\u9fff]"
)
EN_FORUM_RE = re.compile(
    r"\b(?:username|user name|posted by|join date|member since|reply|quote|"
    r"looking for advice|what do you think|anyone else|help me choose|"
    r"to no avail|head canons?|i searched all over)\b",
    re.IGNORECASE,
)
EN_PROMO_RE = re.compile(
    r"\b(?:buy now|order now|coupon|promo code|affiliate|discount|sale price|"
    r"limited time|free shipping|shipping and handling|pricing program|"
    r"ships free|check out|shop now|price:|from \$|free download|"
    r"download .* for mac|safety suite|latest malware|sales consultant)\b",
    re.IGNORECASE,
)
EN_ACCOUNT_WRAPPER_RE = re.compile(
    r"\b(?:get access|lost your password|please enter your email address|"
    r"create a new password|sign in|log in|register|my account)\b",
    re.IGNORECASE,
)
EN_SERVICE_DOMAIN_RE = re.compile(
    r"\b(?:roofing|electrician|electrical systems?|plumbing|hvac|"
    r"home improvement|contractor|repair services?|installation services?|"
    r"information technology|it infrastructure|managed it)\b",
    re.IGNORECASE,
)
EN_SERVICE_MARKETING_RE = re.compile(
    r"\b(?:reliable|skilled|professional|experienced|licensed|insured|"
    r"services?|customers?|contact|call|quote|whether you need|your home|"
    r"your roof|our team|we can|we offer|customer satisfaction)\b",
    re.IGNORECASE,
)
EN_PERSONAL_FRAGMENT_RE = re.compile(
    r"\b(?:hi,?\s+i(?:'| a)m|my qualifications|my experience|"
    r"i have a passion|as an educator with over|my grandfather)\b",
    re.IGNORECASE,
)
EN_BIBLIOGRAPHIC_FRAGMENT_RE = re.compile(
    r"\b(?:list of publications on a keyword|book chapter or conference paper title|"
    r"publication \(\s*name of journal\s*\)|proceedings of|"
    r"additional document info)\b",
    re.IGNORECASE,
)
EN_COURSE_LISTING_RE = re.compile(
    r"\b(?:introducing [a-z]+ letters|identify the words and sentences|"
    r"identify grammars|lesson planner|quick checks|post-level assessment)\b",
    re.IGNORECASE,
)
EN_SOFT_PROMO_CONTEXT_RE = re.compile(
    r"\b(?:roundup of the best|best [a-z ]+ of the year|"
    r"memorial tree planted|when you choose to have|your wardrobe|"
    r"showcase team spirit|back to school season|your organization|"
    r"your business|our bulletin|our program|our team|we have dedicated|"
    r"we offer|please note: this article is a starting point|"
    r"in no way do i believe)\b",
    re.IGNORECASE,
)
EN_PROMO_SINGLE_RE = re.compile(
    r"(?:roundup of the best|best [a-z ]+ of the year|"
    r"back to school season|it(?:'| i)s that time of the year again|"
    r"search engine optimization \(seo\)|we have dedicated)",
    re.IGNORECASE,
)
EN_DESCENDING_RANGE_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|~|to)\s*(\d+(?:\.\d+)?)(?!\d)"
)
EN_TRUNCATION_TAIL_RE = re.compile(
    r"(?:\b(?:a|an|and|as|at|be|by|for|from|in|is|it|of|on|or|the|to|was|were|with)\b|"
    r"[A-Za-z]{1,3})$",
    re.IGNORECASE,
)
EN_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]?$")


def english_seed_risk_labels(text: str) -> list[str]:
    """Conservative English seed audit used only by the opt-in rewrite runner."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labels: list[str] = []
    wrapper_hits = len(EN_WEB_WRAPPER_RE.findall(text))
    if wrapper_hits >= 2 or any(
        re.fullmatch(
            r"(?:subscribe|newsletter|read more|related posts?|advertisement|sponsored|comments?|share|follow us)",
            line,
            flags=re.IGNORECASE,
        )
        for line in lines
    ) or EN_ACCOUNT_WRAPPER_RE.search(text):
        labels.append("web_wrapper_noise")
    urls = len(EN_URL_RE.findall(text))
    if urls >= 3 or (urls >= 1 and len(text) > 0 and urls * 80 > len(text)):
        labels.append("url_link_heavy")
    if EN_HTML_RE.search(text):
        labels.append("html_artifact")
    if EN_ENCODING_ARTIFACT_RE.search(text) or EN_MOJIBAKE_RE.search(text):
        labels.append("encoding_artifact")
    letters = [char for char in text if char.isalpha()]
    ascii_letters = sum(1 for char in letters if char.isascii())
    if len(letters) >= 200 and ascii_letters / max(len(letters), 1) < 0.65:
        labels.append("non_english_text")
    non_latin_script_chars = len(EN_NON_LATIN_SCRIPT_RE.findall(text))
    if non_latin_script_chars >= 40 or non_latin_script_chars / max(len(text), 1) > 0.05:
        labels.append("non_english_text")
    if EN_FORUM_RE.search(text) or EN_PERSONAL_FRAGMENT_RE.search(text):
        labels.append("forum_personal_fragment")
    promo_hits = len(EN_PROMO_RE.findall(text))
    service_hits = len(EN_SERVICE_MARKETING_RE.findall(text))
    soft_promo_hits = len(EN_SOFT_PROMO_CONTEXT_RE.findall(text))
    if (
        EN_PROMO_SINGLE_RE.search(text)
        or promo_hits >= 2
        or soft_promo_hits >= 2
        or (EN_SERVICE_DOMAIN_RE.search(text) and service_hits >= 5)
    ):
        labels.append("ecommerce_affiliate_promotion")
    if EN_BIBLIOGRAPHIC_FRAGMENT_RE.search(text):
        labels.append("bibliographic_metadata_fragment")
    if EN_COURSE_LISTING_RE.search(text) and len(lines) <= 30:
        labels.append("course_listing_fragment")
    if len(text) >= 300:
        tail = text.rstrip()
        sentence_end_count = len(re.findall(r"[.!?]", tail))
        tail_fragment = tail.rsplit(maxsplit=1)[-1] if tail.split() else ""
        if (
            sentence_end_count >= 3
            and not EN_SENTENCE_END_RE.search(tail)
            and len(tail_fragment) <= 5
            and EN_TRUNCATION_TAIL_RE.search(tail)
        ):
            labels.append("abrupt_truncation")
        elif (
            len(tail) >= 7900
            and sentence_end_count >= 3
            and not EN_SENTENCE_END_RE.search(tail)
            and re.fullmatch(r"[A-Za-z]{1,12}", tail_fragment or "")
        ):
            labels.append("abrupt_truncation")
    for match in EN_DESCENDING_RANGE_RE.finditer(text):
        try:
            if float(match.group(2)) < float(match.group(1)):
                labels.append("corrupted_numeric_range")
                break
        except ValueError:
            continue
    return list(dict.fromkeys(labels))


def extract_seed_text(row: dict[str, Any]) -> str:
    return str(
        row.get("_source_text")
        or row.get("content")
        or row.get("text")
        or ""
    ).strip()


def collect_seed_docs(
    rows: Any,
    limit: int | None,
    *,
    min_chars: int | None = None,
    max_chars: int | None = None,
    filter_mode: str = "none",
    max_scan: int | None = None,
    text_getter=extract_seed_text,
) -> tuple[list[str], dict[str, Any]]:
    docs: list[str] = []
    seen: set[str] = set()
    scanned = candidates = 0
    truncated = dropped_short = dropped_quality = 0
    label_counts: Counter[str] = Counter()
    for row in rows:
        scanned += 1
        if max_scan is not None and scanned > max_scan:
            break
        doc = text_getter(row)
        if not doc or doc in seen:
            continue
        if min_chars is not None and len(doc) < min_chars:
            dropped_short += 1
            continue
        if min_chars is not None:
            candidates += 1
        if max_chars is not None and len(doc) > max_chars:
            doc = doc[:max_chars].rstrip()
            truncated += 1
        if filter_mode == "en-soft":
            labels = english_seed_risk_labels(doc)
            if labels:
                dropped_quality += 1
                label_counts.update(labels)
                continue
        seen.add(doc)
        docs.append(doc)
        if limit is not None and len(docs) >= limit:
            break
    return docs, {
        "seed_scanned": scanned,
        "seed_candidates_ge_min_chars": candidates,
        "seed_kept": len(docs),
        "seed_truncated_to_max_chars": truncated,
        "seed_dropped_short": dropped_short,
        "seed_dropped_quality": dropped_quality,
        "seed_quality_filter": filter_mode,
        "seed_quality_reject_labels": dict(label_counts),
    }


def read_existing_seed_docs(path: Path, limit: int | None) -> list[str]:
    docs, _ = collect_seed_docs(
        C.read_jsonl(path),
        limit,
        text_getter=lambda row: str(row.get("_source_text") or "").strip(),
    )
    return docs


def read_seed_jsonl_docs(path: Path, limit: int | None) -> list[str]:
    docs, _ = collect_seed_docs(C.read_jsonl(path), limit)
    return docs


def read_seed_jsonl_docs_with_stats(
    path: Path,
    limit: int | None,
    *,
    min_chars: int | None,
    max_chars: int | None,
    filter_mode: str,
) -> tuple[list[str], dict[str, Any]]:
    return collect_seed_docs(
        C.read_jsonl(path),
        limit,
        min_chars=min_chars,
        max_chars=max_chars,
        filter_mode=filter_mode,
    )


def read_hf_seed_docs(
    *,
    dataset_name: str,
    split: str,
    language: str,
    limit: int | None,
    min_chars: int | None,
    max_chars: int | None,
    filter_mode: str,
) -> tuple[list[str], dict[str, Any]]:
    from datasets import load_dataset

    data_files = f"data/ultrafineweb_{language}/*.parquet"
    try:
        dataset = load_dataset(dataset_name, data_files=data_files, split=split, streaming=True)
    except (FileNotFoundError, ValueError):
        hf_path = f"hf://datasets/{dataset_name}/{data_files}"
        dataset = load_dataset("parquet", data_files=hf_path, split=split, streaming=True)
    target = limit if limit is not None else 500
    return collect_seed_docs(
        dataset,
        target,
        min_chars=min_chars,
        max_chars=max_chars,
        filter_mode=filter_mode,
        max_scan=max(target * 50, 1000),
    )


def read_config_seed_docs(cfg: dict[str, Any], limit: int | None) -> list[str]:
    cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
    if limit is not None:
        cfg["seed"]["n_docs"] = limit
    return load_seed(cfg)


def run_one_style(
    llm: C.LLM,
    cfg: dict[str, Any],
    prompts: dict[str, str],
    doc: str,
    doc_index: int,
    style: str,
) -> dict[str, Any]:
    model = cfg["llm"]["synth_model"]
    temperature = cfg["llm"]["temperature"]
    sys_prompt = prompts["_system"]
    template = prompts.get(style)
    if not template:
        perf = normalize_call_perf(
            {},
            doc_index=doc_index,
            internal_style=style,
            input_chars=0,
            output_chars=0,
            ok=False,
            failure_reason="missing style prompt",
        )
        return {
            "ok": False,
            "perf": perf,
            "failure": {
                "idx": doc_index,
                "task": "rewrite",
                "_internal_style": style,
                "raw_prefix": "",
                "reason": "missing style prompt",
            },
        }

    try:
        user = template.replace("{document}", doc)
        input_chars = len(sys_prompt) + len(user)
        raw, raw_perf = chat_with_metrics(llm, model, sys_prompt, user, temperature)
        rewrite = C.parse_rewrite(raw)
        if rewrite and rewrite.content.strip():
            content = rewrite.content.strip()
            perf = normalize_call_perf(
                raw_perf,
                doc_index=doc_index,
                internal_style=style,
                input_chars=input_chars,
                output_chars=len(content),
                ok=True,
            )
            return {
                "ok": True,
                "perf": {**perf, "raw_chars": len(raw)},
                "record": {
                    "uid": str(uuid.uuid4()),
                    "content": content,
                    "style": "multi_style",
                    "_internal_style": style,
                    "_source_text": doc,
                },
            }
        perf = normalize_call_perf(
            raw_perf,
            doc_index=doc_index,
            internal_style=style,
            input_chars=input_chars,
            output_chars=0,
            ok=False,
            failure_reason="parse_rewrite returned empty result or blank content",
        )
        return {
            "ok": False,
            "perf": {**perf, "raw_chars": len(raw)},
            "failure": {
                "idx": doc_index,
                "task": "rewrite",
                "_internal_style": style,
                "raw_prefix": raw[:500],
                "reason": "parse_rewrite returned empty result or blank content",
            },
        }
    except MeteredLLMError as e:
        perf = normalize_call_perf(
            e.metrics,
            doc_index=doc_index,
            internal_style=style,
            input_chars=len(sys_prompt) + len(template.replace("{document}", doc)),
            output_chars=0,
            ok=False,
            failure_reason=str(e),
        )
        return {
            "ok": False,
            "perf": perf,
            "failure": {
                "idx": doc_index,
                "task": "rewrite",
                "_internal_style": style,
                "raw_prefix": "",
                "reason": str(e),
            },
        }
    except Exception as e:
        perf = normalize_call_perf(
            {},
            doc_index=doc_index,
            internal_style=style,
            input_chars=len(sys_prompt) + len(template.replace("{document}", doc)),
            output_chars=0,
            ok=False,
            failure_reason=str(e),
        )
        return {
            "ok": False,
            "perf": {**perf, "raw_chars": 0},
            "failure": {
                "idx": doc_index,
                "task": "rewrite",
                "_internal_style": style,
                "raw_prefix": "",
                "reason": str(e),
            },
        }


def record_with_perf(record: dict[str, Any], doc_index: int, perf: dict[str, Any]) -> dict[str, Any]:
    output = dict(record)
    output.update({
        "_doc_index": doc_index,
        "_api_attempts": perf["api_attempts"],
        "_elapsed_seconds": perf["elapsed_seconds"],
        "_process_cpu_seconds": perf["process_cpu_seconds"],
        "_retry_sleeps_seconds": perf["retry_sleeps_seconds"],
        "_finish_reason": perf.get("finish_reason"),
        "_prompt_tokens": perf["prompt_tokens"],
        "_completion_tokens": perf["completion_tokens"],
        "_total_tokens": perf["total_tokens"],
        "_input_chars": perf["input_chars"],
        "_output_chars": perf["output_chars"],
    })
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-source",
        choices=["existing", "config", "jsonl", "hf"],
        default="existing",
        help=(
            "existing reuses _source_text from --seed-full; "
            "config samples seeds via config.yaml; "
            "jsonl reads fixed docs from --seed-jsonl; "
            "hf streams openbmb/Ultra-FineWeb L2"
        ),
    )
    parser.add_argument("--seed-full", default="data/output/zh/multi_style_full.jsonl")
    parser.add_argument("--seed-jsonl", default="data/seeds/zh_rewrite_low_risk_seed_docs.jsonl")
    parser.add_argument("--out-dir", default="data/output/zh_rewrite_v4_0")
    parser.add_argument("--prompt-path", default=None, help="Override paths.rewrite_prompt_zh/en")
    parser.add_argument("--language", choices=["en", "zh"], default=None)
    parser.add_argument("--hf-dataset", default=None, help="HF dataset name for --seed-source hf")
    parser.add_argument("--hf-split", default=None, help="HF split for --seed-source hf")
    parser.add_argument("--min-chars", type=int, default=None)
    parser.add_argument("--max-chars", type=int, default=None)
    parser.add_argument(
        "--seed-filter",
        choices=["auto", "none", "en-soft"],
        default="auto",
        help="auto enables the conservative English seed audit only for English runs",
    )
    parser.add_argument("--n-docs", type=int, default=None, help="Limit unique source documents")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = C.load_config()
    prompt_path = args.prompt_path or cfg["paths"]["rewrite_prompt_zh"]
    inferred_language = "en" if prompt_path.lower().endswith("_en.yaml") else cfg["seed"].get("language", "en")
    language = args.language or inferred_language
    if args.prompt_path is None and language == "en":
        prompt_path = "src/prompts/rewrite_styles_v3_en.yaml"
    prompt_abs = REPO_ROOT / prompt_path
    prompts = C.load_yaml(prompt_path)
    styles = list(cfg["synthesis"]["rewrite_styles"])
    seed_path = REPO_ROOT / args.seed_full
    seed_jsonl_path = REPO_ROOT / args.seed_jsonl
    out_dir = REPO_ROOT / args.out_dir
    min_chars = args.min_chars if args.min_chars is not None else cfg["seed"].get("min_chars")
    max_chars = args.max_chars if args.max_chars is not None else cfg["seed"].get("max_chars")
    filter_mode = "en-soft" if args.seed_filter == "en-soft" or (
        args.seed_filter == "auto" and language == "en"
    ) else "none"

    if args.seed_source == "existing":
        docs = read_existing_seed_docs(seed_path, args.n_docs)
        seed_stats = {
            "seed_scanned": len(docs),
            "seed_kept": len(docs),
            "seed_quality_filter": "none",
        }
    elif args.seed_source == "jsonl":
        docs, seed_stats = read_seed_jsonl_docs_with_stats(
            seed_jsonl_path,
            args.n_docs,
            min_chars=min_chars,
            max_chars=max_chars,
            filter_mode=filter_mode,
        )
    elif args.seed_source == "hf":
        docs, seed_stats = read_hf_seed_docs(
            dataset_name=args.hf_dataset or cfg["seed"]["hf_dataset"],
            split=args.hf_split or cfg["seed"].get("hf_split", "train"),
            language=language,
            limit=args.n_docs or cfg["seed"].get("n_docs"),
            min_chars=min_chars,
            max_chars=max_chars,
            filter_mode=filter_mode,
        )
    else:
        cfg_for_seed = json.loads(json.dumps(cfg, ensure_ascii=False))
        cfg_for_seed["seed"]["language"] = language
        docs = read_config_seed_docs(cfg_for_seed, args.n_docs)
        seed_stats = {
            "seed_scanned": len(docs),
            "seed_kept": len(docs),
            "seed_quality_filter": "config_load_seed",
        }
    if not docs:
        raise RuntimeError("No source documents found for rewrite-only experiment")

    out_dir.mkdir(parents=True, exist_ok=True)
    partial_paths = {
        "synthetic": out_dir / "multi_style_synthetic.partial.jsonl",
        "full": out_dir / "multi_style_full.partial.jsonl",
        "failures": out_dir / "failures.partial.jsonl",
        "performance_calls": out_dir / "performance_calls.partial.jsonl",
    }
    for partial_path in partial_paths.values():
        partial_path.write_text("", encoding="utf-8", newline="\n")

    llm = C.LLM(cfg)
    workers = max(1, int(cfg.get("synthesis", {}).get("concurrency", 1)))
    ms_full: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    perf_rows: list[dict[str, Any]] = []

    jobs = [(doc_index, doc, style) for doc_index, doc in enumerate(docs) for style in styles]
    start_wall_iso = utc_now()
    start_perf = time.perf_counter()
    start_cpu = time.process_time()
    sampler = ResourceSampler()
    sampler.start()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_one_style, llm, cfg, prompts, doc, doc_index, style): (doc_index, style)
            for doc_index, doc, style in jobs
        }
        completed: dict[tuple[int, str], dict[str, Any]] = {}
        for future in tqdm(as_completed(futures), total=len(futures), desc="rewrite-only"):
            key = futures[future]
            result = future.result()
            completed[key] = result
            doc_index, style = key
            perf = result.get("perf") or normalize_call_perf(
                {},
                doc_index=doc_index,
                internal_style=style,
                input_chars=0,
                output_chars=0,
                ok=False,
                failure_reason="missing perf row",
            )
            append_jsonl(partial_paths["performance_calls"], perf)
            if result.get("ok"):
                record = record_with_perf(result["record"], doc_index, perf)
                append_jsonl(partial_paths["full"], record)
                append_jsonl(partial_paths["synthetic"], C.official_record(record))
            else:
                append_jsonl(partial_paths["failures"], result.get("failure") or {
                    "idx": doc_index,
                    "task": "rewrite",
                    "_internal_style": style,
                    "raw_prefix": "",
                    "reason": "missing failure row",
                })
    sampler.stop()
    wall_seconds = time.perf_counter() - start_perf
    process_cpu_seconds = time.process_time() - start_cpu
    end_wall_iso = utc_now()

    for doc_index, _, style in jobs:
        result = completed[(doc_index, style)]
        perf = result.get("perf") or normalize_call_perf(
            {},
            doc_index=doc_index,
            internal_style=style,
            input_chars=0,
            output_chars=0,
            ok=False,
            failure_reason="missing perf row",
        )
        perf_rows.append(perf)
        if result.get("ok"):
            ms_full.append(record_with_perf(result["record"], doc_index, perf))
        else:
            failures.append(result["failure"])

    C.write_jsonl([C.official_record(row) for row in ms_full], out_dir / "multi_style_synthetic.jsonl")
    C.write_jsonl(ms_full, out_dir / "multi_style_full.jsonl")
    C.write_jsonl(failures, out_dir / "failures.jsonl")
    C.write_jsonl(perf_rows, out_dir / "performance_calls.jsonl")

    performance_summary = build_performance_summary(
        perf_rows,
        docs,
        prompts,
        styles,
        wall_seconds,
        process_cpu_seconds,
        sampler.summary(),
    )
    (out_dir / "performance_summary.json").write_text(
        json.dumps(performance_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    stats = {
        "docs": len(docs),
        "rewrite_attempt": len(jobs),
        "rewrite_ok": len(ms_full),
        "rewrite_failure": len(jobs) - len(ms_full),
        "rewrite_success_rate": round(len(ms_full) / max(len(jobs), 1), 4),
        "do_qa": False,
        "language": language,
        "seed_source": args.seed_source,
        "seed_filter": filter_mode,
        "seed_stats": seed_stats,
        "rewrite_styles": styles,
        "seed_full": rel(seed_path) if args.seed_source == "existing" else None,
        "seed_jsonl": rel(seed_jsonl_path) if args.seed_source == "jsonl" else None,
        "hf_dataset": (args.hf_dataset or cfg["seed"].get("hf_dataset")) if args.seed_source == "hf" else None,
        "hf_split": (args.hf_split or cfg["seed"].get("hf_split", "train")) if args.seed_source == "hf" else None,
        "seed_jsonl_sha256": file_sha256(seed_jsonl_path) if args.seed_source == "jsonl" else None,
        "seed_config_source": cfg["seed"].get("source") if args.seed_source == "config" else None,
        "seed_config_dataset": cfg["seed"].get("hf_dataset") if args.seed_source == "config" else None,
        "prompt_path": prompt_path,
        "prompt_sha256": file_sha256(prompt_abs),
        "out_dir": rel(out_dir),
        "generated_at": end_wall_iso,
        "started_at": start_wall_iso,
        "performance_summary": performance_summary,
        "performance_calls": rel(out_dir / "performance_calls.jsonl"),
        "performance_summary_path": rel(out_dir / "performance_summary.json"),
    }
    (out_dir / "run_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
