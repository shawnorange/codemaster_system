from django.db import migrations, models
import django.db.models.deletion


def seed_teacher_profiles(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")
    Teacher = apps.get_model("entry", "Teacher")
    TeacherStudentAssignment = apps.get_model("entry", "TeacherStudentAssignment")

    teacher_users = PortalUser.objects.filter(role="teacher").order_by("id")
    for user in teacher_users:
        teacher, _ = Teacher.objects.get_or_create(
            user_id=user.id,
            defaults={
                "display_name": user.full_name,
                "phone": user.phone,
            },
        )
        update_fields = []
        if teacher.display_name != user.full_name:
            teacher.display_name = user.full_name
            update_fields.append("display_name")
        if teacher.phone != user.phone:
            teacher.phone = user.phone
            update_fields.append("phone")
        if update_fields:
            teacher.save(update_fields=update_fields)

        course_ids = (
            TeacherStudentAssignment.objects.filter(teacher_id=user.id, is_active=True)
            .values_list("course_id", flat=True)
            .distinct()
        )
        if course_ids:
            teacher.courses.add(*course_ids)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0050_alter_examsession_session_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="Teacher",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("display_name", models.CharField(max_length=64, verbose_name="教师姓名")),
                ("phone", models.CharField(blank=True, max_length=32, verbose_name="手机号")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teacher_profile",
                        to="entry.portaluser",
                    ),
                ),
                (
                    "courses",
                    models.ManyToManyField(
                        blank=True,
                        related_name="teacher_profiles",
                        to="entry.course",
                        verbose_name="关联课程",
                    ),
                ),
            ],
            options={
                "verbose_name": "教师",
                "verbose_name_plural": "教师",
                "ordering": ["id"],
            },
        ),
        migrations.RunPython(seed_teacher_profiles, noop_reverse),
    ]
