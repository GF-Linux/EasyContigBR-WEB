"""
rotas_de_consulta_de_lote.py — tudo o que se le de um lote depois de enviado: a pagina, as amostras, o traco, a arvore, os arquivos.

    GET  /lotes/{lote_id}?tela_do_lote=1                 pagina_lote        lote.html; PRONTO com UMA amostra -> 303 para a amostra
    GET  /lotes/{lote_id}/amostras/{chave}               pagina_amostra     amostra.html (fatos da amostra, referencias consultaveis)
    GET  /api/lotes/{lote_id}                            api_lote           o Lote em JSON (a pagina recarrega por aqui)
    GET  /api/lotes/{lote_id}/amostras/{chave}/traco     api_traco          remonta (vaga pesada) -> tracado.para_navegador -> JSON
    GET  /api/lotes/{lote_id}/amostras/{chave}/consultar api_consultar      ?banco=<id>: remonta + blastn contra UM banco extra -> {banco, acertos, consulta: true}

    POST /lotes/{lote_id}/arvore                         pedir_arvore       lote PRONTO -> arvores.novo_pedido -> 303 .../arvore
    GET  /lotes/{lote_id}/arvore                         pagina_arvore      arvore.html (pedido, resumo por marcador, recarregar)
    GET  /api/lotes/{lote_id}/arvore                     api_arvore         o PedidoDeArvore em JSON
    GET  /lotes/{lote_id}/arvore/{pasta}/{arquivo}       arquivo_da_arvore  a figura (.png) ou o Newick (download)

    POST /lotes/{lote_id}/apagar                         apagar_lote        expurgo.apagar_lote -> 303 /   (em uso -> 409)
    GET  /lotes/{lote_id}/relatorio                      relatorio          o HTML gravado pelo trabalhador
    GET  /lotes/{lote_id}/resultado.csv                  resultado_csv      download resultado_<id>.csv
    GET  /lotes/{lote_id}/relatorio.json                 relatorio_json     download relatorio_<id>.json

    toda rota comeca por instalacao.lote_do_usuario(lote_id, u): lote de outro ou inexistente -> 404 "lote nao encontrado"

COMO FUNCIONA

    O relatorio e lido do relatorio.json (leitura_de_amostras.carregar ->
    RelatorioDoLote); None -> "relatorio indisponivel". O traco e a consulta
    remontam a amostra sob demanda (cromatograma.remontar, ~0,3 s de tracy)
    dentro de uma vaga pesada: sem vaga, 503 na hora.

    A consulta a outro banco e segunda opiniao, nao veredito: o resultado
    validado continua sendo o da referencia do lote; a resposta sai rotulada
    consulta: true.

COMO USAR

    app.include_router(rotas_de_consulta_de_lote.router)

O QUE MUDOU (em relacao ao original)

 1. As amostras chegam ao template como FatosDaAmostra / dicionarios com nomes
    em portugues (chave, acesso, valor_e, cobertura_da_consulta,
    pct_cobertura_1) — veja leitura_de_amostras. Um relatorio.json de versao
    antiga (chaves em ingles) da "relatorio indisponivel".

 2. api_consultar devolve {'banco', 'acertos': [acerto_na_tela(Acerto)],
    'consulta': True}: cada acerto tem organismo, acesso, titulo, identidade
    e cobertura com 1 casa (as mesmas do relatorio), valor_e e pontuacao como
    numeros. O original devolvia 'hits' com accession/e_value/bits/alinhado
    (e_value e bits como texto); o oficina.js so lia titulo, identidade,
    cobertura e accession -> passa a ler acesso. Sem blastn na instalacao, 500
    com a orientacao (a busca original quebrava com FileNotFoundError e
    virava o 500 generico).

 3. api_lote e api_arvore devolvem vars(Lote) / vars(PedidoDeArvore): os
    mesmos campos que as linhas do SQLite tinham (o resumo da arvore ja vem
    como lista, nao como texto JSON).

 4. O 500 da consulta que falhou vai para o logger em vez do stderr.

POR QUE nao nasce classe

    Rotas; o estado (lotes, arvores, estante, vagas) e da Instalacao.
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from bancos_de_referencia import POR_ID

from easycontig_web.dados import expurgo_por_retencao, leitura_de_amostras
from easycontig_web.processamento import cromatograma, executor_de_arvore, executor_de_lote, fila_de_arvores
from easycontig_web.processamento.executor_de_lote import CURADO
from easycontig_web.processamento.fila_de_lotes import NA_FILA, PRONTO
from easycontig_web.web import comum


REGISTRO = logging.getLogger('easycontig.consulta')

SEM_TRACO = 'os arquivos originais não estão mais no servidor, ou esta amostra não é um par F+R'

SEM_BLASTN = ('o blastn não está instalado neste servidor, e a consulta a outro banco precisa dele: '
              'defina EASYCONTIG_BLAST_BIN (ou avise quem cuida do servidor)')

router = APIRouter()




def relatorio_ou_404(instalacao, lote_id):

    relatorio = instalacao.relatorio(lote_id)

    if not relatorio:
        raise HTTPException(404, 'relatório indisponível')

    return relatorio




def amostra_ou_404(relatorio, chave):

    #* a amostra do NUCLEO (com as leituras e os caminhos dos traces): e ela que o cromatograma remonta.
    #! a tela recebe leitura_de_amostras.formatar_amostra(fatos), nunca este objeto: os numeros da pagina
    #! saem da mesma formatacao da tabela (listar), e a linha e a pagina nao podem divergir

    fatos = relatorio.amostra(chave)

    if fatos is None:
        raise HTTPException(404, 'amostra não encontrada no lote')

    return fatos




def acerto_na_tela(acerto):

    #* um Acerto do nucleo como a pagina o mostra: identidade e cobertura com 1 casa, as MESMAS do relatorio ao lado
    #! o blastn mede 99.540 e o relatorio grava round(identidade, 1) (relatorio.py); a consulta nao pode afirmar
    #! 99,54 na mesma tela em que o veredito diz 99,5

    return {'organismo': acerto.organismo, 'acesso': acerto.acesso, 'titulo': acerto.titulo, 'identidade': round(acerto.identidade, 1),
            'cobertura': round(acerto.cobertura, 1), 'valor_e': acerto.valor_e, 'pontuacao': acerto.pontuacao}




@router.get('/api/lotes/{lote_id}/amostras/{chave}/consultar')
def api_consultar(request: Request, lote_id: str, chave: str, banco: str):

    #! nao e identificacao: o veredito continua vindo da referencia do lote; a resposta sai rotulada consulta

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.lote_do_usuario(lote_id, u)

    relatorio = instalacao.relatorio(lote_id)

    fatos = relatorio.amostra(chave) if relatorio else None

    if fatos is None:
        raise HTTPException(404, 'amostra não encontrada')

    permitidos = set(POR_ID) | {estado.id for estado in instalacao.estante.meus_bancos(u.email)}

    if banco not in permitidos or not instalacao.estante.existe(banco):
        raise HTTPException(404, 'banco não montado')

    busca = executor_de_lote.busca_de_referencia(instalacao.cfg, banco)

    if busca is None:
        raise HTTPException(500, SEM_BLASTN)

    with instalacao.vaga_pesada():

        montagem = cromatograma.remontar(instalacao.cfg, lote_id, fatos)

        if montagem is None:
            raise HTTPException(409, 'não foi possível remontar a amostra')

        try:
            acertos = [acerto_na_tela(acerto) for alvo in busca.bancos for acerto in busca.acertos(montagem.consenso_limpo, alvo)]

        except Exception as erro:

            REGISTRO.error('falha ao consultar o banco %s: %s: %s', banco, type(erro).__name__, erro, exc_info=erro)

            raise HTTPException(500, 'não foi possível consultar o banco; tente de novo. O motivo completo está no log.')

    return JSONResponse({'banco': banco, 'acertos': acertos, 'consulta': True})




@router.get('/lotes/{lote_id}', response_class=HTMLResponse)
def pagina_lote(request: Request, lote_id: str, tela_do_lote: int = 0):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    lote = instalacao.lote_do_usuario(lote_id, u)

    relatorio = instalacao.relatorio(lote_id) if lote.status == PRONTO else None

    amostras = leitura_de_amostras.listar(relatorio) if relatorio else None

    if amostras and not tela_do_lote and len(amostras) == 1:
        return RedirectResponse(f'/lotes/{lote_id}/amostras/{quote(amostras[0]["chave"])}', status_code=303)

    return comum.TEMPLATES.TemplateResponse(request, 'lote.html', {
        **instalacao.casca(u),
        'lote': lote,
        'recarregar': lote.ativo,
        'fila_atendida': instalacao.lotes.fila_atendida() if lote.status == NA_FILA else True,
        'amostras': amostras,
        'resumo': leitura_de_amostras.resumo(relatorio) if relatorio else None,
        'expira_em': expurgo_por_retencao.expira_em(lote, instalacao.cfg.retencao_dias),
    })




@router.get('/api/lotes/{lote_id}/amostras/{chave}/traco')
def api_traco(request: Request, lote_id: str, chave: str):

    #* por API e nao embutido na pagina porque e o pedaco pesado (~112 KB comprimidos): a pagina abre com os numeros, o traco chega depois

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.lote_do_usuario(lote_id, u)

    fatos = amostra_ou_404(relatorio_ou_404(instalacao, lote_id), chave)

    with instalacao.vaga_pesada():
        dados = cromatograma.traco_da_amostra(instalacao.cfg, lote_id, fatos)

    if not dados:
        return JSONResponse({'disponivel': False, 'motivo': SEM_TRACO})

    dados['disponivel'] = True

    return JSONResponse(dados)




@router.get('/lotes/{lote_id}/amostras/{chave}', response_class=HTMLResponse)
def pagina_amostra(request: Request, lote_id: str, chave: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    lote = instalacao.lote_do_usuario(lote_id, u)

    fatos = amostra_ou_404(relatorio_ou_404(instalacao, lote_id), chave)

    referencia_usada = lote.referencia or CURADO

    return comum.TEMPLATES.TemplateResponse(request, 'amostra.html', {
        **instalacao.casca(u),
        'lote': lote,
        'amostra': leitura_de_amostras.formatar_amostra(fatos),
        'consultaveis': [item for item in instalacao.referencias(u.email) if item['id'] != referencia_usada],
        'referencia_usada': referencia_usada,
    })




@router.get('/api/lotes/{lote_id}')
def api_lote(request: Request, lote_id: str):

    instalacao = comum.instalacao_de(request)

    return JSONResponse(vars(instalacao.lote_do_usuario(lote_id, comum.exigir(request))))




@router.post('/lotes/{lote_id}/arvore')
def pedir_arvore(request: Request, lote_id: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    lote = instalacao.lote_do_usuario(lote_id, u)

    if lote.status != PRONTO:
        raise HTTPException(409, 'a árvore só pode ser montada depois que o lote terminar')

    instalacao.arvores.novo_pedido(lote_id, u.email)

    return RedirectResponse(f'/lotes/{lote_id}/arvore', status_code=303)




def pedido_do_usuario(instalacao, lote_id, u):

    #* confere o dono pelo LOTE, como o resto; sem pedido -> 404

    instalacao.lote_do_usuario(lote_id, u)

    pedido = instalacao.arvores.do_lote(lote_id)

    if not pedido:
        raise HTTPException(404, 'nenhuma árvore pedida para este lote')

    return pedido




@router.get('/lotes/{lote_id}/arvore', response_class=HTMLResponse)
def pagina_arvore(request: Request, lote_id: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    lote = instalacao.lote_do_usuario(lote_id, u)

    pedido = instalacao.arvores.do_lote(lote_id)

    return comum.TEMPLATES.TemplateResponse(request, 'arvore.html', {
        **instalacao.casca(u),
        'lote': lote,
        'pedido': pedido,
        'resumo': pedido.resumo if pedido else [],
        'recarregar': bool(pedido and pedido.ativo),
        'fila_atendida': instalacao.arvores.fila_atendida() if pedido and pedido.status == fila_de_arvores.NA_FILA else True,
    })




@router.get('/api/lotes/{lote_id}/arvore')
def api_arvore(request: Request, lote_id: str):

    instalacao = comum.instalacao_de(request)

    return JSONResponse(vars(pedido_do_usuario(instalacao, lote_id, comum.exigir(request))))




@router.get('/lotes/{lote_id}/arvore/{pasta}/{arquivo}')
def arquivo_da_arvore(request: Request, lote_id: str, pasta: str, arquivo: str):

    #* o Newick sai como download porque o destino dele e outro programa (FigTree, iTOL, MEGA); a figura abre na pagina

    instalacao = comum.instalacao_de(request)

    instalacao.lote_do_usuario(lote_id, comum.exigir(request))

    try:
        caminho = executor_de_arvore.caminho_de_saida(instalacao.cfg, lote_id, pasta, arquivo)

    except ValueError:
        raise HTTPException(404, 'arquivo não encontrado')

    if not caminho.exists():
        raise HTTPException(404, 'arquivo não gerado')

    if caminho.suffix == '.png':
        return FileResponse(caminho, media_type='image/png')

    return FileResponse(caminho, media_type='text/plain', filename=f'{pasta}_{lote_id}{caminho.suffix}')




@router.post('/lotes/{lote_id}/apagar')
def apagar_lote(request: Request, lote_id: str):

    #* apagar agora, sem esperar o prazo: quem mandou a pasta errada precisa tira-la do servidor no mesmo minuto

    instalacao = comum.instalacao_de(request)

    instalacao.lote_do_usuario(lote_id, comum.exigir(request))

    if not expurgo_por_retencao.apagar_lote(instalacao.banco, instalacao.cfg.pasta_de_lotes, lote_id):
        raise HTTPException(409, 'o lote está em uso; tente de novo quando terminar')

    return RedirectResponse('/', status_code=303)




def arquivo_do_lote(request, lote_id, qual, midia, baixar_como=None):

    #* um dos arquivos gravados pelo trabalhador (relatorio_html, resultado_csv, relatorio_json), so de lote PRONTO

    instalacao = comum.instalacao_de(request)

    lote = instalacao.lote_do_usuario(lote_id, comum.exigir(request))

    if lote.status != PRONTO:
        raise HTTPException(409, f"lote ainda em '{lote.status}'")

    caminho = getattr(instalacao.cfg.pastas_do_lote(lote_id), qual)

    if not caminho.exists():
        raise HTTPException(404, 'arquivo não gerado')

    return FileResponse(caminho, media_type=midia, filename=baixar_como)




@router.get('/lotes/{lote_id}/relatorio')
def relatorio(request: Request, lote_id: str):

    return arquivo_do_lote(request, lote_id, 'relatorio_html', 'text/html')




@router.get('/lotes/{lote_id}/resultado.csv')
def resultado_csv(request: Request, lote_id: str):

    return arquivo_do_lote(request, lote_id, 'resultado_csv', 'text/csv', baixar_como=f'resultado_{lote_id}.csv')




@router.get('/lotes/{lote_id}/relatorio.json')
def relatorio_json(request: Request, lote_id: str):

    return arquivo_do_lote(request, lote_id, 'relatorio_json', 'application/json', baixar_como=f'relatorio_{lote_id}.json')




def main():

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:48} {rota.endpoint.__name__}')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
