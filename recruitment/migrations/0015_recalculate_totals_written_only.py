from decimal import Decimal

from django.db import migrations

MARK_MAX = Decimal("5")


def recalculate(apps, schema_editor):
    """Dress Up / Body Language / Viva moved to Marking Criteria - the total is now the Written mark only.
    The old values stay in their columns; only the calculated total changes."""
    Candidate = apps.get_model("recruitment", "Candidate")
    for c in Candidate.objects.exclude(total_marks=None).only("pk", "written_mark"):
        if c.written_mark is None:
            total, out_of, percent = None, 0, None
        else:
            total, out_of = c.written_mark, int(MARK_MAX)
            percent = (total * 100 / out_of).quantize(Decimal("0.01"))
        Candidate.objects.filter(pk=c.pk).update(total_marks=total, marks_out_of=out_of, score_percent=percent)


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0014_interview_call_manual_sms"),
    ]

    operations = [
        migrations.RunPython(recalculate, migrations.RunPython.noop),
    ]
