from django.db import migrations, models


def populate_collaborator_email(apps, schema_editor):
    Collaborator = apps.get_model('users', 'Collaborator')

    used_emails = set(
        email
        for email in Collaborator.objects.exclude(email__isnull=True).values_list('email', flat=True)
        if email
    )

    for collaborator in Collaborator.objects.all().only('id', 'name', 'email'):
        if collaborator.email:
            continue
        raw = (collaborator.name or '').strip().lower()
        if not raw or '@' not in raw or raw in used_emails:
            continue
        collaborator.email = raw
        collaborator.save(update_fields=['email'])
        used_emails.add(raw)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_create_collaborators_for_existing_users'),
    ]

    operations = [
        migrations.AddField(
            model_name='collaborator',
            name='email',
            field=models.EmailField(blank=True, max_length=254, null=True, unique=True),
        ),
        migrations.RunPython(populate_collaborator_email, migrations.RunPython.noop),
    ]
