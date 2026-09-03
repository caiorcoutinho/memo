"""Inspeciona as tags de data EXIF de uma foto, para diagnosticar de onde
(ou se) a data de captura é lida.

Uso:
    python manage.py inspect_exif /caminho/para/a/foto.jpg
"""
import io

from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from core.utils import extract_taken_at

_EXIF_IFD = 0x8769


class Command(BaseCommand):
    help = 'Mostra as tags de data EXIF de uma foto e o que o sistema extrairia.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Caminho do arquivo de imagem.')

    def handle(self, *args, **options):
        path = options['path']
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as exc:
            raise CommandError(f'Não consegui abrir o arquivo: {exc}')

        self.stdout.write(self.style.MIGRATE_HEADING(f'Arquivo: {path}'))
        self.stdout.write(f'Tamanho: {len(data) / 1e6:.2f} MB')

        try:
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                exif = img.getexif()
                ifd = exif.get_ifd(_EXIF_IFD) if exif else {}
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f'O Pillow não conseguiu abrir a imagem: {exc}'))
            self.stdout.write(
                'Se for HEIC, confirme que o pacote pillow-heif está instalado.')
            return

        self.stdout.write(f'Formato: {fmt}')
        self.stdout.write(f'Tem bloco EXIF: {bool(exif)}')
        self.stdout.write('Tags de data encontradas:')
        self.stdout.write(f'  DateTimeOriginal (36867): {ifd.get(36867)!r}')
        self.stdout.write(f'  DateTimeDigitized (36868): {ifd.get(36868)!r}')
        self.stdout.write(f'  DateTime (306):            {exif.get(306)!r}')

        detected = extract_taken_at(io.BytesIO(data))
        if detected:
            self.stdout.write(self.style.SUCCESS(
                f'\nData que o sistema usaria: {detected:%d/%m/%Y %H:%M}'))
        else:
            self.stdout.write(self.style.WARNING(
                '\nNenhuma data legível nos metadados — o sistema cairia na data atual.'))
