import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0015_homeworkassignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomeworkImportJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_file", models.FileField(upload_to="homework_imports/%Y/%m/%d", verbose_name="源文件")),
                ("source_filename", models.CharField(max_length=255, verbose_name="原始文件名")),
                (
                    "source_type",
                    models.CharField(
                        choices=[("pdf", "PDF"), ("image", "图片"), ("html", "HTML"), ("docx", "DOCX")],
                        max_length=16,
                        verbose_name="源文件类型",
                    ),
                ),
                (
                    "parse_status",
                    models.CharField(
                        choices=[
                            ("uploaded", "已上传"),
                            ("parsing", "解析中"),
                            ("parsed", "待确认"),
                            ("confirmed", "已确认"),
                            ("failed", "解析失败"),
                            ("cancelled", "已取消"),
                        ],
                        default="uploaded",
                        max_length=16,
                        verbose_name="解析状态",
                    ),
                ),
                ("candidates_json", models.JSONField(blank=True, default=list, verbose_name="候选题目")),
                ("parse_notes", models.TextField(blank=True, verbose_name="解析备注")),
                ("confirmed_at", models.DateTimeField(blank=True, null=True, verbose_name="确认时间")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_jobs",
                        to="entry.homeworkassignment",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_import_jobs",
                        to="entry.portaluser",
                    ),
                ),
            ],
            options={
                "verbose_name": "作业导入任务",
                "verbose_name_plural": "作业导入任务",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="HomeworkQuestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("question_no", models.PositiveIntegerField(verbose_name="题号")),
                (
                    "question_type",
                    models.CharField(
                        choices=[("single_choice", "单选题")],
                        default="single_choice",
                        max_length=32,
                        verbose_name="题型",
                    ),
                ),
                ("stem", models.TextField(verbose_name="题干")),
                ("options_json", models.JSONField(blank=True, default=dict, verbose_name="选项")),
                ("correct_answer", models.CharField(max_length=1, verbose_name="正确答案")),
                ("analysis", models.TextField(blank=True, verbose_name="解析")),
                ("source_snapshot_json", models.JSONField(blank=True, default=dict, verbose_name="来源快照")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        to="entry.homeworkassignment",
                    ),
                ),
                (
                    "import_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="questions",
                        to="entry.homeworkimportjob",
                    ),
                ),
            ],
            options={
                "verbose_name": "作业题目",
                "verbose_name_plural": "作业题目",
                "ordering": ["question_no", "id"],
            },
        ),
        migrations.CreateModel(
            name="HomeworkSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("in_progress", "作答中"),
                            ("submitted", "已提交"),
                            ("auto_checked", "已自动判分"),
                            ("reviewed", "已复核"),
                        ],
                        default="in_progress",
                        max_length=16,
                        verbose_name="提交状态",
                    ),
                ),
                ("total_count", models.PositiveIntegerField(default=0, verbose_name="总题数")),
                ("correct_count", models.PositiveIntegerField(default=0, verbose_name="正确数")),
                ("wrong_count", models.PositiveIntegerField(default=0, verbose_name="错误数")),
                ("score", models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name="分数")),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="开始时间")),
                ("submitted_at", models.DateTimeField(blank=True, null=True, verbose_name="提交时间")),
                ("checked_at", models.DateTimeField(blank=True, null=True, verbose_name="判分时间")),
                ("is_active", models.BooleanField(default=True, verbose_name="是否启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submissions",
                        to="entry.homeworkassignment",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_submissions",
                        to="entry.student",
                    ),
                ),
            ],
            options={
                "verbose_name": "作业提交",
                "verbose_name_plural": "作业提交",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="HomeworkSubmissionAnswer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("selected_answer", models.CharField(blank=True, max_length=1, verbose_name="学生答案")),
                ("is_correct", models.BooleanField(default=False, verbose_name="是否正确")),
                ("correct_answer_snapshot", models.CharField(max_length=1, verbose_name="正确答案快照")),
                ("analysis_snapshot", models.TextField(blank=True, verbose_name="解析快照")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "homework_question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submission_answers",
                        to="entry.homeworkquestion",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to="entry.homeworksubmission",
                    ),
                ),
            ],
            options={
                "verbose_name": "作业作答明细",
                "verbose_name_plural": "作业作答明细",
                "ordering": ["homework_question_id", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="homeworkquestion",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("assignment", "question_no"),
                name="hw_question_assignment_no_active_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="homeworksubmission",
            constraint=models.UniqueConstraint(
                fields=("assignment", "student"),
                name="hw_submission_assignment_student_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="homeworksubmissionanswer",
            constraint=models.UniqueConstraint(
                fields=("submission", "homework_question"),
                name="hw_submission_answer_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="homeworkimportjob",
            index=models.Index(fields=["assignment", "parse_status", "is_active"], name="hw_imp_assign_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworkimportjob",
            index=models.Index(fields=["teacher", "parse_status", "is_active"], name="hw_import_teacher_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworkquestion",
            index=models.Index(fields=["assignment", "is_active", "question_no"], name="hw_question_assign_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworkquestion",
            index=models.Index(fields=["import_job", "is_active", "question_no"], name="hw_question_import_active_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworksubmission",
            index=models.Index(fields=["student", "status", "is_active"], name="hw_sub_student_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworksubmission",
            index=models.Index(fields=["assignment", "status", "is_active"], name="hw_sub_assign_status_idx"),
        ),
        migrations.AddIndex(
            model_name="homeworksubmissionanswer",
            index=models.Index(fields=["submission", "is_correct"], name="hw_ans_sub_correct_idx"),
        ),
    ]
