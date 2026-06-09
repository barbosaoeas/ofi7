from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0017_cashcategory_and_cashmovement_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='cashcategory',
            name='group',
            field=models.CharField(blank=True, choices=[('OPERATIONAL', 'Operacional'), ('ADMIN', 'Administrativo'), ('TAXES', 'Impostos/Taxas'), ('FINANCIAL', 'Financeiro'), ('PERSONNEL', 'Pessoal'), ('CAPEX', 'Investimentos/Capex'), ('OTHER', 'Outros')], max_length=20),
        ),
    ]

