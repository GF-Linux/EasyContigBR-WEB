# EasyContig BR — web (lotes)

Fachada web do EasyContig BR. Sobe uma pasta de `.ab1`, devolve o relatório da
corrida e o CSV. **Não** reproduz a janela do aplicativo: nada de cromatograma
interativo nem edição de base — isso continua sendo do desktop.

Por que assim, e não o app inteiro no navegador: [ADR 0050][adr50] no segundo
cérebro. Resumo: o lote é 100 % backend, já estava implementado, e é o que o
laboratório pediu na prática duas vezes.

## Como está montado

```
   navegador                     este repo                    EasyContig BR
  ┌──────────┐   .ab1     ┌───────────────────┐            ┌────────────────┐
  │  upload  │ ─────────▶ │  main.py (FastAPI)│            │   app/core/    │
  └──────────┘            │  grava e ENFILEIRA│            │  (biblioteca)  │
       ▲                  └─────────┬─────────┘            │  tracy · blast │
       │  relatório                 │ SQLite               │  árvore · lote │
       │                  ┌─────────▼─────────┐  importa   │                │
       └───────────────── │  trabalhador.py   │ ──────────▶│                │
                          │  (outro processo) │            └────────────────┘
                          └───────────────────┘
```

O servidor web **não processa nada**. Ele grava os arquivos, põe o lote na fila
e responde — 116 ms para um envio de 26 MB, medido. Os ~14 s de montagem e
identificação acontecem no trabalhador, onde ninguém está com uma conexão
aberta esperando. É essa separação que faz o serviço aguentar 100 pessoas na
mesma máquina que aguentaria 20.

Nenhuma decisão científica mora aqui. Montagem, identidade, limiares e o texto
do relatório são todos de `app/core/`, o mesmo código que o desktop usa — o que
mantém as duas fachadas dizendo a mesma coisa sobre a mesma amostra.

## Rodar em desenvolvimento

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../EasyContig-BR-Demo-Deck    # o núcleo científico
pip install -e ".[dev]"

cp .env.example .env        # já vem apontando para a instalação do Deck
set -a; . ./.env; set +a

uvicorn easycontig_web.main:app --port 8099 --reload   # num terminal
python -m easycontig_web.trabalhador                   # noutro
```

Abra <http://127.0.0.1:8099>. Em `EASYCONTIG_AUTH=dev` qualquer e-mail entra sem
senha — **nunca use assim num servidor**.

`GET /saude` diz o que falta na instalação (tracy, blastn, bancos). Vale conferir
antes de culpar a amostra: **`blastn` ausente não é "nenhum acerto"** — é falta
de ferramenta, e o relatório separa as duas coisas (ADR 0047).

## Testes

```bash
python -m pytest tests -q      # 26 testes
```

Os de `test_executor.py` rodam o pipeline de verdade e se pulam sozinhos quando
tracy/blastn/bancos não estão configurados.

## Produção

```bash
ln -s ../EasyContig-BR-Demo-Deck nucleo    # o núcleo entra no contexto de build
mkdir bancos && cp ../EasyContig-BR-Demo-Deck/db/referencias_18S.* bancos/
cp ../EasyContig-BR-Demo-Deck/db_16s/referencias_16S.* bancos/
echo "EASYCONTIG_SECRET_KEY=$(openssl rand -hex 32)" >> .env
docker compose up -d --build
```

Sirva atrás de um proxy reverso com TLS — o compose publica só em `127.0.0.1`.
`TRABALHADORES=3` no `.env` ajusta a vazão (cada réplica come ~1 núcleo).

## O que ainda não está feito

- **Login Google** — o fluxo está inteiro (`/auth/google` e a volta, com `state`
  contra CSRF e exigência de `email_verified`), mas **nunca foi exercitado com
  credencial real**: depende de um CLIENT_ID/SECRET do Google Cloud. Até alguém
  rodar de verdade, considere não testado.
- **O prazo de retenção** — o mecanismo existe (`retencao.py`: expurgo por prazo,
  botão "apagar agora", aviso da data na página do lote), mas **o número de dias
  não tem dono**. `EASYCONTIG_RETENCAO_DIAS` vem `0` no exemplo — desligado, o
  comportamento antigo — porque 90 dias é um padrão técnico e não a política do
  laboratório sobre dado de sequenciamento **não publicado**. Ligue quando
  alguém assinar embaixo do prazo: apagar não tem desfazer.
- **Ninguém é avisado antes de o dado vencer.** A página do lote mostra a data,
  mas não há e-mail de "seu lote vence em 7 dias"; quem não abrir a página
  descobre pela ausência.
- **`.ab1` e relatório vencem juntos.** A faxina apaga a pasta inteira. Dá para
  argumentar que o traço bruto (pesado, existe no laboratório) e o relatório
  (leve, é o produto) merecem prazos diferentes — não implementado, é política.
- **Fase 2** — cromatograma e grade editável no navegador. Adiado de propósito.

## Números medidos (Steam Deck, hardware fraco de propósito)

| | |
|---|---|
| montagem de um par (`tracy consensus`) | 0,07 s |
| identificação (`blastn` local + árvore NJ) | 0,57 s |
| corrida real de 40 amostras (80 `.ab1`, 26 MB) | **14,1 s** |
| upload de 26 MB + resposta HTTP | 116 ms |

A corrida F13719 processada por aqui reproduz o que já estava estabelecido nas
ADRs 0046/0048: 40/40 amostras sem erro, 21 identificadas pelo 16S e **19 sem
acerto local — que é o resultado certo**, porque groEL/dsb/sodB não são rRNA;
identidade de 96,5 % a 100 %; cobertura de 1,0× a 1,8×.

## Licença

PolyForm Noncommercial 1.0.0, como o EasyContig BR. ⚠️ A licença cobre software
distribuído; **serviço em rede é superfície que ela cobre pior** — conferir com o
NIT antes de abrir para fora da UFRRJ.

Motor de montagem: [Tracy](https://github.com/gear-genomics/tracy) (Rausch et
al., BMC Genomics 2020), BSD-3, usado como subprocesso.

[adr50]: ../segundo-cerebro/projetos/dna-contingency/decisoes/0050-porte-web-comeca-pelo-lote.md
