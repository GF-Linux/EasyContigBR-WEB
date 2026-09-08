"""
conftest.py — o preparo comum da suite: uma instalacao inteira, descartavel, por teste.

    teste (pede a fixture) --> bancada() ---> Bancada(pasta temporaria)
                                                 |-- configuracao.carregar()   (le o ambiente, como no arranque)
                                                 |-- Instalacao(cfg)           (SQLite proprio, filas, cotas, limitador)
                                                 |-- criar_app(instalacao)     (o app de verdade, com os middlewares)
                                                 '-- entrar(email) ----------> TestClient ja com a sessao aberta

COMO FUNCIONA
  1. cada teste ganha uma Bancada NOVA: pasta temporaria, banco SQLite proprio, Instalacao propria
     e o app montado por criar_app(). Nada e compartilhado entre testes — o limitador de taxa, as
     cotas e as filas moram na Instalacao — entao nao ha o que zerar entre um caso e outro.
  2. o ambiente EASYCONTIG_* e GOOGLE_* e LIMPO antes de cada teste (fixture ambiente, autouse);
     o teste que precisa de uma variavel pede a bancada com ela: montar(MAX_ARQUIVOS='1').
  3. ferramentas externas (tracy, blastn, makeblastdb, mb) e os bancos curados NAO sao exigidos:
     o teste que depende deles chama exigir_ferramenta('tracy') ou exigir_bancos_curados() e e
     PULADO com o nome do que falta, em vez de falhar por causa da maquina. Como o ambiente e
     limpo antes de cada teste, esses ajudantes leem AMBIENTE_DA_MAQUINA (a copia de antes da
     limpeza) e as pastas conhecidas; o teste entrega o que achou a montar(DB_18S=...).
  4. os .ab1 sao FABRICADOS (amostras_sinteticas.py, via tools/gerar_ab1.py); dado real do
     laboratorio nao entra na suite. O teste que precisa de .ab1 real procura a pasta em
     EASYCONTIG_AB1_REAIS (da maquina) e nas pastas conhecidas, e e pulado sem ela.

COMO USAR
  python -m pytest tests -q            (a suite inteira; pythonpath vem do pyproject.toml)
  python tests/teste_lotes_na_web.py   (um arquivo so: cada teste_*.py tem main() que chama o pytest)

O QUE MUDOU (em relacao ao tests/ original)
  - 42 arquivos organizados por achado viraram 1 arquivo por assunto, na ordem dos modulos.
  - o conftest antigo so zerava o limitador global; aqui o limitador e da Instalacao e cada teste
    tem a sua, entao a fixture sumiu junto com o estado de processo.
  - o app nao e mais recarregado com importlib.reload(): criar_app(instalacao) recebe a
    instalacao do teste, que e o mesmo caminho do arranque em producao.

POR QUE nasce classe
  - Bancada: e dona de recursos com ciclo de vida (pasta, SQLite, app, clientes HTTP) — e o que
    cada teste abre e descarta.
"""

import os
from pathlib import Path


NUCLEO = ('bancos_de_referencia', 'cliente_ncbi', 'ferramentas', 'filogenia', 'identificacao', 'lote',
          'montagem', 'motor_tracy', 'orientacao', 'pareamento', 'relatorio', 'tracado')  #* os modulos da raiz, que NAO sao versionados




def conferir_o_que_nao_e_versionado(raiz):

    #! o nucleo e o front ficam fora do git (ver .gitignore): num clone limpo a suite quebraria com ImportError
    #! cru na primeira linha de um teste qualquer, e quem visse nao saberia que nao e bug -- e falta de arquivo

    faltando = [f'{nome}.py' for nome in NUCLEO if not (raiz / f'{nome}.py').is_file()]

    if not (raiz / 'easycontig_web' / 'templates' / 'base.html').is_file():
        faltando.append('easycontig_web/templates/')

    if not (raiz / 'easycontig_web' / 'estatico' / 'oficina.js').is_file():
        faltando.append('easycontig_web/estatico/')

    if not faltando:
        return

    raise SystemExit(f'FALHOU: faltam {len(faltando)} item(ns) que NAO sao versionados: {", ".join(faltando)}\n'
                     f'        O nucleo (os .py da raiz) e o front ficam fora do git por decisao de 08/09/2026.\n'
                     f'        Traga-os da maquina de desenvolvimento (ou da VPS) para {raiz} antes de rodar a suite:\n'
                     f'        rsync -a <origem>/*.py <origem>/easycontig_web/templates <origem>/easycontig_web/estatico {raiz}/')




