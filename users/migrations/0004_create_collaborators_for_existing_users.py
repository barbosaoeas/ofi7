from django.db import migrations


def create_collaborators(apps, schema_editor):
    CustomUser = apps.get_model('users', 'CustomUser')
    Collaborator = apps.get_model('users', 'Collaborator')

    for user in CustomUser.objects.all().only('id', 'email', 'role'):
        if not user.email:
            continue
        Collaborator.objects.get_or_create(
            name=user.email,
            defaults={'function': user.role},
        )


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0003_collaborator_image_file'),
    ]

    operations = [
        migrations.RunPython(create_collaborators, migrations.RunPython.noop),
    ]

