from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0015_servicecatalog_commission_fields_workordertask_service'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('IN', 'Entrada'), ('OUT', 'Saída')], default='IN', max_length=10)),
                ('source', models.CharField(choices=[('CUSTOMER', 'Cliente'), ('INSURER', 'Seguradora'), ('OTHER', 'Outro')], default='OTHER', max_length=20)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('due_date', models.DateField(blank=True, null=True)),
                ('is_realized', models.BooleanField(default=False)),
                ('realized_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'budget',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='cash_movements',
                        to='budgets.budget',
                    ),
                ),
            ],
            options={
                'ordering': ('due_date', 'created_at'),
            },
        ),
    ]

