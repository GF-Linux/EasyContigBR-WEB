# Domínio, DNS e login pelo Google

Escrito em 2026-08-06, quando o domínio **`easycontigbr.com.br`** foi registrado.

Reúne as duas pendências que sempre voltam: pôr o app num endereço público e
fazer o caminho `google` do `auth.py` funcionar — que até hoje **nunca foi
exercitado**, nem uma vez, nem em teste.

---

## 1. O que já existe (conferido, não suposto)

Consulta ao DNS em 2026-08-06:

```
NS   easycontigbr.com.br  →  a.auto.dns.br. , b.auto.dns.br.
SOA  easycontigbr.com.br  →  a.auto.dns.br. hostmaster.registro.br. …
A    easycontigbr.com.br  →  (não existe)
```

Ou seja: **não é preciso criar servidor DNS nenhum.** O domínio já usa o DNS
gratuito do Registro.br, a zona já responde, e o que falta dentro dela é um
registro `A` — o endereço IP para onde o nome aponta.

E é aí que a coisa trava: **não há IP porque não há máquina decidida**. Esta é
a pendência (b) da sessão de 2026-08-04, e continua sendo decisão do autor.

> ⚠️ A [[ADR 0050]] previa **VPS da universidade** (`easycontig.ufrrj.br`), pelo
> no-break, backup e independência de operador. Um `.com.br` próprio aponta para
> hospedagem fora da UFRRJ, e isso **muda três coisas que já estavam marcadas
> como em aberto**: onde moram os `.ab1` **não publicados** do LHV, se a
> PolyForm cobre serviço em rede (pergunta para o NIT) e quem mantém o servidor
> quando o autor terminar. O domínio não decide nada disso sozinho — mas escolher
> a máquina decide.

---

## 2. Criar o registro `A` no Registro.br

Só depois de ter o IP.

1. <https://registro.br> → entrar → **Painel** → **easycontigbr.com.br**
2. Aba **DNS** → **Editar zona** (é o editor do DNS gratuito deles)
3. Acrescentar:

   | Nome  | Tipo    | Valor                    |
   |-------|---------|--------------------------|
   | `@`   | `A`     | `<IP do servidor>`       |
   | `www` | `CNAME` | `easycontigbr.com.br.`   |

   O ponto final no CNAME não é enfeite: sem ele o Registro.br completa o nome
   e vira `easycontigbr.com.br.easycontigbr.com.br`.

4. Salvar. O TTL negativo da zona é **900 s**, então em ~15 min o nome resolve.

Conferir de fora, sem confiar no cache da máquina:

```bash
python3 -c "import socket; print(socket.gethostbyname('easycontigbr.com.br'))"
```

Se for IPv6 também, é um `AAAA` com o mesmo nome.

---

## 3. HTTPS

O Google **não aceita `https://` sem certificado válido** no retorno do OAuth, e
o `EASYCONTIG_HTTPS_ONLY=1` (obrigatório sob `EASYCONTIG_PRODUCAO=1`) marca o
cookie de sessão como `secure` — sem TLS, ninguém entra.

Certificado por Let's Encrypt, emitido **depois** do passo 2: a validação do
Let's Encrypt bate no nome, então ele precisa já resolver.

---

## 4. Google Cloud Console — o que cadastrar, exatamente

<https://console.cloud.google.com> → criar um projeto (ex.: `EasyContig BR`).

### 4.1. Tela de permissão OAuth

**APIs e serviços → Tela de permissão OAuth**

* Tipo de usuário: **Externo** (interno só existe com Google Workspace da
  instituição; se a UFRRJ tiver, interno é melhor — já restringe por si).
* Nome do app: `EasyContig BR` · e-mail de suporte · e-mail do desenvolvedor.
* **Domínio autorizado:** `easycontigbr.com.br`
* Escopos: `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`.
  São exatamente os três que o `auth.py` pede (`"scope": "openid email profile"`)
  e **nenhum deles é sensível** — então publicar o app **não** passa pela
  verificação demorada do Google.

> ⚠️ **A lista de "usuários de teste" NÃO é uma porta — medido em 2026-08-06.**
> Com a tela em *Testando* e **um** e-mail na lista, o autor entrou com **três**
> contas diferentes, duas delas fora da lista (`juaredpokemon@gmail.com` e
> `jared@ufrrj.br`). Eu tinha escrito aqui o contrário, e estava errado.
>
> O motivo é o mesmo que torna a publicação fácil: a trava de usuário de teste e
> a verificação do Google existem para escopos **sensíveis**, e os nossos três
> não são. Sem verificação exigida, não há gate. Ou seja: **quem decide quem
> entra é o `EASYCONTIG_DOMINIO`, e só ele** — a tela do Google prova a
> identidade, não a autorização.

