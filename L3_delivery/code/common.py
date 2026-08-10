"""共享工具：配置、LLM 客户端、输出 schema、JSON 解析、IO。
所有脚本 `import common`，从仓库根目录运行（python src/xxx.py）。
"""
import os
import re
import json
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ValidationError
from openai import BadRequestError, OpenAI

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------- 配置 ----------------
def load_config(path: str | None = None) -> dict:
    # 优先 REWRITE_CONFIG 环境变量（本地 5090 用 config.v5090.yaml），否则默认 config.yaml
    config_path = path or os.environ.get("REWRITE_CONFIG") or "config.yaml"
    with open(REPO_ROOT / config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------- 输出 schema ----------------
class QAItem(BaseModel):
    question: str
    answer: str


class QAOutput(BaseModel):
    qa_pairs: list[QAItem]


class RewriteOutput(BaseModel):
    content: str


# ---------------- LLM 客户端 ----------------
class LLM:
    def __init__(self, cfg: dict):
        c = cfg["llm"]
        key = os.environ.get(c["api_key_env"], "")
        if not key:
            raise RuntimeError(f"环境变量 {c['api_key_env']} 未设置，请先 export 你的 API key")
        base_url = os.environ.get(c.get("base_url_env", ""), c.get("base_url", ""))
        if not base_url:
            raise RuntimeError("llm.base_url 未配置，或对应环境变量为空")
        self.client = OpenAI(base_url=base_url, api_key=key,
                             timeout=c.get("request_timeout", 120))
        self.max_tokens = c.get("max_tokens", 4096)
        self.max_retries = c.get("max_retries", 3)
        # OpenRouter 系可选归因头；过滤掉空值
        self.extra_headers = {k: v for k, v in (c.get("extra_headers") or {}).items() if v}
        # 可选透传体（如本地 vLLM 关思考：{"chat_template_kwargs": {"enable_thinking": false}}）
        # 阿里云端点不配置此字段则行为不变。
        self.extra_body = c.get("extra_body") or {}

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            data = usage.model_dump()
        elif isinstance(usage, dict):
            data = dict(usage)
        else:
            data = {
                key: getattr(usage, key)
                for key in ["prompt_tokens", "completion_tokens", "total_tokens"]
                if hasattr(usage, key)
            }
        return {
            key: value
            for key, value in data.items()
            if isinstance(value, (int, float, str, bool, list, dict, type(None)))
        }

    @staticmethod
    def _finish_reason(resp: Any) -> str | None:
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return None
        return getattr(choices[0], "finish_reason", None)

    def chat_detailed(self, model: str, system: str, user: str, temperature: float) -> tuple[str, dict]:
        last_err = None
        omit_temperature = False
        start = time.perf_counter()
        start_cpu = time.process_time()
        api_attempts = 0
        retry_sleeps_seconds = 0
        for attempt in range(self.max_retries):
            try:
                kwargs = dict(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    max_tokens=self.max_tokens,
                    extra_headers=self.extra_headers or None,
                )
                if self.extra_body:
                    kwargs["extra_body"] = self.extra_body
                if not omit_temperature:
                    kwargs["temperature"] = temperature
                api_attempts += 1
                resp = self.client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or "", {
                    "api_attempts": api_attempts,
                    "elapsed_seconds": round(time.perf_counter() - start, 4),
                    "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
                    "retry_sleeps_seconds": retry_sleeps_seconds,
                    "temperature_omitted": omit_temperature,
                    "finish_reason": self._finish_reason(resp),
                    "usage": self._usage_to_dict(getattr(resp, "usage", None)),
                }
            except BadRequestError as e:
                if getattr(e, "status_code", None) == 400 and "temperature" in str(e).lower() and not omit_temperature:
                    omit_temperature = True
                    try:
                        kwargs.pop("temperature", None)
                        api_attempts += 1
                        resp = self.client.chat.completions.create(**kwargs)
                        return resp.choices[0].message.content or "", {
                            "api_attempts": api_attempts,
                            "elapsed_seconds": round(time.perf_counter() - start, 4),
                            "process_cpu_seconds": round(time.process_time() - start_cpu, 4),
                            "retry_sleeps_seconds": retry_sleeps_seconds,
                            "temperature_omitted": omit_temperature,
                            "finish_reason": self._finish_reason(resp),
                            "usage": self._usage_to_dict(getattr(resp, "usage", None)),
                        }
                    except Exception as retry_err:  # 去掉 temperature 后仍失败 → 进入普通退避
                        last_err = retry_err
                else:
                    last_err = e
                sleep_seconds = 2 ** attempt
                retry_sleeps_seconds += sleep_seconds
                time.sleep(sleep_seconds)
            except Exception as e:  # 网络/限速/超时 → 退避重试
                last_err = e
                sleep_seconds = 2 ** attempt
                retry_sleeps_seconds += sleep_seconds
                time.sleep(sleep_seconds)
        raise RuntimeError(f"LLM 调用失败（{self.max_retries} 次）: {last_err}")

    def chat(self, model: str, system: str, user: str, temperature: float) -> str:
        text, _ = self.chat_detailed(model, system, user, temperature)
        return text


# ---------------- JSON 解析（防代码块围栏/前后缀噪声）----------------
def _escape_control_chars_in_strings(text: str) -> str:
    """把 JSON 字符串里的裸换行等控制字符转义，便于宽松解析。"""
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            if ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
                continue
        out.append(ch)
    return "".join(out)


def _strip_reasoning(text: str) -> str:
    """剥掉 reasoning 模型的思维链，只保留最后一个 </think> 之后的正文。"""
    if not text:
        return text
    idx = text.rfind("</think>")
    if idx != -1:
        return text[idx + len("</think>"):]
    return text


def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text).strip()
    candidates = [t]
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(t[start:end + 1])

    for candidate in candidates:
        for payload in (candidate, _escape_control_chars_in_strings(candidate)):
            try:
                obj = json.loads(payload)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                pass
    return None


