from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0028_thirdpartyservice_expense_movement_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='budget',
            name='administrative_closure',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='budget',
            name='administrative_closed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='budget',
            name='administrative_closed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='administratively_closed_budgets',
                to='users.customuser',
            ),
        ),
        migrations.AddField(
            model_name='budget',
            name='administrative_closure_reason',
            field=models.TextField(blank=True),
        ),
    ]
