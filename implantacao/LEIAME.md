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

O `client_max_body_size` é a **única** barreira contra o H1. O
`MultiPartParser` do Starlette spoola cada parte de arquivo em disco (`/tmp`)
**antes** de o manipulador da rota rodar, então `max_bytes`, cota e retenção —
que são todos do app — só agem depois de o corpo inteiro já ter subido. Medido:
12.000.296 bytes subiram inteiros e só então voltou o `413`.

Enquanto não houver um middleware olhando `Content-Length`, subir sem este
arquivo é subir sem teto de corpo.
