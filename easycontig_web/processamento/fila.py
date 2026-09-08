"""
fila.py — o que as duas filas do SQLite (lotes e arvores) tem em comum: pegar, reivindicar, carimbar so pelo dono, pulso e orfaos.

A web tem duas filas na mesma base: a de lotes (a corrida que o usuario
enviou) e a de arvores (a filogenia pedida sobre uma corrida pronta). As duas
tabelas tem as mesmas colunas de fila (id, status, etapa, erro, trabalhador,
criado_em, iniciado_em, terminado_em) e o mesmo protocolo: o trabalhador
reivindica o registro mais antigo em NA_FILA, grava progresso, e no fim
carimba PRONTO ou FALHOU — mas so se AINDA for o dono. Este arquivo e esse
protocolo, escrito uma vez; FilaDeLotes e FilaDeArvores dizem a tabela, o
formato do registro e o que so elas fazem.


COMO FUNCIONA — a vida de um registro em qualquer fila
======================================================

    NA_FILA ----reivindicar(eu)----> RODANDO ----concluir(id, eu)----> PRONTO
       ^          (o mais antigo;       |   \\
       |           UPDATE condicional:  |    '---falhar(id, erro, eu)--> FALHOU
       |           quem perde a corrida |
       |           pega 0 linhas e      '---- gravar_do_dono(...): so grava se `trabalhador` da linha == eu
       |           tenta o proximo)           (eu='' grava sem conferir: e o servidor web marcando falha
       |                                       num lote que ninguem reivindicou)
       |
       '---- reenfileirar_orfaos(eu): RODANDO cujo dono NAO esta pulsando volta para NA_FILA
             (dono vazio, banco anterior a migracao 3: so volta se NINGUEM esta vivo)

    pulso:  pulsar(eu) a cada INTERVALO_PULSO s   ->   trabalhadores_vivos() = quem pulsou ha <= PULSO_VELHO s
            fila_atendida() = o pulso mais novo tem <= PULSO_VELHO s (a tela deixa de prometer o que ninguem vai cumprir)


COMO USAR
=========

    from easycontig_web.dados import banco_sqlite
    from easycontig_web.processamento import fila_de_lotes, fila_de_arvores

    banco = banco_sqlite.BancoSqlite(cfg.caminho_do_sqlite)
    lotes = fila_de_lotes.FilaDeLotes(banco)             #* as duas herdam de Fila
    arvores = fila_de_arvores.FilaDeArvores(banco)

    lote = lotes.reivindicar('vps-1234')                  #* Lote em RODANDO, ou None se a fila esta vazia
    lotes.falhar(lote.id, 'ValueError: ...', 'vps-1234')  #* True se eu ainda era o dono
    lotes.pulsar('vps-1234')                              #* a cada 5 s, numa thread
    lotes.fila_atendida()                                 #* a pagina do lote pergunta antes de dizer "na fila"
    lotes.reenfileirar_orfaos('vps-1234')                 #* no arranque do trabalhador; idem em arvores

    python -m easycontig_web.processamento.fila           #* quem esta pulsando e ha quanto tempo


O QUE MUDOU DO ORIGINAL (fila_de_lotes.py 460 linhas + fila_de_arvores.py 176 linhas)
=====================================================================================

 1. O protocolo comum estava escrito duas vezes (pegar, reivindicar, _dono_confere,
    falhar, reenfileirar_orfaos — copiados letra por letra de uma fila para a
    outra). Agora e a classe Fila; cada fila concreta so diz `tabela` e monta o
    seu registro.

 2. Os carimbos do dono (progresso, concluir, falhar, etapa) eram quatro UPDATEs
    escritos a mao com o mesmo `WHERE id=? AND trabalhador=?`. Virou UM metodo,
    gravar_do_dono(id, trabalhador, **campos), e cada carimbo e uma linha.

 3. reivindicar devolve o registro COMO FICOU (status RODANDO, iniciado_em e
    trabalhador preenchidos). O original devolvia a linha lida ANTES do UPDATE:
    status 'na_fila' e trabalhador '' num lote que acabou de virar RODANDO.

 4. O pulso mora na base: as duas filas respondem pulsar, trabalhadores_vivos e
    fila_atendida (e a mesma tabela `pulso`). No original so a fila de lotes
    sabia, e reenfileirar_orfaos da fila de arvores recebia `vivos` de fora.
    Agora as duas tem a mesma assinatura: reenfileirar_orfaos(eu).

 5. Quem recebe o relogio nos testes chama o parametro de `momento` (datetime
    com fuso); `agora()` e a funcao do banco_sqlite que carimba as linhas.

 6. Registro e objeto, nao dict: FilaDeLotes devolve Lote e FilaDeArvores devolve
    PedidoDeArvore (lote.status, e nao lote['status']; os templates ja liam
    lote.id). O que cada um tem esta no arquivo de cada fila.

 7. Nada foi cortado: cada funcao do original tem chamador na web (trabalhador,
    rotas, cota, expurgo, /saude) e esta aqui ou nas filas concretas.


POR QUE NASCE CLASSE
====================

    Fila e uma tabela com trava de dono dentro de um recurso externo (o
    BancoSqlite), e TODA operacao precisa do banco e do nome da tabela — esse e
    o estado. No original cada funcao recebia o caminho do arquivo e repetia a
    tabela no SQL; agora o banco entra uma vez, e a tabela e um atributo da
    classe. As regras que nao guardam estado (condicao_do_dono, momento_de,
    e_orfao) ficam funcoes.
"""

