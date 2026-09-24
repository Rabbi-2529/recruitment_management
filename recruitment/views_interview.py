"""Admin pages: Interview Call (upload -> map columns -> preview -> SMS), SMS log/settings,
interviewers, marking criteria and every interviewer's evaluations."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import sms
from .evaluation import (
    chart_data,
    combined_result,
    date_filter,
    expected_interviewers,
    latest_written,
    save_evaluation,
    sheet_rows,
    status_filter,
    summary,
)
from .forms import (
    ColumnMappingForm,
    EvaluationCriterionForm,
    EvaluationForm,
    InterviewCallForm,
    InterviewerForm,
    InterviewForm,
    InterviewSettingForm,
    SmsGatewayForm,
    SmsSendForm,
    SmsSenderForm,
)
from .interview_call_import import ImportError_, build_people, guess_mapping, read_rows
from .models import (
    Candidate,
    Department,
    EvaluationCriterion,
    InterviewCall,
    InterviewCallCandidate,
    InterviewerDepartment,
    InterviewerProfile,
    InterviewEvaluation,
    InterviewSetting,
    SmsGateway,
    SmsLog,
    SmsSenderId,
)
from .permissions import admin_required

User = get_user_model()
SmsStatus = InterviewCallCandidate.SmsStatus


def _page(request, queryset, per_page=25):
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


# ================================================================== interview call
@admin_required
def interview_call_list(request):
    calls = (
        InterviewCall.objects.select_related("department", "sender", "created_by")
        .annotate(
            people_count=Count("people", distinct=True),
            sent_count=Count("people", filter=Q(people__sms_status=SmsStatus.SENT), distinct=True),
            failed_count=Count("people", filter=Q(people__sms_status=SmsStatus.FAILED), distinct=True),
        )
    )
    department = request.GET.get("department", "")
    date = request.GET.get("date", "")
    if department.isdigit():
        calls = calls.filter(department_id=department)
    if date:
        calls = calls.filter(created_at__date=date)
    return render(request, "panel/interview_call_list.html", {
        "calls": calls, "departments": Department.objects.all(),
        "filters": {"department": department, "date": date},
        "has_gateway": SmsGateway.objects.filter(is_active=True).exists(),
        "active": "interview_call",
    })


@admin_required
def interview_call_create(request):
    """Step 1: upload the file with the interview details."""
    form = InterviewCallForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["uploaded_file"]
        try:
            headers, rows = read_rows(upload, upload.name)
        except ImportError_ as exc:
            form.add_error("uploaded_file", str(exc))
        else:
            if not rows:
                form.add_error("uploaded_file", "No candidate rows were found in the file.")
            else:
                upload.seek(0)
                call = form.save(commit=False)
                call.title = form.cleaned_data["title"]
                call.original_filename = upload.name[:255]
                call.headers = headers
                call.column_map = guess_mapping(headers)
                call.created_by = request.user
                call.save()
                messages.success(request, f"{len(rows)} rows read from {upload.name}. Check the columns below.")
                return redirect("panel:interview_call_mapping", pk=call.pk)
    return render(request, "panel/interview_call_form.html", {
        "form": form, "title": "New Interview Call", "placeholders": sms.PLACEHOLDERS,
        "has_gateway": SmsGateway.objects.filter(is_active=True).exists(), "active": "interview_call",
    })


def _read_call_file(call):
    with call.uploaded_file.open("rb") as handle:
        return read_rows(handle, call.original_filename or call.uploaded_file.name)


@admin_required
def interview_call_mapping(request, pk):
    """Step 2: tell the system which column is the name, phone, email ... then build the list."""
    call = get_object_or_404(InterviewCall.objects.select_related("department"), pk=pk)
    try:
        headers, rows = _read_call_file(call)
    except (ImportError_, FileNotFoundError) as exc:
        messages.error(request, f"The uploaded file cannot be read any more: {exc}")
        return redirect("panel:interview_call_list")

    form = ColumnMappingForm(request.POST or None, headers=headers, initial_map=call.column_map)
    if request.method == "POST" and form.is_valid():
        mapping = {field: column for field, column in form.cleaned_data.items() if column}
        with transaction.atomic():
            call.column_map = mapping
            call.stage = InterviewCall.Stage.READY
            call.save(update_fields=["column_map", "stage"])
            result = build_people(call, headers, rows, mapping)
        text = f"{result['created']} candidates added to this interview call."
        if result["duplicates"]:
            text += f" {result['duplicates']} duplicate phone number(s) were removed."
        if result["invalid"]:
            text += f" {result['invalid']} row(s) have no valid phone number - they will not get an SMS."
        messages.success(request, text)
        return redirect("panel:interview_call_detail", pk=call.pk)

    return render(request, "panel/interview_call_mapping.html", {
        "call": call, "form": form, "headers": headers,
        "sample": [[row.get(h, "") for h in headers] for row in rows[:5]],
        "row_count": len(rows), "active": "interview_call",
    })


@admin_required
def interview_call_edit(request, pk):
    call = get_object_or_404(InterviewCall, pk=pk)
    form = InterviewCallForm(request.POST or None, instance=call)
    if request.method == "POST" and form.is_valid():
        call = form.save(commit=False)
        call.title = form.cleaned_data["title"] or call.title
        call.save()
        messages.success(request, "Interview call updated.")
        return redirect("panel:interview_call_detail", pk=call.pk)
    return render(request, "panel/interview_call_form.html", {
        "form": form, "title": "Edit Interview Call", "call": call, "placeholders": sms.PLACEHOLDERS,
        "has_gateway": True, "active": "interview_call",
    })


@admin_required
def interview_call_delete(request, pk):
    call = get_object_or_404(InterviewCall, pk=pk)
    if request.method == "POST":
        call.uploaded_file.delete(save=False)
        call.delete()
        messages.success(request, "Interview call deleted. Registered candidates were not touched.")
        return redirect("panel:interview_call_list")
    return render(request, "panel/confirm_delete.html", {
        "object": call,
        "warning": f"This removes the uploaded list of {call.people.count()} people and its SMS log. "
                   "Registered candidates are not affected.",
        "back_url": reverse("panel:interview_call_detail", args=[call.pk]),
        "active": "interview_call",
    })


@admin_required
def interview_call_detail(request, pk):
    """Step 3: preview the list, choose people, check the message and send."""
    call = get_object_or_404(InterviewCall.objects.select_related("department", "gateway", "sender"), pk=pk)
    if call.stage == InterviewCall.Stage.MAPPING:
        return redirect("panel:interview_call_mapping", pk=call.pk)

    # every person on one page - DataTables does the search, sorting and paging in the browser
    people = call.people.select_related("matched_candidate")
    first = call.people.filter(is_valid=True).first()
    return render(request, "panel/interview_call_detail.html", {
        "call": call, "people": people,
        "counts": call.sms_counts(), "send_form": SmsSendForm(instance=call),
        "placeholders": sms.PLACEHOLDERS, "preview": sms.render_message(call.message_template, first) if first else "",
        "preview_person": first, "sms_statuses": SmsStatus.choices,
        "pending_logs": call.sms_logs.filter(status__in=[SmsLog.Status.PENDING, SmsLog.Status.SENDING]).count(),
        "active": "interview_call",
    })


@admin_required
@require_POST
def interview_call_preview(request, pk):
    """Render the SMS for one person with the message as it is being typed (nothing is sent)."""
    call = get_object_or_404(InterviewCall, pk=pk)
    person = call.people.filter(pk=request.POST.get("person") or 0).first() or call.people.filter(is_valid=True).first()
    if person is None:
        return JsonResponse({"ok": False, "error": "No valid candidate in this list."}, status=400)
    text = sms.render_message(request.POST.get("message_template", call.message_template), person)
    return JsonResponse({"ok": True, "message": text, "length": len(text), "parts": _sms_parts(text),
                         "person": person.candidate_name or person.phone})


def _sms_parts(text):
    unicode = any(ord(ch) > 127 for ch in text)
    size, multi = (70, 67) if unicode else (160, 153)
    return 1 if len(text) <= size else -(-len(text) // multi)


def _counts(call):
    counts = call.sms_counts()
    counts["pending"] = call.sms_logs.filter(status__in=[SmsLog.Status.PENDING, SmsLog.Status.SENDING]).count()
    return counts


@admin_required
@require_POST
def interview_call_queue(request, pk):
    """Save the SMS settings, then queue one SMS per chosen person (never someone already sent)."""
    call = get_object_or_404(InterviewCall, pk=pk)
    form = SmsSendForm(request.POST, instance=call)
    if not form.is_valid():
        errors = {field: [str(e) for e in errs] for field, errs in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    call = form.save()

    people = call.people.filter(is_valid=True).exclude(sms_status__in=[SmsStatus.SENT, SmsStatus.QUEUED])
    retry = request.POST.get("retry") == "1"
    if retry:
        people = people.filter(sms_status=SmsStatus.FAILED)  # "Retry failed": only the failed ones
    elif request.POST.get("scope") == "all":
        people = people.filter(sms_status=SmsStatus.NOT_SENT)  # "everyone not sent yet" - failed ones use Retry
    else:
        ids = [int(i) for i in request.POST.getlist("ids") if str(i).isdigit()]
        if not ids:
            return JsonResponse({"ok": False, "errors": {"__all__": ["Select at least one candidate."]}}, status=400)
        people = people.filter(pk__in=ids)  # chosen by hand: not sent yet or failed

    queued = 0
    with transaction.atomic():
        for person in people.select_for_update():
            if person.sms_logs.filter(status=SmsLog.Status.SUCCESS).exists():
                continue  # already received it once
            SmsLog.objects.create(
                interview_call=call, recipient=person, phone=person.phone, sender_id=call.sender.sender_id,
                message=sms.render_message(call.message_template, person), is_retry=retry,
                created_by=request.user,
            )
            person.sms_status = SmsStatus.QUEUED
            person.save(update_fields=["sms_status"])
            queued += 1
    return JsonResponse({"ok": True, "queued": queued, "counts": _counts(call)})


@admin_required
@require_POST
def interview_call_send_batch(request, pk):
    """Send the next few queued SMS - one API call per person. The page keeps calling until none are left."""
    call = get_object_or_404(InterviewCall.objects.select_related("gateway"), pk=pk)
    if call.gateway is None or not call.gateway.is_active:
        return JsonResponse({"ok": False, "error": "Choose an active SMS API first."}, status=400)
    sent = failed = 0
    for log in call.sms_logs.filter(status=SmsLog.Status.PENDING).select_related("recipient").order_by("id")[:sms.BATCH_SIZE]:
        # claim the message first, so two open tabs can never send it twice
        if not SmsLog.objects.filter(pk=log.pk, status=SmsLog.Status.PENDING).update(status=SmsLog.Status.SENDING):
            continue
        if SmsLog.objects.filter(recipient=log.recipient, status=SmsLog.Status.SUCCESS).exists():
            SmsLog.objects.filter(pk=log.pk).update(status=SmsLog.Status.SKIPPED, sent_at=timezone.now())
            InterviewCallCandidate.objects.filter(pk=log.recipient_id).update(sms_status=SmsStatus.SENT)
            continue
        if sms.send_log(log, call.gateway):
            sent += 1
        else:
            failed += 1
    counts = _counts(call)
    return JsonResponse({"ok": True, "sent": sent, "failed": failed, "counts": counts, "done": counts["pending"] == 0})


# ================================================================== SMS log & settings
def _filtered_logs(params):
    logs = SmsLog.objects.select_related("interview_call", "recipient")
    status = params.get("status", "")
    call_id = params.get("call", "")
    q = (params.get("q") or params.get("search[value]") or "").strip()
    if status == "pending":
        logs = logs.filter(status__in=[SmsLog.Status.PENDING, SmsLog.Status.SENDING])
    elif status in SmsLog.Status.values:
        logs = logs.filter(status=status)
    if call_id.isdigit():
        logs = logs.filter(interview_call_id=call_id)
    if q:
        logs = logs.filter(
            Q(phone__icontains=q) | Q(recipient__candidate_name__icontains=q) | Q(sender_id__icontains=q)
            | Q(message__icontains=q) | Q(interview_call__title__icontains=q)
        )
    return logs


@admin_required
def sms_log(request):
    """The page; the rows come from sms_log_data (server-side DataTables - the log keeps growing)."""
    stats = SmsLog.objects.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(status=SmsLog.Status.SUCCESS)),
        failed=Count("id", filter=Q(status=SmsLog.Status.FAILED)),
        pending=Count("id", filter=Q(status__in=[SmsLog.Status.PENDING, SmsLog.Status.SENDING])),
    )
    return render(request, "panel/sms_log.html", {
        "stats": stats, "statuses": SmsLog.Status.choices, "calls": InterviewCall.objects.all(),
        "filters": {"status": request.GET.get("status", ""), "call": request.GET.get("call", "")},
        "active": "sms_log",
    })


# DataTables column index -> field used for sorting
SMS_LOG_ORDER = {0: "recipient__candidate_name", 1: "phone", 2: "sender_id", 4: "status", 6: "sent_at"}


@admin_required
def sms_log_data(request):
    params = request.GET
    logs = _filtered_logs(params)
    column = params.get("order[0][column]", "6")
    field = SMS_LOG_ORDER.get(int(column) if column.isdigit() else 6, "sent_at")
    direction = "-" if params.get("order[0][dir]", "desc") == "desc" else ""
    logs = logs.order_by(f"{direction}{field}", "-id")
    try:
        start = max(int(params.get("start", 0)), 0)
        length = min(max(int(params.get("length", 25)), 1), 500)
    except ValueError:
        start, length = 0, 25
    rows = [
        {
            "candidate": log.recipient.candidate_name,
            "call": log.interview_call.title,
            "call_url": reverse("panel:interview_call_detail", args=[log.interview_call_id]),
            "phone": log.phone,
            "sender_id": log.sender_id,
            "message": log.message,
            "status": log.status,
            "status_label": log.get_status_display(),
            "is_retry": log.is_retry,
            "error": log.error,
            "api_response": log.api_response[:300],
            "http_status": log.http_status or "",
            "sent_at": timezone.localtime(log.sent_at).strftime("%d %b %Y, %I:%M %p") if log.sent_at else "",
        }
        for log in logs[start:start + length]
    ]
    return JsonResponse({
        "draw": int(params.get("draw", 0) or 0),
        "recordsTotal": SmsLog.objects.count(),
        "recordsFiltered": logs.count(),
        "data": rows,
    })


@admin_required
def sms_settings(request):
    gateway_form = SmsGatewayForm(prefix="gw")
    sender_form = SmsSenderForm(prefix="sd")
    if request.method == "POST":
        if "add_gateway" in request.POST:
            gateway_form = SmsGatewayForm(request.POST, prefix="gw")
            if gateway_form.is_valid():
                gateway_form.save()
                messages.success(request, "SMS API saved.")
                return redirect("panel:sms_settings")
        elif "add_sender" in request.POST:
            sender_form = SmsSenderForm(request.POST, prefix="sd")
            if sender_form.is_valid():
                sender_form.save()
                messages.success(request, "Sender ID saved.")
                return redirect("panel:sms_settings")
    return render(request, "panel/sms_settings.html", {
        "gateways": SmsGateway.objects.prefetch_related("sender_ids"),
        "gateway_form": gateway_form, "sender_form": sender_form, "active": "sms_settings",
    })


@admin_required
def sms_gateway_edit(request, pk):
    gateway = get_object_or_404(SmsGateway, pk=pk)
    form = SmsGatewayForm(request.POST or None, instance=gateway)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "SMS API updated.")
        return redirect("panel:sms_settings")
    return render(request, "panel/form.html", {
        "form": form, "title": f"Edit SMS API - {gateway.name}", "back_url": reverse("panel:sms_settings"),
        "active": "sms_settings",
    })


@admin_required
def sms_sender_edit(request, pk):
    sender = get_object_or_404(SmsSenderId, pk=pk)
    form = SmsSenderForm(request.POST or None, instance=sender)
    if request.method == "POST":
        if "delete" in request.POST:
            sender.delete()
            messages.success(request, "Sender ID deleted.")
            return redirect("panel:sms_settings")
        if form.is_valid():
            form.save()
            messages.success(request, "Sender ID updated.")
            return redirect("panel:sms_settings")
    return render(request, "panel/form.html", {
        "form": form, "title": f"Edit Sender ID - {sender.sender_id}", "back_url": reverse("panel:sms_settings"),
        "active": "sms_settings",
    })


# ================================================================== interviewers
@admin_required
def interviewer_list(request):
    profiles = InterviewerProfile.objects.select_related("user").prefetch_related("departments").annotate(
        evaluation_count=Count("user__evaluations", filter=Q(user__evaluations__is_submitted=True), distinct=True)
    )
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "")
    if q:
        profiles = profiles.filter(
            Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(user__email__icontains=q)
        )
    if department.isdigit():
        profiles = profiles.filter(departments__id=department).distinct()
    return render(request, "panel/interviewer_list.html", {
        "profiles": profiles, "departments": Department.objects.all(),
        "filters": {"q": q, "department": department}, "active": "interviewers",
    })


@admin_required
def interviewer_form(request, pk=None):
    profile = get_object_or_404(InterviewerProfile.objects.select_related("user"), pk=pk) if pk else None
    user = profile.user if profile else None
    initial = {}
    if profile:
        initial = {
            "first_name": user.first_name, "last_name": user.last_name, "email": user.email,
            "phone": profile.phone, "designation": profile.designation, "is_active": user.is_active,
            "departments": list(profile.departments.all()),
            "can_edit_written_marks": profile.can_edit_written_marks, "can_edit_submitted": profile.can_edit_submitted,
        }
    form = InterviewerForm(request.POST or None, user=user, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            if user is None:
                user = User(username=data["email"])
            user.username = user.email = data["email"]
            user.first_name, user.last_name = data["first_name"], data.get("last_name", "")
            user.is_active = data.get("is_active", False)
            user.is_staff = user.is_superuser = False  # interviewers never get admin rights
            if data.get("password"):
                user.set_password(data["password"])
            user.save()
            profile = profile or InterviewerProfile(user=user)
            profile.phone = data.get("phone", "")
            profile.designation = data.get("designation", "")
            profile.can_edit_written_marks = data.get("can_edit_written_marks", False)
            profile.can_edit_submitted = data.get("can_edit_submitted", False)
            profile.save()
            chosen = set(data["departments"])
            InterviewerDepartment.objects.filter(interviewer=profile).exclude(department__in=chosen).delete()
            for department in chosen:
                InterviewerDepartment.objects.get_or_create(interviewer=profile, department=department)
        messages.success(request, f"Interviewer {profile.display_name} saved.")
        return redirect("panel:interviewer_list")
    return render(request, "panel/interviewer_form.html", {
        "form": form, "profile": profile, "title": "Edit Interviewer" if profile else "New Interviewer",
        "active": "interviewers",
    })


# ================================================================== marking criteria
@admin_required
def criteria(request):
    settings_obj = InterviewSetting.load()
    form = EvaluationCriterionForm(prefix="c")
    settings_form = InterviewSettingForm(instance=settings_obj, prefix="s")
    if request.method == "POST":
        if "add_criterion" in request.POST:
            form = EvaluationCriterionForm(request.POST, prefix="c")
            if form.is_valid():
                form.save()
                messages.success(request, "Criterion added. New marking sheets use it; submitted sheets keep theirs.")
                return redirect("panel:criteria")
        elif "save_settings" in request.POST:
            settings_form = InterviewSettingForm(request.POST, instance=settings_obj, prefix="s")
            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, "Final result settings saved.")
                return redirect("panel:criteria")
    items = EvaluationCriterion.objects.all()
    return render(request, "panel/criteria.html", {
        "criteria": items, "form": form, "settings_form": settings_form,
        "total": sum((c.max_marks for c in items if c.is_active), Decimal("0")), "active": "criteria",
    })


@admin_required
def criterion_edit(request, pk):
    criterion = get_object_or_404(EvaluationCriterion, pk=pk)
    form = EvaluationCriterionForm(request.POST or None, instance=criterion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Criterion updated. Sheets already started keep the old wording and marks.")
        return redirect("panel:criteria")
    return render(request, "panel/form.html", {
        "form": form, "title": f"Edit criterion - {criterion.name}", "back_url": reverse("panel:criteria"),
        "active": "criteria",
    })


# ================================================================== evaluations list (Admin, server-side DataTable)
@admin_required
def evaluation_list(request):
    """The page; the rows come from evaluation_data. The status shown is always the candidate's one main status."""
    return render(request, "panel/evaluation_list.html", {
        "departments": Department.objects.all(),
        "statuses": Candidate.Status.choices,
        "interviewers": InterviewerProfile.objects.select_related("user"),
        "setting": InterviewSetting.load(),
        "filters": {k: request.GET.get(k, "") for k in ("department", "status", "interviewer", "date")},
        "active": "evaluations",
    })


