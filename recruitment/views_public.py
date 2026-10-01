from decimal import Decimal

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.dateformat import format as date_format

from .forms import ExamAnswerForm, ExamIdentifyForm, PublicSearchForm, ReferenceFormSet, RegistrationForm
from .models import Answer, Candidate, Circular, Exam, ExamAttempt, SubmitReason
from .utils import normalize_phone


def landing(request):
    results = None
    searched = False

    if request.method == "POST":
        form = PublicSearchForm(request.POST)
        if form.is_valid():
            searched = True
            results = list(
                Candidate.objects.filter(
                    phone=form.cleaned_data["phone"],
                    department=form.cleaned_data["department"],
                    circular__is_active=True,
                )
                .select_related("circular", "department")
                .order_by("-circular__published_on", "-created_at")
            )
    else:
        form = PublicSearchForm()

    return render(
        request,
        "public/landing.html",
        {"form": form, "results": results, "searched": searched, "Status": Candidate.Status, "nav": "result"},
    )


def interview_text(circular):
    """Human friendly interview date, e.g. 'Sunday, 04 Oct 2026'."""
    if not circular.interview_date:
        return ""
    return date_format(circular.interview_date, "l, d M Y")


def register(request):
    form = RegistrationForm(request.POST or None, request.FILES or None)
    posted_references = "ref-TOTAL_FORMS" in request.POST
    references = ReferenceFormSet(request.POST if posted_references else None, prefix="ref")

    if request.method == "POST" and form.is_valid() and (not posted_references or references.is_valid()):
        candidate = form.save(commit=False)
        candidate.source = Candidate.Source.ONLINE
        candidate.status = Candidate.Status.PENDING
        try:
            with transaction.atomic():
                candidate.save()
                if posted_references:
                    references.save_for(candidate)
        except IntegrityError:  # same phone submitted twice at the same moment
            form.add_error("phone", "This phone number is already registered for this department.")
        else:
            request.session["registered_candidate"] = candidate.pk
            return redirect("public:register_done")

    mapping, interviews = {}, {}
    for circular in Circular.objects.open_for_registration().prefetch_related("departments"):
        mapping[str(circular.pk)] = [d.pk for d in circular.departments.all()]
        interviews[str(circular.pk)] = interview_text(circular)

    return render(
        request,
        "public/register.html",
        {
            "form": form,
            "references": references,
            "has_open_circulars": bool(mapping),
            "circular_departments": mapping,
            "circular_interviews": interviews,
            "nav": "register",
        },
    )


def register_done(request):
    pk = request.session.get("registered_candidate")
    candidate = Candidate.objects.select_related("circular", "department").filter(pk=pk).first() if pk else None
    if candidate is None:
        return redirect("public:register")
    return render(request, "public/register_done.html", {"candidate": candidate, "nav": "register"})


# ----------------------------------------------------------------- written exam
def _find_candidates(exam, identity):
    """Match the typed phone number or email against the candidates registered for this exam."""
    identity = (identity or "").strip()
    people = exam.eligible_candidates().select_related("department", "circular")
    if "@" in identity:
        return list(people.filter(email__iexact=identity))
    phone = normalize_phone(identity)
    return list(people.filter(phone=phone)) if phone else []


def _session_key(exam):
    return f"exam_attempt_{exam.pk}"


def exam_start(request, token):
    """Candidate opens the exam link and proves they registered."""
    exam = get_object_or_404(Exam.objects.select_related("circular"), token=token)
    form = ExamIdentifyForm(request.POST or None)
    error = None

    if not exam.is_available():
        return render(request, "public/exam_closed.html", {"exam": exam, "nav": "exam"})

    if request.method == "POST" and form.is_valid():
        matches = _find_candidates(exam, form.cleaned_data["identity"])
        chosen = request.POST.get("candidate")
        if chosen:
            matches = [c for c in matches if str(c.pk) == chosen] or matches
        if not matches:
            error = "not_registered"
        elif len(matches) > 1:
            return render(
                request,
                "public/exam_start.html",
                {"exam": exam, "form": form, "matches": matches, "nav": "exam"},
            )
        else:
            attempt, _ = ExamAttempt.objects.get_or_create(exam=exam, candidate=matches[0])
            request.session[_session_key(exam)] = attempt.pk
            if attempt.is_submitted:
                return redirect("public:exam_done", token=exam.token)
            if not attempt.question_order:
                attempt.question_order = exam.draw_questions()  # this candidate's random set
                attempt.save(update_fields=["question_order"])
            return redirect("public:exam_questions", token=exam.token)

    return render(
        request,
        "public/exam_start.html",
        {"exam": exam, "form": form, "error": error, "question_count": exam.questions.count(), "nav": "exam"},
    )


def _current_attempt(request, exam):
    pk = request.session.get(_session_key(exam))
    if not pk:
        return None
    return ExamAttempt.objects.select_related("candidate", "exam").filter(pk=pk, exam=exam).first()


def _answers_from(post, questions):
    """{"<question id>": value} for this candidate's questions, from a posted form."""
    values = {}
    for question in questions:
        value = (post.get(f"q{question.pk}") or "").strip()
        if value:
            values[str(question.pk)] = value[:20000]
    return values


