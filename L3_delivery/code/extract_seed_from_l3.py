"""Extract original seed text from official zh L3 QA content."""
from __future__ import annotations

from dataclasses import dataclass

QUESTION_MARK = "问题："
ANSWER_MARK = "答案："
MIN_SEED_CHARS = 100


@dataclass(frozen=True)
class SplitFailure:
    reason: str


@dataclass(frozen=True)
class SplitResult:
    seed_text: str
    official_qa_pairs: str
    pair_count: int
    pairs: tuple[tuple[str, str], ...]


def _question_positions(content: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        pos = content.find(QUESTION_MARK, start)
        if pos == -1:
            return positions
        positions.append(pos)
        start = pos + len(QUESTION_MARK)


def _parse_qa_suffix(text: str) -> tuple[tuple[str, str], ...] | SplitFailure:
    pairs: list[tuple[str, str]] = []
    pos = 0
    length = len(text)

    while pos < length:
        while pos < length and text[pos].isspace():
            pos += 1
        if pos == length:
            break
        if not text.startswith(QUESTION_MARK, pos):
            return SplitFailure("suffix_not_starting_with_question")

        question_start = pos + len(QUESTION_MARK)
        answer_pos = text.find(ANSWER_MARK, question_start)
        if answer_pos == -1:
            return SplitFailure("missing_answer_marker")

        question = text[question_start:answer_pos]
        if not question.strip():
            return SplitFailure("empty_question")

        answer_start = answer_pos + len(ANSWER_MARK)
        next_question = text.find(QUESTION_MARK, answer_start)
        if next_question == -1:
            answer_end = length
            pos = length
        else:
            answer_end = next_question
            pos = next_question

        answer = text[answer_start:answer_end]
        if not answer.strip():
            return SplitFailure("empty_answer")
        pairs.append((question, answer))

    if not pairs:
        return SplitFailure("no_pairs")
    return tuple(pairs)


def split_content_with_reason(content: str) -> SplitResult | SplitFailure:
    """Split official zh QA content, retaining exact characters on both sides."""
    if not isinstance(content, str) or not content:
        return SplitFailure("empty_content")

    positions = _question_positions(content)
    if not positions:
        return SplitFailure("no_question_marker")

    first_result: SplitResult | None = None
    last_failure = SplitFailure("no_valid_suffix")
    for pos in reversed(positions):
        parsed = _parse_qa_suffix(content[pos:])
        if isinstance(parsed, SplitFailure):
            last_failure = parsed
            continue

        first_result = SplitResult(
            seed_text=content[:pos],
            official_qa_pairs=content[pos:],
            pair_count=len(parsed),
            pairs=parsed,
        )

    if first_result is None:
        return last_failure
    if len(first_result.seed_text.strip()) < MIN_SEED_CHARS:
        return SplitFailure("seed_too_short")
    return first_result


def split_content(content: str) -> tuple[str, str] | None:
    """Return (seed_text, official_qa_pairs) if content passes zh QA split checks."""
    result = split_content_with_reason(content)
    if isinstance(result, SplitFailure):
        return None
    return result.seed_text, result.official_qa_pairs