# DataTables column name -> field used for sorting
EVALUATION_ORDER = {
    "name": "name", "department": "department__name", "interview_date": "circular__interview_date",
    "evaluations": "evaluation_count", "interview": "interview_avg", "status": "status", "updated": "updated_at",
}


@admin_required
def evaluation_data(request):
    params = request.GET
    candidates = Candidate.objects.select_related("department", "circular").annotate(
        evaluation_count=Count("evaluations", filter=Q(evaluations__is_submitted=True), distinct=True),
        draft_count=Count("evaluations", filter=Q(evaluations__is_submitted=False), distinct=True),
        interview_avg=Avg(
            F("evaluations__total_mark") * 100 / F("evaluations__out_of"),
            filter=Q(evaluations__is_submitted=True, evaluations__out_of__gt=0),
        ),
    )
    total = Candidate.objects.count()
    department = params.get("department", "")
    if department.isdigit():
        candidates = candidates.filter(department_id=department)
    candidates = status_filter(candidates, params.get("status", ""))
    candidates = date_filter(candidates, params.get("date", ""))
    interviewer = params.get("interviewer", "")
    if interviewer.isdigit():
        candidates = candidates.filter(evaluations__interviewer_id=interviewer).distinct()
    evaluated = params.get("evaluated", "")
    if evaluated == "yes":
        candidates = candidates.filter(evaluation_count__gt=0)
    elif evaluated == "no":
        candidates = candidates.filter(evaluation_count=0)
    q = (params.get("search[value]") or "").strip()
    if q:
        candidates = candidates.filter(
            Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q)
            | Q(department__name__icontains=q) | Q(circular__reference_no__icontains=q)
            | Q(evaluations__interviewer__first_name__icontains=q) | Q(evaluations__interviewer__email__icontains=q)
        ).distinct()
    filtered = candidates.count()

    column = params.get("order[0][column]", "")
    name = params.get(f"columns[{column}][data]", "") if column.isdigit() else ""
    field = EVALUATION_ORDER.get(name, "updated_at")
    direction = "-" if params.get("order[0][dir]", "desc") == "desc" else ""
    candidates = candidates.order_by(F(field).desc(nulls_last=True) if direction else F(field).asc(nulls_last=True), "-pk")

    try:
        start = max(int(params.get("start", 0)), 0)
        length = min(max(int(params.get("length", 25)), 1), 500)
    except ValueError:
        start, length = 0, 25
    page = list(candidates[start:start + length])

    evaluators = {}
    for e in InterviewEvaluation.objects.filter(candidate__in=page).select_related("interviewer").order_by("interviewer__first_name"):
        evaluators.setdefault(e.candidate_id, []).append({
            "name": e.interviewer_name, "admin": e.interviewer.is_superuser, "submitted": e.is_submitted,
            "total": _fmt_number(e.total_mark), "out_of": _fmt_number(e.out_of),
        })
    setting = InterviewSetting.load()
    rows = []
    for c in page:
        written, written_percent = latest_written(c)
        interview_percent = Decimal(c.interview_avg).quantize(Decimal("0.01")) if c.interview_avg is not None else None
        combined = setting.combined(written_percent, interview_percent) if setting.show_combined else None
        rows.append({
            "id": c.pk,
            "name": c.name,
            "registration_no": c.registration_no,
            "phone": c.phone,
            "department": c.department.name,
            "position": c.circular.title,
            "interview_date": c.circular.interview_date.strftime("%d %b %Y") if c.circular.interview_date else "",
            "interviewers": evaluators.get(c.pk, []),
            "evaluations": c.evaluation_count,
            "drafts": c.draft_count,
            "written": (f"{_fmt_number(written.total_score)} / {_fmt_number(written.out_of)}" if written else ""),
            "written_label": written.result_label if written else "",
            "interview": _fmt_number(interview_percent),
            "combined": _fmt_number(combined),
            "status": c.status,
            "status_label": c.get_status_display(),
            "updated": timezone.localtime(c.updated_at).strftime("%d %b %Y"),
            "url": reverse("panel:evaluation_detail", args=[c.pk]),
        })
    return JsonResponse({"draw": int(params.get("draw", 0) or 0), "recordsTotal": total,
                         "recordsFiltered": filtered, "data": rows})


