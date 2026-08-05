from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0029_budget_administrative_closure_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='XMLImportJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('MANUAL', 'Manual'), ('DROPBOX', 'Dropbox')], default='MANUAL', max_length=20)),
                ('external_file_id', models.CharField(blank=True, max_length=255)),
                ('file_name', models.CharField(max_length=255)),
                ('file_hash', models.CharField(blank=True, max_length=64)),
                ('cilia_number', models.PositiveIntegerField(blank=True, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pendente'), ('PROCESSING', 'Processando'), ('IMPORTED', 'Importado'), ('DUPLICATE', 'Duplicado'), ('ERROR', 'Erro')], default='PENDING', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('raw_xml', models.TextField(blank=True)),
                ('detected_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('budget', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='xml_import_jobs', to='budgets.budget')),
            ],
            options={
                'ordering': ('-detected_at', '-id'),
            },
        ),
    ]
