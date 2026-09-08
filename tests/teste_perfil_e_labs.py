"""
teste_perfil_e_labs.py — o perfil de cada conta, o diretorio /labs e a casca (lateral) que toda pagina veste.

    entrar --> perfis.garantir_registro(email, nome_do_provedor)   linha em `perfis`; nome vazio, nunca o local-part
    POST /perfil --> perfis.salvar(...)                             campos limpos, links so http/https, foto com nome sorteado
    GET /labs --> perfis.listar(eu)                                 so o que cada um DECLAROU; e-mail dos outros nao sai
    GET /labs/foto/{arquivo} --> perfis.foto_registrada             so o que esta na coluna foto de alguem

O QUE SE PROTEGE
  1. o XSS armazenado do commit bb72803: o Jinja escapa o TEXTO, nao o ESQUEMA de uma URL — `javascript:` num href e clicavel.
  2. o achado L1 (08/08/2026): quem salvava sem nome ficava gravado com o local-part do e-mail, e o /labs mostra o nome de todos.
  3. o achado F2 (20/08/2026): um link cortado no `[` de um IPv6 derrubava com 500 TODA pagina da conta, sem saida dentro do app.
  4. o que a tela promete tem que existir: caminho de volta ao workspace, a foto chegando a lateral, a pagina aberta marcada.
  5. a rota da foto nao e leitor da pasta: exige sessao, nao atravessa pasta e so entrega o que e foto de algum perfil.
"""

import json
import re

from pathlib import Path
from urllib.parse import urlparse

import pytest

from conftest import EMAIL, OUTRO_EMAIL
from easycontig_web.contas import perfil_do_laboratorio as perfil
from easycontig_web.contas.perfil_do_laboratorio import MAX_ITENS, MAX_LINKS, TAMANHO_DA_URL, TAMANHO_DO_TEXTO, Perfil, Perfis, limpar_itens, limpar_links, links_gravados
from easycontig_web.dados.banco_sqlite import BancoSqlite
from easycontig_web.web.comum import TEMPLATES


PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 200

HOSTIS = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "'; DROP TABLE lotes;--", '{{ 7*7 }}', 'javascript:alert(1)']

VENENO = 'https://' + 'a' * 290 + '@[::1]/'  #* 300 caracteres terminando dentro do colchete de IPv6: o corte cai no `[`




@pytest.fixture
def perfis(tmp_path):

    banco = BancoSqlite(tmp_path / 'fila.sqlite3')

    banco.preparar()

    return Perfis(banco)




def cartoes(html):

    #* so a area de trabalho do /labs: a lateral mostra o nome de quem esta logado e as corridas DA PROPRIA conta, de proposito

    return html.split('class="trabalho"', 1)[1]




def nomes_dos_cartoes(html):

    return re.findall(r'<div class="nm">(.*?)</div>', cartoes(html), re.S)




def regra(html, seletor):

    #* o bloco `{...}` de uma regra CSS da pagina, sem os comentarios que a explicam

    partes = html.split(seletor + '{', 1)

    assert len(partes) == 2, f'regra `{seletor}` sumiu da folha'

    return partes[1].split('}', 1)[0]




def salvar_com_foto(bancada, cliente, nome='Gustavo'):

    #* salva o perfil com foto e devolve o nome sorteado do arquivo

    cliente.post('/perfil', data={'nome': nome}, files={'foto': ('minha.png', PNG, 'image/png')})

    return bancada.instalacao.perfis.pegar(EMAIL).foto




@pytest.mark.parametrize('entrada, esperado', [
    ('github.com/fulano', 'GitHub'),
    ('https://orcid.org/0000-0002-1825-0097', 'ORCID'),
    ('http://lattes.cnpq.br/123', 'Lattes'),
    ('www.linkedin.com/in/fulano', 'LinkedIn'),
    ('instagram.com/lab', 'Instagram'),
    ('ufrrj.br/lhv', 'Site'),
])
def teste_a_rede_e_reconhecida_pelo_endereco(entrada, esperado):

    #* desconhecido cai no globo ("Site"), nao some

    assert limpar_links(entrada)[0].nome == esperado




