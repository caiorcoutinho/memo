from itertools import groupby

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import TruncMonth
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format

from .forms import MemoForm, PhotoEditForm, PhotoForm
from .models import (SHARE_EXPIRY_OPTIONS, Memo, Photo, ShareLink,
                     expiry_from_key)
from .utils import (METADATA_LABELS, compress_image, datetime_from_epoch_ms,
                    extract_metadata, extract_taken_at, to_web_jpeg)


def _humanize_bytes(size):
    """Tamanho de arquivo em unidade legível (para as mensagens de upload)."""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def _share_url(obj):
    """Volta para a página do memo/foto com o modal de compartilhamento aberto."""
    return f'{obj.get_absolute_url()}?share=1'


def _handle_share_link(request, **target):
    """Cria ou revoga o link público de um memo/foto a partir do modal.

    Recebe o alvo já validado como ``memo=`` ou ``photo=``. Devolve True quando
    a ação era de link, para a view não seguir tratando como compartilhamento
    por usuário.
    """
    action = request.POST.get('action')
    if action == 'link_create':
        ShareLink.objects.create(
            created_by=request.user,
            expires_at=expiry_from_key(request.POST.get('expires')),
            **target)
        return True
    if action == 'link_remove':
        # O filtro pelo alvo impede revogar o link de outra pessoa pelo id.
        ShareLink.objects.filter(pk=request.POST.get('link') or 0, **target).delete()
        return True
    return False


def _visible_memos(user):
    """Memos que o usuário pode ver: os seus + os compartilhados com ele."""
    return Memo.objects.filter(
        Q(owner=user) | Q(shared_view=user) | Q(shared_edit=user)).distinct()


def _visible_photos(user):
    """Fotos que o usuário pode ver: as suas + compartilhadas (avulsas ou via memo)."""
    return Photo.objects.filter(
        Q(owner=user) | Q(shared_with=user)
        | Q(memo__shared_view=user) | Q(memo__shared_edit=user)).distinct()


def _acervo_stats(photos, memos):
    """Números reais do acervo, tirados dos metadados já guardados no banco.

    Nada aqui é fixo no template: contagens, primeira e última foto e a
    quantidade de meses vêm de agregações sobre as fotos visíveis.
    """
    agg = photos.aggregate(
        total=Count('id', distinct=True),
        first=Min('taken_at'),
        last=Max('taken_at'),
    )
    # order_by() limpa o ordering padrão do model — sem isso o Django incluiria
    # taken_at no SELECT e o distinct() contaria cada foto como um mês.
    months = (photos.annotate(month=TruncMonth('taken_at'))
              .order_by().values('month').distinct().count())
    first, last = agg['first'], agg['last']
    return {
        'total_photos': agg['total'],
        'total_memos': memos.distinct().count(),
        'total_months': months,
        'first_photo_at': timezone.localtime(first) if first else None,
        'last_photo_at': timezone.localtime(last) if last else None,
        'span_days': (last - first).days if first and last else 0,
    }


def home(request):
    """Hero de entrada: o que é o memo, contado com os números do próprio acervo.

    Visitante anônimo vê os totais da instância (só números, nenhuma foto);
    quem está logado vê os seus próprios e uma amostra das fotos recentes.
    """
    if request.user.is_authenticated:
        photos = _visible_photos(request.user)
        memos = _visible_memos(request.user)
    else:
        photos = Photo.objects.all()
        memos = Memo.objects.all()

    context = _acervo_stats(photos, memos)
    context['mosaic'] = photos[:8] if request.user.is_authenticated else []
    return render(request, 'core/home.html', context)


@login_required
def memo_home(request):
    # Cada usuário vê seus memos/fotos e também os compartilhados com ele.
    memos = _visible_memos(request.user).prefetch_related('photos')
    recent_photos = _visible_photos(request.user)[:12]
    return render(request, 'memo/home.html', {
        'memos': memos,
        'recent_photos': recent_photos,
    })


@login_required
def timeline(request):
    """Fotos visíveis em ordem cronológica, agrupadas por mês/ano."""
    photos = _visible_photos(request.user).select_related('memo')

    def month_key(photo):
        local = timezone.localtime(photo.taken_at)
        return local.year, local.month

    groups = []
    # As fotos já vêm ordenadas por -taken_at, então meses iguais ficam juntos.
    for _, items in groupby(photos, key=month_key):
        items = list(items)
        label = date_format(timezone.localtime(items[0].taken_at), r'F \d\e Y')
        groups.append({'label': label, 'photos': items})

    return render(request, 'memo/timeline.html', {'groups': groups})


