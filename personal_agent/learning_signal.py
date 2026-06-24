from __future__ import annotations

from dataclasses import dataclass


APPROVAL_REVIEW_TERMS = (
    "批准这条经验",
    "批准刚才那条",
    "记住刚才那条",
    "驳回刚才那条",
    "驳回这条经验",
    "不要记",
    "别记",
    "approvelatest",
    "rejectlatest",
)

STRONG_FUTURE_CONSTRAINT_TERMS = (
    "以后都",
    "以后不要",
    "下次不要",
    "以后别",
    "以后避免",
    "以后尽量",
    "别再",
    "不要再",
    "再也不要",
)

WEAK_FUTURE_SCOPE_TERMS = (
    "以后",
    "下次",
    "后面",
    "后续",
)

DIRECT_CORRECTION_TERMS = (
    "你理解错了",
    "理解错",
    "不对",
    "错了",
    "误解",
    "纠正",
    "修正",
    "不是这个意思",
)

ISSUE_EVALUATION_TERMS = (
    "应该是",
    "有点问题",
    "这里有问题",
    "不太合适",
    "不符合预期",
    "不应该",
)

PREFERENCE_TERMS = (
    "我希望",
    "我不希望",
    "我更喜欢",
    "我不喜欢",
    "尽量避免",
    "避免出现",
    "最好规避",
    "按这种方式",
    "不要固定模板",
    "固定模板",
)

ANALOGOUS_SCOPE_TERMS = (
    "类似情况",
    "这种情况",
    "同类情况",
    "遇到这种",
    "这种场景",
    "类似场景",
)


@dataclass(frozen=True)
class LearningSignalMatch:
    has_signal: bool
    reason: str
    categories: tuple[str, ...]
    matched_terms: tuple[str, ...]
    is_review_signal: bool = False


def compact_learning_text(text: str) -> str:
    return "".join(str(text or "").lower().split())


def detect_learning_signal(text: str) -> LearningSignalMatch:
    compact = compact_learning_text(text)
    if not compact:
        return LearningSignalMatch(False, "no_learning_signal", (), ())

    approval_hits = _hits(compact, APPROVAL_REVIEW_TERMS)
    if approval_hits:
        return LearningSignalMatch(
            True,
            "approval_review_signal",
            ("approval_review",),
            approval_hits,
            True,
        )

    strong_future_hits = _hits(compact, STRONG_FUTURE_CONSTRAINT_TERMS)
    if strong_future_hits:
        return LearningSignalMatch(
            True,
            "strong_future_constraint",
            ("future_scope", "preference"),
            strong_future_hits,
        )

    direct_correction_hits = _hits(compact, DIRECT_CORRECTION_TERMS)
    issue_hits = _hits(compact, ISSUE_EVALUATION_TERMS)
    correction_hits = direct_correction_hits + tuple(term for term in issue_hits if term not in direct_correction_hits)
    preference_hits = _hits(compact, PREFERENCE_TERMS)
    future_hits = _hits(compact, WEAK_FUTURE_SCOPE_TERMS)
    analogous_hits = _hits(compact, ANALOGOUS_SCOPE_TERMS)

    if direct_correction_hits:
        return LearningSignalMatch(
            True,
            "explicit_correction_signal",
            ("correction",),
            direct_correction_hits,
        )

    if correction_hits and future_hits:
        return LearningSignalMatch(
            True,
            "correction_with_future_scope",
            ("correction", "future_scope"),
            correction_hits + future_hits,
        )
    if correction_hits and preference_hits:
        return LearningSignalMatch(
            True,
            "correction_with_preference",
            ("correction", "preference"),
            correction_hits + preference_hits,
        )
    if preference_hits and future_hits:
        return LearningSignalMatch(
            True,
            "preference_with_future_scope",
            ("preference", "future_scope"),
            preference_hits + future_hits,
        )
    if preference_hits and analogous_hits:
        return LearningSignalMatch(
            True,
            "preference_with_analogous_scope",
            ("preference", "analogous_scope"),
            preference_hits + analogous_hits,
        )
    return LearningSignalMatch(False, "no_learning_signal", (), ())


def _hits(compact: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in compact)