### 4.2. O ID do cliente

**Credenciais → Criar credenciais → ID do cliente OAuth → Aplicativo da Web**

*Origens JavaScript autorizadas*

```
https://easycontigbr.com.br
http://localhost:8099
```

*URIs de redirecionamento autorizados*

```
https://easycontigbr.com.br/auth/google/volta
http://localhost:8099/auth/google/volta
```

O caminho `/auth/google/volta` é o do `main.py` (`_redirect_uri`) e tem de bater
**caractere por caractere** — o Google recusa por uma barra a mais.

As duas linhas de `localhost` são de propósito: o Google abre exceção para
`http://localhost`, e é isso que permite **exercitar o login hoje, sem DNS e sem
servidor** (§6). Podem sair do cadastro quando o app estiver no ar.

Copiar o **ID do cliente** e a **chave secreta** ao final.

---

## 5. O `.env` do servidor

```ini
EASYCONTIG_PRODUCAO=1
EASYCONTIG_URL_BASE=https://easycontigbr.com.br
EASYCONTIG_AUTH=google
GOOGLE_CLIENT_ID=<COLE AQUI>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<COLE AQUI>
EASYCONTIG_SECRET_KEY=<saída de `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`>
EASYCONTIG_HTTPS_ONLY=1

# Quem entra. Vazio = QUALQUER conta Google do mundo — e é o padrão.
EASYCONTIG_DOMINIO=ufrrj.br
```

Sobre as duas últimas linhas, que são a decisão de verdade deste arquivo:

* `EASYCONTIG_URL_BASE` existe porque atrás de proxy reverso a URL da requisição
  chega com o host errado, e o retorno do Google precisa ser idêntico ao
  cadastrado. Com ela, o endereço sai da configuração, não do pedido.
* `EASYCONTIG_DOMINIO` é o que separa "o Google prova QUEM é" de "quem pode
  entrar". **Sem ela, qualquer pessoa com conta Google entra e passa a mandar
  `.ab1` para o volume `dados/`.** A [[ADR 0050]] previa `@ufrrj.br`; com
  domínio próprio a restrição vale ainda mais, porque o endereço agora é
  descobrível de fora. Aceita lista: `ufrrj.br, usp.br, unicamp.br`.
  **O subdomínio entra junto** (`ufrrj.br` cobre `ppgcv.ufrrj.br` e
  `lhv.ufrrj.br`); sósia não entra (`falso-ufrrj.br` fica de fora, porque o
  ponto é exigido). Para prender no domínio exato: `=ufrrj.br`.
  **Endereço nomeado:** item com `@` no meio vale por uma pessoa só —
  `ufrrj.br, fulano@gmail.com` libera a universidade inteira mais uma conta
  pessoal, sem abrir o Gmail junto.
* `EASYCONTIG_PRODUCAO=1` faz o servidor **recusar subir** em modo `dev`, sem
  `SECRET_KEY` ou sem `HTTPS_ONLY` — aviso em arquivo de exemplo não é trava.

### O que a lista de domínios NÃO consegue fazer

A licença é PolyForm Noncommercial ([[ADR 0006]]): livre para ensino e pesquisa,
paga para a iniciativa privada. **Nenhuma lista de domínios expressa isso.** Um
`@gmail.com` pode ser um aluno de mestrado; um `@bayer.com` é indústria; e
`.edu.br` não serve de atalho, porque as universidades federais brasileiras usam
`.br` direto — `ufrrj.br`, `usp.br`, `unicamp.br`, `ufmg.br` ficariam todas de
fora de um filtro `.edu.br`.

O que a lista faz bem é ser uma **porta com nomes escritos**: cada instituição
que passa a usar entra na lista, uma por uma, quando pede. É trabalho manual e é
justamente por isso que é honesto — o registro de quem foi autorizado fica no
`.env`, legível, e não numa heurística que erra nos dois sentidos.

Recomendação para o primeiro dia: **`ufrrj.br`**, que já cobre o LHV e todo o
resto da universidade. Somar instituição conforme aparecer pedido. A cobrança da
iniciativa privada continua sendo cláusula de licença e contrato, não código.

---

## 5.1. Começar numa VPS paga e migrar para a da universidade depois

Pergunta do autor em 2026-08-06. **Dá, e a mudança é de horas, não de semanas —
porque o endereço público é o `easycontigbr.com.br`, e ele é seu.** Quem aponta
para a máquina é o registro `A`; trocar de máquina é trocar um número.

