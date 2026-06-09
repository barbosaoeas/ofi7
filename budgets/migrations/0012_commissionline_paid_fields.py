from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0011_workordertask_allow_overtime'),
    ]

    operations = [
        migrations.AddField(
            model_name='commissionline',
            name='is_paid',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='commissionline',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

