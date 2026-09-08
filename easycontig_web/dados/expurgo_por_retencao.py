"""
expurgo_por_retencao.py — quando o .ab1 que o laboratorio subiu deixa de existir aqui, e quem o apaga.

No desktop o dado nunca saia da maquina de quem monta. Na web ele passa a morar
num servidor — e ficava PARA SEMPRE. Sao corridas Sanger NAO publicadas: volume
que so cresce e risco (dado de terceiro sem prazo) e conta de disco (~26 MB por
corrida de 40). Este modulo NAO decide a politica: quem tem o dado e quem define
o prazo (EASYCONTIG_RETENCAO_DIAS; 0 = nunca apagar, que e o padrao de quem nao
decidiu nada). E se todos os lotes vencem de uma vez, a faxina TRAVA e nao apaga
nada — isso nao e passagem do tempo, e relogio errado ou prazo mal posto.


COMO FUNCIONA — uma passada da faxina (o trabalhador chama a cada INTERVALO_FAXINA)
==================================================================================

    faxina(banco, cfg.pasta_de_lotes, cfg.retencao_dias) -> BalancoDaFaxina
         |
         |  dias <= 0 -> "retenção desligada", balanco zerado, NADA apagado
         |
         |  lotes = FilaDeLotes(banco).listar(limite=0)          TODOS, de todas as contas
         |  vencidos = expirados(lotes, dias, agora)             criado_em + dias <= agora, do mais antigo ao mais novo
         |     recebendo / rodando: INTOCAVEIS, idade nenhuma justifica (outro processo esta escrevendo ali AGORA)
         |     criado_em ilegivel: fica, e aparece no log (data estranha NUNCA vira "muito velho")
         |
         |  freio: vencidos >= 6 E mais da metade do acervo E EASYCONTIG_EXPURGO_EM_MASSA != 1
         |     -> "N de M lotes venceriam de uma vez — isso não parece passagem do tempo ..." no log e no balanco; NADA apagado
         |
         |  para cada vencido:  bytes = bytes_da_pasta(pasta)  ->  apagar_lote(banco, pasta_de_lotes, id)
         |     falhou um? log com a pilha e SEGUE: uma pasta travada nao pode fazer o volume voltar a crescer em silencio
         v
    balanco.apagados / ids / bytes / travado


    apagar_lote(banco, pasta_de_lotes, lote_id) -> True apagou algo / False nao havia nada ou esta em uso
         |
         |  nome_simples(lote_id)                 '..' aqui seria rm -rf na pasta de cima
         |  numa transacao so (BEGIN IMMEDIATE):
         |     arvore em montagem deste lote?    -> False (o MrBayes esta lendo trabalho/*.cons.fa com o lote em `pronto`)
         |     DELETE lotes WHERE id=? AND status NOT IN intocaveis     A LINHA SAI PRIMEIRO: e o banco que arbitra, nao o SELECT de antes
         |     0 linhas e o lote existe          -> esta em uso (recebendo/rodando), False
         |     apagou a linha                    -> os pedidos de arvore vao junto
         |  rmtree(pasta, ignore_errors) e confere: sobrou coisa -> log de erro "remova na mão" (nao derruba a faxina)
         v
    (a cota nao precisa ser avisada: a memoria curta dela se invalida sozinha ao ver que a pasta sumiu)


COMO USAR
=========

    from easycontig_web.dados import expurgo_por_retencao as expurgo

    balanco = expurgo.faxina(banco, cfg.pasta_de_lotes, cfg.retencao_dias)       #* o trabalhador, a cada INTERVALO_FAXINA
    if balanco.apagados:
        REGISTRO.info('%d lote(s), %s liberados', balanco.apagados, tamanho(balanco.bytes))

    expurgo.apagar_lote(banco, cfg.pasta_de_lotes, lote_id)                       #* "apagar agora" da tela; False -> 409 "em uso"
    expurgo.expira_em(lote, cfg.retencao_dias)                                     #* datetime para a tela avisar, ou None

    python -m easycontig_web.dados.expurgo_por_retencao [dias]                     #* mostra o que venceria; apaga so se confirmar


O QUE MUDOU DO expurgo_por_retencao.py ORIGINAL (354 linhas)
============================================================

 1. As funcoes recebem o BancoSqlite e a pasta dos lotes, nao o caminho do
    sqlite: `faxina(banco, pasta_de_lotes, dias, agora=None)`,
    `apagar_lote(banco, pasta_de_lotes, lote_id)`. Os lotes vem de
    `FilaDeLotes.listar(limite=0)` (objetos Lote), nao de um SELECT proprio;
    `expirados(lotes, dias, agora=None)` e uma funcao pura sobre essa lista
    (era `lotes_expirados(sqlite_path, dias=, agora=)`, que ia ao banco).
    `expira_em(lote, dias)` le `lote.criado_em` (era dict ou sqlite3.Row).

 2. `apagar_lote` faz a conferencia da arvore, o DELETE do lote e o DELETE
    dos pedidos numa transacao so (`banco.transacao()`, BEGIN IMMEDIATE). No
    original o SELECT da arvore e o DELETE eram passos soltos na mesma
    conexao: entre um e outro cabia um `reivindicar` de arvore.

 3. A tolerancia a "tabela arvores nao existe" saiu: `BancoSqlite.preparar()`
    cria as 5 tabelas antes de qualquer processo trabalhar, e tanto o
    trabalhador (faxina) quanto o servidor (apagar agora) preparam ao subir.
    Se a tabela sumir mesmo assim, o erro sobe e aparece no log com a pilha —
    que e melhor do que fingir que nao ha arvore.

 4. `tamanho_em_bytes` saiu: e `cota_de_espaco.bytes_da_pasta` (a unica
    medida de pasta da web; a diferenca era lstat x stat, que so muda para
    link simbolico, e a pasta de um lote nao tem nenhum). `_total_de_lotes`
    (um COUNT com `except: 0`) saiu: o total e o tamanho da lista ja lida.
    `dias_configurados` e `DIAS_PADRAO`/`DIAS_SUGERIDO` moram em
    configuracao.py (`dias_de_retencao`, `DIAS_DE_RETENCAO_*`) desde a
    configuracao; `_nome_simples` e `configuracao.nome_simples`.

 5. O resultado da faxina e um `BalancoDaFaxina` (ids, bytes, travado;
    `apagados` = len(ids)), nao um dict de 4 chaves.

 6. `expirados` ordena pela data lida (datetime), nao pelo texto de criado_em:
    da no mesmo quando tudo e ISO em UTC (que e o que a fila grava) e fica
    certo se um dia houver fuso misturado.

 7. Os textos de log sao os mesmos, no registrador `easycontig.retencao`.


POR QUE NAO NASCE CLASSE
========================

A faxina nao guarda nada entre uma passada e outra: cada passada le a fila e o
disco de novo, o freio e por passada, e apagar e uma acao com comeco e fim. O
que ela precisa (banco, pasta, prazo) vem por parametro de quem ja os tem — o
trabalhador e a rota. O unico objeto e o balanco devolvido, com nome em cada
campo.
"""

