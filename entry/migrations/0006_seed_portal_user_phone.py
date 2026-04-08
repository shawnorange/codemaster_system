from django.db import migrations


def seed_portal_user_phone(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")

    phone_map = {
        "teacher001": "13900010001",
        "parent001": "13800138001",
        "principal001": "13700010001",
    }
    for username, phone in phone_map.items():
        PortalUser.objects.filter(username=username).update(phone=phone)


def unseed_portal_user_phone(apps, schema_editor):
    PortalUser = apps.get_model("entry", "PortalUser")
    PortalUser.objects.filter(username__in=["teacher001", "parent001", "principal001"]).update(phone="")


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0005_portaluser_phone"),
    ]

    operations = [
        migrations.RunPython(seed_portal_user_phone, unseed_portal_user_phone),
    ]
