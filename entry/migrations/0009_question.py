from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0008_alter_coursecontent_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Question",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=128, unique=True, verbose_name="题目标识")),
                ("content_slug", models.CharField(max_length=64, verbose_name="知识点/专题标识")),
                ("level_code", models.CharField(max_length=16, verbose_name="级别")),
                ("question_type", models.CharField(max_length=32, verbose_name="题型")),
                ("source_year", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="来源年份")),
                ("source_month", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="来源月份")),
                ("source_question_no", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="来源题号")),
                ("title", models.CharField(max_length=255, verbose_name="题目标题")),
                ("payload", models.JSONField(blank=True, default=dict, verbose_name="题目内容")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("is_demo", models.BooleanField(default=False, verbose_name="是否适合作教学 Demo")),
            ],
            options={
                "db_table": "questions",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(source_month__isnull=True)
                    | models.Q(source_month__gte=1, source_month__lte=12)
                ),
                name="questions_source_month_valid",
            ),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(fields=["content_slug", "is_active", "sort_order", "id"], name="q_content_active_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(fields=["level_code", "is_active", "sort_order", "id"], name="q_level_active_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(fields=["question_type", "is_active", "sort_order", "id"], name="q_type_active_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(fields=["source_year", "source_month", "is_active", "sort_order", "id"], name="q_source_active_sort_idx"),
        ),
        migrations.AddIndex(
            model_name="question",
            index=models.Index(fields=["content_slug", "is_demo", "is_active", "sort_order", "id"], name="q_content_demo_sort_idx"),
        ),
    ]
