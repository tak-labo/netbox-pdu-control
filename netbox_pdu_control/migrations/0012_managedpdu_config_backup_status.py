from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_pdu_control", "0011_managedpdu_config_backup"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedpdu",
            name="config_backup_status",
            field=models.CharField(
                choices=[
                    ("success", "Success"),
                    ("failed", "Failed"),
                    ("never", "Never synced"),
                ],
                default="never",
                max_length=30,
                verbose_name="Config Backup Status",
            ),
        ),
    ]