import logging

import os

import shutil

from datetime import datetime, timedelta, timezone

from pathlib import Path

from easycontig_web.configuracao import nome_simples

from easycontig_web.contas.cota_de_espaco import bytes_da_pasta

from easycontig_web.processamento.fila_de_lotes import FilaDeLotes, RECEBENDO, RODANDO



REGISTRO = logging.getLogger('easycontig.retencao')

INTOCAVEIS = (RECEBENDO, RODANDO)  #! a pasta esta sendo escrita NESTE INSTANTE por outro processo (upload em curso ou o trabalhador montando); idade nenhuma justifica

FREIO_FRACAO = 0.5  #* um dia normal vence uma fatia; um relogio errado vence tudo: o freio pega com MAIS DA METADE do acervo...

FREIO_MINIMO = 6    #* ...e mais que um punhado: com 3 lotes no total, apagar 2 e rotina, nao catastrofe

VARIAVEL_EM_MASSA = 'EASYCONTIG_EXPURGO_EM_MASSA'  #* =1 solta o freio UMA vez: alguem decide, em vez de o relogio decidir




class BalancoDaFaxina:

    #* o que uma passada da faxina fez: quais lotes apagou, quanto liberou e, se travou, por que

    def __init__(self, ids=None, bytes=0, travado=''):

        self.ids = list(ids or [])  #* os lotes apagados, na ordem (do mais antigo ao mais novo)

        self.bytes = bytes  #* quanto as pastas ocupavam antes de sumir

        self.travado = travado  #* '' ou o motivo do freio (nada foi apagado)


    @property
    def apagados(self):

        return len(self.ids)


    def __repr__(self):

        return f'BalancoDaFaxina({self.apagados} apagados, {self.bytes} bytes' + (f', TRAVADA: {self.travado[:40]}...)' if self.travado else ')')




