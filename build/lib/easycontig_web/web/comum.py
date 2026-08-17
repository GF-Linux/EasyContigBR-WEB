#? COMUM DA WEB — Decisão sobre o que toda rota usa 17/08/2026
#!
#! 1. Fica aqui o que TODAS as rotas precisam: os templates, a sessão, a casca da
#!    página e a leitura do lote.
#! 2. Existe para as rotas poderem morar em arquivos por assunto. Sem isto elas
#!    importariam do servidor, e o servidor importa elas — ciclo de importação.
#! 3. A configuração é lida A CADA USO (`cfg()`), e não guardada no import.
#!    Motivo medido: a suíte tem 28 pontos de `importlib.reload(main)` para dar
#!    a cada teste uma pasta própria. Recarregar o servidor NÃO recarrega este
#!    módulo — um `cfg` guardado aqui ficaria preso na pasta do primeiro teste, e
#!    as falhas apareceriam dependendo da ORDEM dos testes.
#! 4. Custo medido da leitura fresca: 15,9 µs por chamada, ou 1,6 ms de CPU por
#!    segundo a 100 req/s. Desprezível diante do que evita.
#! 5. Este módulo NÃO conhece rota nenhuma. Quem importa daqui é rota.

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from .. import configuracao as config
from ..contas import autenticacao as auth
from ..contas import cota_de_espaco as cotas
from ..contas import perfil_do_laboratorio as perfil
from ..dados import bancos_de_referencia as bancos
from ..dados import pedidos_entre_labs as pedidos
from ..processamento import fila_de_lotes as fila


#* A configuração, lida do ambiente NA HORA DO USO. Ver o item 3 do cabeçalho:
#* guardá-la no import prende o valor da primeira vez que o módulo carregou.
def cfg():
    return config.carregar()

TEMPLATES = Jinja2Templates(
    #! `.parent.parent`: este arquivo mora em `web/`, e os templates ficam na
    #! raiz do pacote. Com um `.parent` só, a pasta não existe.
    directory=str(Path(__file__).parent.parent / "templates"))


def _br(valor) -> str:
    """Número no formato daqui: 99,4 e não 99.4.

    Só na APRESENTAÇÃO. O CSV e o JSON continuam com ponto, porque são lidos por
    programa (e o CSV vai para planilha e para o R). Misturar os dois na mesma
    tela era o defeito: a página escrevia "25,1 MB" com vírgula e "96.500" com
    ponto, e `96.500` em português lê-se noventa e seis mil e quinhentos.
    """
    texto = "" if valor is None else str(valor)
    return texto.replace(".", ",") if texto else ""


TEMPLATES.env.filters["br"] = _br



def _marcar_resposta(resposta, nonce: str) -> None:
    """Carimba a política de segurança numa resposta. Fica em função à parte
    porque a página de 500 precisa dos MESMOS cabeçalhos e **não passa por
    middleware nenhum** — ver `_erro_nao_previsto`."""
    h = resposta.headers
    h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "same-origin"
    h["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'")
    if os.environ.get("EASYCONTIG_HTTPS_ONLY", "0") == "1":
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"



EXT_ACEITAS = {".ab1", ".abi", ".scf"}



# --------------------------------------------------------------------- sessão
def _u(request: Request) -> auth.Usuario | None:
    return auth.usuario_da_sessao(request)


def _exigir(request: Request) -> auth.Usuario:
    u = _u(request)
    if not u:
        raise HTTPException(status_code=401, detail="entre para continuar")
    return u


# ------------------------------------------------------- recado de uma vez só
# ⚠️ A MENSAGEM DE ERRO NÃO VIAJA MAIS NA URL (achado de 2026-08-11).
#
# O padrão antigo era `RedirectResponse("/bancos?erro=" + quote(texto))`, e a
# página imprimia `{{ erro }}` direto do parâmetro. Como o leitor não conferia
# nada, o texto exibido não era o que o app escreveu — era o que estivesse na
# URL. Qualquer um monta `/labs?erro=sua+sessão+expirou,+reenvie+suas+
# credenciais+em...` e a frase sai dentro da caixa de erro do próprio site, com
# a autoridade visual dele. Não é XSS (o Jinja escapa, e foi conferido), é
# falsificação de conteúdo — que é o que uma isca precisa.
#
# Podia ter virado um catálogo de códigos (`?erro=cota_bancos`), mas isso
# obrigaria a jogar fora o detalhe das falhas de montagem, que é justamente o
# que ajuda quem está tentando entender por que o banco não subiu. A sessão
# resolve melhor: ela é um cookie ASSINADO pelo servidor, então o cliente não
# consegue escrever nela, e o texto continua sendo exatamente o que o app pôs.
#
# "De uma vez só" é a outra metade: o recado é REMOVIDO ao ser lido, senão ele
# reaparece em todo recarregamento da página e a pessoa fica olhando um erro que
# já resolveu.
_RECADO = "recado"


