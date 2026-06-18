from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0049_exam_knowledge_point_map"),
    ]

    operations = [
        migrations.AlterField(
            model_name="examsession",
            name="session_type",
            field=models.CharField(
                choices=[
                    ("exam", "正式考试"),
                    ("full_practice", "整卷练习"),
                    ("wrong_practice", "错题练习"),
                    ("free_practice", "自由练习"),
                ],
                default="exam",
                max_length=32,
                verbose_name="场次类型",
            ),
        ),
    ]
