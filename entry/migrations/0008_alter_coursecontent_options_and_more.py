from django.db import migrations, models


def seed_gesp2_catalog_and_backfill_content(apps, schema_editor):
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

    existing_specs = [
        {
            "slug": "array-2d",
            "sort_order": 401,
            "has_real_content": True,
            "content_type": "C++信奥",
        },
        {
            "slug": "binary-search",
            "sort_order": 402,
            "has_real_content": False,
            "content_type": "C++信奥",
        },
        {
            "slug": "sorting",
            "sort_order": 403,
            "has_real_content": False,
            "content_type": "C++信奥",
        },
        {
            "slug": "enumeration-simulation",
            "sort_order": 404,
            "has_real_content": False,
            "content_type": "C++信奥",
        },
        {
            "slug": "strings",
            "sort_order": 405,
            "has_real_content": False,
            "content_type": "C++信奥",
        },
        {
            "slug": "algorithm-foundation",
            "sort_order": 406,
            "has_real_content": False,
            "content_type": "C++信奥",
        },
    ]

    for spec in existing_specs:
        CourseContent.objects.filter(slug=spec["slug"]).update(
            content_type=spec["content_type"],
            sort_order=spec["sort_order"],
            has_real_content=spec["has_real_content"],
        )

    gesp2_specs = [
        {
            "slug": "enumeration-method",
            "title": "枚举法",
            "phase": "GESP2",
            "summary": "GESP2 枚举法真实教学页，围绕候选空间、边界判断、易错点和真题例子展开。",
            "route_path": "/student/cpp/gesp/gesp2/enumeration-method",
            "sort_order": 201,
            "has_real_content": True,
        },
        {
            "slug": "branch-structure",
            "title": "分支结构",
            "phase": "GESP2",
            "summary": "GESP2 分支结构已纳入知识点目录，当前先用预留页承接。",
            "route_path": "/student/cpp/gesp/gesp2/branch-structure",
            "sort_order": 202,
            "has_real_content": False,
        },
        {
            "slug": "loop-structure",
            "title": "循环结构",
            "phase": "GESP2",
            "summary": "GESP2 循环结构已纳入知识点目录，当前先用预留页承接。",
            "route_path": "/student/cpp/gesp/gesp2/loop-structure",
            "sort_order": 203,
            "has_real_content": False,
        },
        {
            "slug": "basic-simulation",
            "title": "简单模拟",
            "phase": "GESP2",
            "summary": "GESP2 简单模拟已纳入知识点目录，当前先用预留页承接。",
            "route_path": "/student/cpp/gesp/gesp2/basic-simulation",
            "sort_order": 204,
            "has_real_content": False,
        },
        {
            "slug": "string-basics",
            "title": "字符串基础",
            "phase": "GESP2",
            "summary": "GESP2 字符串基础已纳入知识点目录，当前先用预留页承接。",
            "route_path": "/student/cpp/gesp/gesp2/string-basics",
            "sort_order": 205,
            "has_real_content": False,
        },
    ]

    gesp2_contents = []
    for spec in gesp2_specs:
        content, _ = CourseContent.objects.update_or_create(
            slug=spec["slug"],
            defaults={
                "course": course,
                "content_type": "C++信奥",
                "title": spec["title"],
                "phase": spec["phase"],
                "sort_order": spec["sort_order"],
                "route_path": spec["route_path"],
                "summary": spec["summary"],
                "has_real_content": spec["has_real_content"],
                "is_active": True,
            },
        )
        gesp2_contents.append(content)

    for student in Student.objects.all():
        for content in gesp2_contents:
            StudentContentAccess.objects.update_or_create(
                student=student,
                content=content,
                defaults={},
            )


def rollback_gesp2_catalog(apps, schema_editor):
    CourseContent = apps.get_model("entry", "CourseContent")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    gesp2_slugs = [
        "enumeration-method",
        "branch-structure",
        "loop-structure",
        "basic-simulation",
        "string-basics",
    ]
    StudentContentAccess.objects.filter(content__slug__in=gesp2_slugs).delete()
    CourseContent.objects.filter(slug__in=gesp2_slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('entry', '0007_refactor_student_learning_fields'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='coursecontent',
            options={'ordering': ['course_id', 'phase', 'sort_order', 'id'], 'verbose_name': '课程内容', 'verbose_name_plural': '课程内容'},
        ),
        migrations.AddField(
            model_name='coursecontent',
            name='content_type',
            field=models.CharField(blank=True, max_length=64, verbose_name='课程类别'),
        ),
        migrations.AddField(
            model_name='coursecontent',
            name='has_real_content',
            field=models.BooleanField(default=False, verbose_name='是否已有真实内容'),
        ),
        migrations.AddField(
            model_name='coursecontent',
            name='sort_order',
            field=models.PositiveIntegerField(default=0, verbose_name='排序'),
        ),
        migrations.RunPython(seed_gesp2_catalog_and_backfill_content, rollback_gesp2_catalog),
    ]
