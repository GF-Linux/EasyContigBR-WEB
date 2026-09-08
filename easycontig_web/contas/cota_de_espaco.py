"""
cota_de_espaco.py — quanto UMA CONTA ocupa (corridas + bancos proprios) e se ela pode enviar mais uma corrida agora.

Os tetos por lote (400 arquivos, 300 MB) seguram o envio unico absurdo e mais
nada: 50 lotes legitimos enchem o disco sem cruzar teto nenhum. E disco cheio
no meio de um upload e o defeito de sempre — metade dos arquivos gravados e
relatorio com menos amostras do que a corrida tem. Este modulo NAO levanta
excecao e NAO decide status HTTP: responde "como esta esta conta agora" e
deixa a rota traduzir em 413. E a MESMA medida serve para barrar o envio e
para dizer na tela quanto sobra — se fossem duas, uma envelheceria e a tela
mentiria.


COMO FUNCIONA — o que entra na conta
====================================

    cotas = Cotas(lotes, estante, cfg.pasta_de_lotes)       (uma vez; le os tetos do ambiente)
         |     EASYCONTIG_MAX_LOTES_ATIVOS   10        corridas em recebendo/na_fila/rodando ao mesmo tempo
         |     EASYCONTIG_MAX_BYTES_CONTA    2 GiB     o acumulado da conta: ~3 200 pares de 650 KB ou ~80 corridas de 26 MB
         |     EASYCONTIG_MAX_BANCOS_CONTA   12        bancos proprios (nao expiram: a faxina so apaga lote)
         |     0 = sem limite; ilegivel ou negativo = o padrao (um .env com erro de digitacao nao pode valer "sem limite")
         v
    cotas.situacao(email) -> Cota
         |
         |  bancos proprios: estante.meus_bancos(email) -> quantos, e os bytes de cada pasta
         |  corridas: lotes.listar(dono=email, limite=0) -> TODAS, inclusive pronto/falhou (continuam ocupando disco)
         |     a fila nao respondeu? -> Cota que DEIXA enviar (o novo_lote logo adiante falha de forma visivel se o banco estiver mesmo quebrado)
         |  bytes de cada corrida: o DISCO e a unica fonte (ninguem grava tamanho de lote em lugar nenhum)
         |     terminada (pronto/falhou): pasta nao muda mais -> mede UMA vez e guarda em `medidos`; pasta sumiu -> 0 e tira da memoria
         |     ativa (recebendo/na_fila/rodando): esta sendo escrita agora -> mede toda vez
         |
         |  ativos >= teto?  -> pode_enviar=False, "Você tem 10 corridas ainda em processamento (o limite é 10); espere ..."
         |  usados >= teto?  -> pode_enviar=False, "Suas corridas e bancos já ocupam 2,0 GB dos 2,0 GB reservados ..."
         v
    cota.lotes_ativos / max_lotes_ativos / bytes_usados / max_bytes_conta / pode_enviar / motivo / bancos / max_bancos
         (a pagina inicial e o perfil mostram; o envio recusa com 413 e o motivo; a montagem de banco confere bancos/max_bancos)


COMO USAR
=========

    from easycontig_web.contas import cota_de_espaco

    cotas = cota_de_espaco.Cotas(lotes, estante, cfg.pasta_de_lotes)
    cota = cotas.situacao(u.email)
    if not cota.pode_enviar:
        raise HTTPException(413, cota.motivo)
    lotes.novo_lote(..., teto_ativos=cotas.max_lotes_ativos, teto_bytes=cota.max_bytes_conta, usados_em_disco=cota.bytes_usados)

    cota_de_espaco.Cotas(lotes, estante, pasta, max_lotes_ativos=1, max_bytes_conta=1000, max_bancos=0)   #* tetos a mao (testes)
    cota_de_espaco.tamanho(1536 * 1024)                                                                 #* '1,5 MB'

    python -m easycontig_web.contas.cota_de_espaco      #* tetos em vigor e a situacao de uma conta


O QUE MUDOU DO cota_de_espaco.py ORIGINAL (241 linhas)
=====================================================

 1. `situacao(sqlite_path, lotes_dir, email, data_dir)` virou Cotas.situacao(email):
    o objeto guarda a fila de lotes, a estante de bancos e a pasta dos lotes.
    A memoria curta dos tamanhos (`_medidos`, global do modulo) mora nele.

 2. Os tetos sao lidos do ambiente ao criar o objeto, nao a cada chamada;
    a rota de envio passa `cotas.max_lotes_ativos` ao novo_lote no lugar de
    `teto_lotes_ativos()`. As tres funcoes de teto continuam existindo para
    quem quiser ler o ambiente.

 3. Os lotes vem de `FilaDeLotes.listar(dono, limite=0)` (objetos Lote, com
    `.ativo`), nao de um SELECT proprio; os bytes dos bancos vem do
    `EstadoDoBanco.bytes` que `meus_bancos` ja calcula — o original chamava
    meus_bancos duas vezes (contar e somar) e percorria cada pasta de novo.

 4. A fila que nao responde vai para o log (aviso com o erro); o original
    engolia em silencio. A decisao e a mesma: deixa enviar.

 5. Cota e uma classe simples (era dataclass congelada), mesmos 8 campos, na
    mesma ordem. `_inteiro_do_ambiente` virou configuracao.inteiro_do_ambiente
    (a mesma do limite_de_requisicoes); `_tamanho`/`_corridas`/`_bytes_do_lote`
    /`_TERMINADOS` viraram tamanho/corridas/Cotas.bytes_do_lote/TERMINADOS.

 6. `bytes_dos_bancos(data_dir, email)` foi absorvida por situacao e
    `esquecer(lote_id)` saiu: nenhum chamador (nem os testes, que zeravam
    `_medidos` direto). `Cotas.medidos` e um dicionario comum: `pop`/`clear`
    fazem o mesmo. `bytes_da_pasta` fica publica para o expurgo reaproveitar
    (o original tinha uma segunda copia la, `tamanho_em_bytes`).


POR QUE NASCE CLASSE
====================

Cotas e dona da memoria curta dos tamanhos — que cresce a cada lote terminado,
e invalidada quando a pasta some e e zerada nos testes — e junta as tres
coisas de que a medida depende (fila, estante, pasta). Cota e o valor
devolvido, com nome em cada campo, que a tela e a rota leem. Medir uma pasta e
escrever bytes em texto e funcao.
"""

