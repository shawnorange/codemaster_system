from django.db import migrations, models


def backfill_teacher_subject(apps, schema_editor):
    Teacher = apps.get_model("entry", "Teacher")
    for teacher in Teacher.objects.all().prefetch_related("courses"):
        if teacher.subject:
            continue
        course_titles = [course.title for course in teacher.courses.all()]
        if course_titles:
            teacher.subject = "、".join(course_titles)
            teacher.save(update_fields=["subject"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0051_teacher"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="subject",
            field=models.CharField(blank=True, max_length=64, verbose_name="负责学科"),
        ),
        migrations.RunPython(backfill_teacher_subject, noop_reverse),
    ]
