import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


CPP_LEVEL_CODE_MAP = {
    "C1": "C1",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
    "GESP1": "C1",
    "GESP2": "C2",
    "GESP3": "C3",
    "GESP4": "C4",
}

UAV_LEVEL_CODE_MAP = {
    "S1": "S1",
    "S2": "S2",
    "S3": "S3",
}

COURSE_NAME_SLUG_ALIASES = {
    "c++": "cpp",
    "cpp": "cpp",
    "无人机": "uav",
    "uav": "uav",
    "drone": "uav",
}


def normalize_value(value):
    return (value or "").strip()


def resolve_course_slug(raw_course_name="", raw_level_code=""):
    normalized_course_name = normalize_value(raw_course_name).lower()
    if normalized_course_name in COURSE_NAME_SLUG_ALIASES:
        return COURSE_NAME_SLUG_ALIASES[normalized_course_name]

    normalized_level_code = normalize_value(raw_level_code).upper()
    if normalized_level_code in CPP_LEVEL_CODE_MAP:
        return "cpp"
    if normalized_level_code in UAV_LEVEL_CODE_MAP:
        return "uav"
    return None


def normalize_assignment_level(course_slug, raw_level_code):
    normalized_level_code = normalize_value(raw_level_code).upper()
    if not normalized_level_code:
        return None
    if course_slug == "cpp":
        return CPP_LEVEL_CODE_MAP.get(normalized_level_code)
    if course_slug == "uav":
        return UAV_LEVEL_CODE_MAP.get(normalized_level_code)
    return normalized_level_code


def seed_courses_and_backfill_assignments(apps, schema_editor):
    Course = apps.get_model("entry", "Course")
    Student = apps.get_model("entry", "Student")
    TeacherStudentAssignment = apps.get_model("entry", "TeacherStudentAssignment")

    course_specs = [
        ("cpp", "C++", "算法与竞赛方向"),
        ("uav", "无人机", "设备实践与控制逻辑方向"),
    ]
    course_map = {}
    for slug, title, summary in course_specs:
        course, _ = Course.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "summary": summary,
            },
        )
        course_map[slug] = course

    now = timezone.now()
    for student in Student.objects.exclude(teacher_user__isnull=True):
        course_slug = resolve_course_slug(
            raw_course_name=getattr(student, "primary_course_name", ""),
            raw_level_code=getattr(student, "primary_level_name", ""),
        )
        if not course_slug:
            continue

        level_code = normalize_assignment_level(course_slug, getattr(student, "primary_level_name", ""))
        if not level_code:
            continue

        TeacherStudentAssignment.objects.update_or_create(
            teacher_id=student.teacher_user_id,
            student_id=student.id,
            course_id=course_map[course_slug].id,
            level_code=level_code,
            defaults={
                "is_active": True,
                "assigned_at": now,
                "updated_at": now,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0012_fix_ascii_code_blocks"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherStudentAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("level_code", models.CharField(max_length=16, verbose_name="级别编码")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否生效")),
                ("assigned_at", models.DateTimeField(auto_now_add=True, verbose_name="分配时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teacher_student_assignments",
                        to="entry.course",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teacher_assignments",
                        to="entry.student",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_assignments",
                        to="entry.portaluser",
                    ),
                ),
            ],
            options={
                "verbose_name": "教师学生负责关系",
                "verbose_name_plural": "教师学生负责关系",
                "ordering": ["teacher_id", "student_id", "course_id", "level_code", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("teacher", "student", "course", "level_code"),
                        name="teacher_student_course_level_unique",
                    ),
                ],
                "indexes": [
                    models.Index(fields=["teacher", "is_active"], name="tsa_teacher_active_idx"),
                    models.Index(fields=["student", "is_active"], name="tsa_student_active_idx"),
                    models.Index(fields=["course", "level_code", "is_active"], name="tsa_course_level_active_idx"),
                ],
            },
        ),
        migrations.RunPython(seed_courses_and_backfill_assignments, migrations.RunPython.noop),
    ]
