import uuid

from django.db import migrations, models


def fill_public_ids(apps, schema_editor):
    AuthToken = apps.get_model("accounts", "AuthToken")
    for row in AuthToken.objects.filter(public_id__isnull=True):
        row.public_id = uuid.uuid4()
        row.save(update_fields=["public_id"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        # Add as nullable, fill existing rows, then make it unique and required.
        migrations.AddField(
            model_name="authtoken",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(fill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="authtoken",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="max_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
