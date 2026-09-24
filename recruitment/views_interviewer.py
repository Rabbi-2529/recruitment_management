"""Interviewer portal (/interviewer/): only the departments assigned to the signed-in interviewer.

The status shown everywhere is the candidate's one main status (Candidate.status) - the same the
Admin sees on the candidate sheet and the Evaluations page.
"""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .evaluation import can_edit, decision_made, latest_written, save_evaluation, sheet_rows
from .forms import EvaluationForm
from .models import Candidate, ExamAttempt, InterviewEvaluation
from .permissions import candidates_for_interviewer, interviewer_departments, interviewer_required
from .views_panel import save_written_marks


def _my_candidates(request):
    """Candidates of my departments, with my own evaluation state attached."""
    mine = InterviewEvaluation.objects.filter(candidate=OuterRef("pk"), interviewer=request.user)
    return (
        candidates_for_interviewer(request.user)
        .select_related("department", "circular")
        .annotate(
            my_submitted=Exists(mine.filter(is_submitted=True)),
            my_draft=Exists(mine.filter(is_submitted=False)),
        )
    )


def _get_candidate(request, pk):
    """404 for a candidate outside my departments - they must not even know it exists."""
    return get_object_or_404(_my_candidates(request), pk=pk)


def _context(request, **extra):
    return {
        "departments": interviewer_departments(request.user),
        "profile": request.user.interviewer_profile,
        "statuses": Candidate.Status.choices,
        **extra,
    }


def _with_my_marks(request, candidates):
    """Attach my own total / % to each candidate (for the tables)."""
    candidates = list(candidates)
    mine = {
        e.candidate_id: e
        for e in InterviewEvaluation.objects.filter(interviewer=request.user, candidate__in=candidates)
    }
    for c in candidates:
        c.my_evaluation = mine.get(c.pk)
    return candidates


@interviewer_required
def dashboard(request):
    candidates = _my_candidates(request)
    pending = candidates.filter(status=Candidate.Status.PENDING)
    stats = {
        "candidates": candidates.count(),
        "to_interview": pending.filter(my_submitted=False).count(),
        "submitted": candidates.filter(my_submitted=True).count(),
        "drafts": candidates.filter(my_draft=True).count(),
    }
    to_interview = _with_my_marks(request, pending.filter(my_submitted=False).order_by("name"))
    recent = (
        InterviewEvaluation.objects.filter(interviewer=request.user)
        .select_related("candidate", "department").order_by("-updated_at")[:50]
    )
    return render(request, "interviewer/dashboard.html", _context(
        request, stats=stats, to_interview=to_interview, recent=recent, nav="dashboard",
    ))


@interviewer_required
def my_interviews(request):
    """Candidates still to interview (main status Pending) or already marked by me."""
    from django.db.models import Q

    queryset = _my_candidates(request).filter(
        Q(status=Candidate.Status.PENDING) | Q(my_submitted=True) | Q(my_draft=True)
    ).order_by("name")
    return render(request, "interviewer/candidate_list.html", _context(
        request, candidates=_with_my_marks(request, queryset), title="My Interviews", nav="interviews",
    ))


@interviewer_required
def candidates(request):
    return render(request, "interviewer/candidate_list.html", _context(
        request, candidates=_with_my_marks(request, _my_candidates(request).order_by("name")),
        title="Candidates", nav="candidates",
    ))


@interviewer_required
def candidate_detail(request, pk):
    """The interviewer's sheet: candidate + interview details, written result and MY marks (not others')."""
    candidate = _get_candidate(request, pk)
    evaluation = InterviewEvaluation.objects.filter(candidate=candidate, interviewer=request.user).first()
    editable = can_edit(evaluation, request.user, candidate)
    form = EvaluationForm(
        request.POST if (request.method == "POST" and editable) else None,
        instance=evaluation, rows=sheet_rows(evaluation),
    )
    if request.method == "POST":
        if not editable:
            messages.error(request, "This evaluation is locked. Ask the Admin if a change is needed.")
            return redirect("interviewer:candidate", pk=candidate.pk)
        submit = "submit" in request.POST
        if form.is_valid() and (not submit or form.require_all_marks()):
            save_evaluation(form, candidate, request.user, evaluation, submit)
            messages.success(request, "Evaluation submitted." if submit else "Draft saved - you can finish it later.")
            return redirect("interviewer:candidate", pk=candidate.pk)
        messages.error(request, "Please check the marks below - whole numbers 0 to 5 only.")

    written, written_percent = latest_written(candidate)
    calls = list(candidate.interview_calls.select_related("interview_call"))
    interview_date = candidate.circular.interview_date or next(
        (c.interview_call.interview_date for c in calls if c.interview_call.interview_date), None
    )
    return render(request, "interviewer/candidate_detail.html", _context(
        request, candidate=candidate, evaluation=evaluation, form=form, editable=editable,
        decision_made=decision_made(candidate), written_attempts=candidate.written_attempts,
        written=written, written_percent=written_percent, interview_date=interview_date, nav="candidates",
    ))


@interviewer_required
def candidate_cv(request, pk):
    candidate = _get_candidate(request, pk)
    if not candidate.cv:
        raise Http404("This candidate has no CV.")
    try:
        file = candidate.cv.open("rb")
    except FileNotFoundError:
        raise Http404("The CV file is missing from the server.")
    return FileResponse(file, as_attachment=request.GET.get("download") == "1", filename=candidate.cv_filename)


@interviewer_required
def written_results(request):
    attempts = (
        ExamAttempt.objects.filter(
            submitted_at__isnull=False, candidate__department__in=interviewer_departments(request.user)
        )
        .select_related("exam", "candidate", "candidate__department")
        .order_by("-submitted_at")
    )
    return render(request, "interviewer/written_results.html", _context(
        request, attempts=attempts, nav="written",
    ))


@interviewer_required
def written_sheet(request, pk):
    """View one answer sheet; marking only if the Admin granted "May mark written exam answers"."""
    attempt = get_object_or_404(
        ExamAttempt.objects.select_related("exam", "candidate", "candidate__department"),
        pk=pk, submitted_at__isnull=False, candidate__department__in=interviewer_departments(request.user),
    )
    can_mark = request.user.interviewer_profile.can_edit_written_marks
    answers = list(attempt.answers.select_related("question", "choice").prefetch_related("question__choices"))
    if request.method == "POST":
        if not can_mark:
            messages.error(request, "You can view written results but not change them.")
        else:
            errors = save_written_marks(attempt, answers, request.POST)
            for error in errors:
                messages.error(request, error)
            if not errors:
                messages.success(request, "Written marks saved.")
        return redirect("interviewer:written_sheet", pk=attempt.pk)
    return render(request, "interviewer/written_sheet.html", _context(
        request, attempt=attempt, answers=answers, can_mark=can_mark, nav="written",
    ))


@interviewer_required
def profile(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs["class"] = "input"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)  # stay signed in
        messages.success(request, "Password changed.")
        return redirect("interviewer:profile")
    return render(request, "interviewer/profile.html", _context(request, form=form, nav="profile"))


@interviewer_required
@require_POST
def logout_view(request):
    from django.contrib.auth import logout

    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("panel:login")
