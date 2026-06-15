from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0022_merge_20260612_0001'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cashmovement',
            name='source',
            field=models.CharField(
                choices=[
                    ('PARTICULAR', 'Particular'),
                    ('INSURERS', 'Seguradoras'),
                    ('COMPANY', 'Empresa'),
                    ('PARTS_SALE', 'Venda de pecas'),
                    ('LOANS', 'Emprestimos'),
                    ('CUSTOMER', 'Particular (legado)'),
                    ('INSURER', 'Seguradoras (legado)'),
                    ('OTHER', 'Empresa (legado)'),
                ],
                default='COMPANY',
                max_length=20,
            ),
        ),
    ]

