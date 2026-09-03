import io
import shutil
import tempfile
from datetime import date, datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import Memo, Photo
from .utils import METADATA_KEYS, MAX_SIDE, extract_metadata, extract_taken_at

MEDIA_ROOT = tempfile.mkdtemp()


def make_jpeg_with_exif(dt_str='2024:05:01 13:45:30', color=(200, 90, 130)):
    """Gera bytes de um JPEG com a tag EXIF DateTime preenchida."""
    img = Image.new('RGB', (32, 48), color)
    exif = img.getexif()
    exif[306] = dt_str  # DateTime (base IFD)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    return buf.getvalue()


# O que o navegador envia quando todas as caixas de metadados estão marcadas.
ALL_METADATA = list(METADATA_KEYS)


def make_jpeg_with_full_exif(dt_str='2024:05:01 13:45:30'):
    """JPEG com EXIF variado: câmera, lente, exposição, GPS e software."""
    img = Image.new('RGB', (64, 48), (10, 20, 30))
    exif = Image.Exif()
    exif[271] = 'Canon'                 # Make
    exif[272] = 'Canon EOS R6'          # Model
    exif[305] = 'Darktable 4.6'         # Software
    exif[0x8769] = {                    # bloco Exif
        36867: dt_str,                  # DateTimeOriginal
        0x8827: 400,                    # ISO
        0x829D: 2.8,                    # FNumber
        0x829A: 0.008,                  # ExposureTime
        0x920A: 50.0,                   # FocalLength
        0xA434: 'RF50mm F1.8 STM',      # LensModel
    }
    exif[0x8825] = {                    # bloco GPS (Rio de Janeiro)
        1: 'S', 2: (22.0, 54.0, 24.0),
        3: 'W', 4: (43.0, 10.0, 20.0),
    }
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    return buf.getvalue()


def make_heic_with_exif(dt_str='2017:08:21 16:45:00', color=(120, 60, 90)):
    """Gera bytes de um HEIC (formato do iPhone) com DateTimeOriginal no sub-IFD."""
    img = Image.new('RGB', (48, 48), color)
    exif = Image.Exif()
    exif[0x8769] = {36867: dt_str}  # DateTimeOriginal
    buf = io.BytesIO()
    img.save(buf, format='HEIF', exif=exif)
    return buf.getvalue()


