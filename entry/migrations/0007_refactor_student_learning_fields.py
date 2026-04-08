from django.contrib.auth.hashers import identify_hasher, make_password
from django.db import migrations, models


def _split_learning_path(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in (value or "").split(">") if part.strip()]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def forwards(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")
    Student = apps.get_model("entry", "Student")

    for portal_user in PortalUser.objects.all():
        password = portal_user.password or ""
        if not password:
            continue
        try:
            identify_hasher(password)
        except Exception:
            portal_user.password = make_password(password)
            portal_user.save(update_fields=["password"])

    for student in Student.objects.all():
        primary_course_name, primary_track_name, primary_level_name = _split_learning_path(
            getattr(student, "current_program", "")
        )
        student.primary_course_name = primary_course_name
        student.primary_track_name = primary_track_name
        student.primary_level_name = primary_level_name
        student.save(
            update_fields=[
                "primary_course_name",
                "primary_track_name",
                "primary_level_name",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0006_seed_portal_user_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="primary_course_name",
            field=models.CharField(blank=True, max_length=64, verbose_name="主课程方向"),
        ),
        migrations.AddField(
            model_name="student",
            name="primary_level_name",
            field=models.CharField(blank=True, max_length=64, verbose_name="主当前级别"),
        ),
        migrations.AddField(
            model_name="student",
            name="primary_track_name",
            field=models.CharField(blank=True, max_length=64, verbose_name="主学习体系"),
        ),
        migrations.AlterField(
            model_name="portaluser",
            name="password",
            field=models.CharField(max_length=128, verbose_name="密码哈希"),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="student",
            name="current_program",
        ),
        migrations.RemoveField(
            model_name="student",
            name="phase_label",
        ),
    ]
