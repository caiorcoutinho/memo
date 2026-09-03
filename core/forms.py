import json

from django import forms
from django.db.models import Q

from .models import Memo, Photo
from .utils import (JPEG_QUALITY, MAX_SIDE, METADATA_CHOICES, METADATA_FIELDS,
                    clean_metadata_fields, default_metadata_fields)


def _editable_memos(user):
    """Memos onde o usuário pode colocar fotos: os seus + os que pode editar."""
    if not user:
        return Memo.objects.none()
    return Memo.objects.filter(Q(owner=user) | Q(shared_edit=user)).distinct()


def metadata_choice_field(label):
    """Caixas de seleção com um metadado da foto em cada uma.

    Usado nos três formulários: no memo define o padrão dele, no envio e na
    edição decide o que aquela foto guarda. Nunca é obrigatório — desmarcar tudo
    é uma escolha válida (nenhum metadado é lido).
    """
    return forms.MultipleChoiceField(
        label=label,
        choices=METADATA_CHOICES,
        initial=default_metadata_fields,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )


class MetadataTogglesMixin:
    """Ajuda os formulários que têm o campo `metadata_fields`.

    Também identifica os campos que moram no modal de opções avançadas: são
    ajustes finos, com padrão bom para quase todo mundo, que não precisam
    ocupar a tela principal do formulário.
    """

    # Ordem em que aparecem dentro do modal (metadata_fields vai por último,
    # renderizado à parte por causa das caixas com explicação).
    advanced_names = ['compress', 'compress_photos', 'metadata_fields']

    def advanced_toggles(self):
        """Campos simples do modal (fora as caixas de metadados), já ligados."""
        return [self[name] for name in self.advanced_names
                if name != 'metadata_fields' and name in self.fields]

    def metadata_toggles(self):
        """Lista pronta para o template: rótulo, explicação e estado de cada caixa."""
        chosen = set(self['metadata_fields'].value() or [])
        return [{'key': key, 'label': label, 'help': help_text,
                 'checked': key in chosen}
                for key, label, help_text in METADATA_FIELDS]

    def clean_metadata_fields(self):
        # Só chaves conhecidas, sem repetição e na ordem do catálogo.
        return clean_metadata_fields(self.cleaned_data.get('metadata_fields'))


class MemoSelect(forms.Select):
    """Select de memos que carrega, em cada opção, os padrões do memo.

    Os atributos data-compress e data-metadata são lidos pelo JavaScript da tela
    de envio para pré-marcar a caixa "comprimir" e as caixas de metadados
    conforme o memo escolhido.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compress_by_memo = {}
        self.metadata_by_memo = {}

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        default = self.compress_by_memo.get(str(value))
        if default is not None:
            option['attrs']['data-compress'] = '1' if default else '0'
        fields = self.metadata_by_memo.get(str(value))
        if fields is not None:
            option['attrs']['data-metadata'] = json.dumps(fields)
        return option


class PhotoForm(MetadataTogglesMixin, forms.ModelForm):
    # A data não é pedida na criação: vem dos metadados (EXIF) ou da data do
    # arquivo, e pode ser ajustada depois na tela de edição.
    # Preenchido por JavaScript com File.lastModified (data de modificação do
    # arquivo no computador), usado como fallback quando não há data no EXIF.
    file_modified = forms.IntegerField(required=False, widget=forms.HiddenInput())
    # Compressão é opcional e decidida foto a foto; o memo só define o padrão.
    compress = forms.BooleanField(
        label='Comprimir a imagem',
        required=False,
        initial=True,
        help_text='Reduz o arquivo (no máximo {side}px de lado, JPEG de qualidade '
                  '{quality}) para ocupar menos espaço. Desmarque para guardar a '
                  'foto exatamente como saiu da câmera.'.format(
                      side=MAX_SIDE, quality=JPEG_QUALITY),
    )

    # Quais metadados ler desta foto. O memo escolhido só pré-marca as caixas;
    # a palavra final é de quem envia. O que ficar desmarcado não é lido do
    # arquivo nem guardado no banco.
    metadata_fields = metadata_choice_field('Metadados da foto')

    class Meta:
        model = Photo
        fields = ['image', 'name', 'description', 'memo']
        widgets = {'memo': MemoSelect}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Escolhe entre os memos do usuário e os que ele pode editar.
        memos = _editable_memos(user)
        self.fields['memo'].queryset = memos
        self.fields['memo'].empty_label = 'Sem memo'
        defaults = memos.values_list('pk', 'compress_photos', 'metadata_fields')
        self.fields['memo'].widget.compress_by_memo = {}
        self.fields['memo'].widget.metadata_by_memo = {}
        for pk, compress, metadata in defaults:
            self.fields['memo'].widget.compress_by_memo[str(pk)] = compress
            self.fields['memo'].widget.metadata_by_memo[str(pk)] = \
                clean_metadata_fields(metadata)
        self.fields['description'].widget.attrs.setdefault('rows', 3)
        # Só aceita imagens (no celular abre direto câmera/galeria) — previne erro.
        self.fields['image'].widget.attrs['accept'] = 'image/*'


class PhotoEditForm(MetadataTogglesMixin, forms.ModelForm):
    """Edita os metadados de uma foto já enviada (sem trocar o arquivo)."""
    metadata_fields = metadata_choice_field('Metadados guardados')
    taken_at = forms.DateTimeField(
        label='Data',
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M',
        ),
    )

    class Meta:
        model = Photo
        fields = ['name', 'taken_at', 'description', 'memo', 'metadata_fields']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Pode mover a foto para um dos memos do usuário ou que ele pode editar.
        self.fields['memo'].queryset = _editable_memos(user)
        self.fields['memo'].empty_label = 'Sem memo'
        self.fields['description'].widget.attrs.setdefault('rows', 3)


class MemoForm(MetadataTogglesMixin, forms.ModelForm):
    # Padrão do memo: pré-marca essas caixas na tela de envio de cada foto.
    metadata_fields = metadata_choice_field('Metadados por padrão')

    class Meta:
        model = Memo
        fields = ['name', 'description', 'compress_photos', 'metadata_fields']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.setdefault('rows', 3)
        self.fields['name'].widget.attrs.setdefault('autofocus', True)