@login_required
def memo_detail(request, pk):
    memo = get_object_or_404(Memo, pk=pk)
    if not memo.can_view(request.user):
        raise Http404
    is_owner = memo.owner_id == request.user.pk
    return render(request, 'memo/detail.html', {
        'memo': memo,
        'photos': memo.photos.all(),
        'can_edit': memo.can_edit(request.user),
        'is_owner': is_owner,
        # Resumo mostrado no botão de compartilhar, sem abrir o modal.
        'share_people': memo.shared_view.count() + memo.shared_edit.count(),
        'expiry_options': SHARE_EXPIRY_OPTIONS,
    })


@login_required
def memo_edit(request, pk):
    memo = get_object_or_404(Memo, pk=pk)
    if not memo.can_edit(request.user):
        raise Http404
    is_owner = memo.owner_id == request.user.pk
    if request.method == 'POST':
        form = MemoForm(request.POST, instance=memo)
        if form.is_valid():
            form.save()
            messages.success(request, 'Memo atualizado.')
            return redirect(memo)
    else:
        form = MemoForm(instance=memo)
    return render(request, 'memo/memo_form.html', {
        'form': form,
        'memo': memo,
        'is_owner': is_owner,
        'share_people': memo.shared_view.count() + memo.shared_edit.count(),
        'expiry_options': SHARE_EXPIRY_OPTIONS,
    })


@login_required
def memo_share(request, pk):
    # Só o dono define o compartilhamento do memo.
    memo = get_object_or_404(Memo, pk=pk, owner=request.user)
    if request.method == 'POST':
        if _handle_share_link(request, memo=memo):
            return redirect(_share_url(memo))
        action = request.POST.get('action')
        role = request.POST.get('role')
        rel = {'view': memo.shared_view, 'edit': memo.shared_edit}.get(role)
        target = (User.objects.exclude(pk=request.user.pk)
                  .filter(pk=request.POST.get('user') or 0).first())
        if target and rel is not None:
            if action == 'add':
                # "Editar" implica "ver": mantém o usuário em apenas um dos grupos.
                other = memo.shared_view if role == 'edit' else memo.shared_edit
                other.remove(target)
                rel.add(target)
                verbo = 'editar' if role == 'edit' else 'visualizar'
                messages.success(request, f'{target.username} agora pode {verbo} o memo.')
            elif action == 'remove':
                rel.remove(target)
                messages.success(request, f'{target.username} não tem mais acesso ao memo.')
    return redirect(_share_url(memo))


@login_required
def memo_delete(request, pk):
    # Excluir o memo é exclusivo do dono.
    memo = get_object_or_404(Memo, pk=pk, owner=request.user)
    if request.method == 'POST':
        if request.POST.get('delete_photos'):
            # Apaga também as fotos (e seus arquivos do storage).
            for photo in memo.photos.all():
                photo.image.delete(save=False)
                photo.delete()
            memo.delete()
            messages.success(request, 'Memo e suas fotos foram excluídos.')
        else:
            # Mantém as fotos (memo = NULL), só o memo é removido.
            memo.delete()
            messages.success(request, 'Memo excluído. As fotos foram mantidas (sem memo).')
        return redirect('core:memo_home')
    return redirect(memo)


@login_required
def memo_create(request):
    if request.method == 'POST':
        form = MemoForm(request.POST)
        if form.is_valid():
            memo = form.save(commit=False)
            memo.owner = request.user
            memo.save()
            return redirect(memo)
    else:
        form = MemoForm()
    return render(request, 'memo/memo_form.html', {'form': form})


