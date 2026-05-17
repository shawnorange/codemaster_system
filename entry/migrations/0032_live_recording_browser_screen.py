# Generated for browser screen recordings on 2026-05-17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0031_live_recording_downloads"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classroomliverecording",
            name="provider",
            field=models.CharField(
                choices=[
                    ("livekit_egress", "LiveKit Egress"),
                    ("browser_audio", "浏览器录音"),
                    ("browser_screen", "浏览器录屏"),
                ],
                default="livekit_egress",
                max_length=32,
                verbose_name="录制服务",
            ),
        ),
    ]