def _por_recado(request: Request, texto: str) -> None:
    request.session[_RECADO] = (texto or "")[:300]


def _pegar_recado(request: Request) -> str:
    return request.session.pop(_RECADO, "")


def _referencias(u: auth.Usuario) -> list[dict]:
    """As referências que esta conta pode escolher para identificar.

    O curado vem primeiro e é o padrão: é o que produziu todos os resultados
    validados até aqui (ADR 0037). Os demais só aparecem depois de montados —
    oferecer o que não está pronto seria oferecer um erro.
    """
    itens = [{"id": "curado", "nome": "Banco curado do laboratório",
              "detalhe": "18S + 16S · RAIC 2026 · o que validou os resultados até aqui",
              "grupo": "Padrão"}]
    for c in bancos.POR_ID.values():
        if bancos.existe(cfg().data_dir, c.id):
            e = bancos.estado(cfg().data_dir, c.id)
            itens.append({"id": c.id, "nome": f"{c.nome} · {c.marcador}",
                          "detalhe": f"{e.get('sequencias')} sequências do GenBank",
                          "grupo": c.grupo})
    for b in bancos.meus_bancos(cfg().data_dir, u.email):
        itens.append({"id": b["id"], "nome": b["apelido"],
                      "detalhe": f"{b.get('sequencias')} sequências suas",
                      "grupo": "Meus bancos"})
    return itens


def _casca(u: auth.Usuario) -> dict:
    """O que a barra lateral precisa, em qualquer página.

    A lista de corridas mora na lateral porque é o que a pessoa volta para
    consultar — o uso real é par a par (ADR 0051), e o que se acumula é o
    histórico. Fica curta de propósito: a lateral é atalho, não arquivo.

    `perfil_lateral` traz nome e foto porque a lateral mostrava a INICIAL do
    e-mail mesmo depois de a pessoa ter enviado a foto — que aparecia só na
    página de perfil. Quem troca a foto e continua vendo "GU" conclui, com
    razão, que o envio não funcionou.
    """
    p = perfil.pegar(cfg().sqlite_path, u.email) or {}
    return {
        "usuario": u,
        "corridas": fila.listar(cfg().sqlite_path, dono=u.email, limite=25),
        "perfil_lateral": {"nome": p.get("nome") or "", "foto": p.get("foto") or ""},
        # O contador de pedidos esperando ESTA conta. Vai na casca, e não só na
        # página de pedidos, porque este app **não manda e-mail**: se o número
        # não estiver na lateral de toda página, um pedido pode ficar meses sem
        # ninguém saber que chegou. É um COUNT com índice próprio
        # (`idx_pedidos_para`), medido em microssegundos.
        "pedidos_pendentes": pedidos.contar_pendentes(cfg().sqlite_path, u.email),
    }


def _lote_do_usuario(lote_id: str, u: auth.Usuario) -> dict:
    lote = fila.pegar(cfg().sqlite_path, lote_id)
    if not lote:
        raise HTTPException(status_code=404, detail="lote não encontrado")
    # Dono confere mesmo com o id sendo aleatório: o link é compartilhável por
    # descuido, e um .ab1 não publicado do laboratório não pode vazar por URL
    # adivinhada nem por link colado no grupo errado.
    if lote["dono"] != u.email:
        raise HTTPException(status_code=404, detail="lote não encontrado")
    return lote




#* Toma uma das vagas de trabalho pesado (`tracy`, `blastn`), ou recusa NA HORA
#* com 503. Não espera por vaga: esperar seguraria a thread, que é exatamente o
#* que se quer liberar.
#! O semáforo mora no `servidor_web`, e é lido aqui por importação TARDIA. Não é
#!   capricho: os testes ajustam `EASYCONTIG_MAX_PESADAS` e recarregam o servidor,
#!   e só assim esta função enxerga o semáforo NOVO em vez do antigo.
@contextmanager
def _vaga_pesada():
    from .. import servidor_web
    if not servidor_web._VAGAS_PESADAS.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="o servidor está ocupado montando outras amostras; tente de novo",
            headers={"Retry-After": "5"})
    try:
        yield
    finally:
        servidor_web._VAGAS_PESADAS.release()
