from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0010_workordertask_timer_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='workordertask',
            name='allow_overtime',
            field=models.BooleanField(default=False),
        ),
    ]

