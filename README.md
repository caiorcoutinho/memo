# memo

Memos de fotos em Django.

## Rodando com Docker

A stack sobe quatro serviços: **nginx** (porta de entrada, arquivos estáticos e
proxy das fotos), **web** (Django/gunicorn), **db** (PostgreSQL 16) e **minio**
(armazenamento das fotos, compatível com S3).

```sh
cp .env.example .env      # ajuste as senhas e os hosts
docker compose up -d --build
```

O site fica em `http://localhost:8080` (ou o valor de `HTTP_PORT`) e o console do
MinIO em `http://127.0.0.1:9001`.

Na subida, o container `web` espera o Postgres, aplica as migrations e roda o
`collectstatic`; o container `minio-init` cria o bucket das fotos e libera a
leitura anônima. Nenhum dos dois precisa ser executado à mão.

Criar o primeiro usuário administrador:

```sh
docker compose exec web python manage.py createsuperuser
```

### Variáveis de ambiente

Todas ficam no `.env` (veja `.env.example`):

| Variável | Para que serve |
| --- | --- |
| `DJANGO_SECRET_KEY` | Chave secreta do Django. Troque antes de expor o site. |
| `DJANGO_DEBUG` | `0` em produção. |
| `DJANGO_ALLOWED_HOSTS` | Domínios aceitos, separados por vírgula. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origens (com esquema) que podem enviar formulários. |
| `HTTP_PORT` | Porta do nginx no host. |
| `POSTGRES_*` | Banco de dados. |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | Credenciais do MinIO, usadas também pelo Django. |
| `MEDIA_BUCKET` | Bucket das fotos. |
| `MEDIA_PUBLIC_URL` | URL pública das fotos, ex.: `https://seu-dominio/media`. |

`MEDIA_PUBLIC_URL` precisa apontar para o `/media/` do próprio site: o nginx
repassa esse caminho para o MinIO, então o bucket nunca é exposto diretamente e
as URLs das fotos ficam no domínio do site.

### Como as fotos são guardadas

O bucket de fotos tem leitura anônima e o nginx serve os objetos em `/media/` —
mesma exposição que o Django já fazia servindo `MEDIA_ROOT` em desenvolvimento:
quem tiver o link da foto consegue abri-la, mesmo sem estar logado. As telas de
memo continuam protegidas por login e pelas regras de compartilhamento.

### Metadados das fotos

Cada foto tem sete grupos de metadados que podem ser habilitados ou
desabilitados um a um: data da captura, câmera, lente, exposição, localização
(GPS), imagem/arquivo e software. As caixas aparecem na tela de envio e na de
edição da foto; no formulário do memo elas definem o padrão do memo, que
pré-marca as caixas quando aquele memo é escolhido no envio.

O que fica desmarcado não é lido do arquivo nem guardado no banco — inclusive a
data, que nesse caso vem da data de modificação do arquivo em vez do EXIF.
Desabilitar um metadado numa foto já enviada apaga o dado guardado; habilitar de
volta só o recupera se o EXIF ainda estiver no arquivo (a compressão o descarta).

### Rodando fora do Docker

O `settings.py` cai nos padrões antigos quando as variáveis não estão definidas:
sem `POSTGRES_DB` usa o SQLite local, e sem `MEDIA_BUCKET` guarda as fotos em
`media/`. Então `python manage.py runserver` continua funcionando como antes.

### Acessando pela rede local

O `runserver` deste projeto escuta em `0.0.0.0:8000` (o padrão do Django,
127.0.0.1, só aceita a própria máquina), então o site já sai visível para os
outros aparelhos da rede:

```sh
python manage.py runserver          # 0.0.0.0:8000
hostname -I | awk '{print $1}'      # o IP para digitar no celular
```

Com isso o endereço no celular é `http://<esse-ip>:8000`. Para fechar o
servidor na própria máquina, passe o endereço: `runserver 127.0.0.1:8000`.

Enquanto `DJANGO_DEBUG` está ligado (o padrão fora do Docker), o
`ALLOWED_HOSTS` aceita qualquer Host — não é preciso listar o IP, que muda de
rede para rede. Com `DJANGO_DEBUG=0` a lista passa a valer, e aí o IP ou o
domínio precisam estar em `DJANGO_ALLOWED_HOSTS`.

Na stack Docker quem escuta é o gunicorn (já em `0.0.0.0:8000`, dentro do
contêiner) com o nginx publicando a porta `HTTP_PORT` no host. O `HTTP_BIND`
controla em qual interface: `0.0.0.0` (padrão) deixa o site visível na rede,
`127.0.0.1` restringe à máquina. O console do MinIO segue preso ao
`127.0.0.1:9001` de propósito.

Se o site não abrir de outro aparelho, o firewall local é o suspeito:
`sudo ufw allow 8000/tcp` (ou `8080/tcp`, no caso do Docker).

### Migrando dados de uma instalação em SQLite

```sh
python manage.py dumpdata --natural-foreign --natural-primary \
    -e contenttypes -e auth.Permission > dump.json
docker compose cp dump.json web:/tmp/dump.json
docker compose exec web python manage.py loaddata /tmp/dump.json
```

As fotos já existentes em `media/` precisam ser copiadas para o bucket, por
exemplo com `mc mirror media/ local/media/`.