def make_big_jpeg(side=3200, color=(30, 140, 90)):
    """JPEG grande e ruidoso — o suficiente para a compressão ter o que cortar."""
    import random
    rnd = random.Random(7)
    img = Image.new('RGB', (side, side), color)
    px = img.load()
    for x in range(0, side, 4):
        for y in range(0, side, 4):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class MemoFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user('caio', password='senha-forte-123')
        self.client.force_login(self.user)

    def test_extract_taken_at_reads_exif(self):
        dt = extract_taken_at(io.BytesIO(make_jpeg_with_exif('2023:12:25 08:00:00')))
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour), (2023, 12, 25, 8))

    def test_zero_date_exif_is_ignored(self):
        # Câmera sem relógio configurado grava '0000:00:00 00:00:00'.
        self.assertIsNone(extract_taken_at(io.BytesIO(make_jpeg_with_exif('0000:00:00 00:00:00'))))

    def test_photo_edit_updates_date(self):
        photo = Photo.objects.create(owner=self.user, name='Lumix',
                                     image='photos/x.jpg', taken_at=timezone.now())
        url = reverse('core:photo_edit', args=[photo.pk])
        resp = self.client.post(url, {
            'name': 'Lumix',
            'taken_at': '2015-09-10T14:20',
            'description': '',
        })
        self.assertRedirects(resp, photo.get_absolute_url())
        photo.refresh_from_db()
        self.assertEqual((photo.taken_at.year, photo.taken_at.month, photo.taken_at.day),
                         (2015, 9, 10))

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('core:memo_home'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_create_memo(self):
        resp = self.client.post(reverse('core:memo_create'),
                                {'name': 'Viagem', 'description': 'praia'})
        memo = Memo.objects.get(name='Viagem')
        self.assertEqual(memo.owner, self.user)
        self.assertRedirects(resp, memo.get_absolute_url())

    def test_upload_uses_exif_date(self):
        memo = Memo.objects.create(owner=self.user, name='Memórias')
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_exif('2022:07:04 19:30:00'),
                                    content_type='image/jpeg')
        resp = self.client.post(reverse('core:photo_upload'), {
            'name': 'Pôr do sol',
            'description': 'lindo',
            'memo': memo.pk,
            'image': upload,
            'metadata_fields': ALL_METADATA,
        })
        photo = Photo.objects.get(name='Pôr do sol')
        self.assertRedirects(resp, photo.get_absolute_url())
        self.assertEqual(photo.owner, self.user)
        self.assertEqual(photo.memo, memo)
        self.assertEqual((photo.taken_at.year, photo.taken_at.month, photo.taken_at.day),
                         (2022, 7, 4))

    def test_heic_upload_reads_exif_and_converts_to_jpeg(self):
        upload = SimpleUploadedFile('IMG_0001.HEIC', make_heic_with_exif('2017:08:21 16:45:00'),
                                    content_type='image/heic')
        resp = self.client.post(reverse('core:photo_upload'), {
            'name': 'iPhone',
            'image': upload,
            'metadata_fields': ALL_METADATA,
        })
        photo = Photo.objects.get(name='iPhone')
        self.assertRedirects(resp, photo.get_absolute_url())
        # Data veio do EXIF do HEIC, não de "hoje".
        self.assertEqual((photo.taken_at.year, photo.taken_at.month, photo.taken_at.day),
                         (2017, 8, 21))
        # E o arquivo foi convertido para JPEG para exibir no navegador.
        self.assertTrue(photo.image.name.lower().endswith('.jpg'))

    def test_falls_back_to_file_modified_when_no_exif(self):
        # Foto sem data válida no EXIF + data de modificação enviada pelo navegador.
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_exif('0000:00:00 00:00:00'),
                                    content_type='image/jpeg')
        # 10/09/2015 12:00:00 UTC em milissegundos.
        ms = 1441886400000
        self.client.post(reverse('core:photo_upload'), {
            'name': 'Lumix',
            'image': upload,
            'file_modified': ms,
            'metadata_fields': ALL_METADATA,
        })
        photo = Photo.objects.get(name='Lumix')
        self.assertEqual((photo.taken_at.year, photo.taken_at.month, photo.taken_at.day),
                         (2015, 9, 10))

    def test_upload_ignores_posted_date_uses_exif(self):
        # O formulário de criação não tem campo de data: mesmo que algo seja
        # enviado, a data vem do EXIF (ajuste manual só na edição).
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_exif('2022:07:04 19:30:00'),
                                    content_type='image/jpeg')
        self.client.post(reverse('core:photo_upload'), {
            'name': 'SemCampoData',
            'taken_at': '2020-01-15T10:00',
            'image': upload,
            'metadata_fields': ALL_METADATA,
        })
        photo = Photo.objects.get(name='SemCampoData')
        self.assertEqual((photo.taken_at.year, photo.taken_at.month, photo.taken_at.day),
                         (2022, 7, 4))

    def test_photo_delete(self):
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_exif(), content_type='image/jpeg')
        self.client.post(reverse('core:photo_upload'), {'name': 'X', 'image': upload})
        photo = Photo.objects.get(name='X')
        resp = self.client.post(reverse('core:photo_delete', args=[photo.pk]))
        self.assertRedirects(resp, reverse('core:timeline'))
        self.assertFalse(Photo.objects.filter(pk=photo.pk).exists())

    def test_memo_edit(self):
        memo = Memo.objects.create(owner=self.user, name='Antigo')
        self.client.post(reverse('core:memo_edit', args=[memo.pk]),
                         {'name': 'Novo nome', 'description': 'desc'})
        memo.refresh_from_db()
        self.assertEqual(memo.name, 'Novo nome')

    def test_memo_delete_keeps_photos(self):
        memo = Memo.objects.create(owner=self.user, name='A')
        photo = Photo.objects.create(owner=self.user, memo=memo, name='p',
                                     image='photos/x.jpg', taken_at=timezone.now())
        resp = self.client.post(reverse('core:memo_delete', args=[memo.pk]))
        self.assertRedirects(resp, reverse('core:memo_home'))
        self.assertFalse(Memo.objects.filter(pk=memo.pk).exists())
        photo.refresh_from_db()
        self.assertIsNone(photo.memo)

    def test_memo_delete_with_photos(self):
        memo = Memo.objects.create(owner=self.user, name='A')
        photo = Photo.objects.create(owner=self.user, memo=memo, name='p',
                                     image='photos/x.jpg', taken_at=timezone.now())
        resp = self.client.post(reverse('core:memo_delete', args=[memo.pk]),
                                {'delete_photos': '1'})
        self.assertRedirects(resp, reverse('core:memo_home'))
        self.assertFalse(Memo.objects.filter(pk=memo.pk).exists())
        self.assertFalse(Photo.objects.filter(pk=photo.pk).exists())

    def test_upload_compresses_when_asked(self):
        raw = make_big_jpeg()
        upload = SimpleUploadedFile('grande.jpg', raw, content_type='image/jpeg')
        self.client.post(reverse('core:photo_upload'), {
            'name': 'Comprimida',
            'image': upload,
            'compress': 'on',
        })
        photo = Photo.objects.get(name='Comprimida')
        self.assertLess(photo.image.size, len(raw))
        with Image.open(photo.image) as img:
            self.assertLessEqual(max(img.size), MAX_SIDE)

    def test_upload_keeps_original_when_compression_is_off(self):
        raw = make_big_jpeg()
        upload = SimpleUploadedFile('grande.jpg', raw, content_type='image/jpeg')
        # Caixa desmarcada não vai no POST — é assim que o navegador envia.
        self.client.post(reverse('core:photo_upload'), {
            'name': 'Original',
            'image': upload,
        })
        photo = Photo.objects.get(name='Original')
        self.assertEqual(photo.image.size, len(raw))
        with Image.open(photo.image) as img:
            self.assertEqual(max(img.size), 3200)

    def test_heic_is_converted_even_without_compression(self):
        # Sem compressão, o HEIC ainda precisa virar JPEG para o navegador exibir.
        upload = SimpleUploadedFile('IMG_0002.HEIC', make_heic_with_exif(),
                                    content_type='image/heic')
        self.client.post(reverse('core:photo_upload'), {'name': 'HeicSemCompressao',
                                                        'image': upload})
        photo = Photo.objects.get(name='HeicSemCompressao')
        self.assertTrue(photo.image.name.lower().endswith('.jpg'))

    def test_memo_carries_compression_default(self):
        # O padrão do memo vai para o <select> como data-compress, e o
        # JavaScript da tela de envio pré-marca a caixa a partir dele.
        memo = Memo.objects.create(owner=self.user, name='Originais',
                                   compress_photos=False)
        html = self.client.get(reverse('core:photo_upload')).content.decode()
        self.assertIn(f'data-compress="0"', html)
        self.assertIn('id_compress', html)
        self.client.post(reverse('core:memo_edit', args=[memo.pk]),
                         {'name': 'Originais', 'description': '', 'compress_photos': 'on'})
        memo.refresh_from_db()
        self.assertTrue(memo.compress_photos)

    def test_create_memo_without_compression_default(self):
        self.client.post(reverse('core:memo_create'),
                         {'name': 'Brutos', 'description': ''})
        self.assertFalse(Memo.objects.get(name='Brutos').compress_photos)

    def test_advanced_options_live_inside_the_modal(self):
        """As opções finas saem da tela principal e vão para o <dialog>."""
        memo = Memo.objects.create(owner=self.user, name='Viagem')
        photo = Photo.objects.create(owner=self.user, name='p', image='photos/x.jpg',
                                     taken_at=timezone.now())
        paginas = [
            (reverse('core:photo_upload'), ['id_compress', 'data-metadata-toggle']),
            (reverse('core:memo_edit', args=[memo.pk]),
             ['id_compress_photos', 'data-metadata-toggle']),
            (reverse('core:photo_edit', args=[photo.pk]), ['data-metadata-toggle']),
        ]
        for url, esperados in paginas:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('data-settings-open', html)  # botão que abre
                dialog = html.split('<dialog', 1)[1].split('</dialog>', 1)[0]
                antes = html.split('<dialog', 1)[0]
                for marca in esperados:
                    # Dentro do modal e fora da tela principal do formulário.
                    self.assertIn(marca, dialog)
                    self.assertNotIn(marca, antes)
                # O <dialog> precisa estar dentro do <form>: é o que faz os
                # campos serem enviados mesmo com o modal fechado. Entre a
                # última abertura de <form> e o modal não pode haver </form>.
                self.assertNotIn('</form>', antes.rsplit('<form', 1)[1])

    def test_options_still_submit_from_the_modal(self):
        # Fechado ou aberto, o modal é parte do formulário: o POST não muda.
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_exif(),
                                    content_type='image/jpeg')
        self.client.post(reverse('core:photo_upload'), {
            'name': 'ViaModal',
            'image': upload,
            'compress': 'on',
            'metadata_fields': ['taken_at'],
        })
        photo = Photo.objects.get(name='ViaModal')
        self.assertEqual(photo.metadata_fields, ['taken_at'])

    def test_runserver_listens_on_the_network(self):
        """O runserver do projeto substitui o do Django e abre para a rede."""
        from django.core.management import get_commands, load_command_class
        # Depende de `core` vir antes do staticfiles no INSTALLED_APPS.
        self.assertEqual(get_commands()['runserver'], 'core')
        self.assertEqual(load_command_class('core', 'runserver').default_addr,
                         '0.0.0.0')

    def test_pages_render(self):
        memo = Memo.objects.create(owner=self.user, name='X')
        photo = Photo.objects.create(owner=self.user, memo=memo, name='p',
                                     image='photos/none.jpg', taken_at=timezone.now())
        for name, args in [
            ('core:memo_home', []),
            ('core:timeline', []),
            ('core:photo_upload', []),
            ('core:memo_create', []),
            ('core:memo_detail', [memo.pk]),
            ('core:memo_edit', [memo.pk]),
            ('core:photo_edit', [photo.pk]),
        ]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_timeline_groups_by_month(self):
        from datetime import datetime
        from django.utils.formats import date_format
        d1 = timezone.make_aware(datetime(2015, 9, 10, 12, 0))
        d2 = timezone.make_aware(datetime(2026, 1, 5, 9, 0))
        Photo.objects.create(owner=self.user, name='antiga', image='photos/a.jpg', taken_at=d1)
        Photo.objects.create(owner=self.user, name='nova', image='photos/b.jpg', taken_at=d2)
        resp = self.client.get(reverse('core:timeline'))
        body = resp.content.decode()
        label_old = date_format(timezone.localtime(d1), r'F \d\e Y')
        label_new = date_format(timezone.localtime(d2), r'F \d\e Y')
        self.assertContains(resp, label_old)
        self.assertContains(resp, label_new)
        # Grupo mais recente aparece antes do mais antigo.
        self.assertLess(body.index(label_new), body.index(label_old))

    def test_memos_are_private(self):
        # Multi-tenant: memo de outro usuário não é visível nem acessível.
        other = User.objects.create_user('outro', password='x')
        memo = Memo.objects.create(owner=other, name='Particular')
        # Não acessível por URL direta.
        self.assertEqual(self.client.get(memo.get_absolute_url()).status_code, 404)
        # Não aparece na home do usuário logado.
        self.assertNotContains(self.client.get(reverse('core:memo_home')), 'Particular')

    def test_photos_are_private(self):
        # Foto de outro usuário não é acessível nem aparece na timeline.
        other = User.objects.create_user('outro2', password='x')
        photo = Photo.objects.create(owner=other, name='Secreta',
                                     image='photos/x.jpg', taken_at=timezone.now())
        self.assertEqual(self.client.get(photo.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('core:timeline')), 'Secreta')

    def test_upload_memo_choices_limited_to_owner(self):
        # No upload, só os memos do próprio usuário aparecem como opção.
        other = User.objects.create_user('outro3', password='x')
        Memo.objects.create(owner=other, name='AlheioOption')
        Memo.objects.create(owner=self.user, name='MeuOption')
        body = self.client.get(reverse('core:photo_upload')).content.decode()
        self.assertIn('MeuOption', body)
        self.assertNotIn('AlheioOption', body)

    # --- Compartilhamento -------------------------------------------------

    def test_shared_view_memo_is_visible_not_editable(self):
        owner = User.objects.create_user('dono', password='x')
        memo = Memo.objects.create(owner=owner, name='Viagem')
        memo.shared_view.add(self.user)
        photo = Photo.objects.create(owner=owner, memo=memo, name='praia',
                                     image='photos/x.jpg', taken_at=timezone.now())
        # Pode ver o memo, a foto e aparece na home/timeline.
        self.assertEqual(self.client.get(memo.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(photo.get_absolute_url()).status_code, 200)
        self.assertContains(self.client.get(reverse('core:memo_home')), 'Viagem')
        self.assertContains(self.client.get(reverse('core:timeline')), 'praia')
        # Mas não pode editar nem excluir.
        self.assertEqual(self.client.get(reverse('core:memo_edit', args=[memo.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('core:photo_edit', args=[photo.pk])).status_code, 404)
        resp = self.client.post(reverse('core:memo_delete', args=[memo.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_shared_edit_memo_is_editable(self):
        owner = User.objects.create_user('dono2', password='x')
        memo = Memo.objects.create(owner=owner, name='Casa')
        memo.shared_edit.add(self.user)
        # Quem pode editar abre a edição e salva.
        self.assertEqual(self.client.get(reverse('core:memo_edit', args=[memo.pk])).status_code, 200)
        self.client.post(reverse('core:memo_edit', args=[memo.pk]),
                         {'name': 'Casa nova', 'description': ''})
        memo.refresh_from_db()
        self.assertEqual(memo.name, 'Casa nova')
        # Mas não pode excluir (exclusivo do dono).
        self.assertEqual(self.client.post(reverse('core:memo_delete', args=[memo.pk])).status_code, 404)

    def test_shared_photo_is_view_only(self):
        owner = User.objects.create_user('dono3', password='x')
        photo = Photo.objects.create(owner=owner, name='retrato',
                                     image='photos/x.jpg', taken_at=timezone.now())
        photo.shared_with.add(self.user)
        # Visível...
        self.assertEqual(self.client.get(photo.get_absolute_url()).status_code, 200)
        self.assertContains(self.client.get(reverse('core:timeline')), 'retrato')
        # ...mas não editável nem removível.
        self.assertEqual(self.client.get(reverse('core:photo_edit', args=[photo.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('core:photo_delete', args=[photo.pk])).status_code, 404)

    def test_owner_can_add_and_remove_memo_share(self):
        memo = Memo.objects.create(owner=self.user, name='Meu')
        friend = User.objects.create_user('amiga', password='x')
        resp = self.client.post(reverse('core:memo_share', args=[memo.pk]),
                                {'action': 'add', 'role': 'edit', 'user': friend.pk})
        self.assertRedirects(resp, memo.get_absolute_url())
        self.assertTrue(memo.can_edit(friend))
        # Remover devolve o acesso.
        self.client.post(reverse('core:memo_share', args=[memo.pk]),
                         {'action': 'remove', 'role': 'edit', 'user': friend.pk})
        self.assertFalse(memo.can_view(friend))

    def test_non_owner_cannot_share_memo(self):
        owner = User.objects.create_user('dono4', password='x')
        memo = Memo.objects.create(owner=owner, name='Alheio')
        memo.shared_edit.add(self.user)  # mesmo podendo editar, não re-compartilha
        friend = User.objects.create_user('amiga2', password='x')
        resp = self.client.post(reverse('core:memo_share', args=[memo.pk]),
                                {'action': 'add', 'role': 'view', 'user': friend.pk})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(memo.can_view(friend))

    def test_owner_can_share_photo(self):
        photo = Photo.objects.create(owner=self.user, name='minha',
                                     image='photos/x.jpg', taken_at=timezone.now())
        friend = User.objects.create_user('amiga3', password='x')
        resp = self.client.post(reverse('core:photo_share', args=[photo.pk]),
                                {'action': 'add', 'user': friend.pk})
        self.assertRedirects(resp, photo.get_absolute_url())
        self.assertTrue(photo.can_view(friend))

    def test_memo_share_add_edit_supersedes_view(self):
        # Adicionar como "editar" tira o usuário de "visualizar" (sem duplicar).
        memo = Memo.objects.create(owner=self.user, name='Dedup')
        friend = User.objects.create_user('amiga4', password='x')
        self.client.post(reverse('core:memo_share', args=[memo.pk]),
                         {'action': 'add', 'role': 'view', 'user': friend.pk})
        self.client.post(reverse('core:memo_share', args=[memo.pk]),
                         {'action': 'add', 'role': 'edit', 'user': friend.pk})
        self.assertFalse(memo.shared_view.filter(pk=friend.pk).exists())
        self.assertTrue(memo.shared_edit.filter(pk=friend.pk).exists())

    def test_user_search(self):
        User.objects.create_user('mariana', password='x')
        User.objects.create_user('marina', password='x')
        User.objects.create_user('joao', password='x')
        resp = self.client.get(reverse('core:user_search'), {'q': 'mar'})
        names = {r['username'] for r in resp.json()['results']}
        self.assertEqual(names, {'mariana', 'marina'})
        # Não retorna o próprio usuário nem exige correspondência vazia.
        self.assertEqual(self.client.get(reverse('core:user_search'),
                                         {'q': 'caio'}).json()['results'], [])
        self.assertEqual(self.client.get(reverse('core:user_search')).json()['results'], [])


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class HomeHeroTests(TestCase):
    """A entrada é pública e mostra números vindos do banco, não fixos no template."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user('caio', password='senha-forte-123')

    def _photo(self, owner, name, taken_at, memo=None):
        return Photo.objects.create(owner=owner, name=name, image='photos/x.jpg',
                                    taken_at=taken_at, memo=memo)

    def test_home_is_public(self):
        resp = self.client.get(reverse('core:home'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'core/home.html')

    def test_stats_come_from_the_real_photos(self):
        memo = Memo.objects.create(owner=self.user, name='Viagem')
        tz = timezone.get_current_timezone()
        # Três fotos em dois meses distintos: jan/2024 (duas) e mar/2024 (uma).
        self._photo(self.user, 'a', datetime(2024, 1, 10, 12, tzinfo=tz), memo)
        self._photo(self.user, 'b', datetime(2024, 1, 20, 12, tzinfo=tz), memo)
        self._photo(self.user, 'c', datetime(2024, 3, 10, 12, tzinfo=tz))

        self.client.force_login(self.user)
        ctx = self.client.get(reverse('core:home')).context

        self.assertEqual(ctx['total_photos'], 3)
        self.assertEqual(ctx['total_memos'], 1)
        self.assertEqual(ctx['total_months'], 2)
        self.assertEqual(ctx['first_photo_at'].date(), date(2024, 1, 10))
        self.assertEqual(ctx['last_photo_at'].date(), date(2024, 3, 10))
        self.assertEqual(ctx['span_days'], 60)
        self.assertEqual(len(ctx['mosaic']), 3)

    def test_logged_user_only_counts_what_they_can_see(self):
        tz = timezone.get_current_timezone()
        other = User.objects.create_user('estranho', password='x')
        self._photo(other, 'alheia', datetime(2020, 5, 1, 12, tzinfo=tz))
        self._photo(self.user, 'minha', datetime(2024, 1, 10, 12, tzinfo=tz))

        self.client.force_login(self.user)
        ctx = self.client.get(reverse('core:home')).context
        self.assertEqual(ctx['total_photos'], 1)
        self.assertEqual(ctx['first_photo_at'].date(), date(2024, 1, 10))

    def test_shared_photo_is_not_counted_twice(self):
        # A foto chega por dois caminhos (compartilhada e via memo); conta uma vez.
        tz = timezone.get_current_timezone()
        other = User.objects.create_user('dono', password='x')
        memo = Memo.objects.create(owner=other, name='Nosso')
        memo.shared_view.add(self.user)
        photo = self._photo(other, 'dupla', datetime(2024, 2, 2, 12, tzinfo=tz), memo)
        photo.shared_with.add(self.user)

        self.client.force_login(self.user)
        ctx = self.client.get(reverse('core:home')).context
        self.assertEqual(ctx['total_photos'], 1)
        self.assertEqual(ctx['total_months'], 1)

    def test_anonymous_gets_counts_but_no_photos(self):
        tz = timezone.get_current_timezone()
        self._photo(self.user, 'privada', datetime(2024, 1, 10, 12, tzinfo=tz))

        ctx = self.client.get(reverse('core:home')).context
        self.assertEqual(ctx['total_photos'], 1)
        self.assertEqual(list(ctx['mosaic']), [])

    def test_empty_instance_renders(self):
        ctx = self.client.get(reverse('core:home')).context
        self.assertEqual(ctx['total_photos'], 0)
        self.assertEqual(ctx['total_months'], 0)
        self.assertEqual(ctx['span_days'], 0)
        self.assertIsNone(ctx['first_photo_at'])


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class MetadataTogglesTests(TestCase):
    """Habilitar/desabilitar cada metadado no memo, no envio e na edição."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user('caio', password='senha-forte-123')
        self.client.force_login(self.user)

    def upload(self, fields, name='Foto', **extra):
        """Envia uma foto com EXIF completo marcando só os metadados de `fields`."""
        upload = SimpleUploadedFile('foto.jpg', make_jpeg_with_full_exif(),
                                    content_type='image/jpeg')
        data = {'name': name, 'image': upload, 'metadata_fields': fields,
                'compress': ''}
        data.update(extra)
        self.client.post(reverse('core:photo_upload'), data)
        return Photo.objects.get(name=name)

    # ── leitura ──────────────────────────────────────────────────────────
    def test_extract_metadata_reads_each_group(self):
        found = extract_metadata(io.BytesIO(make_jpeg_with_full_exif()), ALL_METADATA)
        self.assertEqual(found['camera'], {'Fabricante': 'Canon', 'Modelo': 'EOS R6'})
        self.assertEqual(found['exposure']['ISO'], '400')
        self.assertEqual(found['exposure']['Abertura'], 'f/2.8')
        self.assertEqual(found['lens']['Lente'], 'RF50mm F1.8 STM')
        self.assertEqual(found['software'], {'Software': 'Darktable 4.6'})
        # Hemisférios S/W viram coordenadas negativas.
        self.assertEqual(found['gps']['Coordenadas'], '-22.906667, -43.172222')

    def test_extract_metadata_only_returns_enabled_groups(self):
        found = extract_metadata(io.BytesIO(make_jpeg_with_full_exif()), ['camera'])
        self.assertEqual(list(found), ['camera'])

    def test_extract_metadata_without_fields_reads_nothing(self):
        self.assertEqual(extract_metadata(io.BytesIO(make_jpeg_with_full_exif()), []), {})

    # ── envio ────────────────────────────────────────────────────────────
    def test_upload_stores_only_enabled_metadata(self):
        photo = self.upload(['taken_at', 'camera'], name='SóCâmera')
        self.assertEqual(photo.metadata_fields, ['taken_at', 'camera'])
        self.assertEqual(list(photo.metadata), ['camera'])
        # A data continua vindo do EXIF, porque o grupo dela está habilitado.
        self.assertEqual(photo.taken_at.year, 2024)

    def test_upload_with_all_metadata_disabled_stores_nothing(self):
        photo = self.upload([], name='SemMetadados')
        self.assertEqual(photo.metadata_fields, [])
        self.assertEqual(photo.metadata, {})

    def test_disabled_date_is_not_read_from_exif(self):
        # Sem o grupo taken_at, a data do EXIF (2024) é ignorada e entra a data
        # de modificação do arquivo mandada pelo navegador (10/09/2015).
        photo = self.upload(['camera'], name='SemData', file_modified=1441886400000)
        self.assertEqual((photo.taken_at.year, photo.taken_at.month), (2015, 9))
        self.assertFalse(photo.reads_date_from_metadata)

    def test_gps_is_only_stored_when_enabled(self):
        com_gps = self.upload(ALL_METADATA, name='ComGPS')
        self.assertIn('gps', com_gps.metadata)
        sem_gps = self.upload([k for k in ALL_METADATA if k != 'gps'], name='SemGPS')
        self.assertNotIn('gps', sem_gps.metadata)

    def test_image_group_describes_the_stored_file(self):
        photo = self.upload(['image'], name='Arquivo')
        self.assertEqual(photo.metadata['image']['Dimensões'], '64 × 48 px')
        self.assertIn('Tamanho', photo.metadata['image'])

    # ── padrão do memo ───────────────────────────────────────────────────
    def test_memo_create_saves_metadata_defaults(self):
        self.client.post(reverse('core:memo_create'), {
            'name': 'Viagem', 'description': '',
            'metadata_fields': ['taken_at', 'gps'],
        })
        memo = Memo.objects.get(name='Viagem')
        self.assertEqual(memo.metadata_fields, ['taken_at', 'gps'])

    def test_memo_defaults_to_all_metadata(self):
        self.client.post(reverse('core:memo_create'),
                         {'name': 'Padrão', 'description': '',
                          'metadata_fields': ALL_METADATA})
        self.assertEqual(Memo.objects.get(name='Padrão').metadata_fields, ALL_METADATA)

    def test_upload_page_carries_each_memo_default(self):
        memo = Memo.objects.create(owner=self.user, name='Só data',
                                   metadata_fields=['taken_at'])
        html = self.client.get(reverse('core:photo_upload')).content.decode()
        # A opção do memo leva o padrão para o JavaScript pré-marcar as caixas.
        self.assertIn(f'value="{memo.pk}"', html)
        self.assertIn('data-metadata=', html)
        self.assertIn('taken_at', html)

    def test_upload_form_offers_every_metadata(self):
        html = self.client.get(reverse('core:photo_upload')).content.decode()
        for key in ALL_METADATA:
            self.assertIn(f'value="{key}"', html)

    # ── edição ───────────────────────────────────────────────────────────
    def test_edit_removes_disabled_metadata(self):
        photo = self.upload(ALL_METADATA, name='Completa')
        self.assertIn('gps', photo.metadata)
        self.client.post(reverse('core:photo_edit', args=[photo.pk]), {
            'name': photo.name,
            'taken_at': timezone.localtime(photo.taken_at).strftime('%Y-%m-%dT%H:%M'),
            'description': '',
            'memo': '',
            'metadata_fields': ['camera'],
        })
        photo.refresh_from_db()
        self.assertEqual(photo.metadata_fields, ['camera'])
        self.assertEqual(list(photo.metadata), ['camera'])

    def test_edit_recovers_metadata_still_in_the_file(self):
        # Enviada sem compressão e sem o grupo GPS: o EXIF continua no arquivo,
        # então habilitar o grupo depois traz o dado de volta.
        photo = self.upload(['taken_at'], name='Recuperável')
        self.assertNotIn('gps', photo.metadata)
        self.client.post(reverse('core:photo_edit', args=[photo.pk]), {
            'name': photo.name,
            'taken_at': timezone.localtime(photo.taken_at).strftime('%Y-%m-%dT%H:%M'),
            'description': '',
            'memo': '',
            'metadata_fields': ['taken_at', 'gps'],
        })
        photo.refresh_from_db()
        self.assertIn('gps', photo.metadata)

    # ── exibição ─────────────────────────────────────────────────────────
    def test_detail_shows_only_enabled_metadata(self):
        photo = self.upload(['camera'], name='Detalhe')
        html = self.client.get(photo.get_absolute_url()).content.decode()
        self.assertIn('Canon', html)
        self.assertNotIn('Darktable', html)

    def test_detail_has_no_metadata_card_when_all_disabled(self):
        photo = self.upload([], name='Nada')
        html = self.client.get(photo.get_absolute_url()).content.decode()
        # O nome da classe também aparece no CSS: procura o bloco renderizado.
        self.assertNotIn('<h2 class="metadata-title">', html)
