"""Cria a tabela dos links públicos.

O `ShareLink` nasceu junto com o modal de compartilhamento, quando a 0001 já
constava como aplicada nos bancos existentes. Por isso ele vem numa migração à
parte, e não dentro da inicial: num banco novo a 0001 cria o memo e a foto e
esta cria o link; num banco antigo o Django pula a 0001 (já aplicada) e roda só
esta, que é justamente a tabela que faltava.
"""
import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShareLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(default=core.models.new_share_token, editable=False, max_length=64, unique=True, verbose_name='token')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(blank=True, help_text='Em branco, o link não expira.', null=True, verbose_name='expira em')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to=settings.AUTH_USER_MODEL, verbose_name='criado por')),
                ('memo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to='core.memo', verbose_name='memo')),
                ('photo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to='core.photo', verbose_name='foto')),
            ],
            options={
                'ordering': ['-created_at'],
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('memo__isnull', False), ('photo__isnull', True)), models.Q(('memo__isnull', True), ('photo__isnull', False)), _connector='OR'), name='sharelink_memo_xor_photo')],
            },
        ),
    ]