O que sobrevive à mudança sem tocar em nada:

* **O login pelo Google.** O que está cadastrado no console é a URL, não o
  servidor. Enquanto o endereço for `https://easycontigbr.com.br/auth/google/volta`,
  o console nem fica sabendo que a máquina mudou.
* **O app.** Já é Docker Compose desde o primeiro dia ([[ADR 0050]]) — na
  máquina nova é `docker compose up`.
* **Os dados.** SQLite + o volume `dados/` são arquivos: `docker compose down`
  (com o `stop_grace_period` que a [[ADR 0053]] pôs lá, para não matar um lote
  no meio), copiar o volume, subir do outro lado, trocar o `A`.

O que **não** sobrevive, e é pouco: o certificado (o Let's Encrypt reemite
sozinho no host novo) e qualquer coisa específica de fornecedor. Por isso a regra
para escolher a VPS paga é uma só: **nada de banco gerenciado nem de
armazenamento de objetos do fornecedor.** No dia em que isso entrar, a migração
deixa de ser copiar uma pasta.

> ⚠️ **Uma premissa a corrigir.** Os consensos de *Hepatozoon* e *Babesia* estão
> no GenBank e os artigos saíram — isso vale para **aquelas** sequências, e o
> que foi depositado é o **consenso**, não o `.ab1`. O que decide a governança
> não é o dado da demo: é o que as pessoas vão **enviar** depois. A corrida
> F13719 (bactéria de capivara, quatro marcadores) é de 2026 e inclui a
> coinfecção que ainda está "para o Thiago confirmar" — essa não está publicada
> em lugar nenhum. Enquanto o servidor for só seu e do laboratório, o risco é
> administrável; a conversa com a Maristela e com o NIT é sobre o depois.

---

## 6. O que dá para fazer HOJE, sem servidor e sem DNS

O caminho `google` do `auth.py` nunca rodou. Ele não precisa do domínio para ser
exercitado — só do `localhost` cadastrado em §4.2:

```bash
cd ~/Desktop/easycontig-web
set -a; . ./.env; set +a
EASYCONTIG_AUTH=google \
GOOGLE_CLIENT_ID=<COLE AQUI>.apps.googleusercontent.com \
GOOGLE_CLIENT_SECRET=<COLE AQUI> \
EASYCONTIG_URL_BASE=http://localhost:8099 \
  .venv/bin/uvicorn easycontig_web.main:app --port 8099
```

Abrir <http://localhost:8099/entrar> e clicar em **Entrar com Google**. Sem
`EASYCONTIG_PRODUCAO=1`, porque em `localhost` não há HTTPS.

### Quando o Google responde `401 invalid_client`

`Acesso bloqueado: erro de autorização — The OAuth client was not found`
aconteceu na primeira tentativa, em 2026-08-06. O erro é **da página do Google,
antes de qualquer código nosso**, e quer dizer uma coisa só: a string enviada em
`client_id` não corresponde a cliente nenhum lá. **Não tem relação com a conta**
— por isso falha igual em qualquer e-mail — nem com `EASYCONTIG_DOMINIO`.

Em ordem de probabilidade:

1. **O `<COLE AQUI>` desta receita foi rodado literalmente.** Desde então o
   servidor reclama disso no arranque (`queixa_do_client_id`).
2. **Aspas ou espaço** vindos do `.env` — o Google compara a string inteira.
   Conferir com `printf '[%s]\n' "$GOOGLE_CLIENT_ID"`, que mostra as pontas.
3. **O cliente é de outro tipo.** Tem de ser **Aplicativo da Web**; "App para
   computador"/"Android" geram IDs que não servem para este fluxo.
4. **Projeto errado** no console — o seletor no topo da página.
5. **Recém-criado.** Alguns minutos até valer. Se as quatro acima estiverem
   descartadas, esperar e repetir.

O que isso prova, e que nenhum teste prova: que o `state` volta e confere, que o
Google devolve `email_verified`, e que o `EASYCONTIG_DOMINIO` recusa conta de
fora. É o item que está aberto desde 2026-08-04.

---

## 7. Ordem sugerida

1. **Hoje:** criar o projeto e o ID do cliente (§4) e testar em `localhost` (§6).
   Não depende de decisão nenhuma e fecha a pendência mais antiga.
2. **Decidir a máquina** (§1) — é a única coisa que trava o resto.
3. IP → registro `A` (§2) → certificado (§3) → `.env` de produção (§5).
4. Antes de abrir para fora do laboratório: a conversa com o NIT sobre a
   PolyForm cobrir serviço em rede, e o backup do volume `dados/`, que **hoje
   não existe** — só o banco tem.
