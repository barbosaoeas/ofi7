from django.contrib.auth.hashers import make_password
from django.db import migrations


DEFAULT_PASSWORD = '123456'


def sync_collaborators_to_users(apps, schema_editor):
    Collaborator = apps.get_model('users', 'Collaborator')
    CustomUser = apps.get_model('users', 'CustomUser')

    for collaborator in Collaborator.objects.exclude(email__isnull=True).exclude(email=''):
        email = (collaborator.email or '').strip().lower()
        if not email:
            continue

        user = CustomUser.objects.filter(email__iexact=email).first()
        if user is None:
            CustomUser.objects.create(
                email=email,
                role=collaborator.function,
                password=make_password(DEFAULT_PASSWORD),
                is_active=True,
            )
            continue

        update_fields = []
        if user.role != collaborator.function:
            user.role = collaborator.function
            update_fields.append('role')
        if not user.is_active:
            user.is_active = True
            update_fields.append('is_active')
        if update_fields:
            user.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_collaborator_email'),
    ]

    operations = [
        migrations.RunPython(sync_collaborators_to_users, migrations.RunPython.noop),
    ]
