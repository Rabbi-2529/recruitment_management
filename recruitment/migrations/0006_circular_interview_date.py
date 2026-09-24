from django.db import migrations, models
from django.utils import timezone


def copy_date(apps, schema_editor):
    """Keep the date part of any interview date & time already saved."""
    Circular = apps.get_model("recruitment", "Circular")
    for circular in Circular.objects.exclude(interview_at=None):
        circular.interview_date = timezone.localtime(circular.interview_at).date()
        circular.save(update_fields=["interview_date"])


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0005_circular_interview_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="circular",
            name="interview_date",
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name="Interview date",
                help_text="Shown to candidates after registration and on the result page. Time slots are shared by HR.",
            ),
        ),
        migrations.RunPython(copy_date, migrations.RunPython.noop),
        migrations.RemoveField(model_name="circular", name="interview_at"),
    ]
