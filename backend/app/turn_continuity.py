"""Conservative, non-blocking diagnostics for adjacent-question loops.

These literal rules detect known repetition patterns, not semantic correctness.
They never rewrite the model reply or change the user's programme state.
"""
from collections.abc import Sequence
from difflib import SequenceMatcher
import re

from .schemas import Message


def _time_questions(text: str) -> list[str]:
    return [question for question in re.findall(r"[^。！？!?\n]+[？?]", text)
            if re.search(r"哪(?:一)?天|星期几|周几|几[点号]|什么时[候间]|哪个时间|哪段时间|时间段", question)]


def defers_scheduling(history: Sequence[Message], user_input: str) -> bool:
    # Deliberately narrow: a longer message may supply a date, correct us, or
    # ask a different question. Never reduce it to an uncertainty keyword.
    compact = re.sub(r"[\s，,。.!！~～]", "", user_input)
    uncertain = re.fullmatch(
        r"(?:我)?(?:还|暂时|现在)?(?:没想好|没想清楚|没决定|不知道|不确定)"
        r"(?:呢|啊|呀|吧)?(?:老大|老师)?", compact)
    return bool(uncertain and history and history[-1].role == "assistant"
                and _time_questions(history[-1].content))


def repeats_deferred_schedule(reply: str, history: Sequence[Message], user_input: str) -> bool:
    return defers_scheduling(history, user_input) and bool(_time_questions(reply))


def repeats_previous_question(reply: str, history: Sequence[Message]) -> bool:
    if not history or history[-1].role != "assistant":
        return False

    def questions(text: str) -> list[str]:
        result = []
        for question in re.findall(r"[^。！？!?\n]+[？?]", text):
            question = re.split(r"[：:]", question)[-1]
            normalized = re.sub(r"[\W_]", "", question).casefold()
            if len(normalized) >= 8:
                result.append(normalized)
        return result

    return any(SequenceMatcher(None, old, new, autojunk=False).ratio() >= 0.92
               for old in questions(history[-1].content) for new in questions(reply))