import sqlite3

from datetime import datetime, timezone

from easycontig_web.dados.banco_sqlite import agora



NA_FILA, RODANDO, PRONTO, FALHOU = 'na_fila', 'rodando', 'pronto', 'falhou'  #* os estados que as duas filas tem; RECEBENDO e so dos lotes

INTERVALO_PULSO = 5.0  #* segundos entre dois pulsos do trabalhador

PULSO_VELHO = 90.0  #* pulso mais velho que isto = trabalhador morto; folga larga porque um lote grande segura o laco

TAMANHO_DA_ETAPA = 120  #* a etapa que a tela mostra enquanto roda; cortada para nao encher a tabela

TAMANHO_DO_ERRO = 2000  #* idem para o erro gravado em FALHOU




def condicao_do_dono(trabalhador):

    #* o pedaco de WHERE que faz um UPDATE so acontecer se o trabalhador ainda e o dono da linha.
    #* trabalhador vazio = grava sem conferir (o servidor web marcando falha em lote que ninguem reivindicou)

    if trabalhador:
        return ' AND trabalhador=?', (trabalhador,)

    return '', ()




def momento_de(texto):

    #* a data ISO gravada no banco como datetime com fuso (UTC se veio sem); None se ilegivel

    try:
        momento = datetime.fromisoformat(texto)

    except (ValueError, TypeError):
        return None

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    return momento




def e_orfao(dono, vivos):

    #* o dono existe: so e orfao se morreu. sem dono (banco anterior a migracao 3): so se ninguem esta vivo —
    #* na duvida nao se toca no trabalho alheio; o preco de esperar e uma pagina girando, o de errar e duas montagens misturadas

    if dono:
        return dono not in vivos

    return not vivos




