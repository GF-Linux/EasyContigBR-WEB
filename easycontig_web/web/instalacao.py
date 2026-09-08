"""
instalacao.py — tudo o que esta instalacao do EasyContig abre UMA vez e toda rota usa.

    configuracao.carregar()  ->  Instalacao(cfg)
                                    |
                                    |-- .cfg        Configuracao (pastas, tetos, trim, retencao)
                                    |-- .banco      BancoSqlite(cfg.caminho_do_sqlite)      preparar() no arranque do servidor
                                    |-- .lotes      FilaDeLotes(banco)                      corridas
                                    |-- .arvores    FilaDeArvores(banco)                    pedidos de arvore
                                    |-- .perfis     Perfis(banco)                           perfil de cada laboratorio
                                    |-- .pedidos    Pedidos(banco)                          pedidos de amostra entre labs
                                    |-- .estante    EstanteDeBancos(cfg.pasta_de_bancos)    bancos de referencia (catalogo e do usuario)
                                    |-- .ncbi       ClienteNcbi()                           UM cliente por instalacao (pausa entre pedidos e dele)
                                    |-- .cotas      Cotas(lotes, estante, pasta_de_lotes)   espaco e corridas por conta
                                    |-- .limitador  Limitador()                             pedidos por minuto (login, envio, leitura, pedido, banco)
                                    |-- .vagas      BoundedSemaphore(max_pesadas)           quantas remontagens/consultas BLAST ao mesmo tempo
                                    |-- .administradores                                    e-mails de EASYCONTIG_ADMINS
                                    |-- .max_corpo / .max_corpo_sem_sessao                  tetos do corpo de um POST (com e sem sessao)
                                    |-- .url_base / .https_only / .chave_da_sessao          EASYCONTIG_URL_BASE, _HTTPS_ONLY, _SECRET_KEY
                                    |
                                    '-- o que as rotas perguntam:
                                          .referencias(email)             o menu "referencia" da pagina inicial
                                          .casca(usuario)                 o que base.html precisa em toda pagina
                                          .lote_do_usuario(lote_id, u)    o lote, ou 404 (nao existe / nao e seu)
                                          .relatorio(lote_id)             RelatorioDoLote pronto, ou None
                                          .vaga_pesada()                  with: uma vaga de trabalho pesado, ou 503
                                          .e_administrador(usuario)

COMO FUNCIONA

    O servidor cria UMA Instalacao no arranque e a pendura em app.state.instalacao;
    cada rota a pega pelo request (comum.instalacao_de). E assim que a rota chega
    ao SQLite, a fila, os bancos, o NCBI: nada e global de modulo, nada e
    reaberto por pedido. A mesma Instalacao com outra pasta de dados e outro
    servidor — e o que o harness faz para comparar duas versoes lado a lado.

COMO USAR

    from easycontig_web import configuracao
    from easycontig_web.web.instalacao import Instalacao

    instalacao = Instalacao(configuracao.carregar()).preparar()
    instalacao.lotes.listar(dono='ana@lab.br')
    with instalacao.vaga_pesada():
        ...  # remontar, consultar BLAST

O QUE MUDOU (em relacao a web/comum.py + servidor_web.py originais)

 1. Cada rota original repetia 84 linhas de cabecalho: cfg = config.carregar(),
    a queixa do PRODUCAO, os imports de comum. Cada modulo carregava a
    configuracao de novo; o sqlite era um caminho passado a toda funcao da fila
    (fila.listar(sqlite, ...)); o semaforo das vagas pesadas morava em
    servidor_web e comum o importava por dentro da funcao para escapar do import
    circular. Tudo isso agora e atributo de UMA Instalacao.

 2. _referencias(u) -> referencias(email): a mesma lista, com a mesma ordem
    (curado, catalogo na ordem do CATALOGO so dos montados, depois os do usuario);
    cada item e um dicionario {id, nome, detalhe, grupo} porque o template so
    le esses quatro campos.

 3. _casca(u) -> casca(usuario): perfil_lateral e o proprio Perfil (o template le
    .nome e .foto; um dicionario {nome, foto} era so uma copia). O resto igual:
    usuario, corridas (25 ultimas), pedidos_pendentes.

 4. _lote_do_usuario / _relatorio / _vaga_pesada viraram metodos; o texto do 404
    e do 503 e o mesmo.

 5. MAX_CORPO, MAX_CORPO_SEM_SESSAO, MAX_PESADAS, EASYCONTIG_ADMINS, URL_BASE,
    HTTPS_ONLY e SECRET_KEY eram lidos do ambiente no import do modulo (ou a
    cada request); agora sao lidos UMA vez, no construtor. Os inteiros passam
    por inteiro_do_ambiente: valor invalido -> padrao, em vez de derrubar o
    servidor no import.

POR QUE nasce classe

    A Instalacao E o dono do estado do processo: a conexao com o banco, os
    baldes do limitador, o semaforo das vagas, o cliente do NCBI com a sua pausa.
    Funcoes soltas com globais de modulo faziam o mesmo, mas so UMA vez por
    processo e sem jeito de montar duas (teste) ou de saber de onde cada rota
    tirava o que usava.
"""

