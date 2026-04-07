from django.db import migrations


def expand_gesp4_topics(apps, schema_editor):
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

    topic_specs = [
        {
            "slug": "array-2d",
            "title": "二维数组专题",
            "route_path": "/student/cpp/gesp/gesp4/array-2d",
            "summary": "GESP4 二维数组专题真实内容入口，已接入专题首页和讲次内容。",
        },
        {
            "slug": "binary-search",
            "title": "二分查找专题",
            "route_path": "/student/cpp/gesp/gesp4/binary-search",
            "summary": "GESP4 二分查找专题已纳入内容开放体系，当前先用内容预留页承载。",
        },
        {
            "slug": "sorting",
            "title": "排序专题",
            "route_path": "/student/cpp/gesp/gesp4/sorting",
            "summary": "GESP4 排序专题已纳入内容开放体系，当前先用内容预留页承载。",
        },
        {
            "slug": "enumeration-simulation",
            "title": "枚举与模拟专题",
            "route_path": "/student/cpp/gesp/gesp4/enumeration-simulation",
            "summary": "GESP4 枚举与模拟专题已纳入内容开放体系，当前先用内容预留页承载。",
        },
        {
            "slug": "strings",
            "title": "字符串专题",
            "route_path": "/student/cpp/gesp/gesp4/strings",
            "summary": "GESP4 字符串专题已纳入内容开放体系，当前先用内容预留页承载。",
        },
        {
            "slug": "algorithm-foundation",
            "title": "基础算法综合专题",
            "route_path": "/student/cpp/gesp/gesp4/algorithm-foundation",
            "summary": "GESP4 基础算法综合专题已纳入内容开放体系，当前先用内容预留页承载。",
        },
    ]

    contents = []
    for topic in topic_specs:
        content, _ = CourseContent.objects.update_or_create(
            slug=topic["slug"],
            defaults={
                "course": course,
                "title": topic["title"],
                "phase": "GESP4",
                "route_path": topic["route_path"],
                "summary": topic["summary"],
                "is_active": True,
            },
        )
        contents.append(content)

    for student in Student.objects.all():
        for content in contents:
            StudentContentAccess.objects.update_or_create(
                student=student,
                content=content,
                defaults={},
            )


def rollback_gesp4_topics(apps, schema_editor):
    CourseContent = apps.get_model("entry", "CourseContent")
    StudentContentAccess = apps.get_model("entry", "StudentContentAccess")

    topic_slugs = [
        "binary-search",
        "sorting",
        "enumeration-simulation",
        "strings",
        "algorithm-foundation",
    ]
    StudentContentAccess.objects.filter(content__slug__in=topic_slugs).delete()
    CourseContent.objects.filter(slug__in=topic_slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('entry', '0002_seed_portal_data'),
    ]

    operations = [
        migrations.RunPython(expand_gesp4_topics, rollback_gesp4_topics),
    ]
