"""
Dress Up / Body Language / Viva become Marking Criteria lines (5 marks each, as before), and every
candidate's old marks are moved into an evaluation so nothing entered earlier is lost.

The old marks were given by HR, not by an interviewer, so they go into one submitted evaluation per
candidate under the admin account (root@iglweb.com, or the first superuser), marked "Imported from the
old interview sheet". Interviewers' own sheets are never touched. Running it twice changes nothing.
"""
from decimal import Decimal

from django.db import migrations
from django.utils import timezone

OLD_MARKS = [
    ("dress_up_mark", "Dress Up"),
    ("body_language_mark", "Body Language"),
    ("viva_mark", "Viva"),
]
OLD_MAX = Decimal("5")
IMPORT_NOTE = "Imported from the old interview sheet (Dress Up / Body Language / Viva)."


def move_old_marks(apps, schema_editor):
    Criterion = apps.get_model("recruitment", "EvaluationCriterion")
    Candidate = apps.get_model("recruitment", "Candidate")
    Evaluation = apps.get_model("recruitment", "InterviewEvaluation")
    Score = apps.get_model("recruitment", "EvaluationScore")
    User = apps.get_model("auth", "User")

    # 1. the three criteria (reuse any the Admin already added with the same name)
    next_order = (Criterion.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 1
    criteria = {}
    for field, name in OLD_MARKS:
        criterion = Criterion.objects.filter(name__iexact=name).first()
        if criterion is None:
            criterion = Criterion.objects.create(
                name=name, max_marks=OLD_MAX, order=next_order, is_active=True,
                description="Moved from the old interview sheet",
            )
            next_order += 1
        criteria[field] = criterion

    # 2. the old marks -> one evaluation per candidate, under the admin account
    admin = (User.objects.filter(username__iexact="root@iglweb.com", is_superuser=True).first()
             or User.objects.filter(is_superuser=True).order_by("pk").first())
    if admin is None:
        return
    now = timezone.now()
    for candidate in Candidate.objects.all().only("pk", "department_id", *[f for f, _ in OLD_MARKS]):
        marks = {field: getattr(candidate, field) for field, _ in OLD_MARKS if getattr(candidate, field) is not None}
        if not marks:
            continue
        evaluation, created = Evaluation.objects.get_or_create(
            candidate_id=candidate.pk, interviewer=admin,
            defaults={
                "department_id": candidate.department_id, "comments": IMPORT_NOTE,
                "is_submitted": True, "submitted_at": now,
            },
        )
        if not created and evaluation.comments != IMPORT_NOTE:
            continue  # the admin already has their own sheet for this candidate - leave it alone
        for order, (field, name) in enumerate(OLD_MARKS, start=1):
            if field not in marks:
                continue
            criterion = criteria[field]
            Score.objects.update_or_create(
                evaluation=evaluation, criterion=criterion,
                defaults={"criterion_name": criterion.name, "max_marks": criterion.max_marks,
                          "mark": min(marks[field], criterion.max_marks), "order": order},
            )
        scores = list(Score.objects.filter(evaluation=evaluation))
        evaluation.total_mark = sum((s.mark for s in scores if s.mark is not None), Decimal("0"))
        evaluation.out_of = sum((s.max_marks for s in scores), Decimal("0"))
        evaluation.save(update_fields=["total_mark", "out_of"])


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0015_recalculate_totals_written_only"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(move_old_marks, migrations.RunPython.noop),
    ]