def _finalize(attempt, questions, values, reason):
    """Store the answers and close the sheet: on submit, when time is over, or when a rule is broken."""
    with transaction.atomic():
        attempt = ExamAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.is_submitted:  # a second request (e.g. timer + tab switch) arrived at the same moment
            return attempt
        for question in questions:
            raw = values.get(str(question.pk), "")
            fields = {"choice": None, "choice_text": "", "text_answer": "", "awarded": None}
            if question.is_mcq:
                choice = next((c for c in question.choices.all() if str(c.pk) == str(raw)), None)
                if choice is not None:
                    fields.update(choice=choice, choice_text=choice.text,
                                  awarded=question.marks if choice.is_correct else Decimal("0"))
                else:
                    fields["awarded"] = Decimal("0")  # not answered
            else:
                fields["text_answer"] = str(raw).strip()
            Answer.objects.update_or_create(attempt=attempt, question=question, defaults=fields)
        attempt.draft = values
        attempt.submit_reason = reason
        attempt.log("submitted", reason=reason)
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["draft", "submit_reason", "events", "submitted_at"])
        attempt.recalculate()
    return attempt


def close_expired_attempts(exam=None):
    """Hand in every sheet whose time is over, with the answers saved so far (candidate closed the browser)."""
    attempts = ExamAttempt.objects.filter(submitted_at__isnull=True, exam__duration_minutes__isnull=False)
    if exam is not None:
        attempts = attempts.filter(exam=exam)
    for attempt in attempts.select_related("exam"):
        if attempt.is_expired:
            _finalize(attempt, attempt.questions(), attempt.draft, SubmitReason.TIME_UP)


# a small allowance so the candidate's own clock hitting 00:00 is accepted as "time over"
TIME_UP_GRACE_SECONDS = 5


def exam_questions(request, token):
    exam = get_object_or_404(Exam.objects.select_related("circular"), token=token)
    attempt = _current_attempt(request, exam)
    if attempt is None:
        return redirect("public:exam_start", token=exam.token)
    if attempt.is_submitted:
        return redirect("public:exam_done", token=exam.token)
    if not exam.is_available():
        return render(request, "public/exam_closed.html", {"exam": exam, "nav": "exam"})

    questions = attempt.questions()
    # the timer started when the candidate entered their phone number / email
    if attempt.is_expired and request.method != "POST":
        _finalize(attempt, questions, attempt.draft, SubmitReason.TIME_UP)
        return redirect("public:exam_done", token=exam.token)

    if request.method == "POST":
        values = _answers_from(request.POST, questions)
        near_end = attempt.seconds_left is not None and attempt.seconds_left <= TIME_UP_GRACE_SECONDS
        if attempt.is_expired or near_end:
            _finalize(attempt, questions, values, SubmitReason.TIME_UP)
            return redirect("public:exam_done", token=exam.token)
        # the candidate pressed Submit: required questions must be answered first
        attempt.draft = values
        attempt.save(update_fields=["draft"])
        form = ExamAnswerForm(request.POST, questions=questions, choices_for=attempt.choices_for)
        if form.is_valid():
            _finalize(attempt, questions, values, SubmitReason.SUBMITTED)
            return redirect("public:exam_done", token=exam.token)
    else:
        form = ExamAnswerForm(questions=questions, choices_for=attempt.choices_for, draft=attempt.draft)

    return render(
        request,
        "public/exam_questions.html",
        {
            "exam": exam,
            "attempt": attempt,
            "form": form,
            "deadline": attempt.deadline,
            "seconds_left": attempt.seconds_left,
            "strict": exam.is_strict,
            "nav": "exam",
        },
    )


@require_POST
def exam_autosave(request, token):
    """Answers are saved every few seconds, so a refresh or a closed browser does not lose them."""
    exam = get_object_or_404(Exam, token=token)
    attempt = _current_attempt(request, exam)
    if attempt is None:
        return JsonResponse({"ok": False, "closed": True, "redirect": exam.get_absolute_url()}, status=403)
    done_url = reverse("public:exam_done", args=[exam.token])
    if attempt.is_submitted:
        return JsonResponse({"ok": True, "closed": True, "redirect": done_url})

    questions = attempt.questions()
    values = _answers_from(request.POST, questions)
    if attempt.is_expired:
        _finalize(attempt, questions, values, SubmitReason.TIME_UP)
        return JsonResponse({"ok": True, "closed": True, "redirect": done_url})
    attempt.draft = values
    attempt.save(update_fields=["draft"])
    return JsonResponse({"ok": True, "closed": False, "seconds_left": attempt.seconds_left})


LEAVE_REASONS = {
    "tab_switch": SubmitReason.TAB_SWITCH,
    "window_blur": SubmitReason.WINDOW_BLUR,
    "screenshot": SubmitReason.SCREENSHOT,
}


@require_POST
def exam_leave(request, token):
    """Strict mode: the page reports a rule break (sent with navigator.sendBeacon) - hand in the sheet now."""
    exam = get_object_or_404(Exam, token=token)
    attempt = _current_attempt(request, exam)
    if attempt is None or attempt.is_submitted:
        return JsonResponse({"ok": True})
    questions = attempt.questions()
    values = _answers_from(request.POST, questions) or attempt.draft
    reason = LEAVE_REASONS.get(request.POST.get("reason"), SubmitReason.TAB_SWITCH)
    if not exam.is_strict:
        attempt.log(reason)  # only noted for HR
        attempt.draft = values
        attempt.save(update_fields=["events", "draft"])
        return JsonResponse({"ok": True, "closed": False})
    attempt.log(reason)
    attempt.save(update_fields=["events"])
    _finalize(attempt, questions, values, reason)
    return JsonResponse({"ok": True, "closed": True, "redirect": reverse("public:exam_done", args=[exam.token])})


def exam_done(request, token):
    exam = get_object_or_404(Exam.objects.select_related("circular"), token=token)
    attempt = _current_attempt(request, exam)
    if attempt is None or not attempt.is_submitted:
        return redirect("public:exam_start", token=exam.token)
    return render(request, "public/exam_done.html", {"exam": exam, "attempt": attempt, "nav": "exam"})
