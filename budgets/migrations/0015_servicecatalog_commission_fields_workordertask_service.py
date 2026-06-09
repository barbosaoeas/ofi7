from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0014_piece_purchase_arrival_profit'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicecatalog',
            name='commission_mode',
            field=models.CharField(
                choices=[('NONE', 'Sem comissão'), ('PERCENT', '% sobre valor'), ('FIXED', 'Valor fixo')],
                default='NONE',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='servicecatalog',
            name='commission_value',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='workordertask',
            name='service',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tasks',
                to='budgets.servicecatalog',
            ),
        ),
    ]

