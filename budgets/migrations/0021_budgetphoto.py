from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0020_cashmovement_recurrence_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='BudgetPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image_file', models.FileField(upload_to='uploads/budgets/')),
                ('caption', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('budget', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='photos', to='budgets.budget')),
            ],
            options={
                'ordering': ('-created_at', '-id'),
            },
        ),
    ]

