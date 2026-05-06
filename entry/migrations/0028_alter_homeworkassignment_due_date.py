from datetime import datetime, time

from django.db import migrations, models
from django.utils import timezone


def move_due_date_to_local_day_end(apps, schema_editor) -> None:
    HomeworkAssignment = apps.get_model("entry", "HomeworkAssignment")
    current_timezone = timezone.get_current_timezone()
    pending_updates = []

    for assignment in HomeworkAssignment.objects.exclude(due_date__isnull=True).only("id", "due_date").iterator():
        due_value = assignment.due_date
        if due_value is None:
            continue
        if timezone.is_aware(due_value):
            local_due_date = timezone.localtime(due_value, current_timezone).date()
        else:
            local_due_date = due_value.date()
        assignment.due_date = timezone.make_aware(
            datetime.combine(local_due_date, time(23, 59, 59)),
            current_timezone,
        )
        pending_updates.append(assignment)
        if len(pending_updates) >= 200:
            HomeworkAssignment.objects.bulk_update(pending_updates, ["due_date"])
            pending_updates.clear()

    if pending_updates:
        HomeworkAssignment.objects.bulk_update(pending_updates, ["due_date"])


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0027_remove_homeworksummary_areas_for_growth_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homeworkassignment",
            name="due_date",
            field=models.DateTimeField(verbose_name="截止日期"),
        ),
        migrations.RunPython(move_due_date_to_local_day_end, migrations.RunPython.noop),
    ]
