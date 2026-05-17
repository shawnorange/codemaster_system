# Generated for live classroom recording downloads on 2026-05-16

from datetime import timedelta

from django.db import migrations, models


def classify_existing_recordings(apps, schema_editor):
    recording_model = apps.get_model("entry", "ClassroomLiveRecording")
    recording_model.objects.filter(provider="browser_audio").update(recording_type="audio")
    recording_model.objects.exclude(provider="browser_audio").update(recording_type="screen")

    for recording in recording_model.objects.filter(status="completed", ended_at__isnull=False, expires_at__isnull=True):
        recording.expires_at = recording.ended_at + timedelta(days=15)
        recording.save(update_fields=["expires_at"])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("entry", "0030_classroom_live"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="classroomliverecording",
            name="uniq_active_classroom_recording_per_session",
        ),
        migrations.AddField(
            model_name="classroomliverecording",
            name="content_type",
            field=models.CharField(blank=True, max_length=120, verbose_name="文件类型"),
        ),
        migrations.AddField(
            model_name="classroomliverecording",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="文件删除时间"),
        ),
        migrations.AddField(
            model_name="classroomliverecording",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="下载失效时间"),
        ),
        migrations.AddField(
            model_name="classroomliverecording",
            name="file_size",
            field=models.PositiveBigIntegerField(default=0, verbose_name="文件大小"),
        ),
        migrations.AddField(
            model_name="classroomliverecording",
            name="recording_type",
            field=models.CharField(
                choices=[("audio", "音频"), ("screen", "录屏")],
                default="screen",
                max_length=16,
                verbose_name="录制类型",
            ),
        ),
        migrations.RunPython(classify_existing_recordings, noop_reverse),
        migrations.AddIndex(
            model_name="classroomliverecording",
            index=models.Index(fields=["session", "recording_type", "status"], name="clr_session_type_status_idx"),
        ),
        migrations.AddIndex(
            model_name="classroomliverecording",
            index=models.Index(fields=["expires_at", "deleted_at"], name="clr_expires_deleted_idx"),
        ),
        migrations.AddConstraint(
            model_name="classroomliverecording",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["starting", "active"])),
                fields=("session", "recording_type"),
                name="uniq_active_classroom_recording_type",
            ),
        ),
    ]