import contextlib
import os
import secrets
import threading

from fastapi import HTTPException

from cliente_ncbi import ClienteNcbi
from bancos_de_referencia import CATALOGO

from easycontig_web.configuracao import inteiro_do_ambiente
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.dados import leitura_de_amostras
from easycontig_web.dados.pedidos_entre_labs import Pedidos
from easycontig_web.contas.perfil_do_laboratorio import Perfis
from easycontig_web.contas.cota_de_espaco import Cotas
from easycontig_web.contas.limite_de_requisicoes import Limitador
from easycontig_web.processamento import executor_de_lote
from easycontig_web.processamento.fila_de_lotes import FilaDeLotes
from easycontig_web.processamento.fila_de_arvores import FilaDeArvores


MAX_PESADAS_PADRAO = 4  #* remontagens e consultas BLAST ao mesmo tempo; cada uma segura um nucleo por segundos

FOLGA_MULTIPART = 16 * 1024 * 1024  #* o que o multipart acrescenta ao lote (cabecalhos, fronteiras, o nome do lote) antes do teto do corpo

MAX_CORPO_SEM_SESSAO_PADRAO = 64 * 1024  #* quem ainda nao entrou so manda o formulario de login

CORRIDAS_NA_LATERAL = 25  #* quantas corridas a barra lateral mostra

SEGUNDOS_PARA_TENTAR_DE_NOVO = 5  #* Retry-After quando nao ha vaga pesada