@pytest.mark.parametrize('perigoso', ['javascript:alert(1)', 'JavaScript:alert(1)', 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==', 'vbscript:msgbox(1)', 'file:///etc/passwd'])
def teste_esquema_que_nao_e_http_e_recusado(perigoso):

    assert limpar_links(perigoso) == []




def teste_endereco_sem_esquema_ganha_https():

    assert limpar_links('github.com/x')[0].url == 'https://github.com/x'




def teste_ha_teto_de_enderecos_e_repetidos_saem():

    muitos = '\n'.join(f'site{indice}.com' for indice in range(30))

    assert len(limpar_links(muitos)) == MAX_LINKS

    assert len(limpar_links('github.com/x GITHUB.com/x https://github.com/x http://github.com/x')) == 2, 'sem esquema vira https://, e a caixa nao distingue: so o http e outro'




def teste_o_escritor_nao_grava_link_que_a_leitura_nao_aguenta():

    #* F2: validava a string INTEIRA e gravava bruto[:300] — o corte, nao o validado. o que e gravado tem de ser o que foi validado

    limpos = limpar_links(VENENO)

    for link in limpos:
        urlparse(link.url)  #* nao pode levantar

    assert all(not link.url.endswith('[') for link in limpos)




def teste_a_leitura_cura_uma_linha_ruim_ja_gravada():

    #* a defesa de tras: um banco que JA tem a linha venenosa nao pode estourar na leitura; vira "sem link"

    ruim = json.dumps([{'url': 'https://' + 'a' * 290 + '@['}])

    assert links_gravados(ruim) == []

    assert links_gravados('nao e json') == [] and links_gravados('{"url": 1}') == [] and links_gravados(json.dumps([{'url': 'ftp://x'}])) == []




def teste_link_valido_faz_a_ida_e_volta():

    bons = limpar_links('github.com/fulano https://orcid.org/0000-0002-1825-0097')

    urls = [link.url for link in bons]

    assert any('github.com/fulano' in url for url in urls) and any('orcid.org' in url for url in urls)

    relidos = links_gravados(json.dumps([{'url': url} for url in urls]))

    assert [link.url for link in relidos] == urls




def teste_o_portfolio_aceita_linha_virgula_e_ponto_e_virgula():

    #* nome de especie tem espaco no meio: separar por espaco partiria "Babesia vogeli"

    assert limpar_itens('Babesia vogeli, Hepatozoon canis; Ehrlichia canis\nbabesia VOGELI') == ['Babesia vogeli', 'Hepatozoon canis', 'Ehrlichia canis']

    assert len(limpar_itens('\n'.join(f'sp{indice}' for indice in range(40)))) == MAX_ITENS

    assert limpar_itens('x' * 200)[0] == 'x' * TAMANHO_DO_TEXTO




def teste_o_perfil_decodifica_as_listas_ao_nascer():

    p = Perfil('a@ufrrj.br', especies='["Babesia vogeli", 12, null, {"x": 1}]', marcadores='nao e json', links=json.dumps([{'url': 'https://github.com/x'}]))

    assert p.especies == ['Babesia vogeli', '12'] and p.marcadores == []

    assert p.links[0].nome == 'GitHub' and p.links[0].icone




def teste_salvar_devolve_o_perfil_como_ficou(perfis):

    p = perfis.salvar('ana@ufu.br', nome='  Ana   Souza ', laboratorio='LabPar', especies='Babesia vogeli; Hepatozoon canis', links='github.com/labpar', sobre=' x ' * 400)

    assert p.nome == 'Ana Souza' and p.laboratorio == 'LabPar' and p.especies == ['Babesia vogeli', 'Hepatozoon canis']

    assert p.links[0].url == 'https://github.com/labpar' and len(p.sobre) <= perfil.TAMANHO_DO_SOBRE

    assert p.chave and p.atualizado, 'salvar direto (suite, manutencao) tambem sorteia a chave'

    assert perfis.email_da_chave(p.chave) == 'ana@ufu.br' and perfis.email_da_chave('') == ''




def teste_sem_linha_o_perfil_vem_com_os_padroes(perfis):

    p = perfis.pegar('joao.silva@ufrrj.br')

    assert p.nome == 'joao.silva' and p.especies == [] and p.links == [] and p.chave == '' and p.foto == ''




def teste_quem_nao_digitou_nome_nao_vira_o_local_part_do_email(perfis):

    #* L1: o `nome or atual["nome"]` herdava o padrao sintetizado (local-part) e gravava. com o dominio anunciado na
    #* tela de entrada, qualquer conta remontava o e-mail institucional dos demais

    perfis.salvar('joao.silva@ufrrj.br', laboratorio='LHV')

    assert perfis.listar()[0].nome == '', 'o local-part do e-mail virou nome'




def teste_nome_em_branco_herda_o_que_a_pessoa_digitou_um_dia(perfis):

    perfis.salvar('a@ufrrj.br', nome='Ana')

    assert perfis.salvar('a@ufrrj.br', nome='', laboratorio='LHV').nome == 'Ana'




def teste_foto_none_mantem_a_que_esta_la(perfis):

    perfis.salvar('a@ufrrj.br', nome='Ana', foto='abc.png')

    assert perfis.salvar('a@ufrrj.br', nome='Ana').foto == 'abc.png'

    assert perfis.salvar('a@ufrrj.br', nome='Ana', foto='').foto == '', 'string vazia apaga; None mantem'




def teste_entrar_cadastra_sem_nome_e_o_provedor_preenche_so_o_vazio(perfis):

    #* a armadilha do ON CONFLICT DO NOTHING: o primeiro login sem nome cria linha vazia; o seguinte, com nome do Google,
    #* nao escreveria e a pessoa ficaria "perfil sem nome" para sempre. o UPDATE condicional preenche o vazio e SO o vazio

    perfis.garantir_registro('zulmira@ufrrj.br')

    assert perfis.pegar('zulmira@ufrrj.br').nome == ''

    chave = perfis.pegar('zulmira@ufrrj.br').chave

    assert chave, 'entrar ja sorteia a chave publica'

    perfis.garantir_registro('zulmira@ufrrj.br', 'Zulmira Dias')

    assert perfis.pegar('zulmira@ufrrj.br').nome == 'Zulmira Dias'

    perfis.garantir_registro('zulmira@ufrrj.br')

    assert perfis.pegar('zulmira@ufrrj.br').nome == 'Zulmira Dias', 'login posterior sem nome apagou o que estava la'

    assert perfis.pegar('zulmira@ufrrj.br').chave == chave, 'a chave girou: o formulario de pedido aberto numa aba falharia'




def teste_cadastro_automatico_nao_pisa_no_que_a_pessoa_declarou(perfis):

    perfis.salvar('g@ufrrj.br', nome='Gustavo Freitas', laboratorio='LHV')

    perfis.garantir_registro('g@ufrrj.br', 'Outro Nome')

    p = perfis.pegar('g@ufrrj.br')

    assert (p.nome, p.laboratorio) == ('Gustavo Freitas', 'LHV')




def teste_listar_ordena_e_so_traz_declaracao(perfis):

    perfis.salvar('zulmira@ufrrj.br', nome='Zulmira')

    perfis.salvar('ana@ufrrj.br', nome='Ana', especies='Babesia vogeli')

    lista = perfis.listar('ana@ufrrj.br')

    nomes = [p.nome for p in lista]

    assert nomes == sorted(nomes, key=str.lower)

    ana = next(p for p in lista if p.eu)

    assert ana.email == 'ana@ufrrj.br' and ana.especies == ['Babesia vogeli']

    zulmira = next(p for p in lista if p.nome == 'Zulmira')

    assert zulmira.email == '' and not zulmira.eu, 'o diretorio devolveu o e-mail de terceiro'




def teste_foto_registrada_e_pertencimento_ao_banco(perfis):

    perfis.salvar('a@ufrrj.br', nome='A', foto='sorteada.png')

    assert perfis.foto_registrada('sorteada.png') and not perfis.foto_registrada('outra.png') and not perfis.foto_registrada('')




def teste_a_marca_e_o_botao_de_workspace_levam_para_o_inicio(bancada):

    #* de dentro do perfil ou dos bancos nao havia como voltar ao workspace a nao ser por uma corrida ja existente

    cliente = bancada.entrar()

    for rota in ('/perfil', '/bancos', '/labs'):

        html = cliente.get(rota).text

        assert 'class="lat-topo" href="/"' in html, f'{rota}: a marca nao e link'

        assert 'Workspace' in html and 'href="/labs"' in html, f'{rota}: sem volta ao workspace ou sem botao Labs'




def teste_a_pagina_aberta_fica_marcada_na_lateral(bancada):

    cliente = bancada.entrar()

    assert 'class="ac larga viva"' in cliente.get('/').text

    for rota in ('/perfil', '/labs', '/leiame'):
        assert f'viva"\n           href="{rota}"' in cliente.get(rota).text or f'viva" href="{rota}"' in cliente.get(rota).text, f'{rota}: a pagina aberta nao fica marcada'




def teste_a_foto_do_perfil_aparece_na_lateral_de_toda_pagina(bancada):

    #* trocava a foto e a lateral seguia com a inicial, o que faz concluir que o envio falhou

    cliente = bancada.entrar()

    assert '/perfil/foto' not in cliente.get('/perfil').text

    salvar_com_foto(bancada, cliente, 'Gustavo Freitas')

    for rota in ('/perfil', '/bancos', '/', '/labs'):
        assert '<img src="/perfil/foto"' in cliente.get(rota).text, f'{rota}: a lateral nao mostra a foto'

    assert cliente.get('/perfil/foto').status_code == 200




def teste_sem_foto_a_lateral_usa_a_inicial_do_nome(bancada):

    cliente = bancada.entrar()

    cliente.post('/perfil', data={'nome': 'Maristela'})

    assert '>MA<' in cliente.get('/perfil').text.replace('\n', '').replace(' ', '')

    assert cliente.get('/perfil/foto').status_code == 404




def teste_os_enderecos_sobrevivem_a_ida_e_volta_e_o_perigoso_nao_chega(bancada):

    cliente = bancada.entrar()

    cliente.post('/perfil', data={'nome': 'G', 'links': 'github.com/GF-Linux\norcid.org/0000-0002-1825-0097 javascript:alert(1)'})

    html = cliente.get('/perfil').text

    assert html.count('class="rede"') == 2 and 'rel="noopener noreferrer"' in html

    assert 'javascript:' not in html




def teste_um_link_venenoso_ja_no_banco_nao_derruba_a_conta(bancada):

    #* F2: a casca le o perfil em CADA pagina; uma linha ruim virava 500 em tudo, inclusive no editor que desfaria

    cliente = bancada.entrar()

    with bancada.instalacao.banco.conectar() as con:
        con.execute('UPDATE perfis SET links=? WHERE email=?', (json.dumps([{'url': 'https://' + 'a' * 290 + '@['}]), EMAIL))

    for rota in ('/', '/perfil', '/labs'):
        assert cliente.get(rota).status_code == 200, f'{rota}: a conta ficou presa num 500 sem saida'




def teste_perfil_e_sempre_o_da_sessao(bancada):

    #* nao existe caminho para o perfil de outra pessoa, e passar o e-mail dela na URL nao muda isso

    a = bancada.entrar('a@ufrrj.br')

    b = bancada.entrar('b@ufrrj.br')

    a.post('/perfil', data={'nome': 'Nome do A', 'especies': 'Babesia vogeli'})

    corpo = b.get('/perfil?email=a@ufrrj.br').text

    assert 'Nome do A' not in corpo and 'a@ufrrj.br' not in corpo

    assert b.get('/perfil/a@ufrrj.br').status_code == 404




@pytest.mark.parametrize('mau', HOSTIS)
def teste_campos_do_perfil_nao_viram_html(bancada, mau):

    cliente = bancada.entrar()

    cliente.post('/perfil', data={'nome': mau, 'laboratorio': mau, 'sobre': mau, 'especies': mau, 'marcadores': mau, 'links': mau})

    for rota in ('/perfil', '/labs'):

        corpo = cliente.get(rota).text

        assert '<script>alert' not in corpo and '<img src=x' not in corpo and 'href="javascript:' not in corpo, rota

    assert '49' not in ''.join(nomes_dos_cartoes(cliente.get('/labs').text)), 'o Jinja reavaliou o nome'




def teste_foto_do_perfil_so_aceita_imagem(bancada):

    cliente = bancada.entrar(tolerante=True)

    resposta = cliente.post('/perfil', data={'nome': 'A'}, files={'foto': ('x.svg', b'<svg onload=alert(1)>', 'image/svg+xml')})

    assert resposta.status_code == 400, 'SVG carrega script e nao pode passar como foto'

    grande = cliente.post('/perfil', data={'nome': 'A'}, files={'foto': ('g.png', PNG * 20000, 'image/png')})

    assert grande.status_code == 413 and '2 MB' in grande.text

    assert bancada.instalacao.perfis.pegar(EMAIL).foto == '' and not any(bancada.cfg.pasta_de_fotos.glob('*')), 'a foto recusada ficou no disco'




def teste_trocar_a_foto_apaga_a_anterior(bancada):

    cliente = bancada.entrar()

    primeira = salvar_com_foto(bancada, cliente)

    segunda = salvar_com_foto(bancada, cliente)

    assert primeira != segunda and not (bancada.cfg.pasta_de_fotos / primeira).exists()

    assert (bancada.cfg.pasta_de_fotos / segunda).exists()




def teste_labs_exige_sessao(bancada):

    assert bancada.cliente().get('/labs', headers={'accept': 'application/json'}).status_code == 401




def teste_entrar_no_site_ja_cadastra_no_diretorio_sem_o_local_part(bancada):

    #* 08/08/2026: so quem SALVAVA o perfil ganhava linha, e o autor abriu o /labs e viu so a si mesmo, embora a orientadora
    #* ja tivesse entrado e mandado uma corrida. entrar cadastra; e o cadastro automatico nunca grava o local-part

    cliente = bancada.entrar()

    assert bancada.instalacao.perfis.listar()[0].nome == ''

    html = cliente.get('/labs').text

    assert 'Ainda não há perfis cadastrados' not in html

    nomes = nomes_dos_cartoes(html)

    assert nomes and 'perfil sem nome' in nomes[0]

    assert all('gustavo' not in nome.lower() for nome in nomes), f'o cartao remontou o e-mail: {nomes}'




def teste_nome_declarado_pelo_provedor_entra_no_cadastro(bancada):

    cliente = bancada.entrar()

    bancada.instalacao.perfis.garantir_registro('maristela@ufrrj.br', 'Maristela Peckle')

    html = cartoes(cliente.get('/labs').text)

    assert 'Maristela Peckle' in html and 'maristela@ufrrj.br' not in html, 'o e-mail foi para a tela'




def teste_perfil_salvo_aparece_com_o_que_foi_declarado(bancada):

    cliente = bancada.entrar()

    cliente.post('/perfil', data={'nome': 'Gustavo Freitas', 'laboratorio': 'Laboratório de Hemoparasitas e Vetores', 'instituicao': 'UFRRJ',
                                  'especies': 'Babesia vogeli\nHepatozoon canis', 'marcadores': '18S rRNA, 16S rRNA'})

    html = cartoes(cliente.get('/labs').text)

    for texto in ('Gustavo Freitas', 'Laboratório de Hemoparasitas e Vetores', 'Babesia vogeli', '18S rRNA', 'você'):
        assert texto in html, texto




def teste_labs_nao_expoe_corridas(bancada, ab1):

    #* a lista e declaracao, nao atividade

    cliente = bancada.entrar()

    bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])], nome='F13719 sequencia sigilosa')

    cliente.post('/perfil', data={'nome': 'Gustavo'})

    assert 'sequencia sigilosa' not in cartoes(cliente.get('/labs').text), 'o diretorio vazou nome de corrida'




