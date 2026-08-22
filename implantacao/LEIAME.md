# implantacao/ — o que vive na VPS e não é código

Esta pasta existe por causa do achado **H1** de 2026-08-08: a única defesa
contra "upload sem teto de corpo enche o `/tmp`" é o `client_max_body_size` do
nginx de borda — e o `nginx.conf` **morava só na VPS, sem cópia nenhuma**. Ou
seja, a peça que segura o teto era exatamente a que se perderia junto com a
máquina, e a promessa da §9 do checklist ("copiar uma pasta e trocar um número")
não a cobria.

## `nginx-easycontig.conf`

Cópia fiel de `/etc/nginx/sites-available/easycontig`, tirada da VPS em
2026-08-08. **Não é aplicado por nada automaticamente** — é referência
versionada. Para instalar numa máquina nova:

```bash
sudo cp implantacao/nginx-easycontig.conf /etc/nginx/sites-available/easycontig
sudo ln -sf /etc/nginx/sites-available/easycontig /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

⚠️ **Os blocos `443` e as linhas `# managed by Certbot` são do certbot.** Numa
máquina nova eles ainda não existem: o nginx não valida um bloco `ssl` sem
certificado e não subiria, e sem nginx no ar o desafio HTTP-01 não tem por onde
entrar. Suba primeiro só o bloco `80` e deixe o `certbot --nginx` acrescentar o
resto — a ordem está explicada nos comentários do próprio arquivo.

⚠️ **Mudou o arquivo na VPS? Traga a mudança para cá no mesmo dia**, senão isto
vira uma cópia que mente:

```bash
ssh vps-easycontig 'cat /etc/nginx/sites-available/easycontig' \
  > implantacao/nginx-easycontig.conf
git diff implantacao/
```

## O que este arquivo segura, e que o app não consegue segurar sozinho

O `client_max_body_size` é a barreira de BORDA contra o H1. O `MultiPartParser`
do Starlette spoola cada parte de arquivo em disco (`/tmp`) **antes** de o
manipulador da rota rodar, então `max_bytes`, cota e retenção — que são todos do
app — só agem depois de o corpo inteiro já ter subido. Medido: 12.000.296 bytes
subiram inteiros e só então voltou o `413`.

Hoje o app tem o seu próprio teto (`_corpo_grande_demais`, no middleware, e o
teto do `chunked` logo abaixo dele), então subir sem este arquivo não é mais
subir sem teto nenhum — mas continua sendo subir com uma camada a menos.

⚠️ **O teto não é mais um número só.** Desde o achado F2 de 2026-08-21 o padrão
do servidor é **restritivo** (`2m`) e só as três rotas que recebem arquivo
afrouxam, cada uma no tamanho que a própria aplicação já cobra:

| rota | nginx | o que o app cobra |
|---|---|---|
| `location = /lotes` | `512m` | `EASYCONTIG_MAX_BYTES`, 300 MB + folga de multipart |
| `location = /bancos/meu` | `32m` | recusa FASTA acima de 20 MB |
| `location = /perfil` | `8m` | recusa foto acima de 2 MB |
| todo o resto | `2m` | e `/entrar`, que não exige sessão, recusa em 64 KB |

O motivo de não deixar `512m` valendo para tudo: `POST /entrar` é o único POST
que um anônimo alcança, e com o teto largo ele mandava ~330 partes de quase
1 MiB que o parser do FastAPI segurava em RAM antes de a rota poder recusar —
alguns pedidos simultâneos e o OOM killer do host derrubava o que estivesse pela
frente. **Ao acrescentar uma rota que recebe arquivo, acrescente aqui a
`location` dela**, senão ela nasce com o teto de 2m e o envio legítimo leva um
`413` que não vem da aplicação — portanto sem a mensagem que explica a cota.

Os `proxy_set_header` ficam no bloco `server`, não dentro de cada `location`:
nginx os herda. É o que permite acrescentar uma `location` de upload sem copiar
os cabeçalhos — e copiar cabeçalho é como se perde um deles sem perceber, o que
neste arquivo significaria quebrar o contrato do `X-Forwarded-For` com o
`limites.py`.
