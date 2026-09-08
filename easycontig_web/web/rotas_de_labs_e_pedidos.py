"""
rotas_de_labs_e_pedidos.py — o diretorio dos laboratorios e os pedidos de amostra entre eles.

    GET  /labs                           pagina_labs       labs.html: os perfis (so o que cada um DECLAROU), erro = recado, enviado
    POST /labs/{chave}/pedido            pedir_amostra     chave sorteada -> e-mail do outro (perfis.email_da_chave) -> pedidos.criar -> 303 /pedidos?enviado=1
    GET  /pedidos?enviado=1              pagina_pedidos    pedidos.html: recebidos, enviados, erro = recado (responder sem justificativa)
    POST /pedidos/{pedido_id}/responder  responder_pedido  acao = aceitar | recusar, com justificativa -> pedidos.responder -> 303 /pedidos
    POST /pedidos/{pedido_id}/cancelar   cancelar_pedido   so quem pediu -> pedidos.cancelar -> 303 /pedidos

    NaoPode (regra do pedido) ---> recado na sessao + 303 de volta: a pessoa le o motivo na propria pagina

COMO FUNCIONA

    O alvo de um pedido vem por chave sorteada, nunca por e-mail: o endereco
    do outro nao aparece no HTML do /labs. Chave desconhecida responde como
    pedido invalido (recado), nao 404 — 404 faria da rota um oraculo para
    varrer chaves. Aceitar e o unico ponto do app em que um e-mail atravessa a
    fronteira entre contas; a tela avisa antes do botao.

COMO USAR

    app.include_router(rotas_de_labs_e_pedidos.router)

O QUE MUDOU (em relacao ao original)

 1. perfil.listar_perfis(sqlite, eu) -> instalacao.perfis.listar(eu);
    pedidos.criar(sqlite, ...) -> instalacao.pedidos.criar(...). Os textos
    das respostas sao os mesmos.

 2. pagina_pedidos passa `erro` (pegar_recado) ao template, e pedidos.html o
    mostra. No original responder_pedido gravava o recado da NaoPode ("diga o
    motivo...") e redirecionava para /pedidos, que NAO o lia: a pessoa via a
    recusa sumir sem explicacao, e o recado aparecia mais tarde no /labs.

POR QUE nao nasce classe

    Rotas; o estado (perfis, pedidos) e da Instalacao.
"""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from easycontig_web.dados.pedidos_entre_labs import NaoPode
from easycontig_web.web import comum


ACOES = ('aceitar', 'recusar')

router = APIRouter()




@router.get('/labs', response_class=HTMLResponse)
def pagina_labs(request: Request):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    return comum.TEMPLATES.TemplateResponse(request, 'labs.html', {
        **instalacao.casca(u),
        'pagina': 'labs',
        'perfis': instalacao.perfis.listar(u.email),
        'erro': comum.pegar_recado(request),
        'enviado': request.query_params.get('enviado', '') == '1',
    })




@router.post('/labs/{chave}/pedido')
def pedir_amostra(request: Request, chave: str, itens: list[str] = Form(default=[]), outro: str = Form(''), motivo: str = Form('')):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.limitador.conferir('pedido', request, u.email)

    destino = instalacao.perfis.email_da_chave(chave)

    if not destino:

        comum.por_recado(request, 'não foi possível enviar o pedido a este laboratório; recarregue a página')

        return RedirectResponse('/labs', status_code=303)

    try:
        instalacao.pedidos.criar(u.email, destino, itens, outro=outro, motivo=motivo)

    except NaoPode as erro:

        comum.por_recado(request, str(erro))

        return RedirectResponse('/labs', status_code=303)

    return RedirectResponse('/pedidos?enviado=1', status_code=303)




@router.get('/pedidos', response_class=HTMLResponse)
def pagina_pedidos(request: Request, enviado: int = 0):

    #* nao e caixa de mensagens: cada linha tem no maximo o motivo de quem pediu, a justificativa de quem respondeu e um estado final.
    #* existe porque o app nao manda e-mail: o unico lugar onde um pedido aparece e dentro do site

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    return comum.TEMPLATES.TemplateResponse(request, 'pedidos.html', {
        **instalacao.casca(u),
        'pagina': 'pedidos',
        'recebidos': instalacao.pedidos.recebidos(u.email),
        'enviados': instalacao.pedidos.enviados(u.email),
        'erro': comum.pegar_recado(request),  #! o original gravava o recado de responder_pedido e nunca o lia aqui: a recusa sem justificativa sumia calada
        'enviado': bool(enviado),
    })




@router.post('/pedidos/{pedido_id}/responder')
def responder_pedido(request: Request, pedido_id: str, acao: str = Form(''), resposta: str = Form('')):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.limitador.conferir('pedido', request, u.email)

    if acao not in ACOES:
        raise HTTPException(400, 'ação desconhecida')

    try:
        atualizado = instalacao.pedidos.responder(pedido_id, u.email, acao == 'aceitar', resposta)

    except NaoPode as erro:

        comum.por_recado(request, str(erro))

        return RedirectResponse('/pedidos', status_code=303)

    if atualizado is None:
        raise HTTPException(404, 'pedido não encontrado')

    return RedirectResponse('/pedidos', status_code=303)




@router.post('/pedidos/{pedido_id}/cancelar')
def cancelar_pedido(request: Request, pedido_id: str):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    if not instalacao.pedidos.cancelar(pedido_id, u.email):
        raise HTTPException(404, 'pedido não encontrado')

    return RedirectResponse('/pedidos', status_code=303)




def main():

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:32} {rota.endpoint.__name__}')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