conferir_o_que_nao_e_versionado(Path(__file__).resolve().parent.parent)  #! ANTES dos imports do nucleo, senao quem le so ve o ImportError

import pytest
from fastapi.testclient import TestClient

import ferramentas
from relatorio import FatosDaAmostra, FatosDaLeitura, RelatorioDoLote, Ressalva
from easycontig_web import configuracao
from easycontig_web.processamento import executor_de_lote
from easycontig_web.web.instalacao import Instalacao


RAIZ = Path(__file__).resolve().parent.parent  #* a raiz do repositorio (onde moram tools/ e os modulos do nucleo)

EMAIL = 'gustavo@ufrrj.br'  #* a conta padrao dos testes

OUTRO_EMAIL = 'outra@ufrrj.br'  #* a segunda conta, para os testes de fronteira

CURADO = 'curado'  #* a referencia padrao do envio: toda corrida declara contra o que identifica

FERRAMENTAS = {'tracy': ('EASYCONTIG_TRACY_BIN', 'bin'), 'blastn': ('EASYCONTIG_BLAST_BIN', 'blast'),
               'makeblastdb': ('EASYCONTIG_BLAST_BIN', 'blast'), 'mb': ('EASYCONTIG_MRBAYES_BIN', 'bin')}  #* a escada do ferramentas.py, por programa

PASTAS_DE_AB1_REAIS = (Path.home() / 'Área de trabalho' / 'Raic' / 'ab1_por_especie' / 'Babesia_vogeli',
                       Path.home() / 'Desktop' / 'EasyContig-BR-Demo-Deck' / 'ab1_por_especie' / 'Babesia_vogeli')  #* onde os .ab1 reais costumam estar, depois da variavel

PASTAS_DE_BANCOS_CURADOS = (Path.home() / 'incoming' / 'deck' / 'easycontig-web' / 'bancos',
                            Path.home() / 'Desktop' / 'easycontig-web' / 'bancos')  #* onde os bancos curados (referencias_18S / _16S) costumam estar, depois das variaveis

AMBIENTE_DA_MAQUINA = dict(os.environ)  #! a copia de antes da fixture ambiente limpar: e daqui que os testes com ferramenta real leem as variaveis




def variavel(nome):

    #* MAX_ARQUIVOS -> EASYCONTIG_MAX_ARQUIVOS; GOOGLE_CLIENT_ID fica como esta

    return nome if nome.startswith('GOOGLE_') else 'EASYCONTIG_' + nome




def exigir_ferramenta(nome):

    #* o caminho do programa, ou pula o teste dizendo o que falta e onde por

    caminho_da_variavel, subpasta = FERRAMENTAS[nome]

    caminho = ferramentas.procurar_programa(nome, caminho_da_variavel, subpasta, None)

    if caminho is None:
        pytest.skip(f'{nome} não está nesta máquina: instale-o no PATH ou aponte {caminho_da_variavel}')

    return caminho




def banco_montado(prefixo):

    #* True se ha um banco BLAST neste prefixo (o .nsq e o arquivo das sequencias)

    return bool(prefixo) and Path(prefixo).with_name(Path(prefixo).name + '.nsq').exists()




