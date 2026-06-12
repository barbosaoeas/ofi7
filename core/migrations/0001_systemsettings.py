from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=120)),
                ('address', models.CharField(blank=True, max_length=255)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('primary_color', models.CharField(blank=True, default='#D4AF37', max_length=20)),
                ('secondary_color', models.CharField(blank=True, default='#AA882C', max_length=20)),
                ('logo', models.FileField(blank=True, null=True, upload_to='system/')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Configuração do sistema',
                'verbose_name_plural': 'Configurações do sistema',
            },
        ),
    ]
