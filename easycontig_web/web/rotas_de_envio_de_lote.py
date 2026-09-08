"""
rotas_de_envio_de_lote.py — receber um lote de cromatogramas e po-lo na fila.

    POST /lotes  (multipart: arquivos[], nome, referencia)
       |
       |-- exigir sessao; limitador "envio"
       |-- referencia: vazia -> 400; desconhecida -> 400; do catalogo mas nao montada -> 400
       |-- arquivos: so .ab1/.abi/.scf; nenhum -> 400; mais que cfg.maximo_de_arquivos -> 413
       |-- cotas.situacao(email).pode_enviar? nao -> 413 com o motivo
       |
       |-- lotes.novo_lote(...)  RECEBENDO, com as cotas conferidas DENTRO da transacao
       |       CotaEstourada -> 413 "voce ja tem N corrida(s)..."   CotaDeBytesEstourada -> 413 "este envio nao cabe..."
       |
       |-- grava cada arquivo em pastas.entrada, 1 MiB por vez; passou de cfg.maximo_de_bytes -> 413 (e desfaz)
       |       OSError -> apaga a pasta, lotes.falhar, 507 (sem espaco) ou 500
       |       outra excecao -> apaga a pasta, lotes.falhar, re-levanta
       |
       '-- lotes.liberar_para_fila(lote_id, gravados) -> 303 /lotes/{lote_id}

COMO FUNCIONA

    O que protege o disco vem em camadas: o middleware ja recusou pelo
    Content-Length; aqui o teto por lote e conferido byte a byte enquanto grava,
    e a cota da conta e conferida na transacao que cria o lote (conferir antes,
    na rota, deixava a janela entre contar e criar).

COMO USAR

    app.include_router(rotas_de_envio_de_lote.router)

O QUE MUDOU (em relacao ao original)

 1. As pastas do lote vem de cfg.pastas_do_lote(lote_id) (PastasDoLote) em vez
    de executor.pastas_do_lote(cfg, id)["entrada"].

 2. A referencia "curado" e a constante executor_de_lote.CURADO; os bancos do
    usuario vem de estante.meus_bancos(email) (EstadoDoBanco.id).

 3. O aviso de falha de disco vai para o logger em vez do stderr.

POR QUE nao nasce classe

    Uma rota; o estado (fila, cotas, estante) e da Instalacao.
"""

import errno
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from bancos_de_referencia import POR_ID

from easycontig_web.processamento.executor_de_lote import CURADO
from easycontig_web.processamento.fila_de_lotes import CotaEstourada, CotaDeBytesEstourada
from easycontig_web.web import comum


REGISTRO = logging.getLogger('easycontig.envio')

MEGA = 1024 * 1024

PEDACO = MEGA  #* quanto le de cada arquivo por vez

SEM_ESPACO = ('o servidor está sem espaço em disco — nada foi gravado. '
              'Não é a sua seleção, e reenviar não resolve: avise quem cuida do servidor.')

router = APIRouter()




def conferir_referencia(instalacao, email, referencia):

    #* a referencia tem de ser o curado, um banco do catalogo ja montado ou um banco deste usuario

    if not referencia:
        raise HTTPException(400, 'escolha a referência antes de processar')

    permitidos = {CURADO} | set(POR_ID) | {estado.id for estado in instalacao.estante.meus_bancos(email)}

    if referencia not in permitidos:
        raise HTTPException(400, 'referência desconhecida')

    if referencia != CURADO and not instalacao.estante.existe(referencia):
        raise HTTPException(400, 'essa referência ainda não foi montada nesta instalação')




def tamanho_declarado(request):

    try:
        return int(request.headers.get('content-length') or 0)

    except ValueError:
        return 0




