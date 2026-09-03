"""Utilitários para extrair metadados das fotos enviadas."""
import io
import os
from datetime import datetime
from datetime import timezone as dt_timezone

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageOps

# IDs de tags EXIF relevantes para a data de captura.
_EXIF_IFD = 0x8769          # ponteiro para o bloco Exif
_DATETIME_ORIGINAL = 36867  # DateTimeOriginal (momento da foto)
_DATETIME_DIGITIZED = 36868 # DateTimeDigitized
_DATETIME = 306             # DateTime (base IFD, fallback)

_FORMATS = ('%Y:%m:%d %H:%M:%S', '%Y-%m-%d %H:%M:%S')


def extract_taken_at(image_file):
    """Retorna um datetime *aware* a partir do EXIF da imagem, ou None.

    Lê DateTimeOriginal/DateTimeDigitized/DateTime nessa ordem de preferência.
    Nunca levanta exceção: se algo falhar, devolve None.
    """
    raw = None
    try:
        image_file.seek(0)
        with Image.open(image_file) as img:
            exif = img.getexif()
            if exif:
                ifd = exif.get_ifd(_EXIF_IFD)
                raw = (
                    ifd.get(_DATETIME_ORIGINAL)
                    or ifd.get(_DATETIME_DIGITIZED)
                    or exif.get(_DATETIME)
                )
    except Exception:
        return None
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    raw = str(raw).strip() if raw else ''
    # Câmeras sem relógio configurado gravam data-zero; trate como "sem data".
    if not raw or raw.startswith('0000'):
        return None

    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return None


def datetime_from_epoch_ms(ms):
    """Converte um timestamp em milissegundos (File.lastModified do navegador)
    em um datetime *aware*. Retorna None se for inválido ou não positivo."""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# Formatos que os navegadores exibem nativamente em <img>.
_WEB_FORMATS = {'JPEG', 'PNG', 'GIF', 'WEBP'}


def to_web_jpeg(image_file, original_name):
    """Converte formatos não exibíveis no navegador (ex.: HEIC do iPhone) para JPEG.

    Retorna (ContentFile, novo_nome) quando converte, ou (None, None) se o
    arquivo já está em um formato web ou se a conversão falhar.
    """
    buf = io.BytesIO()
    try:
        image_file.seek(0)
        with Image.open(image_file) as img:
            if (img.format or '').upper() in _WEB_FORMATS:
                return None, None
            img.convert('RGB').save(buf, format='JPEG', quality=90)
    except Exception:
        return None, None
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    base = os.path.splitext(os.path.basename(original_name))[0] or 'foto'
    return ContentFile(buf.getvalue()), base + '.jpg'


# Padrões da compressão opcional: lado maior em 2560px (suficiente para tela
# cheia em telas retina) e qualidade JPEG 82, que é onde o ganho de tamanho
# ainda não é visível a olho nu.
MAX_SIDE = 2560
JPEG_QUALITY = 82


def compress_image(image_file, original_name, max_side=MAX_SIDE, quality=JPEG_QUALITY):
    """Reduz o peso da imagem: redimensiona o lado maior e regrava como JPEG.

    Retorna (ContentFile, novo_nome) quando comprimiu, ou (None, None) quando
    não vale a pena mexer no arquivo — imagem com transparência, GIF animado,
    ou resultado que não ficou menor que o original — e também se algo falhar.
    Nesses casos o chamador deve seguir com o arquivo original (passando pelo
    to_web_jpeg, se for um formato que o navegador não exibe).

    A orientação do EXIF é aplicada nos pixels antes de o EXIF ser descartado,
    então a foto continua "em pé". A data já foi lida na view, antes daqui.
    """
    buf = io.BytesIO()
    fmt = ''
    try:
        original_size = _file_size(image_file)
        image_file.seek(0)
        with Image.open(image_file) as img:
            fmt = (img.format or '').upper()
            # Transparência e animação não sobrevivem ao JPEG: deixa como está.
            if getattr(img, 'is_animated', False) or img.mode == 'P' or 'A' in img.getbands():
                return None, None
            img = ImageOps.exif_transpose(img) or img
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.LANCZOS)
            img.convert('RGB').save(
                buf, format='JPEG', quality=quality, optimize=True, progressive=True)
    except Exception:
        return None, None
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    data = buf.getvalue()
    # Se não encolheu, só faz sentido trocar o arquivo quando o original é um
    # formato que o navegador não exibe (HEIC): aí a conversão é obrigatória.
    if original_size and len(data) >= original_size and fmt in _WEB_FORMATS:
        return None, None

    base = os.path.splitext(os.path.basename(original_name))[0] or 'foto'
    return ContentFile(data), base + '.jpg'


