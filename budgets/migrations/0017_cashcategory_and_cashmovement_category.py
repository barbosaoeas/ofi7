from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('budgets', '0016_cashmovement'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('IN', 'Entrada'), ('OUT', 'Saída')], default='OUT', max_length=10)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ('direction', 'name'),
            },
        ),
        migrations.AddField(
            model_name='cashmovement',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='movements',
                to='budgets.cashcategory',
            ),
        ),
    ]