def quando(valor):

    #* ISO 8601 do banco -> datetime com fuso; None se nao der para ler
    #! data ilegivel NUNCA vira "muito velho": um lote sem data confiavel fica, e aparece no log. perder dado por uma string estranha nao tem desfazer

    if not valor:
        return None

    try:
        data = datetime.fromisoformat(valor)

    except ValueError:
        return None

    return data if data.tzinfo else data.replace(tzinfo=timezone.utc)




def expira_em(lote, dias):

    #* quando este lote sera apagado, para a tela AVISAR antes de o dado sumir. None com a retencao desligada ou criado_em ilegivel.
    #* vale para o lote parado: em recebendo/rodando a faxina nao o toca, entao a remocao real pode ser depois — nunca antes

    if dias <= 0:
        return None

    criado = quando(lote.criado_em)

    return criado + timedelta(days=dias) if criado else None




def expirados(lotes, dias, agora=None):

    #* os lotes que ja passaram do prazo, do mais antigo para o mais novo. `agora` e injetavel: nos testes a alternativa seria forjar datas de tres meses atras no banco

    if dias <= 0:
        return []

    agora = agora or datetime.now(timezone.utc)

    limite = agora - timedelta(days=dias)

    vencidos = []

    for lote in lotes:

        if lote.status in INTOCAVEIS:
            continue

        criado = quando(lote.criado_em)

        if criado is None:
            REGISTRO.warning('lote %s tem criado_em ilegível (%r); não será expurgado', lote.id, lote.criado_em)
            continue

        if criado <= limite:
            vencidos.append((criado, lote))

    return [lote for criado, lote in sorted(vencidos, key=lambda par: par[0])]




def freio_pega(vencidos, total):

    return vencidos >= FREIO_MINIMO and total > 0 and vencidos > total * FREIO_FRACAO




def em_massa_liberado():

    return os.environ.get(VARIAVEL_EM_MASSA) == '1'




def apagar_lote(banco, pasta_de_lotes, lote_id):

    #* apaga a linha da fila E a pasta do lote; False se nao havia nada ou se o lote esta em uso. Idempotente de proposito: a faxina,
    #* o "apagar agora" da tela e um retry do trabalhador podem chegar na mesma pasta, e a segunda chamada tem de ser um silencio
    #! os dois somem juntos: linha sem pasta faz a pagina prometer um relatorio que nao existe; pasta sem linha e lixo invisivel

    nome_simples(lote_id)

    pasta = Path(pasta_de_lotes) / lote_id

    marcas = ','.join('?' * len(INTOCAVEIS))

    with banco.transacao() as con:

        #! uma arvore em montagem TAMBEM segura o lote: ela roda com o lote em `pronto` (o estado que a faxina considera livre)
        #! e o trabalhador le trabalho/*.cons.fa o tempo todo — apagar por baixo mataria o MCMC com um erro de arquivo sumido
        if con.execute('SELECT 1 FROM arvores WHERE lote_id=? AND status=?', (lote_id, RODANDO)).fetchone():

            REGISTRO.info('lote %s tem árvore em montagem; não foi apagado', lote_id)

            return False

        #! A LINHA SAI PRIMEIRO, e com o estado no WHERE: com mais de um trabalhador, entre o SELECT de quem listou os vencidos e este
        #! DELETE cabe um reivindicar marcando o lote como rodando. Quem perde a corrida apaga 0 linhas e desiste
        linhas = con.execute(f'DELETE FROM lotes WHERE id=? AND status NOT IN ({marcas})', (lote_id, *INTOCAVEIS)).rowcount

        ocupado = linhas == 0 and con.execute('SELECT 1 FROM lotes WHERE id=?', (lote_id,)).fetchone() is not None

        if linhas:
            con.execute('DELETE FROM arvores WHERE lote_id=?', (lote_id,))  #* montar a arvore de um lote apagado nao e trabalho pendente, e trabalho sem sentido

    if ocupado:

        REGISTRO.info('lote %s está em uso; não foi apagado', lote_id)

        return False

    tinha_pasta = pasta.exists()

    if tinha_pasta:

        shutil.rmtree(pasta, ignore_errors=True)  #* volume montado com dono errado e o caso real: metade apaga e metade nao. nao levanta: derrubar a faxina deixaria os outros vencidos no disco

        if pasta.exists():
            REGISTRO.error('lote %s: sobrou coisa em %s (permissão?) — remova na mão', lote_id, pasta)

    if tinha_pasta and not linhas:
        REGISTRO.warning('lote %s tinha pasta em disco e nenhuma linha na fila', lote_id)

    return bool(tinha_pasta or linhas)




