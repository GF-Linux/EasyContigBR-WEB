"""
teste_configuracao.py — o que o ambiente diz e o que a instalacao entende: tetos, retencao, pastas
do lote, a trava de producao e o diagnostico das ferramentas.

    EASYCONTIG_* ---> configuracao.carregar() ---> Configuracao (pastas, tetos, dominio, retencao)
                 '--> conferir_producao() ---> [problemas]   (com EASYCONTIG_PRODUCAO=1, problema derruba o arranque)
                 '--> diagnostico(cfg) ---> [(item, ok, detalhe)]   tracy, blastn, banco 18S, banco 16S

O QUE SE PROTEGE
  1. toda bifurcacao de um valor torto erra para o lado de GUARDAR: retencao ausente, ilegivel ou
     negativa e 0 (nao apagar); teto ilegivel cai no padrao, nunca em "sem teto".
  2. nome_simples e a fronteira entre o que vem da URL e o que vira caminho em disco.
  3. producao exige seis coisas escritas: modo google, SECRET_KEY, HTTPS, DOMINIO, URL_BASE e a
     politica de proxy — cada uma fechou um buraco medido (06/08, 08/08 e 11/08/2026).
  4. o detalhe do diagnostico manda o operador para o lugar certo: "defina a variavel" quando ela
     ja esta definida e apontando para um caminho sem o programa e mentira.
"""

from pathlib import Path

import pytest

from easycontig_web import configuracao
from easycontig_web.configuracao import Configuracao, PastasDoLote, conferir_producao, dias_de_retencao, diagnostico, inteiro_do_ambiente, nome_simples


PRODUCAO_BOA = {'EASYCONTIG_AUTH': 'google', 'EASYCONTIG_SECRET_KEY': 'x' * 32, 'EASYCONTIG_HTTPS_ONLY': '1', 'EASYCONTIG_DOMINIO': 'ufrrj.br',
                'EASYCONTIG_URL_BASE': 'https://easycontigbr.com.br', 'EASYCONTIG_PROXIES_CONFIAVEIS': 'nenhum'}




def teste_carregar_le_o_ambiente_e_cria_as_pastas(ambiente, tmp_path):

    ambiente.setenv('EASYCONTIG_DATA_DIR', str(tmp_path / 'dados'))

    ambiente.setenv('EASYCONTIG_MAX_ARQUIVOS', '7')

    ambiente.setenv('EASYCONTIG_DB_18S', '~/bancos/18S')

    cfg = configuracao.carregar()

    assert cfg.pasta_de_dados == tmp_path / 'dados' and cfg.pasta_de_lotes.is_dir()

    assert cfg.maximo_de_arquivos == 7 and cfg.maximo_de_bytes == configuracao.MAXIMO_DE_BYTES_PADRAO

    assert cfg.banco_18s == Path.home() / 'bancos' / '18S' and cfg.banco_16s is None

    assert cfg.caminho_do_sqlite == tmp_path / 'dados' / 'fila.sqlite3'

    assert cfg.trim == configuracao.TRIM_PADRAO and cfg.retencao_dias == 0




def teste_dias_de_retencao_le_o_ambiente(ambiente):

    assert dias_de_retencao() == configuracao.DIAS_DE_RETENCAO_PADRAO == 0, 'instalacao que nao decidiu nada NAO pode apagar dado de ninguem'

    ambiente.setenv('EASYCONTIG_RETENCAO_DIAS', '30')

    assert dias_de_retencao() == 30

    ambiente.setenv('EASYCONTIG_RETENCAO_DIAS', '0')

    assert dias_de_retencao() == 0, 'desligar tem que ser possivel'




@pytest.mark.parametrize('valor', ['-1', 'muitos', '90 dias', ' '])
def teste_retencao_invalida_cai_no_padrao_em_vez_de_esvaziar_o_volume(ambiente, valor):

    #* -1 significaria "tudo ja venceu": um erro de digitacao no .env nao pode apagar o volume inteiro

    ambiente.setenv('EASYCONTIG_RETENCAO_DIAS', valor)

    assert dias_de_retencao() == 0




@pytest.mark.parametrize('valor, esperado', [('', 12), ('7', 7), ('0', 0), ('-3', 12), ('doze', 12), ('1.5', 12)])
def teste_inteiro_do_ambiente_nunca_deixa_a_instalacao_sem_teto_por_engano(ambiente, valor, esperado):

    #* 0 e um pedido explicito de "sem limite"; um .env com erro de digitacao nao pode valer o mesmo

    ambiente.setenv('EASYCONTIG_QUALQUER', valor)

    assert inteiro_do_ambiente('EASYCONTIG_QUALQUER', 12) == esperado




@pytest.mark.parametrize('nome', ['abc123', 'lote-2026_09', 'arvore.png'])
def teste_nome_simples_aceita_um_nome_solto(nome):

    assert nome_simples(nome) == nome




@pytest.mark.parametrize('ruim', ['', '  ', '.', '..', '../x', 'a/b', 'a\\b', 'a\0b', '/etc/passwd', None, 12])
def teste_nome_simples_recusa_o_que_viraria_caminho(ruim):

    with pytest.raises(ValueError) as erro:
        nome_simples(ruim, papel='pasta da arvore')

    assert 'pasta da arvore' in str(erro.value), 'o erro tem de dizer QUAL valor falhou'




def teste_pastas_do_lote_ficam_todas_dentro_da_pasta_do_lote(tmp_path):

    pastas = PastasDoLote(tmp_path, 'abc')

    assert pastas.raiz == tmp_path / 'abc'

    for caminho in (pastas.entrada, pastas.trabalho, pastas.arvore, pastas.relatorio_html, pastas.relatorio_json, pastas.resultado_csv):
        assert caminho.parent == pastas.raiz, caminho

    with pytest.raises(ValueError):
        PastasDoLote(tmp_path, '../fora')