def parse_qa(raw: str) -> Optional[QAOutput]:
    raw = _strip_reasoning(raw)
    d = extract_json(raw)
    if d is None:
        return None
    try:
        return QAOutput(**d)
    except ValidationError:
        return None


# ---------------- QA light post-filter ----------------
_QA_NOISE_RE = re.compile(
    r"扫码|公众号|客服|福利|转发|朋友圈|点个在看|在看功能|在看哦|"
    r"私信|加群|推荐阅读|阅读原文|打赏|赞赏|恶意举报|红友|月儿|"
    r"新平台维护|(?:添加|加).*微信|微信号|微信公众号"
)
_QA_INFERENTIAL_QUESTION_RE = re.compile(
    r"为什么|为何|原因|目的|作用|意义|体现|表明|说明|如果|会怎样|"
    r"区别|差异|比较|关系如何|是否正确|对不对|能否|可否|主要用于|用于什么|功能"
)
_QA_SPECIFIC_EXTERNAL_RE = re.compile(
    r"化用|典故|庄子|此前(?:未|没有|从未)?|首次|第一次|打破规则|行业惯例|"
    r"历史主动权|革命理想|深远意义|构造函数|运行时|高频脉冲|反馈信号|"
    r"过程变量|工业应用场景|自动调节|突破惯性思维|高阶创新|价值判断|外部知识|"
    r"自律|清白|生活态度|对抗虚无|生命意义|根腐病|无需额外润滑|较低吸入压力|"
    r"稳定工作|五倍高|1\.5倍|1倍。5倍|经营|业务类型|可转让项目"
)
_QA_RISKY_TAIL_RE = re.compile(
    r"用于|便于|提高|实现|推动|体现|展现|说明|意味着|因此|从而|导致|"
    r"适用于|突破|高阶|深刻|历史|革命|自动|控制|调节|机制|原理"
)
_QA_UNANCHORED_PRONOUN_RE = re.compile(r"^(?:前者|后者|上述)(?:内容|说法|做法|情况|观点)?")
# v3.28: 试题类源文的元引用式问法（"题目3中…/第2题里…/上述题目问的是…"）——官方更倾向把题库转成独立题，
# 直接就题目内容发问，而不是"题目X中"这种包裹式元引用。只在明确的元引用包裹时移除。
_QA_TEST_META_REFERENCE_RE = re.compile(
    r"^(?:上述|该|这道|这一?道?|下列)?(?:题目|试题|问题)\s*\d*\s*(?:中|里|里面|中的|所|问的是|要求的是|考查的是)"
    r"|^第\s*\d+\s*题(?:中|里|里面|中的|所)"
)
# 答案里把模型的思考/自我论证过程当正文吐了出来（无 </think> 标签的裸推理泄漏）。
_QA_REASONING_LEAK_RE = re.compile(
    r"根据(?:生物学|物理|化学|数学)?常识|结合(?:上下文|语境|常识)推断|"
    r"若严格(?:依|按|根据)|严格依原文|依据原文文字|此处依据|"
    r"原文表述存在矛盾|表述存在矛盾|OCR错误|原文OCR|OCR质量|"
    r"若视为事实|若视为.{0,6}则|考题意图|考虑到题目要求|结合(?:常见)?考题(?:逻辑)?|"
    r"但通常|通常指|通常降低|个人认为|我认为(?:应|是)?|让我们(?:看|来|选择|分析)"
)
# 原文噪声整块混入答案：网友回复/试题分析/点评等抓取残留。
_QA_SOURCE_NOISE_BLOCK_RE = re.compile(
    r"热心网友|【.{0,8}回复】|回复结束|试题分析|点评[:：]|解析[:：].{0,4}考查"
)
# v3.30 强制题型：27b 遇到"信息撑不起强制题型"时会把纠结/出题过程写进答案（"露怯"元话语），
# 典型如 seed12 判断题尾巴"…该判断题的预设前提不成立。让我们换一个更清晰的判断题。"。
# 这不是编造事实，而是脏格式——把出题人自言自语混进了答案。保守起见只匹配明确描述"出题动作/
# 换题/推翻题目前提"的多字短语（均≥4字），绝不用"我/首先/重新/分析"这类裸词（实测正常答案大量出现）。
_QA_META_DISCOURSE_ANSWER_RE = re.compile(
    r"让我们?换|让我重新|我们换一?(?:个|道)|换一?(?:个|道)(?:更|新的|清晰|别的)|"
    r"让我们(?:选择|看|改用|换用)(?:另一|更|一个|别的|其他)|让我们看另一|"
    r"选择更(?:稳妥|简单|清晰)的(?:事实|判断|选择)?题|"
    r"重新(?:出|设计|想)(?:一?道|一?个|个)|我需要重新|"
    r"(?:该|这|这道|这个|此)(?:判断题|选择题|题目?)(?:的)?(?:预设)?前提不成立|"
    r"前提不成立|无法(?:构成|设计|出)(?:一?道|一?个|有效的?)|"
    r"这(?:道|个)题(?:目)?(?:无法|不能|不成立)|"
    r"让我(?:先)?(?:想一?想|思考|分析一下这)|"
    r"作为(?:出题人|命题人|一名出题|一个出题)|我(?:来|将|会)(?:出|设计|换)(?:一?道|一?个)(?:题|判断|选择)|"
    r"重新检查(?:文档|原文|一下)|重新审视|此处(?:应)?修正|应改为(?:问|直接|具体)|应改成(?:问|直接)|"
    r"问.{0,8}是不?合适的|需要修正|（注：[^）]*(?:修正|不合适|误读|重新检查)"
)
_QA_META_QUESTION_RE = re.compile(
    r"(?:是否|是不是|能否|可以)?(?:属于|算作|归为|是)(?:一个|一道|一种)?(?:判断题|选择题|简答题|问答题|填空题|题型|疑问句)|"
    r"(?:这句话|该问句|这个问题|上述问题).*(?:题型|判断题|选择题|疑问句)|"
    r"(?:题型|句式)特征|(?:属于|是哪种|哪类).*(?:视觉挑战|视觉类型|挑战类型)"
)
_QA_CHOICE_QUESTION_RE = re.compile(
    r"(?:下列(?:哪一项|哪项|哪些|哪几项|哪种|哪个)|"
    r"以下(?:哪一项|哪项|哪些|哪几项|哪种|哪个)|"
    r"哪个选项|哪一项|哪项|A[.．、)]|[（(]A[）)])"
)
_QA_OPTION_MARKER_RE = re.compile(r"(?:^|[\s\n（(])([A-DＡ-Ｄ])[.．、)）]")
_QA_WORD_EXERCISE_SEMANTIC_RE = re.compile(r"近义|同义|反义|意思相近|含义相近|语义|词义|意义相同|意义不同|区别|差异")
_OCR_SPLIT_NUMBER_RE = re.compile(
    r"(?:\d+(?:\.\d+)?倍[。．.]\d+(?:\.\d+)?倍(?:高|宽|大)?"
    r"|\d+[。．.]\d+(?:MP|mL|ml|kg|g|mm|cm|米|吨|%)"
    r"|\d+[。．.]\d+(?:倍|MP|mL|ml|kg|g|mm|cm|米|吨|%)?)"
)
_SECTION_ID_RE = re.compile(r"\d+(?:\.\d+){1,3}")