def faxina(banco, pasta_de_lotes, dias, agora=None):

    #* uma passada do expurgo. dias=0 nao apaga NADA: servidor que apaga dado de laboratorio porque uma variavel nao foi definida
    #* e pior que servidor que acumula — o acumulo da para ver no disco; o apagado, nao

    balanco = BalancoDaFaxina()

    if dias <= 0:

        REGISTRO.info('retenção desligada (dias=0): nenhum lote será apagado')

        return balanco

    lotes = FilaDeLotes(banco).listar(limite=0)

    vencidos = expirados(lotes, dias, agora)

    #! medido em 06/08/2026: com 20 corridas do DIA e retencao de 30 dias, um relogio um ano a frente apagaria AS VINTE numa passada.
    #! salto de relogio nao e hipotese exotica: NTP corrigindo a maquina virtual, BIOS zerada, fuso errado na primeira subida
    if freio_pega(len(vencidos), len(lotes)) and not em_massa_liberado():

        balanco.travado = (f'{len(vencidos)} de {len(lotes)} lotes venceriam de uma vez — isso não parece passagem do tempo. '
                           f'Confira o relógio do servidor e o EASYCONTIG_RETENCAO_DIAS (={dias}). NADA foi apagado. '
                           f'Se for mesmo para apagar, suba uma vez com {VARIAVEL_EM_MASSA}=1.')

        REGISTRO.error('faxina TRAVADA: %s', balanco.travado)

        return balanco

    for lote in vencidos:

        try:
            bytes_do_lote = bytes_da_pasta(Path(pasta_de_lotes) / lote.id)

            if apagar_lote(banco, pasta_de_lotes, lote.id):

                balanco.ids.append(lote.id)

                balanco.bytes += bytes_do_lote

                REGISTRO.info('lote %s expurgado (%s, criado em %s, %.1f MB)', lote.id, lote.status, lote.criado_em, bytes_do_lote / 1048576)

        except Exception:
            REGISTRO.exception('lote %s: falhou ao expurgar', lote.id)  #* um lote problematico nao pode impedir o expurgo dos outros

    return balanco




def main():

    import sys

    from easycontig_web import configuracao
    from easycontig_web.contas.cota_de_espaco import tamanho
    from easycontig_web.dados.banco_sqlite import BancoSqlite

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    cfg = configuracao.carregar()

    dias = int(sys.argv[1]) if len(sys.argv) > 1 else cfg.retencao_dias

    if not cfg.caminho_do_sqlite.is_file():

        print(f'a fila ainda nao existe em {cfg.caminho_do_sqlite} (o servidor a cria ao subir): nada a expurgar')

        return

    if dias <= 0:

        print(f'retencao DESLIGADA (EASYCONTIG_RETENCAO_DIAS={cfg.retencao_dias}): nenhum lote e apagado automaticamente.')

        print(f'para simular um prazo: python -m easycontig_web.dados.expurgo_por_retencao {configuracao.DIAS_DE_RETENCAO_SUGERIDO}')

        return

    banco = BancoSqlite(cfg.caminho_do_sqlite)

    lotes = FilaDeLotes(banco).listar(limite=0)

    vencidos = expirados(lotes, dias)

    print(f'prazo: {dias} dias' + ('' if dias == cfg.retencao_dias else f' (simulado; o configurado e {cfg.retencao_dias})') + f'; {len(lotes)} lotes na fila, {len(vencidos)} vencidos')

    total = 0

    for lote in vencidos:

        ocupa = bytes_da_pasta(cfg.pasta_de_lotes / lote.id)

        total += ocupa

        print(f'  {lote.id:14}  {lote.status:9}  criado em {lote.criado_em[:10]}  {lote.dono:30}  {tamanho(ocupa)}')

    if not vencidos:
        return

    print(f'  total: {tamanho(total)}')

    if freio_pega(len(vencidos), len(lotes)) and not em_massa_liberado():
        print(f'o freio vai pegar: {len(vencidos)} de {len(lotes)} e mais da metade do acervo. Confira o relogio e o prazo; se for mesmo para apagar, rode com {VARIAVEL_EM_MASSA}=1')

    if input('apagar agora? (s/N): ').strip().lower() != 's':
        return

    balanco = faxina(banco, cfg.pasta_de_lotes, dias)

    print(f'{balanco!r}')

    if balanco.travado:
        print(balanco.travado)




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
