from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol, Sequence

from question.models import QuestionOptions, Questions, SolveRecords

from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)


@dataclass(frozen=True)
class WrongChoiceCandidate:
    record_id: int
    session_id: int
    question_id: int
    selected_choice_no: int
    correct_choice_no: int
    selected_choice_text: str
    correct_choice_text: str
    selected_choice_explanation: str | None
    correct_choice_explanation: str | None
    question_text: str
    passage: str | None
    image_caption: str | None
    answer_explanation: str
    core_concept: str
    era: str
    topic: str
    question_type: str
    question_subtype: str


@dataclass(frozen=True)
class ResolvedRelationFact:
    subject_id: str
    subject_label: str
    relation_type: str
    relation_label: str
    object_id: str
    object_label: str


@dataclass(frozen=True)
class ResolvedChoiceRelation:
    question_intent: str
    relation_family: str
    correct_fact: ResolvedRelationFact
    selected_fact: ResolvedRelationFact
    comparison_dimensions: tuple[str, ...]
    graph_evidence_ids: tuple[str, ...]
    graph_version: str


class ChoiceRelationResolver(Protocol):
    def resolve(
        self,
        candidate: WrongChoiceCandidate,
    ) -> ResolvedChoiceRelation | None:
        """Return one evidence-backed relation match or None when it is ambiguous."""


def load_wrong_choice_candidates(
    user_id: int,
    period_start: date,
    period_end: date,
    config: WeeklyReportConfig | None = None,
) -> list[WrongChoiceCandidate]:
    resolved_config = config or get_weekly_report_config()
    record_rows = list(
        SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status=resolved_config.completed_session_status,
            session__recorded_date__gte=period_start,
            session__recorded_date__lte=period_end,
            is_correct=False,
            selected_no__isnull=False,
        )
        .values(
            "record_id",
            "session_id",
            "question_id",
            "selected_no",
            "is_correct",
        )
        .order_by("record_id")
    )
    if not record_rows:
        return []

    question_ids = {int(row["question_id"]) for row in record_rows}
    question_rows = list(
        Questions.objects.filter(question_id__in=question_ids).values(
            "question_id",
            "answer_no",
            "content",
            "passage",
            "image_caption",
            "answer_explanation",
            "core_concept",
            "era",
            "topic",
            "question_type",
            "question_subtype",
        )
    )
    option_rows = list(
        QuestionOptions.objects.filter(question_id__in=question_ids).values(
            "question_id",
            "choice_no",
            "content",
            "choice_explanation",
        )
    )
    return build_wrong_choice_candidates(record_rows, question_rows, option_rows)


def build_wrong_choice_candidates(
    record_rows: Sequence[Mapping[str, object]],
    question_rows: Sequence[Mapping[str, object]],
    option_rows: Sequence[Mapping[str, object]],
) -> list[WrongChoiceCandidate]:
    questions_by_id = {
        int(row["question_id"]): row
        for row in question_rows
        if row.get("question_id") is not None
    }
    options_by_question: dict[int, dict[int, Mapping[str, object]]] = {}
    for row in option_rows:
        question_id = row.get("question_id")
        choice_no = row.get("choice_no")
        if question_id is None or choice_no is None:
            continue
        options_by_question.setdefault(int(question_id), {})[int(choice_no)] = row

    candidates: list[WrongChoiceCandidate] = []
    ordered_records = sorted(
        record_rows,
        key=lambda row: int(row.get("record_id") or 0),
    )
    for record in ordered_records:
        if record.get("is_correct") is True:
            continue
        selected_no = record.get("selected_no")
        question_id = record.get("question_id")
        if selected_no is None or question_id is None:
            continue
        question = questions_by_id.get(int(question_id))
        if question is None or question.get("answer_no") is None:
            continue
        correct_no = int(question["answer_no"])
        question_options = options_by_question.get(int(question_id), {})
        selected_option = question_options.get(int(selected_no))
        correct_option = question_options.get(correct_no)
        if selected_option is None or correct_option is None:
            continue
        selected_text = str(selected_option.get("content") or "").strip()
        correct_text = str(correct_option.get("content") or "").strip()
        if not selected_text or not correct_text:
            continue
        candidates.append(
            WrongChoiceCandidate(
                record_id=int(record.get("record_id") or 0),
                session_id=int(record.get("session_id") or 0),
                question_id=int(question_id),
                selected_choice_no=int(selected_no),
                correct_choice_no=correct_no,
                selected_choice_text=selected_text,
                correct_choice_text=correct_text,
                selected_choice_explanation=_optional_text(
                    selected_option.get("choice_explanation")
                ),
                correct_choice_explanation=_optional_text(
                    correct_option.get("choice_explanation")
                ),
                question_text=str(question.get("content") or ""),
                passage=_optional_text(question.get("passage")),
                image_caption=_optional_text(question.get("image_caption")),
                answer_explanation=str(question.get("answer_explanation") or ""),
                core_concept=str(question.get("core_concept") or ""),
                era=str(question.get("era") or ""),
                topic=str(question.get("topic") or ""),
                question_type=str(question.get("question_type") or ""),
                question_subtype=str(question.get("question_subtype") or ""),
            )
        )
    return candidates


