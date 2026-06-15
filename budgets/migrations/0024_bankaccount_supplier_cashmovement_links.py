from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0023_cashmovement_source_choices'),
    ]

    operations = [
        migrations.CreateModel(
            name='BankAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bank_name', models.CharField(max_length=120)),
                ('account_name', models.CharField(max_length=120)),
                ('branch', models.CharField(blank=True, max_length=30)),
                ('account_number', models.CharField(blank=True, max_length=40)),
                ('account_type', models.CharField(choices=[('CHECKING', 'Corrente'), ('SAVINGS', 'Poupanca'), ('CASH', 'Caixa'), ('DIGITAL', 'Digital'), ('OTHER', 'Outro')], default='CHECKING', max_length=20)),
                ('pix_key', models.CharField(blank=True, max_length=120)),
                ('initial_balance', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('initial_balance_date', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Conta bancária',
                'verbose_name_plural': 'Contas bancárias',
                'ordering': ('bank_name', 'account_name'),
            },
        ),
        migrations.CreateModel(
            name='Supplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('kind', models.CharField(choices=[('SERVICE', 'Servico'), ('MATERIAL', 'Material'), ('BOTH', 'Servico e material')], default='BOTH', max_length=20)),
                ('document', models.CharField(blank=True, max_length=20)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('contact_name', models.CharField(blank=True, max_length=120)),
                ('notes', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Fornecedor',
                'verbose_name_plural': 'Fornecedores',
                'ordering': ('name',),
            },
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='bank_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='movements', to='budgets.bankaccount'),
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='supplier',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movements', to='budgets.supplier'),
        ),
    ]

