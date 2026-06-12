from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0019_budget_approved_at'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='cashcategory',
            options={'ordering': ('direction', 'group', 'name')},
        ),
        migrations.AlterField(
            model_name='cashcategory',
            name='group',
            field=models.CharField(
                blank=True,
                choices=[
                    ('OPERATIONAL', 'Operacional'),
                    ('ADMIN', 'Administrativo'),
                ],
                max_length=20,
            ),
        ),
    ]