def build_confusion_patterns(
    candidates: Sequence[WrongChoiceCandidate],
    resolver: ChoiceRelationResolver,
    config: WeeklyReportConfig | None = None,
) -> list[dict[str, object]]:
    resolved_config = config or get_weekly_report_config()
    grouped: dict[tuple[str, ...], dict[str, object]] = {}
    logger = logging.getLogger(__name__)

    for candidate in candidates:
        try:
            relation = resolver.resolve(candidate)
        except Exception as error:
            logger.warning(
                "weekly report relation resolution failed error=%s",
                type(error).__name__,
            )
            continue
        if relation is None:
            continue
        if not _is_usable_relation(relation):
            continue
        group_key = _relation_group_key(relation)
        group = grouped.setdefault(
            group_key,
            {
                "relation": relation,
                "recordIds": set(),
                "questionIds": set(),
                "comparisonDimensions": set(),
                "graphEvidenceIds": set(),
            },
        )
        group["recordIds"].add(candidate.record_id)
        group["questionIds"].add(candidate.question_id)
        group["comparisonDimensions"].update(relation.comparison_dimensions)
        group["graphEvidenceIds"].update(relation.graph_evidence_ids)

    repeated_groups = [
        group
        for group in grouped.values()
        if len(group["recordIds"]) >= resolved_config.minimum_confusion_repeat_count
    ]
    repeated_groups.sort(
        key=lambda group: (
            -len(group["recordIds"]),
            _relation_group_key(group["relation"]),
        )
    )

    patterns: list[dict[str, object]] = []
    selected_groups = repeated_groups[: resolved_config.maximum_confusion_pattern_count]
    for index, group in enumerate(selected_groups):
        relation = group["relation"]
        patterns.append(
            {
                "evidenceId": f"confusion-{index + 1}",
                "questionIntent": relation.question_intent,
                "relationFamily": relation.relation_family,
                "correctFact": _serialize_fact(relation.correct_fact),
                "selectedFact": _serialize_fact(relation.selected_fact),
                "repeatCount": len(group["recordIds"]),
                "sourceQuestionIds": sorted(group["questionIds"]),
                "comparisonDimensions": sorted(group["comparisonDimensions"]),
                "graphEvidenceIds": sorted(group["graphEvidenceIds"]),
                "graphVersion": relation.graph_version,
            }
        )
    return patterns


def build_weekly_confusion_patterns(
    user_id: int,
    period_start: date,
    period_end: date,
    resolver: ChoiceRelationResolver | None,
    config: WeeklyReportConfig | None = None,
) -> list[dict[str, object]]:
    if resolver is None:
        return []
    candidates = load_wrong_choice_candidates(
        user_id,
        period_start,
        period_end,
        config,
    )
    return build_confusion_patterns(candidates, resolver, config)


def _relation_group_key(relation: ResolvedChoiceRelation) -> tuple[str, ...]:
    correct = relation.correct_fact
    selected = relation.selected_fact
    return (
        relation.graph_version,
        relation.question_intent,
        relation.relation_family,
        correct.subject_id,
        correct.relation_type,
        correct.object_id,
        selected.subject_id,
        selected.relation_type,
        selected.object_id,
    )


def _is_usable_relation(relation: ResolvedChoiceRelation) -> bool:
    correct = relation.correct_fact
    selected = relation.selected_fact
    required_values = (
        relation.graph_version,
        relation.question_intent,
        relation.relation_family,
        correct.subject_id,
        correct.subject_label,
        correct.relation_type,
        correct.relation_label,
        correct.object_id,
        correct.object_label,
        selected.subject_id,
        selected.subject_label,
        selected.relation_type,
        selected.relation_label,
        selected.object_id,
        selected.object_label,
    )
    if any(not str(value).strip() for value in required_values):
        return False
    if not relation.graph_evidence_ids:
        return False
    return (
        correct.subject_id,
        correct.relation_type,
        correct.object_id,
    ) != (
        selected.subject_id,
        selected.relation_type,
        selected.object_id,
    )


def _serialize_fact(fact: ResolvedRelationFact) -> dict[str, str]:
    return {
        "subjectId": fact.subject_id,
        "subjectLabel": fact.subject_label,
        "relationType": fact.relation_type,
        "relationLabel": fact.relation_label,
        "objectId": fact.object_id,
        "objectLabel": fact.object_label,
    }


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text
