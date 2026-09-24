import csv
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    BulkActionForm,
    CandidateForm,
    CandidateUploadForm,
    CircularForm,
    DepartmentForm,
    InterviewForm,
    ExamForm,
    InterviewSearchForm,
    PanelLoginForm,
    QuestionForm,
    ReviewForm,
)
from .models import (
    MARK_MAX,
    Answer,
    Candidate,
    Circular,
    Department,
    Exam,
    ExamAttempt,
    Question,
    fmt_mark,
)
from .permissions import admin_required, home_url_for, is_admin, is_interviewer
from .views_public import close_expired_attempts
from .utils import (
    is_valid_phone,
    normalize_phone,
    parse_date,
    parse_mark,
    parse_rating,
    parse_whole_mark,
    parse_salary,
    parse_status,
    read_candidate_file,
)


# ------------------------------------------------------------------ auth
# Admin-only pages. A signed-in interviewer is sent to their own dashboard (see permissions.py).
superuser_required = admin_required


def panel_login(request):
    """One sign-in page for both roles: Admins go to the HR panel, Interviewers to their dashboard."""
    if is_admin(request.user) or is_interviewer(request.user):
        return redirect(home_url_for(request.user))

    form = PanelLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        user = authenticate(request, username=email, password=form.cleaned_data["password"])
        if user is not None and (is_admin(user) or is_interviewer(user)):
            login(request, user)
            home = home_url_for(user)
            next_url = request.GET.get("next", "")
            # an interviewer is never sent on to an admin page, whatever "next" says
            if (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})
                    and (is_admin(user) or next_url.startswith(reverse("interviewer:dashboard")))):
                return redirect(next_url)
            return redirect(home)
        messages.error(request, "Invalid email or password.")
    return render(request, "panel/login.html", {"form": form})


@require_POST
def panel_logout(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("panel:login")


# ------------------------------------------------------------------ dashboard
@superuser_required
def dashboard(request):
    stats = Candidate.objects.aggregate(
        total=Count("id"),
        selected=Count("id", filter=Q(status=Candidate.Status.SELECTED)),
        not_selected=Count("id", filter=Q(status=Candidate.Status.NOT_SELECTED)),
        waiting=Count("id", filter=Q(status=Candidate.Status.WAITING)),
        pending=Count("id", filter=Q(status=Candidate.Status.PENDING)),
    )
    circulars = Circular.objects.annotate(
        total=Count("candidates"),
        selected=Count("candidates", filter=Q(candidates__status=Candidate.Status.SELECTED)),
    )[:8]
    recent = Candidate.objects.select_related("circular", "department").order_by("-updated_at")[:8]
    return render(
        request,
        "panel/dashboard.html",
        {
            "stats": stats,
            "circulars": circulars,
            "recent": recent,
            "circular_count": Circular.objects.count(),
            "department_count": Department.objects.count(),
            "active": "dashboard",
        },
    )


# ------------------------------------------------------------------ circulars
@superuser_required
def circular_list(request):
    circulars = Circular.objects.annotate(
        total=Count("candidates", distinct=True),
        selected=Count("candidates", filter=Q(candidates__status=Candidate.Status.SELECTED), distinct=True),
    ).prefetch_related("departments")
    return render(request, "panel/circular_list.html", {"circulars": circulars, "active": "circulars"})


@superuser_required
def circular_form(request, pk=None):
    instance = get_object_or_404(Circular, pk=pk) if pk else None
    form = CircularForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, f"Circular “{obj.title}” saved.")
        return redirect("panel:circular_list")
    return render(
        request,
        "panel/form.html",
        {
            "form": form,
            "title": "Edit Circular" if instance else "New Circular",
            "back_url": reverse("panel:circular_list"),
            "active": "circulars",
        },
    )