@login_required
def photo_upload(request):
    if request.method == 'POST':
        form = PhotoForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.owner = request.user
            # Metadados: só os grupos que o usuário deixou habilitados são lidos
            # do arquivo — o resto nem chega ao banco. A leitura acontece antes
            # da conversão/compressão, que descartam o EXIF.
            fields = form.cleaned_data.get('metadata_fields') or []
            found = extract_metadata(photo.image, fields)
            reads_date = 'taken_at' in fields
            # Data: EXIF -> data de modificação do arquivo (navegador) -> hoje.
            if not photo.taken_at:
                detected = extract_taken_at(photo.image) if reads_date else None
                file_dt = datetime_from_epoch_ms(form.cleaned_data.get('file_modified'))
                # Sem data no EXIF ou com a leitura desabilitada, o motivo muda.
                sem_data = ('A foto não tem data nos metadados (EXIF).' if reads_date
                            else 'Você desabilitou a data nos metadados desta foto.')
                if detected:
                    photo.taken_at = detected
                    messages.success(
                        request,
                        f'Data lida dos metadados da foto: {detected:%d/%m/%Y %H:%M}.')
                elif file_dt:
                    photo.taken_at = file_dt
                    local = timezone.localtime(file_dt)
                    messages.info(
                        request,
                        f'{sem_data} Usei a data de '
                        f'modificação do arquivo: {local:%d/%m/%Y %H:%M}. '
                        'Confira e ajuste em "editar" se precisar.')
                else:
                    photo.taken_at = timezone.now()
                    messages.warning(
                        request,
                        f'{sem_data} Usei a data de '
                        'hoje — você pode ajustar no campo "Data" ou em "editar".')
            # Compressão é opcional: quem envia decide foto a foto (o memo só
            # pré-marca a caixa). Sem compressão, o arquivo original é mantido —
            # só passa pela conversão quando o navegador não sabe exibi-lo.
            original_size = photo.image.size
            quer_comprimir = form.cleaned_data.get('compress')
            processed = None
            if quer_comprimir:
                processed = compress_image(photo.image, photo.image.name)
            if processed is None or processed[0] is None:
                # Converte HEIC e afins para JPEG para que apareçam no navegador.
                processed = to_web_jpeg(photo.image, photo.image.name)
            new_file, new_name = processed
            # Dimensões/formato/tamanho descrevem o arquivo que ficou guardado,
            # então esse grupo é lido do resultado final (comprimido ou não).
            found.update(extract_metadata(
                new_file if new_file is not None else photo.image,
                ['image'] if 'image' in fields else []))
            photo.set_metadata(fields, found)
            if new_file is not None:
                photo.image.save(new_name, new_file, save=False)
            photo.save()
            saved = original_size - photo.image.size
            # Só anuncia como compressão o que o usuário pediu: uma conversão
            # de HEIC também muda o tamanho, mas não é escolha dele.
            if quer_comprimir and saved > 0:
                messages.info(
                    request,
                    f'Imagem comprimida: {_humanize_bytes(original_size)} → '
                    f'{_humanize_bytes(photo.image.size)} '
                    f'({saved * 100 // original_size}% menor).')
            return redirect(photo)
    else:
        form = PhotoForm(user=request.user)
    return render(request, 'memo/upload.html', {'form': form})


@login_required
def photo_detail(request, pk):
    photo = get_object_or_404(Photo, pk=pk)
    if not photo.can_view(request.user):
        raise Http404
    is_owner = photo.owner_id == request.user.pk
    return render(request, 'memo/photo_detail.html', {
        'photo': photo,
        'can_edit': photo.can_edit(request.user),
        'is_owner': is_owner,
        # Crédito de quem compartilhou: só faz sentido para quem não é o dono.
        'shared_by': None if is_owner else photo.owner.username,
        'share_people': photo.shared_with.count(),
        'expiry_options': SHARE_EXPIRY_OPTIONS,
    })