def teste_o_cartao_do_labs_usa_a_foto_do_perfil(bancada):

    cliente = bancada.entrar()

    arquivo = salvar_com_foto(bancada, cliente)

    assert arquivo and f'/labs/foto/{arquivo}' in cartoes(cliente.get('/labs').text), 'o cartao continuou na inicial'

    assert cliente.get(f'/labs/foto/{arquivo}').status_code == 200




def teste_a_foto_do_diretorio_nao_sai_sem_sessao(bancada):

    arquivo = salvar_com_foto(bancada, bancada.entrar())

    assert bancada.cliente().get(f'/labs/foto/{arquivo}', headers={'accept': 'application/json'}).status_code == 401




def teste_a_rota_de_foto_so_entrega_foto_de_perfil(bancada):

    #* a trava que impede a rota de virar leitor da pasta: sai so o que esta na coluna foto de algum perfil

    cliente = bancada.entrar()

    salvar_com_foto(bancada, cliente)

    (bancada.cfg.pasta_de_fotos / 'naoehperfil.png').write_bytes(PNG)

    assert cliente.get('/labs/foto/naoehperfil.png').status_code == 404




@pytest.mark.parametrize('mau', ['../fila.sqlite3', '..%2ffila.sqlite3', '....//fila.sqlite3', '/etc/passwd'])
def teste_a_rota_de_foto_nao_atravessa_pasta(bancada, mau):

    assert bancada.entrar().get(f'/labs/foto/{mau}').status_code in (400, 404)