def _file_size(image_file):
    """Tamanho em bytes do arquivo enviado; 0 quando não dá para saber."""
    size = getattr(image_file, 'size', None)
    if size:
        return size
    try:
        image_file.seek(0, os.SEEK_END)
        return image_file.tell()
    except Exception:
        return 0
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────
# Catálogo de metadados
#
# Cada grupo abaixo é um "metadado" que o usuário pode habilitar ou desabilitar
# ao criar um memo (define o padrão dele) ou ao enviar/editar uma foto.
# O que estiver desabilitado não é lido do arquivo nem guardado no banco.
# ──────────────────────────────────────────────────────────────────────────

METADATA_FIELDS = [
    ('taken_at', 'Data da captura',
     'Quando a foto foi tirada, lida do EXIF. Sem isso, usamos a data do arquivo.'),
    ('camera', 'Câmera',
     'Fabricante e modelo do aparelho que tirou a foto.'),
    ('lens', 'Lente',
     'Modelo da lente e distância focal.'),
    ('exposure', 'Exposição',
     'ISO, abertura, tempo de exposição e flash.'),
    ('gps', 'Localização',
     'Coordenadas de GPS gravadas pelo aparelho.'),
    ('image', 'Imagem e arquivo',
     'Dimensões, formato e tamanho do arquivo.'),
    ('software', 'Software',
     'Programa ou app que gerou/editou a foto.'),
]

METADATA_KEYS = [key for key, _label, _help in METADATA_FIELDS]
METADATA_CHOICES = [(key, label) for key, label, _help in METADATA_FIELDS]
METADATA_LABELS = {key: label for key, label, _help in METADATA_FIELDS}


def default_metadata_fields():
    """Padrão de novos memos/fotos: todos os metadados habilitados.

    É uma função (e não a lista) porque JSONField precisa de um default
    chamável — senão todos os registros compartilhariam a mesma lista.
    """
    return list(METADATA_KEYS)


def clean_metadata_fields(fields):
    """Normaliza uma lista vinda do formulário/banco: só chaves conhecidas,
    sem repetição e na ordem do catálogo."""
    chosen = set(fields or [])
    return [key for key in METADATA_KEYS if key in chosen]


# Tags EXIF usadas na extração (além das de data, já declaradas acima).
_MAKE, _MODEL, _SOFTWARE = 271, 272, 305
_LENS_MAKE, _LENS_MODEL = 0xA433, 0xA434
_FOCAL_LENGTH, _FOCAL_35MM = 0x920A, 0xA405
_ISO, _F_NUMBER, _EXPOSURE_TIME = 0x8827, 0x829D, 0x829A
_EXPOSURE_BIAS, _FLASH = 0x9204, 0x9209
_GPS_IFD = 0x8825