class Instalacao:

    #* os recursos que o servidor abre uma vez; veja o fluxograma do modulo

    def __init__(self, cfg, max_pesadas=None, max_corpo=None, max_corpo_sem_sessao=None, administradores=None):

        self.cfg = cfg

        self.banco = BancoSqlite(cfg.caminho_do_sqlite)

        self.lotes = FilaDeLotes(self.banco)

        self.arvores = FilaDeArvores(self.banco)

        self.perfis = Perfis(self.banco)

        self.pedidos = Pedidos(self.banco)

        self.estante = executor_de_lote.estante_de_bancos(cfg)

        self.ncbi = ClienteNcbi()

        self.cotas = Cotas(self.lotes, self.estante, cfg.pasta_de_lotes)

        self.limitador = Limitador()

        self.max_pesadas = max(1, inteiro_do_ambiente('EASYCONTIG_MAX_PESADAS', MAX_PESADAS_PADRAO)) if max_pesadas is None else max_pesadas

        self.vagas = threading.BoundedSemaphore(self.max_pesadas)

        self.max_corpo = inteiro_do_ambiente('EASYCONTIG_MAX_CORPO', cfg.maximo_de_bytes + FOLGA_MULTIPART) if max_corpo is None else max_corpo

        self.max_corpo_sem_sessao = inteiro_do_ambiente('EASYCONTIG_MAX_CORPO_SEM_SESSAO', MAX_CORPO_SEM_SESSAO_PADRAO) if max_corpo_sem_sessao is None else max_corpo_sem_sessao

        self.administradores = administradores_do_ambiente() if administradores is None else set(administradores)

        self.url_base = os.environ.get('EASYCONTIG_URL_BASE', '').rstrip('/')  #* atras do nginx, o endereco que o navegador ve; vazio = o do proprio request

        self.https_only = os.environ.get('EASYCONTIG_HTTPS_ONLY', '0') == '1'  #* cookie Secure e HSTS; nunca em http://localhost

        self.chave_da_sessao = os.environ.get('EASYCONTIG_SECRET_KEY') or secrets.token_urlsafe(32)  #! sem a variavel, cada arranque desloga todo mundo


    def preparar(self):

        #* cria/atualiza as tabelas; devolve a propria instalacao para encadear no arranque

        self.banco.preparar()

        return self


    def referencias(self, email):

        #* o menu de referencia da pagina inicial: o curado, os bancos do catalogo ja montados e os bancos do usuario

        lista = [{'id': executor_de_lote.CURADO, 'nome': 'Banco curado do laboratório',
                  'detalhe': '18S + 16S · RAIC 2026 · o que validou os resultados até aqui', 'grupo': 'Padrão'}]

        for ficha in CATALOGO:

            if self.estante.existe(ficha.id):

                estado = self.estante.estado(ficha.id)

                lista.append({'id': ficha.id, 'nome': f'{ficha.nome} · {ficha.marcador}', 'detalhe': f'{estado.sequencias} sequências do GenBank', 'grupo': ficha.grupo})

        for estado in self.estante.meus_bancos(email):
            lista.append({'id': estado.id, 'nome': estado.apelido, 'detalhe': f'{estado.sequencias} sequências suas', 'grupo': 'Meus bancos'})

        return lista


    def casca(self, usuario):

        #* o que base.html precisa em toda pagina de quem entrou

        return {
            'usuario': usuario,
            'corridas': self.lotes.listar(dono=usuario.email, limite=CORRIDAS_NA_LATERAL),
            'perfil_lateral': self.perfis.pegar(usuario.email),
            'pedidos_pendentes': self.pedidos.contar_pendentes(usuario.email),
        }


    def lote_do_usuario(self, lote_id, usuario):

        #* o lote, se existe e e de quem pediu; senao 404 — sem dizer qual das duas coisas faltou

        lote = self.lotes.pegar(lote_id)

        if lote is None or lote.dono != usuario.email:
            raise HTTPException(404, 'lote não encontrado')

        return lote


    def relatorio(self, lote_id):

        #* o relatorio.json do lote ja lido, ou None se ainda nao existe / nao da para ler

        return leitura_de_amostras.carregar(self.cfg.pastas_do_lote(lote_id).relatorio_json)


    @contextlib.contextmanager
    def vaga_pesada(self):

        #* uma das max_pesadas vagas para remontar amostra ou consultar o BLAST; sem vaga, 503 na hora em vez de fila

        if not self.vagas.acquire(blocking=False):
            raise HTTPException(503, 'o servidor está ocupado montando outras amostras; tente de novo', headers={'Retry-After': str(SEGUNDOS_PARA_TENTAR_DE_NOVO)})

        try:
            yield

        finally:
            self.vagas.release()


    def e_administrador(self, usuario):

        return usuario.email.lower() in self.administradores


    def __repr__(self):

        return f'Instalacao({self.cfg.pasta_de_dados}, max_pesadas={self.max_pesadas}, administradores={len(self.administradores)})'




def administradores_do_ambiente():

    #* EASYCONTIG_ADMINS = "ana@lab.br, joao@lab.br" -> {'ana@lab.br', 'joao@lab.br'}

    return {parte.strip().lower() for parte in os.environ.get('EASYCONTIG_ADMINS', '').split(',') if parte.strip()}




def main():

    #* mostra o que esta instalacao abriria com o ambiente atual, sem subir servidor

    from easycontig_web import configuracao

    instalacao = Instalacao(configuracao.carregar())

    print(instalacao)
    print(f'  banco     {instalacao.banco.caminho}')
    print(f'  estante   {instalacao.estante}')
    print(f'  cotas     {instalacao.cotas}')
    print(f'  limitador {instalacao.limitador}')
    print(f'  corpo     {instalacao.max_corpo} bytes com sessão, {instalacao.max_corpo_sem_sessao} sem')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
