from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0039_exam_question_bank_import_job"),
    ]

    operations = [
        migrations.AlterField(
            model_name="examquestionbankpaper",
            name="source",
            field=models.CharField(
                choices=[("hermes", "Hermes 题库"), ("local_ocr", "本地 OCR 导入")],
                default="hermes",
                max_length=32,
                verbose_name="来源",
            ),
        ),
        migrations.AlterField(
            model_name="examquestionbankquestion",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("single_choice", "单选题"),
                    ("true_false", "判断题"),
                    ("programming", "编程题"),
                    ("raw_markdown", "OCR Markdown 页"),
                ],
                max_length=32,
                verbose_name="题型",
            ),
        ),
    ]