import logging

import os

from pathlib import Path

from easycontig_web.configuracao import inteiro_do_ambiente

from easycontig_web.processamento.fila_de_lotes import PRONTO, FALHOU



REGISTRO = logging.getLogger('easycontig.cota')

MAX_LOTES_ATIVOS_PADRAO = 10  #! 10, e nao 3: o uso real e PAR A PAR e cada par e um lote; medido no Deck, um par fica pronto em 582 ms, 10 ativos sao ~6 s

MAX_BYTES_CONTA_PADRAO = 2 * 1024 * 1024 * 1024  #* o acumulado da conta ao longo do tempo (o .ab1 nao e apagado depois de processar)

MAX_BANCOS_PADRAO = 12  #* folgado para o uso real (o catalogo inteiro tem menos), e continua sendo um numero — que era o que faltava

TERMINADOS = (PRONTO, FALHOU)  #* pasta que nao muda mais: so a faxina a apaga inteira




class Cota:

    #* a resposta de situacao(): o que a conta ja usa, de quanto, e se pode enviar

    def __init__(self, lotes_ativos, max_lotes_ativos, bytes_usados, max_bytes_conta, pode_enviar, motivo, bancos=0, max_bancos=0):

        self.lotes_ativos = lotes_ativos  #* em recebendo / na_fila / rodando

        self.max_lotes_ativos = max_lotes_ativos  #* 0 = sem limite

        self.bytes_usados = bytes_usados  #* o que esta conta ocupa em disco (corridas + bancos)

        self.max_bytes_conta = max_bytes_conta  #* 0 = sem limite

        self.pode_enviar = pode_enviar

        self.motivo = motivo  #* vazio quando pode; frase pronta para a tela quando nao

        self.bancos = bancos  #* quantos bancos proprios a conta mantem

        self.max_bancos = max_bancos  #* 0 = sem limite


    def __repr__(self):

        return f'Cota({self.lotes_ativos}/{self.max_lotes_ativos} ativos, {tamanho(self.bytes_usados)}/{tamanho(self.max_bytes_conta)}, {self.bancos}/{self.max_bancos} bancos, {"pode" if self.pode_enviar else "nao pode"})'




