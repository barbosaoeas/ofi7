from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0019_budget_approved_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='cashmovement',
            name='recurrence_group',
            field=models.CharField(blank=True, default='', max_length=36),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='recurrence_index',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='recurrence_total',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
