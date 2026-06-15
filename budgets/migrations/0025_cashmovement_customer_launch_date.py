from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_vehicle_image_file'),
        ('budgets', '0024_bankaccount_supplier_cashmovement_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='cashmovement',
            name='customer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cash_movements', to='customers.customer'),
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='launch_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]