@login_required
def photo_edit(request, pk):
    photo = get_object_or_404(Photo, pk=pk)
    if not photo.can_edit(request.user):
        raise Http404
    is_owner = photo.owner_id == request.user.pk
    if request.method == 'POST':
        form = PhotoEditForm(request.POST, instance=photo, user=request.user)
        if form.is_valid():
            photo = form.save(commit=False)
            fields = form.cleaned_data.get('metadata_fields') or []
            found = dict(photo.metadata or {})
            # Grupo habilitado agora que ainda não tem valor: tenta ler de novo
            # do arquivo guardado. Só volta se o EXIF sobreviveu (fotos enviadas
            # sem compressão) — o que foi comprimido perdeu o EXIF na origem.
            missing = [key for key in fields
                       if key != 'taken_at' and not found.get(key)]
            if missing:
                found.update(extract_metadata(photo.image, missing))
                perdidos = [METADATA_LABELS[key] for key in missing
                            if not found.get(key)]
                if perdidos:
                    messages.info(
                        request,
                        'Não encontrei no arquivo: ' + ', '.join(perdidos).lower()
                        + '. Esses dados não estavam na foto ou se perderam na '
                          'compressão.')
            # Descarta do banco tudo o que foi desabilitado.
            photo.set_metadata(fields, found)
            photo.save()
            messages.success(request, 'Foto atualizada.')
            return redirect(photo)
    else:
        form = PhotoEditForm(instance=photo, user=request.user)
    return render(request, 'memo/photo_form.html', {
        'form': form,
        'photo': photo,
        'is_owner': is_owner,
        'share_people': photo.shared_with.count(),
        'expiry_options': SHARE_EXPIRY_OPTIONS,
    })


@login_required
def photo_share(request, pk):
    # Só o dono compartilha a foto (sempre como somente leitura).
    photo = get_object_or_404(Photo, pk=pk, owner=request.user)
    if request.method == 'POST':
        if _handle_share_link(request, photo=photo):
            return redirect(_share_url(photo))
        action = request.POST.get('action')
        target = (User.objects.exclude(pk=request.user.pk)
                  .filter(pk=request.POST.get('user') or 0).first())
        if target:
            if action == 'add':
                photo.shared_with.add(target)
                messages.success(request, f'{target.username} agora pode ver a foto.')
            elif action == 'remove':
                photo.shared_with.remove(target)
                messages.success(request, f'{target.username} não tem mais acesso à foto.')
    return redirect(_share_url(photo))


@login_required
def user_search(request):
    """Busca usuários por nome para o autocomplete de compartilhamento."""
    q = (request.GET.get('q') or '').strip()
    results = []
    if q:
        users = (User.objects.filter(username__icontains=q)
                 .exclude(pk=request.user.pk).order_by('username')[:10])
        results = [{'id': u.pk, 'username': u.username} for u in users]
    return JsonResponse({'results': results})


@login_required
def photo_delete(request, pk):
    photo = get_object_or_404(Photo, pk=pk)
    if not photo.can_edit(request.user):
        raise Http404
    if request.method == 'POST':
        destino = photo.memo  # volta para o memo, se houver
        photo.image.delete(save=False)  # remove o arquivo do storage
        photo.delete()
        messages.success(request, 'Foto excluída.')
        return redirect(destino if destino else 'core:timeline')
    return redirect(photo)


def _open_link(token):
    """Link público pelo token, ou 404 quando não existe / já foi revogado."""
    link = (ShareLink.objects.select_related('memo', 'photo', 'created_by')
            .filter(token=token).first())
    if link is None:
        raise Http404
    return link


def public_share(request, token):
    """Página aberta de um link público: o memo inteiro ou uma foto avulsa.

    Não pede login — quem tem o endereço vê o conteúdo em modo leitura. O
    prazo é conferido a cada acesso: link vencido devolve 410 com um aviso,
    não os dados.
    """
    link = _open_link(token)
    if link.is_expired:
        return render(request, 'memo/public_expired.html', {'link': link}, status=410)
    if link.photo_id:
        return render(request, 'memo/public_photo.html', {
            'link': link,
            'photo': link.photo,
        })
    return render(request, 'memo/public_memo.html', {
        'link': link,
        'memo': link.memo,
        'photos': link.memo.photos.all(),
    })


def public_share_photo(request, token, pk):
    """Uma foto vista de dentro de um link público de memo."""
    link = _open_link(token)
    if link.is_expired:
        return render(request, 'memo/public_expired.html', {'link': link}, status=410)
    # A foto precisa estar no memo do link: o token não abre o acervo inteiro,
    # e um link de foto avulsa não vira chave para nenhum outro caminho.
    if not link.memo_id:
        raise Http404
    photo = get_object_or_404(Photo, pk=pk, memo_id=link.memo_id)
    return render(request, 'memo/public_photo.html', {'link': link, 'photo': photo})


def register(request):
    if request.user.is_authenticated:
        return redirect('core:memo_home')
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('core:memo_home')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})