def teste_a_configuracao_deriva_as_pastas_da_pasta_de_dados(tmp_path):

    cfg = Configuracao(tmp_path)

    assert cfg.pasta_de_lotes == tmp_path / 'lotes'

    assert cfg.pasta_de_bancos == tmp_path / 'bancos'

    assert cfg.pasta_de_fotos == tmp_path / 'fotos'

    assert cfg.pastas_do_lote('x').raiz == tmp_path / 'lotes' / 'x'




def teste_producao_recusa_subir_com_a_porta_aberta(ambiente):

    #* seis problemas, um por buraco medido: dev, SECRET_KEY, HTTPS, DOMINIO (06/08), URL_BASE (08/08), PROXIES_CONFIAVEIS (11/08/2026)

    ambiente.setenv('EASYCONTIG_AUTH', 'dev')

    ambiente.setenv('EASYCONTIG_HTTPS_ONLY', '0')

    ambiente.delenv('EASYCONTIG_SECRET_KEY')

    problemas = conferir_producao()

    assert len(problemas) == 6

    for palavra in ('dev', 'SECRET_KEY', 'HTTPS', 'EASYCONTIG_DOMINIO', 'EASYCONTIG_URL_BASE', 'PROXIES_CONFIAVEIS'):
        assert any(palavra in problema for problema in problemas), palavra




def teste_producao_bem_configurada_nao_reclama(ambiente):

    for nome, valor in PRODUCAO_BOA.items():
        ambiente.setenv(nome, valor)

    assert conferir_producao() == []




@pytest.mark.parametrize('faltando', sorted(PRODUCAO_BOA))
def teste_cada_item_da_producao_e_cobrado_sozinho(ambiente, faltando):

    for nome, valor in PRODUCAO_BOA.items():
        ambiente.setenv(nome, valor)

    ambiente.delenv(faltando)

    problemas = conferir_producao()

    assert len(problemas) == 1, problemas




def teste_proxies_confiaveis_em_branco_e_cobrado_e_um_ip_declarado_nao(ambiente):

    #* 11/08/2026: a variavel DEFINIDA mas vazia (o .env com `EASYCONTIG_PROXIES_CONFIAVEIS=`) e o mesmo buraco que a ausente,
    #* e a declaracao aceita tanto `nenhum` quanto o endereco de onde o proxy chega

    for nome, valor in PRODUCAO_BOA.items():
        ambiente.setenv(nome, valor)

    ambiente.setenv('EASYCONTIG_PROXIES_CONFIAVEIS', '')

    (problema,) = conferir_producao()

    assert 'PROXIES_CONFIAVEIS' in problema and '172.18.0.1' in problema and '`nenhum`' in problema, 'a frase diz as duas saidas'

    ambiente.setenv('EASYCONTIG_PROXIES_CONFIAVEIS', '172.18.0.1')

    assert conferir_producao() == []




def teste_diagnostico_diz_onde_faltou(ambiente, tmp_path):

    #* a variavel apontando para um lugar sem o programa: o detalhe mostra o caminho e culpa a variavel, nao manda "definir" o que ja esta definido

    ambiente.setenv('EASYCONTIG_TRACY_BIN', str(tmp_path / 'nao-existe' / 'tracy'))

    ambiente.setenv('EASYCONTIG_BLAST_BIN', str(tmp_path / 'blast-vazio'))

    ambiente.setenv('PATH', str(tmp_path))  #* sem o PATH da maquina: o teste nao pode depender de um tracy instalado

    itens = {item: (ok, detalhe) for item, ok, detalhe in diagnostico(Configuracao(tmp_path, banco_18s=tmp_path / 'ref18S'))}

    assert set(itens) == {'tracy', 'blastn', 'banco 18S', 'banco 16S'}

    assert itens['tracy'][0] is False and 'nao-existe' in itens['tracy'][1] and 'EASYCONTIG_TRACY_BIN aponta' in itens['tracy'][1]

    assert itens['blastn'][0] is False and 'blast-vazio' in itens['blastn'][1]

    assert itens['banco 18S'] == (False, f'sem índice .nsq: {tmp_path / "ref18S"}')

    assert itens['banco 16S'] == (False, 'não configurado')




def teste_diagnostico_sem_variavel_manda_definir_ou_por_no_path(ambiente, tmp_path):

    ambiente.setenv('PATH', str(tmp_path))

    itens = {item: detalhe for item, _, detalhe in diagnostico(Configuracao(tmp_path))}

    assert 'defina EASYCONTIG_TRACY_BIN' in itens['tracy'] and 'PATH' in itens['tracy']




def teste_diagnostico_acha_o_programa_apontado_e_o_banco_montado(ambiente, tmp_path):

    tracy = tmp_path / 'tracy'

    tracy.write_text('#!/bin/sh\n')

    tracy.chmod(0o755)

    ambiente.setenv('EASYCONTIG_TRACY_BIN', str(tracy))

    for sufixo in ('.nsq', '.nin', '.nhr'):
        (tmp_path / f'ref16S{sufixo}').write_bytes(b'')

    itens = {item: (ok, detalhe) for item, ok, detalhe in diagnostico(Configuracao(tmp_path, banco_16s=tmp_path / 'ref16S'))}

    assert itens['tracy'] == (True, str(tracy))

    assert itens['banco 16S'] == (True, str(tmp_path / 'ref16S'))




def main():

    #* roda so este arquivo

    raise SystemExit(pytest.main(['-q', __file__]))


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
