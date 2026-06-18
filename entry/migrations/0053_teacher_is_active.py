from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0052_teacher_subject"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="是否在职"),
        ),
    ]