def _compact_for_match(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower(), flags=re.UNICODE)


def _source_support_ratio(answer: str, source_text: str | None) -> float:
    answer_key = _compact_for_match(answer)
    source_key = _compact_for_match(source_text or "")
    if len(answer_key) < 3 or not source_key:
        return 0.0
    chunks = [answer_key[i:i + 3] for i in range(len(answer_key) - 2)]
    if not chunks:
        return 0.0
    hits = sum(1 for chunk in chunks if chunk in source_key)
    return hits / len(chunks)


def _looks_toc_source(source_text: str | None) -> bool:
    lines = [line.strip() for line in (source_text or "").splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    hits = 0
    for line in lines:
        if re.search(r"\s\d{1,4}$", line):
            hits += 1
        elif re.match(r"^(?:第\s*\d+\s*[章节篇部分]|\d+(?:\.\d+)+|[一二三四五六七八九十]+[、.．)])", line):
            hits += 1
    return hits / len(lines) >= 0.45


def _looks_question_list_source(source_text: str | None) -> bool:
    text = source_text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if re.search(r"请(?:叙述|说明|分析|简述|回答|问)", text):
        return True
    hits = 0
    for line in lines:
        if re.match(r"^[一二三四五六七八九十0-9]+[、.．)]", line) and re.search(r"[？?]", line):
            hits += 1
        elif len(line) <= 50 and line.endswith(("?", "？")):
            hits += 1
    return hits / len(lines) >= 0.4


def _looks_word_exercise_source(source_text: str | None) -> bool:
    text = source_text or ""
    if re.search(r"比一比|组词|组成词语|选字填空|形近字|温故知新|看拼音|抄写", text):
        return True
    return bool(re.search(r"[\u4e00-\u9fff]（[^）]{1,12}）", text) and re.search(r"屡|缕|谱|醒|涛|键", text))


def _looks_pure_exam_source(source_text: str | None) -> bool:
    text = source_text or ""
    if not re.search(r"(?:第\s*\d+\s*题|简答题|填空题|选择题|计算题|问答题)", text):
        return False
    if re.search(r"(?:答案|解析|参考答案|正确答案)", text):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    hit_lines = 0
    for line in lines:
        if re.search(r"(?:第\s*\d+\s*题|简答题|填空题|选择题|计算题|问答题|____|___)", line):
            hit_lines += 1
    return hit_lines >= 2


def _looks_contact_ad_source(source_text: str | None) -> bool:
    text = source_text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    hit_lines = 0
    for line in lines:
        if re.search(r"联系人|二维码|扫下方|请扫|客服|微信|营业时间|招商|出兑|转让|公众号|在看|福利|推广", line):
            hit_lines += 1
    return hit_lines / len(lines) >= 0.25


def _looks_symbolic_outline_source(source_text: str | None) -> bool:
    text = source_text or ""
    if not text:
        return False
    if sum(text.count(ch) for ch in "→↔↕∑×·|") >= 6:
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_lines = sum(1 for line in lines if len(line) <= 30)
    return len(lines) >= 6 and short_lines / len(lines) >= 0.6 and bool(re.search(r"[→↔↕∑×·|]", text))


def _looks_visual_title_stream(source_text: str | None) -> bool:
    text = source_text or ""
    return bool(re.search(r"隐藏的脸|第一眼看到了啥|树叶还是小鸟|香草美人|神秘的画|画中画|看到了吗|蒙娜丽莎|梵高|人脸", text))


def _looks_ocr_split_number(source_text: str | None) -> bool:
    return bool(_OCR_SPLIT_NUMBER_RE.search(source_text or ""))


def _question_answer_identical(question: str, answer: str) -> bool:
    q_key = _compact_for_match(question)
    a_key = _compact_for_match(answer)
    if not q_key or not a_key:
        return False
    if q_key == a_key:
        return True
    return len(q_key) >= 10 and len(a_key) >= 10 and (q_key in a_key or a_key in q_key)


def _states_source_absence_or_uncertainty(answer: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:没有|未|未说明|未提供|未提到|不能确定|无法判断|正文(?:未|没有|只)|原文(?:未|没有|只)|文档(?:未|没有|只))",
            answer or "",
        )
    )


