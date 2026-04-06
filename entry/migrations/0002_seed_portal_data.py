from django.db import migrations


def seed_portal_data(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")
    Student = apps.get_model("entry", "Student")
    Course = apps.get_model("entry", "Course")
    CourseContent = apps.get_model("entry", "CourseContent")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    users = {}
    user_specs = [
        ("teacher001", "teacher", "周老师"),
        ("student001", "student", "林一诺"),
        ("parent001", "parent", "林妈妈"),
        ("principal001", "principal", "王校长"),
    ]
    for username, role, full_name in user_specs:
        users[username], _ = PortalUser.objects.update_or_create(
            username=username,
            defaults={
                "password": "123456",
                "role": role,
                "full_name": full_name,
                "is_active": True,
            },
        )

    student, _ = Student.objects.update_or_create(
        user=users["student001"],
        defaults={
            "parent_user": users["parent001"],
            "teacher_user": users["teacher001"],
            "display_name": "林一诺",
            "grade": "四年级",
            "campus": "浦东张江校区",
            "current_program": "C++ > GESP > GESP4",
            "phase_label": "二维数组专题待开放",
        },
    )

    course, _ = Course.objects.update_or_create(
        slug="cpp",
        defaults={
            "title": "C++",
            "summary": "算法与竞赛方向",
        },
    )

    content, _ = CourseContent.objects.update_or_create(
        slug="array-2d",
        defaults={
            "course": course,
            "title": "二维数组专题",
            "phase": "GESP4",
            "route_path": "/student/cpp/gesp/gesp4/array-2d",
            "summary": "GESP4 二维数组专题真实内容入口",
            "is_active": True,
        },
    )

    StudentContentAccess.objects.update_or_create(
        student=student,
        content=content,
        defaults={
            "is_open": False,
            "granted_by": None,
            "granted_at": None,
        },
    )


def unseed_portal_data(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")
    Student = apps.get_model("entry", "Student")
    Course = apps.get_model("entry", "Course")
    CourseContent = apps.get_model("entry", "CourseContent")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    StudentContentAccess.objects.filter(content__slug="array-2d").delete()
    Student.objects.filter(user__username="student001").delete()
    CourseContent.objects.filter(slug="array-2d").delete()
    Course.objects.filter(slug="cpp").delete()
    PortalUser.objects.filter(
        username__in=["teacher001", "student001", "parent001", "principal001"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('entry', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_portal_data, unseed_portal_data),
    ]
