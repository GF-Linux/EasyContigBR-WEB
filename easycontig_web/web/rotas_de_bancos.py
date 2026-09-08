"""
rotas_de_bancos.py — os bancos de referencia: o catalogo (NCBI) e os bancos proprios (FASTA enviado).

    GET  /bancos?feito=<id>          pagina_bancos   bancos.html: catalogo por grupo com o estado de cada um, meus bancos, erro = recado, feito
    POST /bancos/{banco_id}/montar   montar_banco    estante.montar(id, ncbi): procura no NCBI, baixa, makeblastdb -> 303 /bancos?feito=<id>
    POST /bancos/{banco_id}/remover  remover_banco   meu_... so do dono; do catalogo so administrador -> 303 /bancos
    POST /bancos/meu                 enviar_banco    apelido + FASTA (ate 20 MB) -> estante.montar_do_usuario -> 303 /bancos?feito=meu

    o que da errado na montagem:
        BancoOcupado (outro pedido montando o mesmo)  -> 409 com o motivo
        ErroDoBanco / ErroDoNcbi (escritos para a tela) -> recado com o motivo + 303 /bancos
        qualquer outra excecao                          -> log completo; recado generico + 303 /bancos

COMO FUNCIONA

    Montar do catalogo e sincrono de proposito: sao segundos para os pequenos e
    quem aperta esta olhando; os grandes avisam o tamanho antes. O banco do
    usuario roda o makeblastdb numa vaga pesada (instalacao.vaga_pesada) e em
    thread, para nao segurar o laco do servidor.

    Um banco do catalogo e de todo o laboratorio: remontar ou remover so
    administrador (EASYCONTIG_ADMINS; vazio = ninguem, de proposito).

COMO USAR

    app.include_router(rotas_de_bancos.router)

O QUE MUDOU (em relacao ao original)

 1. _so_uma_montagem (trava O_EXCL <prefixo>.montando.lock) sumiu: a
    EstanteDeBancos.trava faz o mesmo por dentro de montar/montar_do_usuario
    e levanta BancoOcupado, que aqui vira 409 — com o motivo da estante
    (diz que outro pedido esta montando e que a trava velha se limpa sozinha),
    em vez de "este banco ja esta sendo montado agora; espere terminar".

 2. ErroDoBanco e ErroDoNcbi chegam a tela: as mensagens ja nascem para
    quem esta na frente do formulario (sem caminho de servidor; a pasta dos
    bancos sai como <bancos>). O original mandava tudo para o log e
    mostrava "nao foi possivel montar o banco; tente de novo. O motivo
    completo esta no log" — e o motivo mais comum (termo sem resultado,
    NCBI fora, FASTA sem '>') era exatamente o que a pessoa precisava ler.
    O generico continua para o que nao foi previsto.

 3. O FASTA e medido em BYTES antes de decodificar. O original decodificava
    com 'replace' e media caracteres: um FASTA de 20 MB + 1 byte com
    acentos multibyte no cabecalho passava do teto sem ser pego.

 4. _e_administrador lia EASYCONTIG_ADMINS a cada clique; e
    instalacao.e_administrador(u), com a lista lida uma vez.

 5. O item do template era {"c": ficha, "estado": ...}; agora {"ficha": ...,
    "estado": ...}. `meus` sao EstadoDoBanco (id, apelido, sequencias, ...).

POR QUE nao nasce classe

    Rotas; o estado (estante, ncbi, cotas, vagas) e da Instalacao.
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from bancos_de_referencia import CATALOGO, POR_ID, BancoOcupado, ErroDoBanco, id_do_usuario
from cliente_ncbi import ErroDoNcbi

from easycontig_web.web import comum


REGISTRO = logging.getLogger('easycontig.bancos')

TETO_DO_FASTA = 20 * 1024 * 1024  #* bytes; 60 000 sequencias de 18S dao uns 100 MB, mas um banco proprio e de dezenas a centenas

PREFIXO_DO_USUARIO = 'meu_'

SO_ADMINISTRADOR_REMONTA = ('este banco compartilhado já está montado; remontá-lo sobrescreve a referência que todo o laboratório usa '
                            'e é ação de quem administra o servidor')

SO_ADMINISTRADOR_REMOVE = 'remover um banco compartilhado é ação de quem administra o servidor; defina EASYCONTIG_ADMINS para liberar'

router = APIRouter()




@router.get('/bancos', response_class=HTMLResponse)
def pagina_bancos(request: Request, feito: str = ''):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    grupos = {}

    for ficha in CATALOGO:
        grupos.setdefault(ficha.grupo, []).append({'ficha': ficha, 'estado': instalacao.estante.estado(ficha.id)})

    return comum.TEMPLATES.TemplateResponse(request, 'bancos.html', {
        **instalacao.casca(u),
        'grupos': grupos,
        'meus': instalacao.estante.meus_bancos(u.email),
        'erro': comum.pegar_recado(request),
        'feito': feito,
    })




def falha_na_tela(request, erro, generico):

    #* o que a pessoa le quando a montagem falha: o motivo, se foi escrito para ela; senao o generico. o log leva tudo

    if isinstance(erro, (ErroDoBanco, ErroDoNcbi)):

        REGISTRO.warning('%s', erro)

        comum.por_recado(request, str(erro))

    else:

        REGISTRO.error('%s: %s: %s', generico, type(erro).__name__, erro, exc_info=erro)

        comum.por_recado(request, generico)

    return RedirectResponse('/bancos', status_code=303)




@router.post('/bancos/{banco_id}/montar')
def montar_banco(request: Request, banco_id: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.limitador.conferir('banco', request, u.email)

    if banco_id not in POR_ID:
        raise HTTPException(404, 'banco desconhecido')

    if instalacao.estante.existe(banco_id) and not instalacao.e_administrador(u):
        raise HTTPException(403, SO_ADMINISTRADOR_REMONTA)

    try:
        instalacao.estante.montar(banco_id, instalacao.ncbi)

    except BancoOcupado as erro:
        raise HTTPException(409, str(erro))

    except Exception as erro:
        return falha_na_tela(request, erro, f'não foi possível montar o banco {banco_id}; tente de novo. O motivo completo está no log.')

    return RedirectResponse(f'/bancos?feito={quote(banco_id)}', status_code=303)




@router.post('/bancos/{banco_id}/remover')
def remover_banco(request: Request, banco_id: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    if banco_id.startswith(PREFIXO_DO_USUARIO):

        if banco_id not in {estado.id for estado in instalacao.estante.meus_bancos(u.email)}:
            raise HTTPException(404, 'banco não encontrado')

    elif banco_id not in POR_ID:
        raise HTTPException(404, 'banco desconhecido')

    elif not instalacao.e_administrador(u):
        raise HTTPException(403, SO_ADMINISTRADOR_REMOVE)

    instalacao.estante.remover(banco_id)

    return RedirectResponse('/bancos', status_code=303)




def cabe_mais_um(request, cota):

    #* a cota de bancos e de bytes da conta, como recado (nao 413): quem esta na pagina de bancos le e decide o que apagar

    if cota.max_bancos and cota.bancos >= cota.max_bancos:

        comum.por_recado(request, f'você já mantém {cota.bancos} bancos próprios (o limite é {cota.max_bancos}); remova um antes de montar outro')

        return False

    if not cota.pode_enviar and cota.bytes_usados >= cota.max_bytes_conta > 0:

        comum.por_recado(request, cota.motivo)

        return False

    return True




@router.post('/bancos/meu')
async def enviar_banco(request: Request, apelido: str = Form(...), fasta: UploadFile = File(...)):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.limitador.conferir('banco', request, u.email)

    if not cabe_mais_um(request, instalacao.cotas.situacao(u.email)):
        return RedirectResponse('/bancos', status_code=303)

    try:
        banco_id = id_do_usuario(u.email, apelido.strip())

        dados = await fasta.read(TETO_DO_FASTA + 1)

        if len(dados) > TETO_DO_FASTA:
            raise ErroDoBanco(f'o FASTA passa de {TETO_DO_FASTA // (1024 * 1024)} MB')

        with instalacao.vaga_pesada():
            await run_in_threadpool(instalacao.estante.montar_do_usuario, banco_id, dados.decode('utf-8', 'replace'))

    except HTTPException:
        raise

    except BancoOcupado as erro:
        raise HTTPException(409, str(erro))

    except Exception as erro:
        return falha_na_tela(request, erro, 'não foi possível montar o seu banco; tente de novo. O motivo completo está no log.')

    return RedirectResponse('/bancos?feito=meu', status_code=303)




def main():

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:28} {rota.endpoint.__name__}')

    print(f'  catálogo: {len(CATALOGO)} bancos em {len({ficha.grupo for ficha in CATALOGO})} grupos; FASTA próprio até {TETO_DO_FASTA // (1024 * 1024)} MB')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
