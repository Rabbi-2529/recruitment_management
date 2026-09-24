from decimal import Decimal

from django.db import migrations

DEFAULT_CRITERIA = [
    ("Technical Knowledge", "25"),
    ("Communication", "20"),
    ("Problem Solving", "20"),
    ("Experience", "15"),
    ("Behavior", "10"),
    ("Overall Impression", "10"),
]


def add_defaults(apps, schema_editor):
    Criterion = apps.get_model("recruitment", "EvaluationCriterion")
    if Criterion.objects.exists():
        return
    for order, (name, marks) in enumerate(DEFAULT_CRITERIA, start=1):
        Criterion.objects.create(name=name, max_marks=Decimal(marks), order=order)
    apps.get_model("recruitment", "InterviewSetting").objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0011_interview_call_interviewers"),
    ]

    operations = [
        migrations.RunPython(add_defaults, migrations.RunPython.noop),
    ]
