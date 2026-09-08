"""
rotas_de_perfil.py — o perfil do laboratorio, a foto e a pagina leia-me.

    GET  /leiame                pagina_leiame      como nomear os arquivos e como o motor decide F/R
    GET  /perfil?editando=1     pagina_perfil      perfil.html: Perfil, relatorios prontos (lote + n amostras), resumo das corridas, cota
    POST /perfil                salvar_perfil      campos do formulario + foto (.png/.jpg/.jpeg/.webp, ate 2 MB) -> perfis.salvar -> 303 /perfil
    GET  /perfil/foto           foto_do_perfil     a foto de quem entrou (404 "sem foto")
    GET  /labs/foto/{arquivo}   foto_do_diretorio  a foto de QUALQUER perfil, pelo nome sorteado do arquivo (nunca pelo e-mail)

    foto ---> cfg.pasta_de_fotos / token_urlsafe(8) + extensao ---> coluna `foto` do perfil; a antiga e apagada

COMO FUNCIONA

    A foto sai por rota, nao de pasta estatica: continua atras do login. No
    diretorio, o endereco e o nome do arquivo (sorteado no envio), com tres
    travas que nao confiam uma na outra: exigir sessao, Path(...).name (nao
    sai da pasta) e perfis.foto_registrada (so o que esta na coluna de algum
    perfil).

COMO USAR

    app.include_router(rotas_de_perfil.router)

O QUE MUDOU (em relacao ao original)

 1. cfg.data_dir / "fotos", escrito a mao em tres rotas, virou cfg.pasta_de_fotos.

 2. perfis.pegar nunca devolve None (o Perfil com os padroes), entao o
    `(pegar(...) or {}).get("foto")` virou `perfis.pegar(email).foto`.

 3. Os relatorios prontos vao ao template como {'lote': Lote, 'n': int}, como
    antes; o Lote e objeto (lote.id, lote.status) em vez de dicionario.

POR QUE nao nasce classe

    Rotas; o estado (perfis, lotes, cotas) e da Instalacao.
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from easycontig_web.contas.perfil_do_laboratorio import resumo_das_corridas
from easycontig_web.dados import leitura_de_amostras
from easycontig_web.processamento.fila_de_lotes import PRONTO
from easycontig_web.web import comum


EXTENSOES_DE_FOTO = {'.png', '.jpg', '.jpeg', '.webp'}

TETO_DA_FOTO = 2 * 1024 * 1024  #* bytes

LOTES_NO_PERFIL = 500  #* quantas corridas a pagina de perfil enxerga para o resumo

BYTES_DO_NOME_DA_FOTO = 8  #* token_urlsafe: nao se adivinha, nao diz de quem e

router = APIRouter()




@router.get('/leiame', response_class=HTMLResponse)
def pagina_leiame(request: Request):

    #! a pagina afirma que a orientacao sai do DNA (k-mers, orientacao.py) e NAO do nome nem do metadado do .ab1: se o motor mudar, ela passa a mentir

    instalacao = comum.instalacao_de(request)

    return comum.TEMPLATES.TemplateResponse(request, 'leiame.html', {**instalacao.casca(comum.exigir(request)), 'pagina': 'leiame'})




def relatorios_prontos(instalacao, lotes):

    #* [{'lote': Lote, 'n': amostras}] dos lotes PRONTO cujo relatorio.json existe

    prontos = []

    for lote in lotes:

        if lote.status != PRONTO:
            continue

        n = leitura_de_amostras.contar_amostras(instalacao.cfg.pastas_do_lote(lote.id).relatorio_json)

        if n is not None:
            prontos.append({'lote': lote, 'n': n})

    return prontos




@router.get('/perfil', response_class=HTMLResponse)
def pagina_perfil(request: Request, editando: int = 0):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    lotes = instalacao.lotes.listar(dono=u.email, limite=LOTES_NO_PERFIL)

    prontos = relatorios_prontos(instalacao, lotes)

    return comum.TEMPLATES.TemplateResponse(request, 'perfil.html', {
        **instalacao.casca(u),
        'pagina': 'perfil',
        'perfil': instalacao.perfis.pegar(u.email),
        'relatorios': prontos,
        'resumo': resumo_das_corridas(lotes, [item['n'] for item in prontos]),
        'cota': instalacao.cotas.situacao(u.email),
        'editando': bool(editando),
    })




async def guardar_foto(instalacao, email, foto):

    #* confere extensao e tamanho, grava com nome sorteado e apaga a foto anterior; devolve o nome novo (None se nao veio foto)

    if foto is None or not foto.filename:
        return None

    extensao = Path(foto.filename).suffix.lower()

    if extensao not in EXTENSOES_DE_FOTO:
        raise HTTPException(400, 'a foto precisa ser .png, .jpg ou .webp')

    dados = await foto.read(TETO_DA_FOTO + 1)

    if len(dados) > TETO_DA_FOTO:
        raise HTTPException(413, 'a foto passa de 2 MB')

    pasta = instalacao.cfg.pasta_de_fotos

    pasta.mkdir(parents=True, exist_ok=True)

    nome_da_foto = secrets.token_urlsafe(BYTES_DO_NOME_DA_FOTO) + extensao

    (pasta / nome_da_foto).write_bytes(dados)

    antiga = instalacao.perfis.pegar(email).foto

    if antiga and antiga != nome_da_foto:
        (pasta / Path(antiga).name).unlink(missing_ok=True)

    return nome_da_foto




@router.post('/perfil')
async def salvar_perfil(request: Request, nome: str = Form(''), laboratorio: str = Form(''), instituicao: str = Form(''), sobre: str = Form(''),
                        especies: str = Form(''), marcadores: str = Form(''), links: str = Form(''), foto: UploadFile | None = File(None)):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    instalacao.limitador.conferir('envio', request, u.email)

    nome_da_foto = await guardar_foto(instalacao, u.email, foto)

    instalacao.perfis.salvar(u.email, nome=nome, laboratorio=laboratorio, instituicao=instituicao, sobre=sobre, especies=especies,
                             marcadores=marcadores, foto=nome_da_foto, links=links)

    return RedirectResponse('/perfil', status_code=303)




def arquivo_da_foto(instalacao, nome):

    #* o caminho da foto na pasta, ou 404 se nao esta la

    caminho = instalacao.cfg.pasta_de_fotos / Path(nome).name

    if not caminho.exists():
        raise HTTPException(404, 'sem foto')

    return FileResponse(caminho)




@router.get('/perfil/foto')
def foto_do_perfil(request: Request):

    instalacao = comum.instalacao_de(request)

    u = comum.exigir(request)

    foto = instalacao.perfis.pegar(u.email).foto

    if not foto:
        raise HTTPException(404, 'sem foto')

    return arquivo_da_foto(instalacao, foto)




@router.get('/labs/foto/{arquivo}')
def foto_do_diretorio(request: Request, arquivo: str):

    instalacao = comum.instalacao_de(request)

    comum.exigir(request)

    nome = Path(arquivo).name

    if not instalacao.perfis.foto_registrada(nome):
        raise HTTPException(404, 'sem foto')

    return arquivo_da_foto(instalacao, nome)




def main():

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:22} {rota.endpoint.__name__}')

    print(f'  foto: {sorted(EXTENSOES_DE_FOTO)}, até {TETO_DA_FOTO // (1024 * 1024)} MB')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
