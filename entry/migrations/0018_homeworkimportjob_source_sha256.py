from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0017_alter_homeworkimportjob_source_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeworkimportjob",
            name="source_sha256",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="源文件内容哈希"),
        ),
        migrations.AddIndex(
            model_name="homeworkimportjob",
            index=models.Index(fields=["assignment", "source_sha256", "created_at"], name="hw_imp_assign_sha_idx"),
        ),
    ]
