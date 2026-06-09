from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0009_servicecatalog_workorder_workordertask_commissionline'),
    ]

    operations = [
        migrations.AddField(
            model_name='workordertask',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workordertask',
            name='elapsed_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='workordertask',
            name='last_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='workordertask',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='workordertask',
            name='status',
            field=models.CharField(
                choices=[
                    ('SCHEDULED', 'Agendado'),
                    ('RUNNING', 'Em andamento'),
                    ('PAUSED', 'Pausado'),
                    ('DONE', 'Concluído'),
                ],
                default='SCHEDULED',
                max_length=20,
            ),
        ),
    ]

