"""
retencao.py — quando o `.ab1` que o laboratório subiu deixa de existir aqui.

Por que existe: no desktop os dados nunca saíam da máquina de quem monta. Na
web eles passam a morar num servidor, e o que estava anotado como pendência é
que ali eles ficavam PARA SEMPRE — são corridas Sanger **não publicadas**, e um
volume que só cresce é ao mesmo tempo risco (dado de terceiro guardado sem
prazo) e conta de disco (uma corrida de 40 amostras são ~26 MB de traço bruto).

O que este módulo NÃO faz: decidir a política. Quem tem o dado é quem define o
prazo, e enquanto ninguém definir o padrão é NÃO APAGAR (`DIAS_PADRAO = 0`) —
o módulo é conservador em todas as bifurcações: na dúvida, guarda.

Fica de propósito sem depender de `executor` (que importa `app.core` inteiro só
para ter o caminho da pasta) e sem ler `Config`: recebe caminhos por argumento,
então dá para chamá-lo do trabalhador, de uma rota ou de um cron sem arrastar o
resto do servidor junto.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .fila import RECEBENDO, RODANDO, conectar

log = logging.getLogger("easycontig.retencao")

# ⚠️ O PADRÃO É NÃO APAGAR. Isto já esteve em 90 e era defeito: uma instalação
# que não define `EASYCONTIG_RETENCAO_DIAS` passava a apagar corridas em 90 dias
# sem ninguém ter decidido nada — e apagar não tem desfazer. O prazo de guarda de
# dado de pesquisa tem dono humano (autor + orientadora) e ainda não foi
# decidido; até lá, acumular é o erro reversível e apagar é o irreversível.
#
# 30 dias é o prazo DECIDIDO pelo autor em 2026-08-06 e é o que vai no
# `.env.example`. Aqui continua 0 porque este é o padrão de quem não configurou
# nada, e instalação que não decidiu não deve apagar dado de ninguém.
DIAS_PADRAO = 0
DIAS_SUGERIDO = 30

# Os estados em que a pasta está sendo escrita NESTE INSTANTE por outro processo:
# um upload em curso ou o trabalhador montando. Apagar por baixo deles é o mesmo
# defeito das "33 de 40 amostras" visto de outro ângulo — só que pior, porque
# some com o arquivo de origem. Idade nenhuma justifica entrar aqui.
INTOCAVEIS = (RECEBENDO, RODANDO)


def dias_configurados() -> int:
    """Prazo em dias, de `EASYCONTIG_RETENCAO_DIAS`. 0 = nunca expurgar.

    Valor ausente cai no padrão (que é 0 = não apagar); valor ilegível ou negativo
    TAMBÉM cai no padrão, com aviso no log. Negativo é o caso perigoso: `dias=-1`
    significaria "tudo já venceu" e um erro de digitação no `.env` esvaziaria o
    volume inteiro. Todas as bifurcações erram para o lado de guardar.
    """
    bruto = os.environ.get("EASYCONTIG_RETENCAO_DIAS", "").strip()
    if not bruto:
        return DIAS_PADRAO
    try:
        dias = int(bruto)
    except ValueError:
        log.warning("EASYCONTIG_RETENCAO_DIAS=%r não é um número; usando %d dias",
                    bruto, DIAS_PADRAO)
        return DIAS_PADRAO
    if dias < 0:
        log.warning("EASYCONTIG_RETENCAO_DIAS=%d é negativo; usando %d dias",
                    dias, DIAS_PADRAO)
        return DIAS_PADRAO
    return dias


def _quando(valor: str | None) -> datetime | None:
    """ISO-8601 do banco -> datetime com fuso. None se não der para ler.

    Data ilegível NUNCA vira "muito velho": um lote sem data confiável fica, e
    aparece no log. Perder dado por causa de uma string estranha no banco é o
    tipo de erro que ninguém consegue desfazer.
    """
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _campo(lote, nome: str):
    """`lote` tanto pode ser o dict de `fila.pegar` quanto o sqlite3.Row cru."""
    try:
        return lote[nome]
    except (KeyError, IndexError):
        return None


def expira_em(lote: dict, dias: int) -> datetime | None:
    """Quando este lote será apagado — para a tela AVISAR antes de o dado sumir.

    None quando a retenção está desligada (`dias=0`) ou quando o `criado_em` do
    lote não é legível. A data vale para o lote parado: enquanto ele estiver
    `recebendo` ou `rodando` a faxina não o toca, então a remoção real pode ser
    depois disto — nunca antes, que é o que importa para quem lê o aviso.
    """
    if dias <= 0:
        return None
    criado = _quando(_campo(lote, "criado_em"))
    return criado + timedelta(days=dias) if criado else None


def lotes_expirados(sqlite_path: Path, *, dias: int,
                    agora: datetime | None = None) -> list[dict]:
    """Os lotes que já passaram do prazo, do mais antigo para o mais novo.

    `agora` é injetável porque a alternativa nos testes seria forjar datas de
    três meses atrás no banco — dá para fazer, mas é o teste que fica frágil.
    """
    if dias <= 0:
        return []
    agora = agora or datetime.now(timezone.utc)
    limite = agora - timedelta(days=dias)

    marcas = ",".join("?" * len(INTOCAVEIS))
    with conectar(sqlite_path) as con:
        linhas = con.execute(
            f"SELECT * FROM lotes WHERE status NOT IN ({marcas}) ORDER BY criado_em",
            INTOCAVEIS,
        ).fetchall()

    # A comparação de data é feita aqui, e não no SQL, para poder IGNORAR (e
    # registrar) a linha com data ilegível em vez de deixar o SQLite comparar
    # texto com texto e chegar a uma conclusão qualquer.
    vencidos = []
    for r in linhas:
        criado = _quando(r["criado_em"])
        if criado is None:
            log.warning("lote %s tem criado_em ilegível (%r); não será expurgado",
                        r["id"], r["criado_em"])
            continue
        if criado <= limite:
            vencidos.append(dict(r))
    return vencidos


def _nome_simples(lote_id: str) -> str:
    """Recusa qualquer id que não seja um nome de pasta solto.

    Esta função apaga uma árvore inteira recursivamente. O chamador já valida o
    id, mas "já validou" é a premissa que costuma envelhecer mal: um `..` que
    passe daqui vira `rm -rf` na pasta de cima. Barato demais para confiar.
    """
    if not isinstance(lote_id, str) or not lote_id.strip():
        raise ValueError("lote_id vazio")
    if lote_id in (".", "..") or "/" in lote_id or "\\" in lote_id \
            or "\0" in lote_id or lote_id != Path(lote_id).name:
        raise ValueError(f"lote_id inválido: {lote_id!r}")
    return lote_id


def tamanho_em_bytes(pasta: Path) -> int:
    """Quanto o lote ocupa. Serve para o log dizer o que a faxina liberou."""
    total = 0
    for raiz, _dirs, arquivos in os.walk(pasta, onerror=lambda _e: None):
        for a in arquivos:
            try:
                total += (Path(raiz) / a).lstat().st_size
            except OSError:
                pass          # arquivo sumiu no meio da varredura: some da conta
    return total


def apagar_lote(sqlite_path: Path, lotes_dir: Path, lote_id: str) -> bool:
    """Apaga a pasta do lote E a linha da fila. False se não havia nada.

    Idempotente de propósito: a faxina, uma rota "apagar agora" e um retry do
    trabalhador podem chegar aqui na mesma pasta, e a segunda chamada tem que ser
    um silêncio, não uma exceção.

    Os dois somem juntos porque cada metade sozinha é um defeito conhecido: linha
    sem pasta faz a página prometer um relatório que não existe; pasta sem linha
    é lixo invisível que enche o volume sem aparecer em lugar nenhum.
    """
    _nome_simples(lote_id)
    # Mesma regra de `executor.pastas_do_lote` (raiz = lotes_dir / lote_id),
    # repetida aqui para não importar `app.core` inteiro só para apagar arquivo.
    pasta = Path(lotes_dir) / lote_id

    # A LINHA SAI PRIMEIRO, e com o estado no WHERE. É o banco que arbitra, não
    # a checagem de `lotes_expirados`: com mais de um trabalhador na mesma fila,
    # entre aquele SELECT e este DELETE cabe um `fila.reivindicar` marcando o
    # lote como `rodando`. Apagando a linha antes da pasta, quem perde a corrida
    # apaga 0 linhas e desiste — e o trabalhador que ganhou continua com os
    # arquivos que está lendo. Na ordem inversa, a pasta sumiria no meio da
    # montagem e o lote viraria um relatório com menos amostras do que a corrida
    # tem, que é exatamente o defeito que o estado `recebendo` existe para
    # impedir.
    marcas = ",".join("?" * len(INTOCAVEIS))
    with conectar(sqlite_path) as con:
        linhas = con.execute(
            f"DELETE FROM lotes WHERE id=? AND status NOT IN ({marcas})",
            (lote_id, *INTOCAVEIS),
        ).rowcount
        ocupado = bool(linhas == 0 and con.execute(
            "SELECT 1 FROM lotes WHERE id=?", (lote_id,)).fetchone())

    if ocupado:
        # Nem a faxina nem um "apagar agora" da tela tiram o chão de um upload em
        # curso ou de uma montagem rodando. Quem quiser apagar, apaga depois.
        log.info("lote %s está em uso; não foi apagado", lote_id)
        return False

    tinha_pasta = pasta.exists()
    if tinha_pasta:
        # `ignore_errors` e depois confere: volume montado com dono errado é o
        # caso real, e ali metade apaga e metade não. Não levantamos — derrubar a
        # faxina por causa de um lote deixaria todos os outros vencidos no disco.
        # O que sobrou vai para o log porque alguém terá que remover na mão.
        shutil.rmtree(pasta, ignore_errors=True)
        if pasta.exists():
            log.error("lote %s: sobrou coisa em %s (permissão?) — remova na mão",
                      lote_id, pasta)

    if tinha_pasta and not linhas:
        log.warning("lote %s tinha pasta em disco e nenhuma linha na fila", lote_id)
    # Nada a avisar para `cotas`: a memória curta de lá se invalida sozinha ao
    # ver que a pasta sumiu. Chamar `cotas.esquecer()` daqui acoplaria este
    # módulo (que é sem dependências de propósito) e ainda por cima resolveria
    # menos — só valeria dentro deste processo, e quem apaga é o trabalhador
    # enquanto quem lê a cota é o servidor web.
    return bool(tinha_pasta or linhas)


# Um dia normal vence uma fatia; um relógio errado vence tudo. O freio pega
# quando os dois valem: MAIS DA METADE do acervo e mais que um punhado. O
# segundo termo existe para não travar instalação pequena — com 3 lotes no
# total, apagar 2 é rotina, não catástrofe.
FREIO_FRACAO = 0.5
FREIO_MINIMO = 6


def _freio_pega(vencidos: int, total: int) -> bool:
    return vencidos >= FREIO_MINIMO and total > 0 and vencidos > total * FREIO_FRACAO


def _expurgo_em_massa_liberado() -> bool:
    return os.environ.get("EASYCONTIG_EXPURGO_EM_MASSA") == "1"


def _total_de_lotes(sqlite_path: Path) -> int:
    try:
        with conectar(sqlite_path) as con:
            return int(con.execute("SELECT COUNT(*) FROM lotes").fetchone()[0])
    except Exception:                            # noqa: BLE001
        return 0


def faxina(sqlite_path: Path, lotes_dir: Path, *, dias: int,
           agora: datetime | None = None) -> dict:
    """Passa o expurgo. {'apagados': int, 'ids': [...], 'bytes': int}.

    `dias=0` não apaga NADA e devolve zero — um servidor que apaga dado de
    laboratório porque uma variável de ambiente não foi definida é pior que um
    servidor que acumula. O acúmulo dá para ver no disco; o apagado, não.
    """
    resultado = {"apagados": 0, "ids": [], "bytes": 0, "travado": ""}
    if dias <= 0:
        log.info("retenção desligada (dias=0): nenhum lote será apagado")
        return resultado

    vencidos = list(lotes_expirados(sqlite_path, dias=dias, agora=agora))

    # ── freio de expurgo em massa ────────────────────────────────────────────
    # Medido em 2026-08-06: com 20 corridas do DIA e retenção de 30 dias, um
    # relógio um ano à frente faz a faxina apagar AS VINTE numa passada — e
    # apagar não tem desfazer. Salto de relógio não é hipótese exótica: é o NTP
    # corrigindo uma máquina virtual, a BIOS zerando, o fuso mal configurado na
    # primeira subida do servidor.
    #
    # O freio não decide a política — o prazo continua sendo do laboratório.
    # Ele distingue "venceu o de ontem" de "venceu tudo": num dia normal a
    # faxina apaga o que completou o prazo, nunca a coleção inteira. Quando o
    # que vence é quase tudo, a explicação mais provável é o relógio ou a
    # variável, não a passagem do tempo — e a hora de descobrir isso é ANTES do
    # `rmtree`.
    #
    # A primeira faxina de uma instalação antiga pode vencer muita coisa com
    # razão. Para essa existe a chave explícita, e é justamente o ponto: alguém
    # decide, em vez de o relógio decidir.
    total = _total_de_lotes(sqlite_path)
    if _freio_pega(len(vencidos), total) and not _expurgo_em_massa_liberado():
        motivo = (
            f"{len(vencidos)} de {total} lotes venceriam de uma vez — isso não "
            f"parece passagem do tempo. Confira o relógio do servidor e o "
            f"EASYCONTIG_RETENCAO_DIAS (={dias}). NADA foi apagado. Se for "
            f"mesmo para apagar, suba uma vez com EASYCONTIG_EXPURGO_EM_MASSA=1.")
        log.error("faxina TRAVADA: %s", motivo)
        resultado["travado"] = motivo
        return resultado

    for lote in vencidos:
        lote_id = lote["id"]
        try:
            bytes_lote = tamanho_em_bytes(Path(lotes_dir) / lote_id)
            if apagar_lote(sqlite_path, lotes_dir, lote_id):
                resultado["apagados"] += 1
                resultado["ids"].append(lote_id)
                resultado["bytes"] += bytes_lote
                log.info("lote %s expurgado (%s, criado em %s, %.1f MB)",
                         lote_id, lote["status"], lote["criado_em"],
                         bytes_lote / 1048576)
        except Exception:                        # noqa: BLE001
            # Um lote problemático não pode impedir o expurgo dos outros: se a
            # faxina parasse no primeiro erro, bastaria uma pasta travada para o
            # volume voltar a crescer sem ninguém notar.
            log.exception("lote %s: falhou ao expurgar", lote_id)

    return resultado
