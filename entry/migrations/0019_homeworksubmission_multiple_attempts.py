from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0018_homeworkimportjob_source_sha256"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="homeworksubmission",
            name="hw_submission_assignment_student_unique",
        ),
        migrations.AddIndex(
            model_name="homeworksubmission",
            index=models.Index(fields=["assignment", "student", "created_at"], name="hw_sub_assign_student_idx"),
        ),
    ]