def exigir_bancos_curados():

    #* os prefixos dos bancos curados (18S e 16S), ou pula o teste dizendo a variavel que falta.
    #* a variavel da maquina ganha; sem ela, as pastas conhecidas. o teste passa os prefixos para montar(DB_18S=..., DB_16S=...)

    prefixos = []

    for nome, arquivo in (('EASYCONTIG_DB_18S', 'referencias_18S'), ('EASYCONTIG_DB_16S', 'referencias_16S')):

        candidatos = [AMBIENTE_DA_MAQUINA.get(nome, '')] + [str(pasta / arquivo) for pasta in PASTAS_DE_BANCOS_CURADOS]

        prefixo = next((candidato for candidato in candidatos if banco_montado(candidato)), None)

        if prefixo is None:
            pytest.skip(f'o banco curado não está nesta máquina: aponte {nome} para o prefixo do banco BLAST')

        prefixos.append(Path(prefixo))


    return tuple(prefixos)




def pasta_de_ab1_reais():

    #* os .ab1 reais do laboratorio, ou pula: DNA sorteado nao identifica especie nenhuma

    candidatos = [Path(AMBIENTE_DA_MAQUINA['EASYCONTIG_AB1_REAIS'])] if AMBIENTE_DA_MAQUINA.get('EASYCONTIG_AB1_REAIS') else []

    for pasta in candidatos + list(PASTAS_DE_AB1_REAIS):

        if pasta.is_dir():
            return pasta

    pytest.skip('os .ab1 reais não estão nesta máquina: aponte EASYCONTIG_AB1_REAIS para a pasta deles')




def relatorio_fabricado(nome='corrida', chaves=('amostra12',), banco='curado'):

    #* um RelatorioDoLote com a cara de um lote que passou pelo tracy e pelo BLAST, sem rodar nenhum dos dois:
    #* uma amostra montada e identificada por chave, com o par F/R e uma ressalva. serve para as rotas que LEEM o resultado

    amostras = []

    for chave in chaves:

        fatos = FatosDaAmostra(chave)

        fatos.leituras = [FatosDaLeitura(f'{chave}_F_BTF2', arquivo=f'{chave}_F_BTF2.ab1', sentido_pelo_nome='F', sentido_medido='F', primer='BTF2', bases=410, q_medio=41.2, q_rotulo='alta'),
                          FatosDaLeitura(f'{chave}_R_BTR2', arquivo=f'{chave}_R_BTR2.ab1', sentido_pelo_nome='R', sentido_medido='R', primer='BTR2', bases=398, q_medio=38.7, q_rotulo='alta')]

        fatos.consenso_pb, fatos.largura, fatos.cobertura_media = 402, 418, 1.9

        fatos.organismo, fatos.acesso, fatos.identidade, fatos.cobertura_da_consulta, fatos.valor_e = 'Babesia vogeli', 'KY290979.1', 99.5, 100.0, '0.0'  #* valor_e e texto, como o nucleo grava (str(acerto.valor_e))

        fatos.marcador, fatos.situacao, fatos.par_pelo_dna = '18S', 'ok', True

        fatos.ressalvas = [Ressalva('cobertura_1', 'cobertura 1 em 12 bases (3,0%)', 'acima de 2% do consenso')]

        amostras.append(fatos)

    return RelatorioDoLote(pasta=nome, gerado_em='05/09/2026 10:00', n_arquivos=2 * len(chaves), trim=4, banco=banco, amostras=amostras, notas=[])