def teste_nenhuma_pagina_prende_a_largura_num_teto_proprio(bancada):

    #* o max-width fixo de 64/74/78rem deixava 416px de vazio num monitor de 1920: a medida de leitura e do texto, nao da pagina

    for caminho in TEMPLATES.env.loader.searchpath:  #* a pasta real dos templates, seja onde for que o pacote esteja instalado

        for arquivo in Path(caminho).glob('*.html'):

            for linha in arquivo.read_text(encoding='utf-8').splitlines():

                if linha.lstrip().startswith(('/*', '*', '#', '{#')):
                    continue

                achado = re.search(r'\.interno\{[^}]*max-width:\s*(\d+)rem', linha)

                assert not achado or int(achado.group(1)) >= 100, f'{arquivo.name}: `.interno` voltou a ter teto estreito ({achado.group(1)}rem)'




def teste_o_nome_de_quem_entrou_nao_e_cortado_numa_linha(bancada):

    #* numa lateral de 212px "Gustavo Goncalves ..." perdia o sobrenome, que e o que distingue duas contas do mesmo lab

    bloco = regra(bancada.entrar().get('/').text, '.perfil .nm')

    assert 'white-space:nowrap' not in bloco and '-webkit-line-clamp:2' in bloco




def teste_os_botoes_da_caixa_tem_a_altura_que_declaram(bancada):

    #* sem line-height explicito o botao herda o 1.6 do corpo e sai com 37,2px onde o min-height promete 32

    assert 'line-height:1.2' in regra(bancada.entrar().get('/').text, '.perfil .ac')