def _clean_answer_label(answer: str) -> tuple[str, list[str]]:
    cleaned = (answer or "").strip()
    adjustments: list[str] = []
    next_cleaned = re.sub(r"^\s*(?:正确选项|选项)\s*[：:]\s*", "", cleaned).strip()
    if next_cleaned != cleaned:
        cleaned = next_cleaned
        adjustments.append("choice_answer_label_removed")
    next_cleaned = re.sub(r"^\s*[A-DＡ-Ｄ]\s*[.．、)]\s*", "", cleaned).strip()
    # 多选答案（如 "C、D。…" / "A. B. …"）不能剥标签，否则会把第一个选项吞掉
    is_multiselect = bool(re.match(r"^\s*[A-DＡ-Ｄ]\s*[.．、)]\s*[A-DＡ-Ｄ][.．、)。\s]", cleaned))
    if next_cleaned != cleaned and not is_multiselect:
        cleaned = next_cleaned
        adjustments.append("choice_answer_label_removed")
    return cleaned, adjustments


def _trim_unsupported_tail(answer: str, source_text: str | None) -> tuple[str, str | None]:
    m = re.match(r"^(.{1,35}?[。.!！?？])([\s\S]+)$", answer.strip())
    if not m:
        return answer, None
    first = m.group(1).strip()
    tail = m.group(2).strip()
    first_key = _compact_for_match(re.sub(r"^(?:正确|错误|真|假|是的|不是)[。.!！?？]?", "", first))
    if (
        len(first_key) >= 2
        and first_key in _compact_for_match(source_text or "")
        and _source_support_ratio(tail, source_text) < 0.25
        and _QA_RISKY_TAIL_RE.search(tail)
    ):
        return first, "unsupported_tail_trimmed"
    return answer, None