def teto_lotes_ativos():

    return inteiro_do_ambiente('EASYCONTIG_MAX_LOTES_ATIVOS', MAX_LOTES_ATIVOS_PADRAO)




def teto_bytes_conta():

    return inteiro_do_ambiente('EASYCONTIG_MAX_BYTES_CONTA', MAX_BYTES_CONTA_PADRAO)




def teto_bancos_conta():

    return inteiro_do_ambiente('EASYCONTIG_MAX_BANCOS_CONTA', MAX_BANCOS_PADRAO)




def tamanho(n):

    #* bytes em texto de gente, com virgula decimal: 0 -> '0 MB', 1,5 MiB -> '1,5 MB', 300 MiB -> '300 MB', 2 GiB -> '2,0 GB'

    mb = n / (1024 * 1024)

    if mb >= 1024:
        return f'{mb / 1024:.1f} GB'.replace('.', ',')

    if mb >= 10 or n == 0:
        return f'{mb:.0f} MB'

    return f'{mb:.1f} MB'.replace('.', ',')




def corridas(n):

    return '1 corrida' if n == 1 else f'{n} corridas'




def bytes_da_pasta(raiz):

    #* soma o que a pasta ocupa, com as subpastas. pasta ausente e 0, nao erro; arquivo que sumiu entre listar e medir some da
    #* conta, nao derruba o envio de quem esta esperando na tela

    total = 0

    for pasta, subpastas, arquivos in os.walk(raiz, onerror=lambda erro: None):

        for nome in arquivos:

            try:
                total += os.stat(os.path.join(pasta, nome)).st_size

            except OSError:
                continue

    return total




