from django.db import migrations


def seed_gesp2_ascii_topic(apps, schema_editor):
    Course = apps.get_model("entry", "Course")
    CourseContent = apps.get_model("entry", "CourseContent")
    Student = apps.get_model("entry", "Student")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    course, _ = Course.objects.update_or_create(
        slug="cpp",
        defaults={
            "title": "C++",
            "summary": "算法与竞赛方向",
        },
    )

    content, _ = CourseContent.objects.update_or_create(
        slug="ascii-char-encoding",
        defaults={
            "course": course,
            "content_type": "C++信奥",
            "title": "ASCII 编码",
            "phase": "GESP2",
            "sort_order": 206,
            "route_path": "/student/cpp/gesp/gesp2/ascii-char-encoding",
            "summary": "GESP2 ASCII 编码知识点页，围绕字符与整数、字符区间判断、大小写偏移和典型真题展开。",
            "has_real_content": True,
            "is_active": True,
        },
    )

    for student in Student.objects.all():
        StudentContentAccess.objects.update_or_create(
            student=student,
            content=content,
            defaults={},
        )


def rollback_gesp2_ascii_topic(apps, schema_editor):
    CourseContent = apps.get_model("entry", "CourseContent")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    StudentContentAccess.objects.filter(content__slug="ascii-char-encoding").delete()
    CourseContent.objects.filter(slug="ascii-char-encoding").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0009_question"),
    ]

    operations = [
        migrations.RunPython(seed_gesp2_ascii_topic, rollback_gesp2_ascii_topic),
    ]