def _unsupported_profile_inference(question: str, answer: str, source_text: str | None) -> bool:
    if not (_looks_toc_source(source_text) or _looks_question_list_source(source_text)):
        return False
    if not _QA_INFERENTIAL_QUESTION_RE.search(question):
        return False
    return _source_support_ratio(answer, source_text) < 0.35


def _unsupported_meta_question(question: str, answer: str, source_text: str | None) -> bool:
    if not _QA_META_QUESTION_RE.search(f"{question} {answer}"):
        return False
    # In real exam lists, asking which type a numbered original question belongs to can be valid.
    if _looks_pure_exam_source(source_text) and re.search(r"第\s*\d+\s*题.*题型", question):
        return False
    return True


def _unsupported_exam_solution(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_pure_exam_source(source_text):
        return False
    if re.search(r"第\s*\d+\s*题.*(?:要求|涉及|问什么|考查|填空要求)|题目(?:清单|列表)?中|是否要求|原文(?:未|没有|未给出)|无法(?:判断|回答|比较)", question):
        return False
    if _source_support_ratio(answer, source_text) >= 0.55:
        return False
    return True


def _unsupported_readonly_or_contrast_expansion(question: str, answer: str, source_text: str | None) -> bool:
    text = f"{question} {answer}"
    source = source_text or ""
    if not re.search(r"readonly|只读", text, flags=re.IGNORECASE):
        return False
    if _states_source_absence_or_uncertainty(answer):
        return False
    if not re.search(r"运行时|动态赋值|一旦赋值|构造函数|实例化|编译时", text):
        return False
    return not re.search(r"运行时|动态赋值|一旦赋值|构造函数|实例化", source)


def _unsupported_contact_inference(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_contact_ad_source(source_text):
        return False
    if _source_support_ratio(answer, source_text) >= 0.45:
        return False
    return bool(re.search(r"业务类型|经营|行业|项目类型|可转让项目|为什么|作用|意义|归类|分类", f"{question} {answer}"))


def _unsupported_word_exercise_semantic(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_word_exercise_source(source_text):
        return False
    text = f"{question} {answer}"
    if not _QA_WORD_EXERCISE_SEMANTIC_RE.search(text):
        return False
    return bool(re.search(r"屡次|一缕缕|形近|组词|搭配|词语", text))


def _unsupported_symbolic_inference(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_symbolic_outline_source(source_text):
        return False
    text = f"{question} {answer}"
    if re.search(r"并列关系|共同参与|共同构成|支撑|上下级|因果|递进|机制|原理|转化|成长|提升|相互作用|逻辑关系", text) and re.search(r"[∑×→=+]", source_text or ""):
        return True
    literal_symbol_relation = bool(
        re.search(
            r"相加关系|相乘关系|加法|乘法|连接符号|用.*连接|由.*(?:和|与|、).*组成|"
            r"下一个|之后|代表|对应|顺序(?:是|为)|依次为",
            text,
        )
    )
    semantic_expansion = bool(re.search(r"递进|支撑|上下级|因果|机制|原理|逻辑|框架|实现|转化|成长|提升|解释|推断", text))
    if literal_symbol_relation and not semantic_expansion:
        return False
    if _source_support_ratio(answer, source_text) >= 0.55:
        return False
    return bool(re.search(r"关系|原因|作用|为什么|如何|步骤|实现|转化|成长|提升|解释|推断|逻辑|框架|递进|支撑|上下级|机制", text))


def _unsupported_symbolic_section_mismatch(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_symbolic_outline_source(source_text):
        return False
    text = f"{question} {answer}"
    if not re.search(r"(?:节|章|模块|心法|路径|阶段|公式)", text):
        return False
    ids = _SECTION_ID_RE.findall(text)
    if not ids:
        return False
    source = source_text or ""
    return not any(section_id in source for section_id in ids)


def _unsupported_ocr_numeric(question: str, answer: str, source_text: str | None) -> bool:
    source = source_text or ""
    if not _looks_ocr_split_number(source):
        return False
    text = f"{question} {answer}"
    if re.search(r"原文(?:疑似|未|不清|有误)|OCR|识别", text):
        return False
    exact_fragments = [m.group(0) for m in _OCR_SPLIT_NUMBER_RE.finditer(source)]
    if any(fragment and fragment in answer for fragment in exact_fragments):
        return False
    return bool(re.search(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万]+)(?:倍|MP|mL|ml|kg|g|mm|cm|米|吨|%)", text))


def _unsupported_visual_inference(question: str, answer: str, source_text: str | None) -> bool:
    if not _looks_visual_title_stream(source_text):
        return False
    if _states_source_absence_or_uncertainty(answer):
        return False
    text = f"{question} {answer}"
    if re.search(r"文末点亮|发给朋友|一起看看吧|第一眼看到了啥|是否给出了具体答案|给出了具体答案|在看|扫码|关注备用号|备用号|朋友圈|转发", text):
        return True
    text_framed = bool(re.search(r"文中|文本|标题|提示语|作品名|名字|主题|围绕|列出|出现", text))
    hard_image_assertion = bool(
        re.search(r"实际|确实|真实|需要仔细观察|才能发现|类似的人脸|自画像|视觉错觉", text)
        or (re.search(r"画中隐藏|画面里隐藏", text) and not re.search(r"主题|围绕|文中.*(?:提到|列出|出现)", text))
    )
    if text_framed and not hard_image_assertion:
        return False
    if _source_support_ratio(answer, source_text) >= 0.55:
        return False
    return bool(re.search(r"隐藏的是|画中隐藏|人脸|自画像|之一|看到了吗|为什么|目的|意义|神奇|巧妙|视觉挑战", text))


def _unsupported_subject_shift(question: str, answer: str, source_text: str | None) -> bool:
    text = f"{question} {answer}"
    source = source_text or ""
    if (
        "德国历史学派" in question
        and re.search(r"历史研究.*社会.*科学|自由.*国家权威|社会控制|公共政策", text)
        and "以黑格尔为代表的哲学家" in source
    ):
        return True
    if re.search(r"四位嘉宾|四名嘉宾|4位嘉宾|4名嘉宾", text) and "四个游戏" in source and not re.search(r"四位嘉宾|四名嘉宾", source):
        return True
    return False


def _unsupported_exception_overgeneralization(question: str, answer: str, source_text: str | None) -> bool:
    source = source_text or ""
    if "除了透明质酸钠" not in source:
        return False
    text = f"{question} {answer}"
    return bool(re.search(r"成分(?:均|都|全部|皆)为纯天然植物萃取|均为纯天然|全部为纯天然|都为纯天然", text))


def _unsupported_tech_causal_mix(question: str, answer: str, source_text: str | None) -> bool:
    source = source_text or ""
    text = f"{question} {answer}"
    if "双螺杆泵" not in source:
        return False
    mixes_smooth_delivery = re.search(r"无搅拌|无脉动|平稳输送", text)
    mixes_seal_self_priming = re.search(r"密封液|自吸", text)
    return bool(re.search(r"为什么|原因|由于|因为|从而", text) and mixes_smooth_delivery and mixes_seal_self_priming)


def _choice_format_problem(question: str, answer: str) -> str | None:
    if not _QA_CHOICE_QUESTION_RE.search(question):
        return None
    labels = sorted(set(_QA_OPTION_MARKER_RE.findall(question)))
    if len(labels) < 4:
        return "choice_missing_options"
    if re.search(r"任一|均可|都可|都属于|多个都|不唯一", answer):
        return "choice_not_unique"
    return None


# v3.30 强制选择题后 27b 常见坏格式：把整道选择题的选项块塞进了 answer 字段，
# 且丢了开头的 "A." 前缀。典型 answer 结构：
#   "响一声\nB. 响两声\nC. 响三声\nD. 响四声\n正确答案是C。依据…"
# 题干里反而没有 A/B/C/D。此时把选项块搬回题干、答案只留"正确答案X + 依据"，
# 既堵住坏数据又保住这道有价值的选择题。无法安全重建时返回 None（交由上层删除）。
_CHOICE_ANSWER_LEADING_OPTION_RE = re.compile(
    r"^\s*(?P<a>[^\nA-DＡ-Ｄ][^\n]*?)\s*\n\s*B[.．、)]\s*(?P<b>[^\n]+?)\s*\n\s*C[.．、)]\s*(?P<c>[^\n]+?)\s*\n\s*D[.．、)]\s*(?P<d>[^\n]+?)\s*\n+"
    r"(?P<rest>[\s\S]*)$"
)
_CHOICE_ANSWER_VERDICT_RE = re.compile(
    r"正确(?:答案|选项)(?:是|为|：|:)?\s*(?P<letter>[A-DＡ-Ｄ])[.．、)]?\s*(?P<opt>[^。.\n，,：:]{0,20})?"
)


def _repair_misplaced_choice_options(question: str, answer: str) -> tuple[str, str, str | None]:
    """把错位到 answer 的选项块搬回题干。返回 (new_question, new_answer, adjustment)。
    仅在题干本身没有 4 个选项、且 answer 明确是 '选项块 + 正确答案X' 结构时触发。"""
    if len(set(_QA_OPTION_MARKER_RE.findall(question))) >= 4:
        return question, answer, None  # 题干已有完整选项，非本情形
    m = _CHOICE_ANSWER_LEADING_OPTION_RE.match(answer)
    if not m:
        return question, answer, None
    rest = m.group("rest").strip()
    verdict = _CHOICE_ANSWER_VERDICT_RE.search(rest)
    if not verdict:
        return question, answer, None  # 没有明确"正确答案X"，无法安全重建 → 交上层删
    opts = {
        "A": m.group("a").strip(),
        "B": m.group("b").strip(),
        "C": m.group("c").strip(),
        "D": m.group("d").strip(),
    }
    if not all(opts.values()) or any("\n" in v for v in opts.values()):
        return question, answer, None
    letter = verdict.group("letter").translate(str.maketrans("ＡＢＣＤ", "ABCD"))
    if letter not in opts:
        return question, answer, None
    new_question = question.strip() + "\n" + "\n".join(f"{k}. {opts[k]}" for k in ["A", "B", "C", "D"])
    # 答案改为：正确选项内容 + 原依据（去掉"正确答案X"这个纯字母裁决前缀，保留其后依据）
    tail = rest[verdict.end():].lstrip("。.：: 、，,").strip()
    new_answer = opts[letter] + (("。" + tail) if tail else "。")
    return new_question, new_answer, "choice_options_moved_to_question"


def _orphan_choice_block_in_answer(question: str, answer: str) -> bool:
    """答案里堆着 B./C./D. 选项块但题干无完整选项，且没能被 repair 挪回（无法安全重建）→ 坏格式。
    只在题干选项 <4 且答案含 ≥3 个选项标记时判真，避免误伤正常判断/事实题。"""
    if len(set(_QA_OPTION_MARKER_RE.findall(question))) >= 4:
        return False
    ans_labels = set(_QA_OPTION_MARKER_RE.findall(answer))
    return len(ans_labels) >= 3


def _unsupported_external_expansion(question: str, answer: str, source_text: str | None) -> bool:
    if not _QA_SPECIFIC_EXTERNAL_RE.search(f"{question} {answer}"):
        return False
    if _states_source_absence_or_uncertainty(answer):
        return False
    return _source_support_ratio(answer, source_text) < 0.35


def _reasoning_leak_answer(answer: str) -> bool:
    """答案把模型的思考/自我论证过程当正文吐了出来（无 </think> 标签的裸推理泄漏）。
    保守判定：需同时命中评注措辞且答案偏长（正常直接作答很少这么啰嗦）。"""
    if not _QA_REASONING_LEAK_RE.search(answer):
        return False
    # 单一命中词 + 短答案可能是正常表达（如“通常指…”），要求足够长才判为泄漏。
    hits = len(_QA_REASONING_LEAK_RE.findall(answer))
    return hits >= 2 or len(answer) >= 80


def _source_noise_block_answer(answer: str) -> bool:
    """原文噪声整块（网友回复/试题分析/点评）被抓进了答案。"""
    return bool(_QA_SOURCE_NOISE_BLOCK_RE.search(answer))


def filter_direct_qa(
    qa: QAOutput,
    source_text: str | None,
) -> tuple[Optional[QAOutput], list[str], list[str]]:
    """Conservative cleanup for direct QA generation; it never invents replacement pairs."""
    kept: list[dict[str, str]] = []
    seen_questions: set[str] = set()
    removed: list[str] = []
    adjusted: list[str] = []

    for item in qa.qa_pairs:
        question = item.question.strip()
        answer = item.answer.strip()
        # v3.30: 先尝试把错位到答案的选项块搬回题干（保住这道选择题）
        question, answer, choice_move_adj = _repair_misplaced_choice_options(question, answer)
        answer, label_adjustments = _clean_answer_label(answer)
        answer, tail_adjustment = _trim_unsupported_tail(answer, source_text)
        pair_adjustments = ([choice_move_adj] if choice_move_adj else []) + label_adjustments + ([tail_adjustment] if tail_adjustment else [])

        if not question or not answer:
            removed.append("empty_pair")
            continue
        # 修复后仍残留孤儿选项块（题干无选项、答案里堆着 B./C./D. 却无法安全重建）→ 坏格式，删除
        if _orphan_choice_block_in_answer(question, answer):
            removed.append("choice_options_misplaced")
            continue
        if _QA_NOISE_RE.search(question) or _QA_NOISE_RE.search(answer):
            removed.append("noise_pair")
            continue
        if _source_noise_block_answer(answer):
            removed.append("source_noise_block")
            continue
        if _reasoning_leak_answer(answer):
            removed.append("reasoning_leak_answer")
            continue
        if _question_answer_identical(question, answer):
            removed.append("question_answer_identical")
            continue
        if _QA_UNANCHORED_PRONOUN_RE.search(question) and not re.search(r"[“”‘’\"'《》]", question):
            removed.append("unanchored_pronoun_question")
            continue
        if _unsupported_meta_question(question, answer, source_text):
            removed.append("meta_question")
            continue
        if _QA_META_DISCOURSE_ANSWER_RE.search(answer):
            removed.append("meta_discourse_answer")
            continue
        if _QA_TEST_META_REFERENCE_RE.search(question.strip()):
            removed.append("test_meta_reference")
            continue
        choice_problem = _choice_format_problem(question, answer)
        if choice_problem:
            removed.append(choice_problem)
            continue
        question_key = _compact_for_match(question)
        if question_key in seen_questions:
            removed.append("duplicate_question")
            continue
        if _unsupported_external_expansion(question, answer, source_text):
            removed.append("unsupported_external_expansion")
            continue
        if _unsupported_ocr_numeric(question, answer, source_text):
            removed.append("unsupported_ocr_numeric")
            continue
        if _unsupported_readonly_or_contrast_expansion(question, answer, source_text):
            removed.append("unsupported_readonly_or_contrast_expansion")
            continue
        if _unsupported_profile_inference(question, answer, source_text):
            removed.append("unsupported_profile_inference")
            continue
        if _unsupported_exam_solution(question, answer, source_text):
            removed.append("unsupported_exam_solution")
            continue
        if _unsupported_contact_inference(question, answer, source_text):
            removed.append("unsupported_contact_inference")
            continue
        if _unsupported_word_exercise_semantic(question, answer, source_text):
            removed.append("unsupported_word_exercise_semantic")
            continue
        if _unsupported_symbolic_inference(question, answer, source_text):
            removed.append("unsupported_symbolic_inference")
            continue
        if _unsupported_symbolic_section_mismatch(question, answer, source_text):
            removed.append("unsupported_symbolic_section_mismatch")
            continue
        if _unsupported_visual_inference(question, answer, source_text):
            removed.append("unsupported_visual_inference")
            continue
        if _unsupported_subject_shift(question, answer, source_text):
            removed.append("unsupported_subject_shift")
            continue
        if _unsupported_exception_overgeneralization(question, answer, source_text):
            removed.append("unsupported_exception_overgeneralization")
            continue
        if _unsupported_tech_causal_mix(question, answer, source_text):
            removed.append("unsupported_tech_causal_mix")
            continue

        seen_questions.add(question_key)
        adjusted.extend(pair_adjustments)
        kept.append({"question": question, "answer": answer})

    if not kept:
        return None, removed or ["no_pairs_after_filter"], adjusted
    return QAOutput(qa_pairs=kept), removed, adjusted


def _extract_content_value(text: str) -> Optional[str]:
    """从含裸换行或被截断的 rewrite JSON 片段中提取 content。"""
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text).strip()
    m = re.search(r'"content"\s*:\s*"', t)
    if not m:
        return None

    out = []
    i = m.end()
    while i < len(t):
        ch = t[i]
        if ch == "\\" and i + 1 < len(t):
            nxt = t[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "u" and i + 5 < len(t):
                code = t[i + 2:i + 6]
                try:
                    out.append(chr(int(code, 16)))
                    i += 4
                except ValueError:
                    out.append(nxt)
            else:
                out.append(nxt)
            i += 2
            continue
        if ch == '"':
            tail = t[i + 1:].lstrip()
            if not tail or tail[0] in ",}":
                break
        out.append(ch)
        i += 1

    content = "".join(out)
    if t.endswith("}") and content.endswith("}"):
        content = content[:-1].rstrip()
    return content if content.strip() else None


def parse_rewrite(raw: str) -> Optional[RewriteOutput]:
    d = extract_json(raw)
    if d is None:
        content = _extract_content_value(raw)
        if content is None:
            text = re.sub(r"```(?:json)?", "", raw or "").strip()
            looks_like_plain_prose = (
                len(text) >= 80
                and "{" not in text[:20]
                and '"content"' not in text[:80]
                and bool(re.search(r"[\u4e00-\u9fff]", text))
                and not text.startswith(("抱歉", "无法", "不能", "As an AI", "I cannot"))
            )
            if not looks_like_plain_prose:
                return None
            content = text
        d = {"content": content}
    try:
        return RewriteOutput(**d)
    except ValidationError:
        return None


# ---------------- 提示词加载 ----------------
def load_text(rel_path: str) -> str:
    with open(REPO_ROOT / rel_path, "r", encoding="utf-8") as f:
        return f.read()


def load_yaml(rel_path: str) -> dict:
    with open(REPO_ROOT / rel_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------- 组装官方 schema 的 content ----------------
def _looks_zh(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def assemble_qa_content(source_text: str, qa: QAOutput) -> str:
    """训练时原文拼在问答对前面（见 SPEC §3）。"""
    q_label, a_label = ("问题：", "答案：") if _looks_zh(source_text) else ("Question:", "Answer:")
    blocks = [source_text.strip(), ""]
    for item in qa.qa_pairs:
        blocks.append(f"{q_label} {item.question.strip()} {a_label} {item.answer.strip()}")
        blocks.append("")
    return "\n".join(blocks).strip()


# ---------------- IO ----------------
def write_jsonl(records: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def official_record(rec: dict) -> dict:
    """导出官方 schema（丢弃下划线开头的评测辅助字段）。"""
    return {k: v for k, v in rec.items() if not k.startswith("_")}
