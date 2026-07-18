from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0025_cashmovement_customer_launch_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='thirdpartyservice',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='thirdpartyservice',
            name='scheduled_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='thirdpartyservice',
            name='status',
            field=models.CharField(
                choices=[
                    ('SCHEDULED', 'Agendado'),
                    ('IN_PROGRESS', 'Em andamento'),
                    ('DONE', 'Concluido'),
                ],
                default='SCHEDULED',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='thirdpartyservice',
            name='supplier',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='third_party_services',
                to='budgets.supplier',
            ),
        ),
        migrations.AlterModelOptions(
            name='thirdpartyservice',
            options={'ordering': ('status', 'scheduled_date', 'id')},
        ),
    ]