def _fmt_number(value):
    if value is None:
        return ""
    value = Decimal(value)
    return f"{value.normalize():f}" if value == value.to_integral_value() else f"{value.quantize(Decimal('0.01')):f}"


# ================================================================== one candidate: Interview Sheet + all marks
@admin_required
def candidate_sheet(request, pk):
    """
    The one page for a candidate (the old "Interview Sheet" and "View All Marks" combined):
    candidate details, the main status, HR's interview information, every interviewer's marks,
    the Admin's own marks, written result and graphs. The status is Candidate.status - nothing else.
    """
    candidate = get_object_or_404(Candidate.objects.select_related("department", "circular"), pk=pk)
    admin_evaluation = InterviewEvaluation.objects.filter(candidate=candidate, interviewer=request.user).first()
    action = ""
    if request.method == "POST":
        if "ev-save" in request.POST or "ev-submit" in request.POST:
            action = "evaluation"
        elif "unlock" in request.POST:
            action = "unlock"
        else:
            action = "sheet"  # the interview information form (main status, written mark, review, CV)

    sheet_form = InterviewForm(
        request.POST if action == "sheet" else None, request.FILES if action == "sheet" else None, instance=candidate,
    )
    eval_form = EvaluationForm(
        request.POST if action == "evaluation" else None, instance=admin_evaluation,
        rows=sheet_rows(admin_evaluation, include_new=True), prefix="ev",
    )
    sheet_url = reverse("panel:evaluation_detail", args=[candidate.pk])

    if action == "sheet" and sheet_form.is_valid():
        sheet_form.save()
        messages.success(request, f"Interview information saved for {candidate.name}.")
        return redirect(sheet_url)
    if action == "evaluation":
        submit = "ev-submit" in request.POST
        if eval_form.is_valid() and (not submit or eval_form.require_all_marks()):
            save_evaluation(eval_form, candidate, request.user, admin_evaluation, submit)
            messages.success(request, "Your marks were submitted." if submit else "Your marks were saved as a draft.")
            return redirect(f"{sheet_url}#my-evaluation")
        messages.error(request, "Please check your marks below - whole numbers 0 to 5 only.")
    if action == "unlock":
        evaluation = get_object_or_404(InterviewEvaluation, pk=request.POST.get("unlock"), candidate=candidate)
        evaluation.is_submitted = False
        evaluation.save(update_fields=["is_submitted", "updated_at"])
        messages.success(request, f"{evaluation.interviewer_name} can edit their evaluation again.")
        return redirect(sheet_url)
    if action == "sheet":
        messages.error(request, "Please check the interview information below.")

    data = summary(candidate)
    written, written_percent = latest_written(candidate)
    combined = combined_result(written_percent, data["average_percent"])
    criteria_names = []
    for evaluation in data["evaluations"]:
        for score in evaluation.scores.all():
            if score.criterion_name not in criteria_names:
                criteria_names.append(score.criterion_name)
    grid = []
    for evaluation in data["evaluations"]:
        by_name = {sc.criterion_name: sc for sc in evaluation.scores.all()}
        grid.append((evaluation, [by_name.get(name) for name in criteria_names]))
    grid_averages = []  # per question, over the submitted sheets
    for i, _name in enumerate(criteria_names):
        values = [scores[i].mark for evaluation, scores in grid
                  if evaluation.is_submitted and scores[i] is not None and scores[i].mark is not None]
        grid_averages.append(round(sum(values) / len(values), 2) if values else None)
    calls = list(candidate.interview_calls.select_related("interview_call"))
    interview_date = candidate.circular.interview_date or next(
        (c.interview_call.interview_date for c in calls if c.interview_call.interview_date), None
    )
    return render(request, "panel/candidate_sheet.html", {
        "candidate": candidate, "sheet_form": sheet_form, "eval_form": eval_form,
        "admin_evaluation": admin_evaluation, "criteria_names": criteria_names, "grid": grid,
        "grid_averages": grid_averages,
        "written_attempts": candidate.written_attempts, "written": written, "written_percent": written_percent,
        "combined": combined, "setting": InterviewSetting.load(), "expected": expected_interviewers(candidate),
        "calls": calls, "interview_date": interview_date,
        "mark_fields": [(sheet_form[field], label) for field, label in Candidate.MARK_FIELDS],
        "chart": chart_data(candidate, data, written_percent, combined),
        **data, "active": "evaluations",
    })