class Cotas:

    #* mede a conta contra os tetos; guarda o tamanho das corridas terminadas para nao percorrer o historico inteiro a cada visita
    #! medido em 06/08/2026: com 40 corridas de 80 arquivos guardadas, medir tudo no disco gastava 23 ms — 68% do tempo da pagina inicial

    def __init__(self, lotes, estante, pasta_de_lotes, max_lotes_ativos=None, max_bytes_conta=None, max_bancos=None):

        self.lotes = lotes  #* FilaDeLotes

        self.estante = estante  #* EstanteDeBancos: os bancos proprios entram na conta (nao expiram, entao tem de ser vistos)

        self.pasta_de_lotes = Path(pasta_de_lotes)

        self.max_lotes_ativos = teto_lotes_ativos() if max_lotes_ativos is None else max_lotes_ativos

        self.max_bytes_conta = teto_bytes_conta() if max_bytes_conta is None else max_bytes_conta

        self.max_bancos = teto_bancos_conta() if max_bancos is None else max_bancos

        self.medidos = {}  #* lote_id -> bytes, so das corridas terminadas; a entrada vale enquanto a pasta existir


    def bytes_do_lote(self, lote):

        #* ativa: esta sendo escrita agora, mede toda vez. terminada: mede uma vez; a entrada so vale se a pasta AINDA existe
        #* (um os.stat, nao um walk), entao um lote apagado a mao some da conta na visita seguinte

        raiz = self.pasta_de_lotes / lote.id

        if lote.status not in TERMINADOS:
            return bytes_da_pasta(raiz)

        if not raiz.exists():

            self.medidos.pop(lote.id, None)

            return 0

        if lote.id not in self.medidos:
            self.medidos[lote.id] = bytes_da_pasta(raiz)

        return self.medidos[lote.id]


    def situacao(self, email):

        #* como esta a conta agora: o que ja usa, de quanto, e se pode enviar. Nunca levanta: a cota e limite de convivencia,
        #* nao trava de seguranca

        bancos = self.estante.meus_bancos(email)

        bytes_dos_bancos = sum(estado.bytes for estado in bancos)

        try:
            lotes = self.lotes.listar(dono=email, limite=0)

        except Exception as erro:

            REGISTRO.warning('a fila não respondeu ao medir a cota de %s (%s: %s); deixando enviar', email, type(erro).__name__, erro)

            return Cota(0, self.max_lotes_ativos, bytes_dos_bancos, self.max_bytes_conta, True, '', len(bancos), self.max_bancos)

        ativos = sum(1 for lote in lotes if lote.ativo)

        usados = sum(self.bytes_do_lote(lote) for lote in lotes) + bytes_dos_bancos  #* TODAS as corridas, inclusive pronto e falhou: continuam ocupando

        motivo = ''

        if self.max_lotes_ativos and ativos >= self.max_lotes_ativos:
            motivo = (f'Você tem {corridas(ativos)} ainda em processamento (o limite é {self.max_lotes_ativos}); '
                      f'espere alguma terminar para enviar outra.')

        elif self.max_bytes_conta and usados >= self.max_bytes_conta:
            motivo = (f'Suas corridas e bancos já ocupam {tamanho(usados)} dos {tamanho(self.max_bytes_conta)} reservados para esta conta. '
                      f'Apague uma corrida antiga ou um banco próprio antes de enviar esta.')

        return Cota(ativos, self.max_lotes_ativos, usados, self.max_bytes_conta, not motivo, motivo, len(bancos), self.max_bancos)


    def __repr__(self):

        return f'Cotas({self.pasta_de_lotes}, {self.max_lotes_ativos} ativos, {tamanho(self.max_bytes_conta)}, {self.max_bancos} bancos, {len(self.medidos)} medidos)'




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados.banco_sqlite import BancoSqlite
    from easycontig_web.processamento.fila_de_lotes import FilaDeLotes
    from easycontig_web.processamento.executor_de_lote import estante_de_bancos

    cfg = configuracao.carregar()

    if not cfg.caminho_do_sqlite.is_file():
        print(f'aviso: a fila ainda nao existe em {cfg.caminho_do_sqlite} (o servidor a cria ao subir); a conta sera medida so pelos bancos proprios')

    cotas = Cotas(FilaDeLotes(BancoSqlite(cfg.caminho_do_sqlite)), estante_de_bancos(cfg), cfg.pasta_de_lotes)

    print(f'{cotas!r}')

    print(f'  corridas ativas por conta: {cotas.max_lotes_ativos or "sem limite"}   (EASYCONTIG_MAX_LOTES_ATIVOS)')

    print(f'  disco por conta:           {tamanho(cotas.max_bytes_conta) if cotas.max_bytes_conta else "sem limite"}   (EASYCONTIG_MAX_BYTES_CONTA)')

    print(f'  bancos proprios por conta: {cotas.max_bancos or "sem limite"}   (EASYCONTIG_MAX_BANCOS_CONTA)')

    email = input('\ne-mail da conta a medir (enter para sair): ').strip()

    if not email:
        return

    cota = cotas.situacao(email)

    print(f'\n{cota!r}')

    print(f'  corridas ativas: {cota.lotes_ativos}' + (f' de {cota.max_lotes_ativos}' if cota.max_lotes_ativos else ''))

    print(f'  disco ocupado:   {tamanho(cota.bytes_usados)}' + (f' de {tamanho(cota.max_bytes_conta)}' if cota.max_bytes_conta else ''))

    print(f'  bancos proprios: {cota.bancos}' + (f' de {cota.max_bancos}' if cota.max_bancos else ''))

    print(f'  pode enviar:     {"sim" if cota.pode_enviar else "nao — " + cota.motivo}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