def abrir_lote(instalacao, email, nome, n_arquivos, referencia, bytes_previstos):

    #* cria o lote em RECEBENDO; as cotas viram 413 com o que a pessoa pode fazer

    situacao = instalacao.cotas.situacao(email)

    if not situacao.pode_enviar:
        raise HTTPException(413, situacao.motivo)

    try:
        return instalacao.lotes.novo_lote(email, nome, n_arquivos, referencia=referencia, teto_ativos=instalacao.cotas.max_lotes_ativos,
                                          bytes_previstos=bytes_previstos, teto_bytes=situacao.max_bytes_conta, usados_em_disco=situacao.bytes_usados)

    except CotaEstourada as erro:
        raise HTTPException(413, f'você já tem {erro.ativos} corrida(s) em processamento, e o teto é {erro.teto}. Espere uma terminar e envie de novo.')

    except CotaDeBytesEstourada:
        raise HTTPException(413, 'este envio não cabe no espaço que sobra para a sua conta. Apague uma corrida antiga ou um banco próprio e tente de novo.')




async def gravar(arquivos, pasta_de_entrada, maximo_de_bytes):

    #* grava cada arquivo pelo nome-base, 1 MiB por vez, somando; passou do teto do lote -> 413. devolve quantos gravou

    total = 0

    for arquivo in arquivos:

        destino = pasta_de_entrada / Path(arquivo.filename or 'sem_nome').name

        with destino.open('wb') as saida:

            while pedaco := await arquivo.read(PEDACO):

                total += len(pedaco)

                if total > maximo_de_bytes:
                    raise HTTPException(413, f'lote acima de {maximo_de_bytes // MEGA} MB')

                saida.write(pedaco)

    return len(arquivos)




@router.post('/lotes')
async def criar_lote(request: Request, arquivos: list[UploadFile] = File(...), nome: str = Form(''), referencia: str = Form('')):

    instalacao = comum.instalacao_de(request)

    cfg = instalacao.cfg

    u = comum.exigir(request)

    instalacao.limitador.conferir('envio', request, u.email)

    referencia = (referencia or '').strip()

    conferir_referencia(instalacao, u.email, referencia)

    aceitos = [arquivo for arquivo in arquivos if Path(arquivo.filename or '').suffix.lower() in comum.EXT_ACEITAS]

    if not aceitos:
        raise HTTPException(400, 'nenhum arquivo .ab1/.abi/.scf no envio')

    if len(aceitos) > cfg.maximo_de_arquivos:
        raise HTTPException(413, f'{len(aceitos)} arquivos; o teto é {cfg.maximo_de_arquivos}')

    lote_id = abrir_lote(instalacao, u.email, nome.strip() or 'lote', len(aceitos), referencia, tamanho_declarado(request))

    pastas = cfg.pastas_do_lote(lote_id)

    pastas.entrada.mkdir(parents=True, exist_ok=True)

    try:
        gravados = await gravar(aceitos, pastas.entrada, cfg.maximo_de_bytes)

    except OSError as erro:

        shutil.rmtree(pastas.raiz, ignore_errors=True)

        instalacao.lotes.falhar(lote_id, f'falha ao gravar no disco: {erro.strerror}')

        REGISTRO.error('falha de disco ao receber o lote %s: %s', lote_id, erro)

        if erro.errno == errno.ENOSPC:
            raise HTTPException(507, SEM_ESPACO)

        raise HTTPException(500, f'o servidor não conseguiu gravar os arquivos ({erro.strerror}). Nada foi processado.')

    except Exception:

        shutil.rmtree(pastas.raiz, ignore_errors=True)

        instalacao.lotes.falhar(lote_id, 'falha ao receber os arquivos')

        raise

    instalacao.lotes.liberar_para_fila(lote_id, gravados)

    return RedirectResponse(f'/lotes/{lote_id}', status_code=303)




def main():

    for rota in router.routes:
        print(f'  {",".join(sorted(rota.methods)):9} {rota.path:22} {rota.endpoint.__name__}')

    print(f'  extensões aceitas: {sorted(comum.EXT_ACEITAS)}')


if __name__ == '__main__':  #* verifica se o arquivo esta sendo executado diretamente
    main()  #* chama a funcao main