class Bancada:

    #* uma instalacao inteira numa pasta temporaria: configuracao, Instalacao, app e os clientes HTTP

    def __init__(self, pasta, monkeypatch, **ambiente):

        for nome, valor in ambiente.items():
            monkeypatch.setenv(variavel(nome), valor)

        monkeypatch.setenv('EASYCONTIG_DATA_DIR', str(pasta))

        from easycontig_web.servidor_web import criar_app  #! importado AQUI, com a pasta ja apontada: o modulo cria o `app` do uvicorn ao ser importado, e no topo do conftest ele nasceria em ./dados, dentro do repositorio

        self.pasta = Path(pasta)

        self.cfg = configuracao.carregar()

        self.instalacao = Instalacao(self.cfg)

        self.app = criar_app(self.instalacao)


    def cliente(self, tolerante=False):

        #* um navegador novo, sem sessao. tolerante=True deixa o erro virar a pagina de 500 em vez de estourar no teste

        return TestClient(self.app, base_url='http://testserver', follow_redirects=False, raise_server_exceptions=not tolerante)


    def entrar(self, email=EMAIL, cliente=None, tolerante=False):

        #* um cliente ja logado pelo login de desenvolvimento

        cliente = cliente or self.cliente(tolerante)

        resposta = cliente.post('/entrar', data={'email': email})

        assert resposta.status_code == 303 and resposta.headers['location'] == '/', (resposta.status_code, resposta.headers.get('location'))

        return cliente


    def enviar_lote(self, cliente, arquivos, referencia=CURADO, nome='corrida'):

        #* POST /lotes com os arquivos (nome -> bytes); devolve o id do lote criado

        dados = {'referencia': referencia} if referencia is not None else {}

        if nome:
            dados['nome'] = nome

        resposta = cliente.post('/lotes', files=[('arquivos', (nome_do_arquivo, conteudo, 'application/octet-stream')) for nome_do_arquivo, conteudo in arquivos], data=dados)

        assert resposta.status_code == 303, (resposta.status_code, resposta.text[:200])

        return resposta.headers['location'].rsplit('/', 1)[-1]


    def lote(self, lote_id):

        return self.instalacao.lotes.pegar(lote_id)


    def concluir(self, lote_id, relatorio=None, trabalhador='bancada'):

        #* o que o trabalhador faria, sem tracy nem BLAST: grava HTML + JSON + CSV do relatorio (fabricado, por padrao) e marca PRONTO

        relatorio = relatorio or relatorio_fabricado(nome=self.lote(lote_id).nome or 'corrida')

        pastas = self.cfg.pastas_do_lote(lote_id)

        pastas.raiz.mkdir(parents=True, exist_ok=True)

        pastas.relatorio_json.write_text(relatorio.para_json(), encoding='utf-8')
        pastas.relatorio_html.write_text(executor_de_lote.documento_html(relatorio), encoding='utf-8')
        pastas.resultado_csv.write_text(executor_de_lote.para_csv(relatorio), encoding='utf-8')

        pego = self.instalacao.lotes.reivindicar(trabalhador)

        assert pego is not None and pego.id == lote_id, f'a fila entregou {pego and pego.id}, nao {lote_id}: outro lote estava na frente'

        assert self.instalacao.lotes.concluir(lote_id, trabalhador)

        return relatorio


    def __repr__(self):

        return f'Bancada({self.pasta})'




@pytest.fixture(autouse=True)
def ambiente(monkeypatch):

    #* comeca do zero: nenhuma EASYCONTIG_* nem GOOGLE_* da maquina vaza para o teste

    for nome in list(os.environ):

        if nome.startswith('EASYCONTIG_') or nome.startswith('GOOGLE_'):
            monkeypatch.delenv(nome)

    monkeypatch.setenv('EASYCONTIG_SECRET_KEY', 'chave-de-teste')

    return monkeypatch




@pytest.fixture
def montar(tmp_path, ambiente):

    #* fabrica de bancadas: montar() ou montar(MAX_ARQUIVOS='1', DOMINIO='ufrrj.br')

    def montar(**variaveis):

        return Bancada(tmp_path / 'dados', ambiente, **variaveis)

    return montar




@pytest.fixture
def bancada(montar):

    return montar()




@pytest.fixture
def ab1():

    #* {nome: bytes} das quatro leituras sinteticas (dois pares F/R)

    from amostras_sinteticas import amostras

    return amostras()




def main():

    #* python tests/conftest.py roda a suite inteira

    raise SystemExit(pytest.main(['-q', str(Path(__file__).parent)]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
