from django.db import migrations, models


def populate_approved_at(apps, schema_editor):
    Budget = apps.get_model('budgets', 'Budget')
    for budget in Budget.objects.filter(status='AUTHORIZED', approved_at__isnull=True).only('id', 'updated_at'):
        budget.approved_at = budget.updated_at
        budget.save(update_fields=['approved_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0018_cashcategory_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='budget',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_approved_at, migrations.RunPython.noop),
    ]