def _text(value):
    """Converte um valor EXIF em texto limpo, ou '' se não houver nada útil."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'ignore')
    return ' '.join(str(value).split()).strip('\x00 ')


def _number(value):
    """Converte racionais do EXIF (IFDRational) em float, ou None."""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms_to_degrees(dms, ref):
    """Converte (graus, minutos, segundos) + hemisfério em graus decimais."""
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return -value if _text(ref).upper() in ('S', 'W') else value


def _camera(exif, ifd):
    make, model = _text(exif.get(_MAKE)), _text(exif.get(_MODEL))
    # Muitos aparelhos repetem o fabricante no modelo ("Apple iPhone 13").
    if make and model.lower().startswith(make.lower()):
        model = model[len(make):].strip()
    data = {}
    if make:
        data['Fabricante'] = make
    if model:
        data['Modelo'] = model
    return data


def _lens(ifd):
    data = {}
    lens = ' '.join(part for part in (_text(ifd.get(_LENS_MAKE)),
                                      _text(ifd.get(_LENS_MODEL))) if part)
    if lens:
        data['Lente'] = lens
    focal = _number(ifd.get(_FOCAL_LENGTH))
    if focal:
        data['Distância focal'] = f'{focal:g} mm'
    focal35 = _number(ifd.get(_FOCAL_35MM))
    if focal35:
        data['Equivalente a 35 mm'] = f'{focal35:g} mm'
    return data


def _exposure(ifd):
    data = {}
    iso = _number(ifd.get(_ISO))
    if iso:
        data['ISO'] = f'{iso:g}'
    aperture = _number(ifd.get(_F_NUMBER))
    if aperture:
        data['Abertura'] = f'f/{aperture:g}'
    shutter = _number(ifd.get(_EXPOSURE_TIME))
    if shutter:
        data['Tempo de exposição'] = (
            f'1/{round(1 / shutter):g} s' if shutter < 1 else f'{shutter:g} s')
    bias = _number(ifd.get(_EXPOSURE_BIAS))
    if bias:
        data['Compensação'] = f'{bias:+g} EV'
    flash = ifd.get(_FLASH)
    if flash is not None:
        try:
            data['Flash'] = 'disparou' if int(flash) & 1 else 'não disparou'
        except (TypeError, ValueError):
            pass
    return data


def _gps(exif):
    try:
        gps = exif.get_ifd(_GPS_IFD)
    except Exception:
        return {}
    if not gps:
        return {}
    lat = _dms_to_degrees(gps.get(2), gps.get(1))
    lon = _dms_to_degrees(gps.get(4), gps.get(3))
    if lat is None or lon is None:
        return {}
    data = {'Coordenadas': f'{lat:.6f}, {lon:.6f}'}
    altitude = _number(gps.get(6))
    if altitude:
        data['Altitude'] = f'{altitude:.0f} m'
    # Valores começando com http viram link no template.
    data['Mapa'] = f'https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=15'
    return data


def _image_info(img, image_file):
    data = {'Dimensões': f'{img.width} × {img.height} px'}
    if img.format:
        data['Formato'] = img.format
    size = getattr(image_file, 'size', None)
    if size:
        data['Tamanho'] = (f'{size / 1e6:.1f} MB' if size >= 1e6
                           else f'{max(size // 1024, 1)} KB')
    return data


def extract_metadata(image_file, fields):
    """Lê da imagem apenas os metadados habilitados em `fields`.

    Devolve ``{chave: {rótulo: valor}}`` contendo só os grupos habilitados que
    de fato trouxeram alguma informação. A data (`taken_at`) não entra aqui:
    ela mora no campo próprio da foto, via :func:`extract_taken_at`.
    Nunca levanta exceção — no pior caso devolve ``{}``.
    """
    wanted = set(fields or [])
    wanted.discard('taken_at')
    if not wanted:
        return {}

    found = {}
    try:
        image_file.seek(0)
        with Image.open(image_file) as img:
            exif = img.getexif()
            try:
                ifd = exif.get_ifd(_EXIF_IFD) if exif else {}
            except Exception:
                ifd = {}
            if 'camera' in wanted:
                found['camera'] = _camera(exif, ifd)
            if 'lens' in wanted:
                found['lens'] = _lens(ifd)
            if 'exposure' in wanted:
                found['exposure'] = _exposure(ifd)
            if 'gps' in wanted and exif:
                found['gps'] = _gps(exif)
            if 'image' in wanted:
                found['image'] = _image_info(img, image_file)
            if 'software' in wanted:
                software = _text(exif.get(_SOFTWARE)) if exif else ''
                found['software'] = {'Software': software} if software else {}
    except Exception:
        return {}
    finally:
        try:
            image_file.seek(0)
        except Exception:
            pass

    # Grupos vazios (a foto não trazia aquele dado) não são guardados.
    return {key: value for key, value in found.items() if value}
