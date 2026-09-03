from django.conf import settings
from django.db import models
from django.urls import reverse

from .utils import METADATA_FIELDS, clean_metadata_fields, default_metadata_fields


class Memo(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memos',
    )
    name = models.CharField('nome', max_length=120)
    description = models.TextField('descrição', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Padrão de compressão das fotos deste memo: só pré-marca a opção na tela
    # de envio, que continua sendo a palavra final para cada foto.
    compress_photos = models.BooleanField(
        'comprimir fotos por padrão',
        default=True,
        help_text='Pré-seleciona a compressão ao enviar fotos para este memo. '
                  'Desmarque para guardar os arquivos originais.',
    )
    # Metadados habilitados por padrão nas fotos deste memo. Como a compressão,
    # é só o padrão: a tela de envio pré-marca essas caixas e quem envia decide.
    metadata_fields = models.JSONField(
        'metadados por padrão',
        default=default_metadata_fields,
        blank=True,
        help_text='Pré-seleciona quais metadados serão lidos das fotos enviadas '
                  'para este memo.',
    )
    # Compartilhamento: usuários que podem ver o memo, e os que também podem editá-lo.
    shared_view = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='shared_memos_view', blank=True,
        verbose_name='pode visualizar',
    )
    shared_edit = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='shared_memos_edit', blank=True,
        verbose_name='pode editar',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('core:memo_detail', args=[self.pk])

    @property
    def cover(self):
        """Foto mais recente do memo, usada como capa."""
        return self.photos.first()

    def can_view(self, user):
        return (self.owner_id == user.pk
                or self.shared_view.filter(pk=user.pk).exists()
                or self.shared_edit.filter(pk=user.pk).exists())

    def can_edit(self, user):
        return self.owner_id == user.pk or self.shared_edit.filter(pk=user.pk).exists()


class Photo(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='photos',
    )
    memo = models.ForeignKey(
        Memo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='photos',
        verbose_name='memo',
    )
    name = models.CharField('nome', max_length=120)
    description = models.TextField('descrição', blank=True)
    image = models.ImageField('arquivo', upload_to='photos/%Y/%m/')
    taken_at = models.DateTimeField(
        'data',
        help_text='Extraída dos metadados da foto quando disponível.',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    # Quais metadados esta foto guarda (escolhidos no envio, ajustáveis depois).
    metadata_fields = models.JSONField(
        'metadados habilitados',
        default=default_metadata_fields,
        blank=True,
    )
    # Valores lidos do arquivo, só dos grupos habilitados:
    # {'camera': {'Modelo': 'iPhone 13'}, ...}. O que está desabilitado não é
    # lido nem guardado. A data fica no campo taken_at, não aqui.
    metadata = models.JSONField('metadados', default=dict, blank=True)
    # Compartilhamento de foto avulsa: somente leitura.
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='shared_photos', blank=True,
        verbose_name='compartilhada com',
    )

    class Meta:
        ordering = ['-taken_at']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('core:photo_detail', args=[self.pk])

    @property
    def metadata_groups(self):
        """Metadados habilitados que têm conteúdo, prontos para a tela de detalhe.

        Devolve ``[{'key', 'label', 'pairs': [(rótulo, valor), ...]}]`` na ordem
        do catálogo. A data não entra: ela já aparece em destaque na foto, e o
        que o grupo `taken_at` controla é se ela é lida do arquivo.
        """
        enabled = set(self.metadata_fields or [])
        stored = self.metadata or {}
        groups = []
        for key, label, _help in METADATA_FIELDS:
            if key == 'taken_at' or key not in enabled:
                continue
            items = stored.get(key) or {}
            if items:
                groups.append({'key': key, 'label': label, 'pairs': list(items.items())})
        return groups

    @property
    def reads_date_from_metadata(self):
        """A data desta foto pode vir do EXIF, ou o usuário desabilitou isso?"""
        return 'taken_at' in (self.metadata_fields or [])

    def set_metadata(self, fields, found):
        """Define os metadados habilitados e guarda só os valores desses grupos.

        Tudo que o usuário desabilitou é descartado aqui — não fica no banco
        nem volta na tela.
        """
        self.metadata_fields = clean_metadata_fields(fields)
        enabled = set(self.metadata_fields)
        self.metadata = {key: value for key, value in (found or {}).items()
                         if key in enabled and value}

    def can_view(self, user):
        if self.owner_id == user.pk:
            return True
        if self.shared_with.filter(pk=user.pk).exists():
            return True
        # Quem tem acesso ao memo (ver ou editar) também vê suas fotos.
        return bool(self.memo_id) and self.memo.can_view(user)

    def can_edit(self, user):
        # Foto avulsa compartilhada é só leitura; edição vem de ser dono
        # ou de poder editar o memo em que a foto está.
        if self.owner_id == user.pk:
            return True
        return bool(self.memo_id) and self.memo.can_edit(user)
