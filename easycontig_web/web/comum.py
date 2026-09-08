"""
comum.py — o que toda rota usa e nao guarda estado: templates, sessao, recado, cabecalhos de seguranca.

    request ---> instalacao_de(request)   app.state.instalacao (veja instalacao.py)
       |
       |-----> usuario(request)           Usuario da sessao, ou None
       |-----> exigir(request)            Usuario, ou HTTPException 401 "entre para continuar"
       |
       |-----> por_recado(request, texto) guarda UMA frase na sessao (assinada) para a proxima pagina mostrar
       |-----> pegar_recado(request)      le e apaga: o recado aparece uma vez so
       |
       '-----> TEMPLATES                  Jinja2 apontado para easycontig_web/templates
                 |-- filtro br            99.4 -> "99,4"   (so na tela; CSV e JSON continuam com ponto)
                 '-- filtro tamanho       1536000 -> "1,5 MB"

    resposta --> marcar_resposta(resposta, nonce, https_only)   CSP com nonce, nosniff, DENY, noindex, HSTS

COMO FUNCIONA

    Sao funcoes puras sobre o request e a resposta. O que tem dono (banco, fila,
    estante, NCBI) mora na Instalacao; aqui so mora o que e igual em qualquer
    instalacao e nao muda entre pedidos.

COMO USAR

    from easycontig_web.web import comum

    @router.get('/x')
    def pagina_x(request: Request):
        instalacao = comum.instalacao_de(request)
        u = comum.exigir(request)
        return comum.TEMPLATES.TemplateResponse(request, 'x.html', {**instalacao.casca(u), ...})

O QUE MUDOU (em relacao a web/comum.py original)

 1. cfg() — a configuracao era relida do ambiente A CADA USO (15,9 us por
    chamada) para a suite antiga poder recarregar o servidor 28 vezes. Agora a
    configuracao e da Instalacao, e um teste que quer outra pasta cria outra
    Instalacao e outro app (servidor_web.criar_app).

 2. _referencias, _casca, _lote_do_usuario, _relatorio e _vaga_pesada sairam
    daqui: dependem do banco, da estante e do semaforo, entao sao metodos da
    Instalacao. _vaga_pesada importava servidor_web por dentro da funcao para
    escapar do import circular; nao ha mais ciclo.

 3. Tres funcoes diferentes formatavam bytes (aqui, no perfil e na cota). Fica
    uma: cota_de_espaco.tamanho, exposta ao template como filtro `tamanho`.

 4. _u -> usuario, _exigir -> exigir, _por_recado/_pegar_recado sem o sublinhado;
    _marcar_resposta -> marcar_resposta(resposta, nonce, https_only): o HSTS
    lia EASYCONTIG_HTTPS_ONLY do ambiente a cada resposta; agora quem chama
    passa o que a Instalacao leu uma vez.

POR QUE nao nasce classe

    Nada aqui tem ciclo de vida. TEMPLATES e um objeto da biblioteca montado
    uma vez; o resto sao funcoes de um request para um valor.
"""

from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from easycontig_web.contas import autenticacao
from easycontig_web.contas.cota_de_espaco import tamanho


PASTA_DE_TEMPLATES = Path(__file__).parent.parent / 'templates'  #* este arquivo mora em web/; os templates ficam na raiz do pacote

EXT_ACEITAS = {'.ab1', '.abi', '.scf'}  #* o que um lote pode conter

RECADO = 'recado'  #* chave na sessao

TAMANHO_DO_RECADO = 300  #* caracteres; um recado e uma frase, nao um log

POLITICA_DE_SEGURANCA = ("default-src 'none'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

UM_ANO = 31536000  #* segundos, para o HSTS




def decimal_brasileiro(valor):

    #* 99.4 -> "99,4"; None -> "". so na tela: o CSV vai para planilha e para o R, e "96.500" em portugues le-se noventa e seis mil e quinhentos

    texto = '' if valor is None else str(valor)

    return texto.replace('.', ',')




TEMPLATES = Jinja2Templates(directory=str(PASTA_DE_TEMPLATES))

TEMPLATES.env.filters['br'] = decimal_brasileiro

TEMPLATES.env.filters['tamanho'] = tamanho




def instalacao_de(request):

    return request.app.state.instalacao




def usuario(request):

    return autenticacao.usuario_da_sessao(request.session)




def exigir(request):

    u = usuario(request)

    if not u:
        raise HTTPException(401, 'entre para continuar')

    return u




def por_recado(request, texto):

    #* a mensagem NAO viaja na URL: `/labs?erro=sua+sessao+expirou...` sairia na caixa de erro do proprio site com a autoridade dele.
    #* a sessao e assinada pelo servidor, entao o texto e sempre o que o app escreveu

    request.session[RECADO] = (texto or '')[:TAMANHO_DO_RECADO]




def pegar_recado(request):

    #* le e apaga: senao o erro reaparece a cada recarregamento, depois de resolvido

    return request.session.pop(RECADO, '')




def quer_html(request):

    return 'text/html' in request.headers.get('accept', '')




def marcar_resposta(resposta, nonce, https_only=False):

    #* em funcao a parte porque a pagina de 500 precisa dos MESMOS cabecalhos e nao passa por middleware nenhum

    cabecalhos = resposta.headers

    cabecalhos['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    cabecalhos['X-Content-Type-Options'] = 'nosniff'
    cabecalhos['X-Frame-Options'] = 'DENY'
    cabecalhos['Referrer-Policy'] = 'same-origin'
    cabecalhos['Content-Security-Policy'] = POLITICA_DE_SEGURANCA.format(nonce=nonce)

    if https_only:
        cabecalhos['Strict-Transport-Security'] = f'max-age={UM_ANO}; includeSubDomains'




def main():

    #* mostra os filtros e a pasta dos templates

    print(f'templates em {PASTA_DE_TEMPLATES} ({"existe" if PASTA_DE_TEMPLATES.is_dir() else "NAO existe"})')

    for valor in (99.4, 12, None, 'abc'):
        print(f'  br({valor!r}) = {decimal_brasileiro(valor)!r}')

    for n in (0, 1536000, 3 * 1024 ** 3):
        print(f'  tamanho({n}) = {tamanho(n)!r}')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
