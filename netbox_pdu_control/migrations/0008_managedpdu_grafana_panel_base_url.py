import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_pdu_control", "0007_managedpdu_sync_metrics_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedpdu",
            name="grafana_panel_base_url",
            field=models.URLField(
                blank=True,
                help_text="Grafana embed URL without var-pduname. e.g. https://grafana:3000/d-solo/UID/slug?orgId=1&panelId=2",
                max_length=1000,
                verbose_name="Grafana Panel URL",
            ),
        ),
    ]