class Fila:

    #* o protocolo comum das duas filas; FilaDeLotes e FilaDeArvores dizem a tabela e montam o registro

    tabela = ''  #* nome da tabela no SQLite ('lotes' ou 'arvores'); as filas concretas preenchem


    def __init__(self, banco):

        self.banco = banco  #* o BancoSqlite; toda operacao abre a conexao por ele


    def registro(self, linha):

        #* a linha do SQLite como o objeto que a fila devolve; a base devolve dict, as filas concretas o seu objeto

        return dict(linha)


    def pegar(self, registro_id):

        #* o registro pelo id, ou None

        with self.banco.conectar() as con:
            linha = con.execute(f'SELECT * FROM {self.tabela} WHERE id=?', (registro_id,)).fetchone()

        return self.registro(linha) if linha else None


    def reivindicar(self, trabalhador=''):

        #* marca o registro mais antigo em NA_FILA como RODANDO e devolve como ficou — ou None se a fila esta vazia.
        #* o UPDATE condicional e o que deixa dois trabalhadores dividirem a fila: quem perde a corrida atualiza 0 linhas

        with self.banco.transacao() as con:

            linha = con.execute(f'SELECT id FROM {self.tabela} WHERE status=? ORDER BY criado_em LIMIT 1', (NA_FILA,)).fetchone()

            if not linha:
                return None

            gravou = con.execute(
                f'UPDATE {self.tabela} SET status=?, iniciado_em=?, trabalhador=? WHERE id=? AND status=?',
                (RODANDO, agora(), trabalhador, linha['id'], NA_FILA)).rowcount

            if gravou != 1:
                return None

            linha = con.execute(f'SELECT * FROM {self.tabela} WHERE id=?', (linha['id'],)).fetchone()

        return self.registro(linha)


    def gravar_do_dono(self, registro_id, trabalhador, **campos):

        #* grava os campos na linha do id, mas so se `trabalhador` ainda e o dono (ou se trabalhador='').
        #* True se gravou. False = perdi o registro: outro trabalhador esta com ele, e carimbar por cima
        #* publicaria a minha montagem no lugar da dele. os nomes dos campos vem do codigo, nunca do usuario

        condicao, extra = condicao_do_dono(trabalhador)

        atribuicoes = ', '.join(f'{campo}=?' for campo in campos)

        with self.banco.conectar() as con:
            return con.execute(
                f'UPDATE {self.tabela} SET {atribuicoes} WHERE id=?{condicao}',
                (*campos.values(), registro_id, *extra)).rowcount == 1


    def falhar(self, registro_id, erro, trabalhador=''):

        #* FALHOU com o erro que a pagina vai mostrar; True se eu ainda era o dono

        return self.gravar_do_dono(registro_id, trabalhador, status=FALHOU, terminado_em=agora(), erro=erro[:TAMANHO_DO_ERRO], etapa='')


    def pulsar(self, trabalhador):

        #* registra que este trabalhador esta vivo e olhando esta base (upsert na tabela pulso)

        with self.banco.conectar() as con:
            con.execute(
                'INSERT INTO pulso (trabalhador, visto_em) VALUES (?,?) ON CONFLICT(trabalhador) DO UPDATE SET visto_em=excluded.visto_em',
                (trabalhador, agora()))


    def trabalhadores_vivos(self, momento=None):

        #* quem bateu pulso ha menos de PULSO_VELHO segundos; banco que nao responde = ninguem

        momento = momento or datetime.now(timezone.utc)

        vivos = set()

        try:
            with self.banco.conectar() as con:
                linhas = con.execute('SELECT trabalhador, visto_em FROM pulso').fetchall()

        except sqlite3.Error:
            return vivos

        for linha in linhas:

            visto = momento_de(linha['visto_em'])

            if visto is not None and (momento - visto).total_seconds() <= PULSO_VELHO:
                vivos.add(linha['trabalhador'])

        return vivos


    def fila_atendida(self, momento=None):

        #* ha algum trabalhador vivo atendendo? e uma pergunta sobre a FILA, nao sobre um registro: responde o pulso mais novo.
        #* falha para o lado de "atendida" quando o banco ou a data nao sao legiveis (anunciar abandono por engano assusta a toa);
        #* pulso NENHUM, nunca — o caso que motivou isto — e um False seco

        momento = momento or datetime.now(timezone.utc)

        try:
            with self.banco.conectar() as con:
                linha = con.execute('SELECT MAX(visto_em) AS visto FROM pulso').fetchone()

        except sqlite3.Error:
            return True

        if not linha or not linha['visto']:
            return False

        visto = momento_de(linha['visto'])

        if visto is None:
            return True

        return (momento - visto).total_seconds() <= PULSO_VELHO


    def reenfileirar_orfaos(self, eu='', momento=None):

        #* devolve a NA_FILA os registros deixados em RODANDO por um trabalhador que morreu (queda, OOM, deploy).
        #* chamado quando o trabalhador sobe. so volta o registro cujo dono NAO esta pulsando: o `docker compose`
        #* sobe duas replicas, e reenfileirar o que o irmao esta montando agora poe os dois escrevendo na mesma pasta.
        #* ler e gravar na MESMA transacao de escrita: dois trabalhadores subindo juntos leriam a mesma lista de orfaos

        vivos = self.trabalhadores_vivos(momento) - {eu}

        with self.banco.transacao() as con:

            rodando = con.execute(f'SELECT id, trabalhador FROM {self.tabela} WHERE status=?', (RODANDO,)).fetchall()

            orfaos = [linha['id'] for linha in rodando if e_orfao(linha['trabalhador'], vivos)]

            if not orfaos:
                return 0

            marcas = ','.join('?' * len(orfaos))

            return con.execute(
                f"UPDATE {self.tabela} SET status=?, etapa='', trabalhador='' WHERE id IN ({marcas}) AND status=?",
                (NA_FILA, *orfaos, RODANDO)).rowcount


    def __repr__(self):

        return f'{type(self).__name__}({self.banco!r})'




def main():

    from easycontig_web import configuracao
    from easycontig_web.dados import banco_sqlite

    digitado = input('Caminho do fila.sqlite3 (enter = o da configuracao, EASYCONTIG_DATA_DIR): ').strip()

    banco = banco_sqlite.BancoSqlite(digitado) if digitado else banco_sqlite.BancoSqlite(configuracao.carregar().caminho_do_sqlite)

    if not banco.caminho.exists():
        print(f'o arquivo nao existe: {banco.caminho}')
        return

    fila = Fila(banco)

    momento = datetime.now(timezone.utc)

    with banco.conectar() as con:
        linhas = con.execute('SELECT trabalhador, visto_em FROM pulso ORDER BY visto_em DESC').fetchall()

    print(f'\n{fila!r}: {len(linhas)} trabalhador(es) ja pulsaram nesta base')

    for linha in linhas:

        visto = momento_de(linha['visto_em'])

        idade = f'{(momento - visto).total_seconds():.0f} s atras' if visto else f'data ilegivel: {linha["visto_em"]!r}'

        print(f'  {linha["trabalhador"]}: {idade}')

    print(f'\nvivos (pulso <= {PULSO_VELHO:.0f} s): {sorted(fila.trabalhadores_vivos(momento)) or "nenhum"}')

    print(f'fila atendida: {fila.fila_atendida(momento)}')




if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
