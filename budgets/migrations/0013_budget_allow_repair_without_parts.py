from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0012_commissionline_paid_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='budget',
            name='allow_repair_without_parts',
            field=models.BooleanField(default=False),
        ),
    ]