def teste_o_retrato_alinha_com_o_selo_da_marca(bancada):

    #* dois circulos de 30px empilhados; 3px de diferenca entre eles e o que fazia a caixa parecer torta

    html = bancada.entrar().get('/').text

    def recuo_esquerdo(bloco):

        achado = re.search(r'padding:\s*([\w. ]+)', bloco)

        assert achado, f'sem padding em `{bloco}`'

        return int(float(achado.group(1).split()[1].replace('px', '')))  #* padding: cima lados baixo -> o lado e o segundo valor

    marca = recuo_esquerdo(regra(html, '.lat-topo'))

    caixa = recuo_esquerdo(regra(html, '.perfil'))

    ident = recuo_esquerdo(regra(html, '.perfil .ident'))

    assert caixa + ident == marca, f'retrato em {caixa + ident}px, selo da marca em {marca}px'




def teste_o_titulo_da_corrida_na_lateral_traz_nome_e_data(bancada, ab1):

    #* quatro corridas "F13719 #80649 27/07" identicas na lateral, cortadas no mesmo ponto, e o title mostrava o STATUS

    cliente = bancada.entrar()

    nome = 'F13719 #80649 27/07 corrida bem comprida'

    for _ in range(2):
        bancada.enviar_lote(cliente, [('amostra12_F_BTF2.ab1', ab1['amostra12_F_BTF2.ab1'])], nome=nome)

    html = cliente.get('/').text

    assert html.count(f'{nome} — na fila') >= 2, 'o title da corrida nao traz o nome inteiro'

    assert 'title="na_fila"' not in html




def teste_botao_do_google_desligado_diz_o_motivo_sem_precisar_de_clique(bancada):

    html = bancada.cliente().get('/entrar').text

    assert 'google desligado' in html, 'o botao nao parece desligado'

    assert 'style="display:none' not in html.split('g-aviso')[1][:40], 'o motivo continua escondido ate o clique'

    assert 'GOOGLE_CLIENT_ID' in html and 'Google Cloud' in html, 'o aviso nao diz o que fazer'




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
