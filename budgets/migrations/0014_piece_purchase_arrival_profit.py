from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0013_budget_allow_repair_without_parts'),
    ]

    operations = [
        migrations.AddField(
            model_name='piece',
            name='arrival_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='piece',
            name='expected_arrival_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='piece',
            name='profit_percent',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name='piece',
            name='purchase_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]

