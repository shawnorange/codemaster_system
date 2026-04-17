import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0014_course_categories_and_levels"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomeworkAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255, verbose_name="作业标题")),
                ("description", models.TextField(blank=True, verbose_name="作业说明")),
                ("due_date", models.DateField(verbose_name="截止日期")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("assigned", "待完成"),
                            ("completed", "已完成"),
                            ("reviewed", "已评阅"),
                            ("cancelled", "已取消"),
                        ],
                        default="assigned",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("teacher_comment", models.TextField(blank=True, verbose_name="教师评语")),
                ("assigned_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="布置时间")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="完成时间")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True, verbose_name="评阅时间")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "content",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_assignments",
                        to="entry.coursecontent",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_assignments",
                        to="entry.student",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_assignments",
                        to="entry.portaluser",
                    ),
                ),
            ],
            options={
                "verbose_name": "作业",
                "verbose_name_plural": "作业",
                "ordering": ["-due_date", "-assigned_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="homeworkassignment",
            index=models.Index(fields=["student", "due_date", "status"], name="hw_student_due_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworkassignment",
            index=models.Index(fields=["teacher", "due_date", "status"], name="hw_teacher_due_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworkassignment",
            index=models.Index(fields=["content", "status"], name="hw_content_status_idx"),
        ),
    ]
