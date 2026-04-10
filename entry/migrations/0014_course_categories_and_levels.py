import django.db.models.deletion
from django.db import migrations, models


CPP_CATEGORY_SPECS = [
    {
        "slug": "gesp",
        "title": "GESP",
        "summary": "围绕 GESP 体系组织的算法与竞赛内容入口。",
        "sort_order": 1,
    },
    {
        "slug": "csp",
        "title": "CSP",
        "summary": "预留给 CSP 算法专题、题单训练和阶段任务。",
        "sort_order": 2,
    },
    {
        "slug": "robot-programming",
        "title": "机器人编程",
        "summary": "预留给机器人编程与工程实践类内容。",
        "sort_order": 3,
    },
]


def seed_course_categories_and_levels(apps, schema_editor):
    Course = apps.get_model("entry", "Course")
    CourseCategory = apps.get_model("entry", "CourseCategory")
    CourseLevel = apps.get_model("entry", "CourseLevel")
    CourseContent = apps.get_model("entry", "CourseContent")

    cpp_course = Course.objects.filter(slug="cpp").first()
    if not cpp_course:
        return

    category_map = {}
    for spec in CPP_CATEGORY_SPECS:
        category, _ = CourseCategory.objects.update_or_create(
            course_id=cpp_course.id,
            slug=spec["slug"],
            defaults={
                "title": spec["title"],
                "summary": spec["summary"],
                "sort_order": spec["sort_order"],
                "is_active": True,
            },
        )
        category_map[spec["slug"]] = category

    gesp_category = category_map["gesp"]
    for level_number in range(1, 9):
        code = f"GESP{level_number}"
        CourseLevel.objects.update_or_create(
            category_id=gesp_category.id,
            code=code,
            defaults={
                "title": code,
                "summary": f"{code} 级别入口",
                "sort_order": level_number,
                "is_active": True,
            },
        )

    level_id_map = {
        level.code: level.id
        for level in CourseLevel.objects.filter(category_id=gesp_category.id)
    }
    for content in CourseContent.objects.filter(course_id=cpp_course.id):
        phase_code = (getattr(content, "phase", "") or "").strip().upper()
        level_id = level_id_map.get(phase_code)
        if level_id:
            CourseContent.objects.filter(id=content.id).update(level_id=level_id)


def unseed_course_categories_and_levels(apps, schema_editor):
    CourseCategory = apps.get_model("entry", "CourseCategory")
    CourseContent = apps.get_model("entry", "CourseContent")

    category_ids = list(
        CourseCategory.objects.filter(
            course__slug="cpp",
            slug__in=[spec["slug"] for spec in CPP_CATEGORY_SPECS],
        ).values_list("id", flat=True)
    )
    if not category_ids:
        return

    CourseContent.objects.filter(level__category_id__in=category_ids).update(level_id=None)
    CourseCategory.objects.filter(id__in=category_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0013_teacherstudentassignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=64, verbose_name="分类标识")),
                ("title", models.CharField(max_length=64, verbose_name="分类名称")),
                ("summary", models.TextField(blank=True, verbose_name="分类说明")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="categories",
                        to="entry.course",
                    ),
                ),
            ],
            options={
                "verbose_name": "课程分类",
                "verbose_name_plural": "课程分类",
                "ordering": ["course_id", "sort_order", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("course", "slug"), name="course_category_slug_unique"),
                ],
                "indexes": [
                    models.Index(fields=["course", "is_active", "sort_order"], name="course_cat_active_sort_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CourseLevel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=32, verbose_name="级别编码")),
                ("title", models.CharField(max_length=64, verbose_name="级别名称")),
                ("summary", models.TextField(blank=True, verbose_name="级别说明")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="levels",
                        to="entry.coursecategory",
                    ),
                ),
            ],
            options={
                "verbose_name": "课程级别",
                "verbose_name_plural": "课程级别",
                "ordering": ["category_id", "sort_order", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("category", "code"), name="course_level_code_unique"),
                ],
                "indexes": [
                    models.Index(fields=["category", "is_active", "sort_order"], name="course_lvl_active_sort_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="coursecontent",
            name="level",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contents",
                to="entry.courselevel",
            ),
        ),
        migrations.AddIndex(
            model_name="coursecontent",
            index=models.Index(fields=["level", "is_active", "sort_order"], name="content_level_active_sort_idx"),
        ),
        migrations.RunPython(seed_course_categories_and_levels, unseed_course_categories_and_levels),
    ]