@superuser_required
def circular_delete(request, pk):
    obj = get_object_or_404(Circular, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Circular and its candidate list deleted.")
        return redirect("panel:circular_list")
    return render(
        request,
        "panel/confirm_delete.html",
        {
            "object": obj,
            "warning": f"This will also delete {obj.candidates.count()} candidate(s) under this circular.",
            "back_url": reverse("panel:circular_list"),
            "active": "circulars",
        },
    )


# ------------------------------------------------------------------ departments
@superuser_required
def department_list(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department added.")
        return redirect("panel:department_list")
    departments = Department.objects.annotate(total=Count("candidates"))
    return render(
        request, "panel/department_list.html", {"form": form, "departments": departments, "active": "departments"}
    )


@superuser_required
def department_edit(request, pk):
    instance = get_object_or_404(Department, pk=pk)
    form = DepartmentForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department updated.")
        return redirect("panel:department_list")
    return render(
        request,
        "panel/form.html",
        {
            "form": form,
            "title": "Edit Department",
            "back_url": reverse("panel:department_list"),
            "active": "departments",
        },
    )


@superuser_required
def department_delete(request, pk):
    obj = get_object_or_404(Department, pk=pk)
    if request.method == "POST":
        try:
            obj.delete()
            messages.success(request, "Department deleted.")
        except ProtectedError:
            messages.error(request, "Cannot delete: candidates are assigned to this department.")
        return redirect("panel:department_list")
    return render(
        request,
        "panel/confirm_delete.html",
        {"object": obj, "back_url": reverse("panel:department_list"), "active": "departments"},
    )


# ------------------------------------------------------------------ candidates
def _filtered_candidates(params):
    qs = Candidate.objects.select_related("circular", "department")
    f = {
        "q": (params.get("q") or params.get("search[value]") or "").strip(),
        "circular": params.get("circular", ""),
        "department": params.get("department", ""),
        "status": params.get("status", ""),
    }
    if f["q"]:
        phone = normalize_phone(f["q"])
        cond = (
            Q(name__icontains=f["q"]) | Q(department__name__icontains=f["q"]) | Q(email__icontains=f["q"])
        )
        if phone:
            cond |= Q(phone__icontains=phone)
        qs = qs.filter(cond)
    if f["circular"].isdigit():
        qs = qs.filter(circular_id=f["circular"])
    if f["department"].isdigit():
        qs = qs.filter(department_id=f["department"])
    if f["status"] in Candidate.Status.values:
        qs = qs.filter(status=f["status"])
    return qs, f


@superuser_required
def candidate_list(request):
    _, filters = _filtered_candidates(request.GET)
    return render(
        request,
        "panel/candidate_list.html",
        {
            "filters": filters,
            "circulars": Circular.objects.all(),
            "departments": Department.objects.all(),
            "statuses": Candidate.Status.choices,
            "bulk_actions": BulkActionForm.ACTIONS,
            "active": "candidates",
        },
    )


# DataTables column name (its "data" key) -> model field used for ordering.
# Tables with different column layouts (candidate list, interview list) share this endpoint.
CANDIDATE_ORDER_BY_NAME = {
    "name": "name",
    "phone": "phone",
    "email": "email",
    "department": "department__name",
    "written_mark": "written_mark",
    "total": "score_percent",
    "status": "status",
    "updated": "updated_at",
}

# Fallback for callers that only send the column index
CANDIDATE_ORDER_FIELDS = {
    1: "name",
    2: "phone",
    3: "department__name",
    4: "written_mark",
    5: "score_percent",
    6: "status",
    7: "updated_at",
}


@superuser_required
def candidate_data(request):
    """Server-side processing endpoint for the DataTables candidate list."""
    params = request.GET
    total = Candidate.objects.count()
    qs, _ = _filtered_candidates(params)
    filtered = qs.count()

    col = params.get("order[0][column]", "")
    field = None
    if col.isdigit():
        field = CANDIDATE_ORDER_BY_NAME.get(params.get(f"columns[{col}][data]", "")) or CANDIDATE_ORDER_FIELDS.get(
            int(col)
        )
    if field:
        qs = qs.order_by(("-" if params.get("order[0][dir]") == "desc" else "") + field, "-pk")

    try:
        start = max(int(params.get("start", 0)), 0)
        length = int(params.get("length", 25))
    except ValueError:
        start, length = 0, 25
    if length <= 0 or length > 1000:
        length = 1000

    rows = [
        {
            "id": c.pk,
            "name": c.name,
            "note": c.note,
            "waiting_reason": c.waiting_reason,
            "phone": c.phone,
            "department": c.department.name,
            "circular": c.circular.title,
            "reference_no": c.circular.reference_no,
            "status": c.status,
            "email": c.email,
            "source": c.source,
            **{f: _fmt(getattr(c, f)) for f, _ in Candidate.MARK_FIELDS},
            "total": _fmt(c.total_marks),
            "out_of": c.marks_out_of,
            "percent": _fmt(c.score_percent),
            "marks_url": reverse("panel:candidate_marks", args=[c.pk]),
            "rating": c.rating or "",
            "expected_salary": c.expected_salary if c.expected_salary is not None else "",
            "available_date": c.available_date.isoformat() if c.available_date else "",
            "available_display": c.available_date.strftime("%d %b %Y") if c.available_date else "",
            "review_url": reverse("panel:candidate_review", args=[c.pk]),
            "interview_url": reverse("panel:interview_detail", args=[c.pk]),
            "cv_url": reverse("panel:candidate_cv", args=[c.pk]) if c.cv else "",
            "updated": c.updated_at.strftime("%d %b %Y"),
            "edit_url": reverse("panel:candidate_edit", args=[c.pk]),
            "delete_url": reverse("panel:candidate_delete", args=[c.pk]),
            "status_url": reverse("panel:candidate_status", args=[c.pk]),
        }
        for c in qs[start : start + length]
    ]
    return JsonResponse(
        {"draw": int(params.get("draw", 0) or 0), "recordsTotal": total, "recordsFiltered": filtered, "data": rows}
    )


@superuser_required
def candidate_form(request, pk=None):
    instance = get_object_or_404(Candidate, pk=pk) if pk else None
    initial = {}
    if not instance and request.GET.get("circular", "").isdigit():
        initial["circular"] = request.GET["circular"]
    form = CandidateForm(request.POST or None, request.FILES or None, instance=instance, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        obj.circular.departments.add(obj.department)  # no-op when already linked
        messages.success(request, f"Candidate {obj.name} saved.")
        if "save_add" in request.POST:
            return redirect(f"{reverse('panel:candidate_add')}?circular={obj.circular_id}")
        return redirect("panel:candidate_list")
    return render(
        request,
        "panel/form.html",
        {
            "form": form,
            "title": "Edit Candidate" if instance else "Add Candidate",
            "back_url": reverse("panel:candidate_list"),
            "save_add": instance is None,
            "circular_departments": _circular_department_map(),
            "active": "candidates",
        },
    )


def _circular_department_map():
    """{circular_id: [department_id, ...]} used to filter the department dropdown by circular."""
    mapping = {}
    for circular_id, department_id in Circular.departments.through.objects.values_list("circular_id", "department_id"):
        mapping.setdefault(str(circular_id), []).append(department_id)
    return mapping


def _fmt(number):
    """Decimal -> short string without trailing zeros ("4.50" -> "4.5"), empty for None."""
    return "" if number is None else f"{Decimal(number).normalize():f}"


@superuser_required
@require_POST
def candidate_marks(request, pk):
    """Save one mark from the candidate list (AJAX). Empty value = not taken."""
    obj = get_object_or_404(Candidate, pk=pk)
    field = request.POST.get("field")
    labels = dict(Candidate.MARK_FIELDS)
    if field not in labels:
        return JsonResponse({"ok": False, "error": "Unknown mark."}, status=400)

    raw = (request.POST.get("value") or "").strip()
    value = None
    if raw:
        value = parse_whole_mark(raw, MARK_MAX)
        if value is None:
            return JsonResponse({"ok": False, "error": "Marks are whole numbers only: 0, 1, 2, 3, 4 or 5."}, status=400)

    setattr(obj, field, value)
    obj.save(update_fields=[field, "updated_at"])
    action = "saved" if value is not None else "cleared (not taken)"
    return JsonResponse(
        {
            "ok": True,
            "value": _fmt(value),
            "total": _fmt(obj.total_marks),
            "out_of": obj.marks_out_of,
            "percent": _fmt(obj.score_percent),
            "message": f"{obj.name}: {labels[field]} mark {action}.",
        }
    )


@superuser_required
@require_POST
def candidate_review(request, pk):
    """Save rating, expected salary, available date and note from the review popup (AJAX)."""
    obj = get_object_or_404(Candidate, pk=pk)
    form = ReviewForm(request.POST, instance=obj)
    if not form.is_valid():
        errors = {field: [str(e) for e in errs] for field, errs in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    form.save()
    return JsonResponse({"ok": True, "message": f"Review saved for {obj.name}."})


@superuser_required
@require_POST
def candidate_status(request, pk):
    obj = get_object_or_404(Candidate, pk=pk)
    status = request.POST.get("status")
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if status not in Candidate.Status.values:
        if wants_json:
            return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
        messages.error(request, "Invalid status.")
        return _back(request, "panel:candidate_list")
    obj.status = status
    fields = ["status", "updated_at"]
    if "waiting_reason" in request.POST:  # asked for when HR picks "Waiting"
        obj.waiting_reason = request.POST.get("waiting_reason", "").strip()
        fields.append("waiting_reason")
    obj.save(update_fields=fields)
    text = f"{obj.name}: status changed to {obj.get_status_display()}."
    if wants_json:
        return JsonResponse({"ok": True, "status": obj.status, "waiting_reason": obj.waiting_reason, "message": text})
    messages.success(request, text)
    return _back(request, "panel:candidate_list")


@superuser_required
def candidate_delete(request, pk):
    obj = get_object_or_404(Candidate, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Candidate deleted.")
        return redirect("panel:candidate_list")
    return render(
        request,
        "panel/confirm_delete.html",
        {"object": obj, "back_url": reverse("panel:candidate_list"), "active": "candidates"},
    )


@superuser_required
@require_POST
def candidate_bulk(request):
    form = BulkActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Select at least one candidate and an action.")
        return _back(request, "panel:candidate_list")
    ids = [i for i in form.cleaned_data["ids"].split(",") if i.isdigit()]
    qs = Candidate.objects.filter(pk__in=ids)
    action = form.cleaned_data["action"]
    if action == "delete":
        count, _ = qs.delete()
        messages.success(request, f"Deleted {count} candidate(s).")
    else:
        count = 0
        for c in qs:
            c.status = action
            c.save(update_fields=["status", "updated_at"])
            count += 1
        messages.success(request, f"Updated status for {count} candidate(s).")
    return _back(request, "panel:candidate_list")


@superuser_required
def candidate_upload(request):
    initial = {}
    if request.GET.get("circular", "").isdigit():
        initial["circular"] = request.GET["circular"]
    form = CandidateUploadForm(request.POST or None, request.FILES or None, initial=initial)
    report = None

    if request.method == "POST" and form.is_valid():
        rows, error = read_candidate_file(form.cleaned_data["file"])
        if error:
            form.add_error("file", error)
        else:
            report = _import_rows(
                rows,
                circular=form.cleaned_data["circular"],
                default_status=form.cleaned_data["default_status"],
                update_existing=form.cleaned_data["update_existing"],
            )
            messages.success(
                request,
                f"Import finished: {report['created']} added, {report['updated']} updated, "
                f"{report['skipped']} skipped, {len(report['errors'])} error(s).",
            )

    return render(request, "panel/candidate_upload.html", {"form": form, "report": report, "active": "upload"})


def _import_rows(rows, circular, default_status, update_existing):
    report = {"created": 0, "updated": 0, "skipped": 0, "errors": [], "new_departments": []}
    with transaction.atomic():
        for row in rows:
            line = row["line"]
            name = " ".join(str(row.get("name") or "").split())
            phone = normalize_phone(row.get("phone"))
            dept_name = " ".join(str(row.get("department") or "").split())
            raw_status = row.get("status")

            if not name:
                report["errors"].append((line, "Name is empty"))
                continue
            if not is_valid_phone(phone):
                report["errors"].append((line, f"Invalid phone “{row.get('phone')}”"))
                continue
            if not dept_name:
                report["errors"].append((line, "Department is empty"))
                continue

            status = default_status
            if raw_status not in (None, ""):
                status = parse_status(raw_status)
                if status is None:
                    report["errors"].append((line, f"Unknown status “{raw_status}”"))
                    continue

            marks, mark_error = {}, None
            for f, label in Candidate.MARK_FIELDS:
                if row.get(f) in (None, ""):
                    continue  # empty = not taken
                mark = parse_whole_mark(row.get(f), MARK_MAX)
                if mark is None:
                    mark_error = f"{label} mark '{row.get(f)}' must be a whole number 0 to 5"
                    break
                marks[f] = mark
            if mark_error:
                report["errors"].append((line, mark_error))
                continue
            email = str(row.get("email") or "").strip()

            review, review_error = {}, None
            for key, parser, label in (
                ("rating", parse_rating, "Rating must be 1 to 5"),
                ("expected_salary", parse_salary, "Expected salary must be a number"),
                ("available_date", parse_date, "Available date must be like 2026-10-01 or 01/10/2026"),
            ):
                raw = row.get(key)
                if raw in (None, ""):
                    continue
                parsed = parser(raw)
                if parsed is None:
                    review_error = f"{label} (got '{raw}')"
                    break
                review[key] = parsed
            if review_error:
                report["errors"].append((line, review_error))
                continue
            note = str(row.get("note") or "").strip()
            if note:
                review["note"] = note

            department, dept_created = Department.get_or_create_by_name(dept_name)
            if dept_created:
                report["new_departments"].append(department.name)
            circular.departments.add(department)

            existing = Candidate.objects.filter(circular=circular, department=department, phone=phone).first()
            if existing:
                if not update_existing:
                    report["skipped"] += 1
                    continue
                existing.name = name
                if raw_status not in (None, ""):
                    existing.status = status
                if email:
                    existing.email = email
                for f, value in {**marks, **review}.items():
                    setattr(existing, f, value)
                existing.save()
                report["updated"] += 1
            else:
                Candidate.objects.create(
                    circular=circular, department=department, name=name, phone=phone, status=status,
                    email=email, source=Candidate.Source.UPLOAD, **marks, **review,
                )
                report["created"] += 1
    return report


@superuser_required
def candidate_template(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="candidate_template.csv"'
    writer = csv.writer(response)
    writer.writerow(["Name", "Phone", "Department", "Email", "Status", "Written",
                     "Rating", "Expected Salary", "Available Date", "Note"])
    writer.writerow(["Abdul Karim", "01711000001", "Software Engineering", "karim@example.com", "Pending",
                     "4.5", "4", "30000", "2026-10-01", "Good communication"])
    writer.writerow(["Nusrat Jahan", "01811000002", "Accounts", "", "Selected", "", "5", "25000", "", ""])
    writer.writerow(["Rahim Uddin", "01911000003", "Marketing", "", "Not Selected", "", "", "", "", ""])
    return response


@superuser_required
def candidate_export(request):
    qs, _ = _filtered_candidates(request.GET)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="candidates.csv"'
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(response)
    writer.writerow(
        ["Name", "Phone", "Email", "Department", "Status", *[label for _, label in Candidate.MARK_FIELDS],
         "Total", "Out Of", "Percent", "Rating", "Expected Salary", "Available Date",
         "Circular", "Reference No", "Registered Via", "Note"]
    )
    for c in qs:
        writer.writerow(
            [c.name, c.phone, c.email, c.department.name, c.get_status_display(),
             *[_fmt(getattr(c, f)) for f, _ in Candidate.MARK_FIELDS],
             _fmt(c.total_marks), c.marks_out_of or "", _fmt(c.score_percent),
             c.rating or "", c.expected_salary if c.expected_salary is not None else "",
             c.available_date.isoformat() if c.available_date else "",
             c.circular.title, c.circular.reference_no, c.get_source_display(), c.note]
        )
    return response


def _back(request, fallback):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(fallback)


# ------------------------------------------------------------------ interview
def _interview_matches(query):
    """Candidates matching a phone number, an email (or a name typed instead)."""
    phone = normalize_phone(query)
    cond = Q(email__icontains=query) | Q(name__icontains=query)
    if phone:
        cond |= Q(phone__icontains=phone)
    return (
        Candidate.objects.select_related("circular", "department").filter(cond).order_by("-updated_at")
    )


@superuser_required
def interview_suggest(request):
    """Select2 autocomplete for the Interview search box: type a phone number (or email) -> candidates."""
    term = (request.GET.get("q") or request.GET.get("term") or "").strip()
    results = []
    if len(term) >= 2:
        for c in _interview_matches(term)[:20]:
            results.append(
                {
                    "id": c.pk,
                    "text": f"{c.phone} · {c.name}",  # what the box shows once picked
                    "name": c.name,
                    "phone": c.phone,
                    "email": c.email,
                    "department": c.department.name,
                    "circular": c.circular.reference_no,
                    "status": c.get_status_display(),
                    "status_key": c.status,
                    "url": reverse("panel:interview_detail", args=[c.pk]),
                }
            )
    return JsonResponse({"results": results})


@superuser_required
def interview(request):
    """Search a candidate by phone number or email and open the interview sheet."""
    searched = "q" in request.GET
    form = InterviewSearchForm(request.GET or None) if searched else InterviewSearchForm()
    matches = []
    if searched and form.is_valid():
        matches = list(_interview_matches(form.cleaned_data["q"])[:50])
        if len(matches) == 1:
            return redirect("panel:interview_detail", pk=matches[0].pk)

    return render(
        request,
        "panel/interview_search.html",
        {
            "form": form,
            "matches": matches,
            "searched": searched,
            "statuses": Candidate.Status.choices,
            "active": "interview",
        },
    )


@superuser_required
def candidate_cv(request, pk):
    """Serve an uploaded CV to logged-in HR only (MEDIA_ROOT is not public)."""
    candidate = get_object_or_404(Candidate, pk=pk)
    if not candidate.cv:
        raise Http404("This candidate has no CV.")
    try:
        file = candidate.cv.open("rb")
    except FileNotFoundError:
        raise Http404("The CV file is missing from the server.")
    download = request.GET.get("download") == "1"
    return FileResponse(file, as_attachment=download, filename=candidate.cv_filename)


# ------------------------------------------------------------------ written exam
@superuser_required
def exam_list(request):
    exams = (
        Exam.objects.select_related("circular")
        .prefetch_related("departments")
        .annotate(
            question_count=Count("questions", distinct=True),
            response_count=Count("attempts", filter=Q(attempts__submitted_at__isnull=False), distinct=True),
        )
    )
    return render(request, "panel/exam_list.html", {"exams": exams, "active": "exams"})


@superuser_required
def exam_form(request, pk=None):
    instance = get_object_or_404(Exam, pk=pk) if pk else None
    form = ExamForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        exam = form.save()
        messages.success(request, f"Exam “{exam.title}” saved.")
        return redirect("panel:exam_questions", pk=exam.pk)
    return render(
        request,
        "panel/form.html",
        {
            "form": form,
            "title": "Edit Exam" if instance else "New Written Exam",
            "back_url": reverse("panel:exam_list"),
            "circular_departments": _circular_department_map(),
            "active": "exams",
        },
    )


@superuser_required
def exam_delete(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    if request.method == "POST":
        exam.delete()
        messages.success(request, "Exam deleted.")
        return redirect("panel:exam_list")
    return render(
        request,
        "panel/confirm_delete.html",
        {
            "object": exam,
            "warning": f"This also deletes {exam.questions.count()} question(s) and "
                       f"{exam.attempts.count()} candidate answer sheet(s).",
            "back_url": reverse("panel:exam_list"),
            "active": "exams",
        },
    )


@superuser_required
def exam_questions(request, pk):
    exam = get_object_or_404(Exam.objects.select_related("circular"), pk=pk)
    return render(
        request,
        "panel/exam_questions.html",
        {
            "exam": exam,
            "questions": exam.questions.prefetch_related("choices"),
            "exam_url": request.build_absolute_uri(exam.get_absolute_url()),
            "active": "exams",
        },
    )


@superuser_required
def question_form(request, exam_pk, pk=None):
    exam = get_object_or_404(Exam, pk=exam_pk)
    instance = get_object_or_404(Question, pk=pk, exam=exam) if pk else None
    initial = {} if instance else {"order": exam.questions.count() + 1}
    form = QuestionForm(request.POST or None, instance=instance, initial=initial)
    form.instance.exam = exam

    if request.method == "POST" and form.is_valid():
        form.save()  # saves the question and rebuilds its options
        messages.success(request, "Question saved.")
        if "save_add" in request.POST:
            return redirect("panel:question_add", exam_pk=exam.pk)
        return redirect("panel:exam_questions", pk=exam.pk)

    return render(
        request,
        "panel/question_form.html",
        {
            "form": form,
            "exam": exam,
            "question": instance,
            "title": "Edit Question" if instance else "Add Question",
            "back_url": reverse("panel:exam_questions", args=[exam.pk]),
            "active": "exams",
        },
    )


@superuser_required
def question_delete(request, exam_pk, pk):
    question = get_object_or_404(Question, pk=pk, exam_id=exam_pk)
    if request.method == "POST":
        question.delete()
        messages.success(request, "Question deleted.")
        return redirect("panel:exam_questions", pk=exam_pk)
    return render(
        request,
        "panel/confirm_delete.html",
        {
            "object": question,
            "back_url": reverse("panel:exam_questions", args=[exam_pk]),
            "active": "exams",
        },
    )


@superuser_required
def exam_responses(request, pk):
    exam = get_object_or_404(Exam.objects.select_related("circular"), pk=pk)
    close_expired_attempts(exam)  # candidates who closed the browser before the time ran out
    attempts = (
        exam.attempts.select_related("candidate", "candidate__department")
        .filter(submitted_at__isnull=False)
        .prefetch_related("answers__question")
    )
    eligible = exam.eligible_candidates().count()
    return render(
        request,
        "panel/exam_responses.html",
        {
            "exam": exam,
            "attempts": attempts,
            "eligible": eligible,
            "pending_marking": sum(1 for a in attempts if a.needs_marking),
            "forced": sum(1 for a in attempts if a.was_forced),
            "active": "exams",
        },
    )


def save_written_marks(attempt, answers, data):
    """Store HR's marks for the written answers ("mark_<answer id>"); returns error messages."""
    errors = []
    for answer in answers:
        if answer.question.kind != Question.Kind.TEXT:
            continue
        raw = (data.get(f"mark_{answer.pk}") or "").strip()
        if not raw:
            answer.awarded = None
        else:
            value = parse_mark(raw, answer.question.marks)
            if value is None:
                errors.append(f"Q{answer.question.order}: mark must be between 0 and {answer.question.marks:g}.")
                continue
            answer.awarded = value
        answer.save(update_fields=["awarded"])
    attempt.recalculate()
    return errors


@superuser_required
def exam_response(request, pk):
    """One answer sheet: marks for the written answers, and copy the result to the Written mark."""
    attempt = get_object_or_404(
        ExamAttempt.objects.select_related("exam", "candidate", "candidate__department"), pk=pk
    )
    answers = list(attempt.answers.select_related("question", "choice").prefetch_related("question__choices"))

    if request.method == "POST":
        if "apply_mark" in request.POST:
            mark = attempt.apply_to_written_mark()
            if mark is None:
                messages.error(request, "Mark the written answers first.")
            else:
                messages.success(request, f"Written mark set to {fmt_mark(mark)} / {MARK_MAX:g} for {attempt.candidate.name}.")
            return redirect("panel:exam_response", pk=attempt.pk)

        errors = save_written_marks(attempt, answers, request.POST)
        for error in errors:
            messages.error(request, error)
        if not errors:
            messages.success(request, "Marks saved.")
        return redirect("panel:exam_response", pk=attempt.pk)

    return render(
        request,
        "panel/exam_response.html",
        {"attempt": attempt, "answers": answers, "active": "exams"},
    )
