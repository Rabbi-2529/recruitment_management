"""
Interview marking: each interviewer fills their own sheet; nothing is shared or overwritten.

There is ONE status for a candidate: Candidate.status (Pending / Selected / Waiting / Not Selected),
the same one the Interview Sheet, the candidate list and the public result page use. Once it is no
longer Pending the Admin has decided, and interviewers can no longer change their marks.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, F, Q
from django.utils import timezone

from .models import (
    Candidate,
    EvaluationCriterion,
    EvaluationScore,
    InterviewerProfile,
    InterviewEvaluation,
    InterviewSetting,
)


def decision_made(candidate):
    return candidate.status != Candidate.Status.PENDING


def sheet_rows(evaluation, include_new=False):
    """
    [(key, label, max_marks, mark)] - an existing sheet keeps its own criteria, a new one uses the active list.
    include_new: also add active criteria the sheet does not have yet (used for the Admin's own sheet).
    """
    active = list(EvaluationCriterion.objects.filter(is_active=True))
    if evaluation is not None and evaluation.pk and evaluation.scores.exists():
        scores = list(evaluation.scores.all())
        rows = [(f"s{sc.pk}", sc.criterion_name, sc.max_marks, sc.mark) for sc in scores]
        if include_new:
            on_sheet = {sc.criterion_id for sc in scores}
            rows += [(f"c{c.pk}", c.name, c.max_marks, None) for c in active if c.pk not in on_sheet]
        return rows
    return [(f"c{c.pk}", c.name, c.max_marks, None) for c in active]


def can_edit(evaluation, user, candidate):
    """
    Interviewers edit only their own sheet:
      - never after the Admin's decision (the main status is no longer Pending)
      - a draft can always be changed
      - a submitted sheet only if the Admin allowed "May edit after submitting" (or unlocked it)
    """
    if user.is_superuser:
        return True  # the Admin can always change their own marks
    if decision_made(candidate):
        return False
    if evaluation is None or not evaluation.is_submitted:
        return True
    profile = getattr(user, "interviewer_profile", None)
    return bool(profile and profile.can_edit_submitted)


def expected_interviewers(candidate):
    """Active interviewers assigned to the candidate's department."""
    return InterviewerProfile.objects.filter(departments=candidate.department, user__is_active=True).distinct()


@transaction.atomic
def save_evaluation(form, candidate, user, evaluation, submit):
    """Store the marks and notes of one person's own sheet."""
    if evaluation is None:
        evaluation = InterviewEvaluation(candidate=candidate, interviewer=user, department=candidate.department)
    for field in ("comments", "strengths", "weaknesses", "recommendation", "remarks"):
        setattr(evaluation, field, form.cleaned_data.get(field) or "")
    if submit:
        evaluation.is_submitted = True
        evaluation.submitted_at = evaluation.submitted_at or timezone.now()
    evaluation.save()

    existing = {f"s{sc.pk}": sc for sc in evaluation.scores.all()}
    for order, (key, label, max_marks, _mark) in enumerate(form.mark_rows, start=1):
        mark = form.cleaned_data.get(f"mark_{key}")
        if key in existing:
            score = existing[key]
            score.mark = mark
            score.save(update_fields=["mark"])
        else:
            criterion = EvaluationCriterion.objects.filter(pk=int(key[1:])).first()
            EvaluationScore.objects.update_or_create(
                evaluation=evaluation, criterion=criterion,
                defaults={"criterion_name": label, "max_marks": max_marks, "mark": mark, "order": order},
            )
    evaluation.recalculate()
    return evaluation


def _avg(values):
    values = [Decimal(v) for v in values if v is not None]
    return (sum(values, Decimal("0")) / len(values)).quantize(Decimal("0.01")) if values else None


def summary(candidate):
    """Numbers for the Admin's view of one candidate."""
    evaluations = list(candidate.evaluations.select_related("interviewer").prefetch_related("scores"))
    submitted = [e for e in evaluations if e.is_submitted]
    return {
        "evaluations": evaluations,
        "submitted_count": len(submitted),
        "interviewer_submitted_count": sum(1 for e in submitted if not e.interviewer.is_superuser),
        "average_total": _avg(e.total_mark for e in submitted),
        # sheets can differ in length - so the average "out of" is averaged too
        "average_out_of": _avg(e.out_of for e in submitted),
        "average_percent": _avg(e.percent for e in submitted),
    }


def interview_percent_by_candidate(candidates):
    """{candidate id: average interview % of the submitted sheets} for a queryset of candidates."""
    rows = (
        InterviewEvaluation.objects.filter(candidate__in=candidates, is_submitted=True, out_of__gt=0)
        .values("candidate_id")
        .annotate(avg=Avg(F("total_mark") * 100 / F("out_of")))
    )
    return {r["candidate_id"]: Decimal(r["avg"]).quantize(Decimal("0.01")) for r in rows if r["avg"] is not None}


def latest_written(candidate):
    """The newest submitted written exam answer sheet and its % (None while written answers await marking)."""
    attempt = next(iter(candidate.written_attempts), None)
    if attempt is None or attempt.needs_marking:
        return attempt, None
    return attempt, attempt.percent


def chart_data(candidate, data, written_percent, combined):
    """Everything the graphs on the candidate sheet are drawn from - all real database values."""
    submitted = [e for e in data["evaluations"] if e.is_submitted]
    labels = []
    for evaluation in submitted:
        for score in evaluation.scores.all():
            if score.criterion_name not in labels:
                labels.append(score.criterion_name)

    per_interviewer = []
    for evaluation in submitted:
        by_name = {sc.criterion_name: sc.mark for sc in evaluation.scores.all()}
        per_interviewer.append({
            "name": evaluation.interviewer_name + (" (Admin)" if evaluation.interviewer.is_superuser else ""),
            "marks": [by_name.get(name) for name in labels],
            "total": float(evaluation.total_mark),
            "out_of": float(evaluation.out_of),
            "percent": float(evaluation.percent) if evaluation.percent is not None else None,
        })
    averages = []
    for i, _name in enumerate(labels):
        values = [p["marks"][i] for p in per_interviewer if p["marks"][i] is not None]
        averages.append(round(sum(values) / len(values), 2) if values else None)

    # where this candidate stands among everyone of the same department who has interview marks
    peers = interview_percent_by_candidate(Candidate.objects.filter(department=candidate.department))
    bins = [0] * 10
    for value in peers.values():
        bins[min(int(value // 10), 9)] += 1
    own = peers.get(candidate.pk)
    rank = (sorted(peers.values(), reverse=True).index(own) + 1) if own is not None else None

    to_float = lambda d: float(d) if d is not None else None  # noqa: E731
    return {
        "criteria": labels,
        "interviewers": per_interviewer,
        "averages": averages,
        "summary": {
            "written": to_float(written_percent),
            "interview": to_float(data["average_percent"]),
            "combined": to_float(combined),
        },
        "distribution": {
            "labels": [f"{i * 10}-{i * 10 + 10}%" for i in range(10)],
            "counts": bins,
            "own_bin": min(int(own // 10), 9) if own is not None else None,
            "rank": rank,
            "of": len(peers),
        },
        "max_mark": 5,
    }


def combined_result(written_percent, interview_percent):
    setting = InterviewSetting.load()
    return setting.combined(written_percent, interview_percent) if setting.show_combined else None


def status_filter(queryset, value):
    """Filter candidates by the one main status."""
    return queryset.filter(status=value) if value in Candidate.Status.values else queryset


def date_filter(queryset, value):
    """Interview date: the circular's interview date or the date of an interview call the person was on."""
    if not value:
        return queryset
    return queryset.filter(
        Q(circular__interview_date=value) | Q(interview_calls__interview_call__interview_date=value)
    ).distinct()
