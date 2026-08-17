"""
Pedidos de amostra entre laboratórios — o que a ADR 0052 apontava e o `/labs`
ainda não fazia: um laboratório acha quem trabalha com o quê e **pede uma
sequência**.

Pedido do autor (2026-08-10): botão no cartão do `/labs`; o pedido nomeia itens
do portfólio DECLARADO do outro mais um texto livre; quem recebe **aceita ou
recusa e justifica**; e não é bate-papo.

O que estes testes travam:

  • **A fronteira do e-mail**, que é a parte que pode dar errado em silêncio.
    Pendente e recusado não mostram endereço nenhum; o ACEITE, e só ele, libera
    os dois lados. É a mesma trava do achado L1 (2026-08-08), agora com uma
    porta declarada e um consentimento explícito.
  • **O alvo do formulário não é o e-mail nem o hash dele** — é chave sorteada.
  • O ciclo é final: respondido não se responde de novo, e ninguém responde
    pedido que não é seu.
  • `itens` é filtrado contra o portfólio do alvo, e não gravado como veio.
  • Justificativa obrigatória nos dois lados.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from easycontig_web.dados import pedidos_entre_labs as pedidos
from easycontig_web.contas import perfil_do_laboratorio as perfil

# ⚠️ O local-part NÃO é o nome de exibição de propósito. Com `maristela@…` e o
# cartão dizendo "Maristela Peckle", procurar a cadeia "maristela" no HTML acusa
# vazamento onde há só o nome que a pessoa declarou — e o teste passa a medir a
# coisa errada. Com `m.peckle@…`, "m.peckle" no HTML **só pode** ter vindo do
# e-mail. É também o formato real dos endereços institucionais, que é o que
# torna o achado L1 possível: endereço adivinhável a partir do nome.
GUSTAVO = "g.freitas@ufrrj.br"
MARISTELA = "m.peckle@ufrrj.br"
LOCAL_GUSTAVO, LOCAL_MARISTELA = "g.freitas", "m.peckle"


@pytest.fixture()
def app_teste(tmp_path, monkeypatch):
    monkeypatch.setenv("EASYCONTIG_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.setenv("EASYCONTIG_AUTH", "dev")
    monkeypatch.setenv("EASYCONTIG_DOMINIO", "")
    monkeypatch.setenv("EASYCONTIG_SECRET_KEY", "teste")
    monkeypatch.delenv("EASYCONTIG_PRODUCAO", raising=False)
    from easycontig_web import servidor_web as main
    importlib.reload(main)
    return main


@pytest.fixture()
def cliente(app_teste):
    """Gustavo entrou. Maristela tem perfil com portfólio declarado."""
    c = TestClient(app_teste.app)
    c.post("/entrar", data={"email": GUSTAVO}, follow_redirects=False)
    perfil.salvar(app_teste.cfg.sqlite_path, MARISTELA, nome="Maristela Peckle",
                  laboratorio="LHV", instituicao="UFRRJ",
                  especies="Anaplasma platys, Ehrlichia canis",
                  marcadores="16S rRNA")
    return c


def _outra(app_teste, email=MARISTELA):
    c = TestClient(app_teste.app)
    c.post("/entrar", data={"email": email}, follow_redirects=False)
    return c


def _chave(app_teste, email):
    return perfil.chave_de(app_teste.cfg.sqlite_path, email)


def _pedir(cliente, chave, **campos):
    dados = {"motivo": "Preciso de controle positivo para padronizar o 16S."}
    dados.update(campos)
    return cliente.post(f"/labs/{chave}/pedido", data=dados,
                        follow_redirects=False)


# ───────────────────────────────────────────── porta fechada
def test_pedidos_exige_sessao(app_teste):
    anonimo = TestClient(app_teste.app)
    r = anonimo.get("/pedidos", headers={"accept": "application/json"})
    assert r.status_code == 401


def test_pedir_sem_sessao_nao_passa(app_teste, cliente):
    chave = _chave(app_teste, MARISTELA)
    anonimo = TestClient(app_teste.app)
    r = anonimo.post(f"/labs/{chave}/pedido", data={"motivo": "oi"},
                     headers={"accept": "application/json"},
                     follow_redirects=False)
    assert r.status_code == 401


# ───────────────────────── o alvo do formulário não pode ser o e-mail
def test_o_formulario_do_labs_nao_leva_email_nem_hash_do_email(app_teste, cliente):
    """A razão de a chave existir.

    O botão precisa dizer PARA QUEM é o pedido, e esse identificador vai para o
    HTML de uma página que todo mundo abre.

      • e-mail está fora pelo achado L1: com o domínio anunciado na entrada,
        qualquer pedaço do endereço alheio remonta o endereço inteiro;
      • `sha256(e-mail)` também está fora, e este é o ponto menos óbvio —
        e-mail institucional é adivinhável (`nome.sobrenome@ufrrj.br`), então
        quem tem a lista de hashes confirma offline quais endereços têm conta
        aqui. Hash de valor de baixa entropia não esconde o valor.
    """
    import hashlib
    html = cliente.get("/labs").text
    corpo = html.split('class="trabalho"', 1)[1]

    assert LOCAL_MARISTELA not in corpo.lower(), "o e-mail do alvo foi para o HTML"
    for candidato in (MARISTELA, MARISTELA.strip().lower()):
        h = hashlib.sha256(candidato.encode()).hexdigest()
        assert h[:16] not in corpo, "o alvo do pedido é o hash do e-mail"

    chave = _chave(app_teste, MARISTELA)
    assert f'action="/labs/{chave}/pedido"' in corpo, "não há botão de pedir"


def test_a_chave_nao_muda_a_cada_login(app_teste, cliente):
    """Girar a chave faria falhar, sem motivo visível, um formulário já aberto
    numa aba."""
    antes = _chave(app_teste, GUSTAVO)
    cliente.post("/entrar", data={"email": GUSTAVO}, follow_redirects=False)
    assert _chave(app_teste, GUSTAVO) == antes


def test_chave_desconhecida_nao_vira_oraculo(app_teste, cliente):
    """404 aqui responderia 'esta chave não é de ninguém', e a rota viraria um
    modo de varrer o espaço de chaves. Responde como pedido inválido."""
    r = _pedir(cliente, "chaveQueNaoExiste", outro="qualquer coisa")
    assert r.status_code == 303
    # O texto do erro saiu da URL em 2026-08-11 (era falsificável por link) e
    # passou a viajar na sessão assinada. O que se confere é o destino e, na
    # página, a mensagem que o APP escreveu.
    assert r.headers["location"] == "/labs"
    assert "não foi possível enviar o pedido" in cliente.get("/labs").text


def test_chave_vazia_nao_casa_com_quem_ainda_nao_tem(app_teste, cliente):
    """A coluna nasce `''` e é preenchida depois — logo, existir linha com
    `chave=''` é estado normal durante uma atualização. Sem a guarda, um POST
    com o campo em branco escolheria a primeira dessas linhas como alvo."""
    con_path = app_teste.cfg.sqlite_path
    from easycontig_web.processamento.fila_de_lotes import conectar
    with conectar(con_path) as con:
        con.execute("INSERT INTO perfis (email, nome, chave) VALUES (?,?,'')",
                    ("fantasma@ufrrj.br", "Fantasma"))
    assert perfil.email_da_chave(con_path, "") == ""


# ───────────────────────────────────── A FRONTEIRA DO E-MAIL (o essencial)
def test_pendente_nao_mostra_email_de_nenhum_dos_lados(app_teste, cliente):
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])

    # quem mandou
    meu = cliente.get("/pedidos").text.split('class="trabalho"', 1)[1]
    assert LOCAL_MARISTELA not in meu.lower()
    assert "Maristela Peckle" in meu, "devia mostrar o nome declarado"

    # quem recebeu
    dela = _outra(app_teste).get("/pedidos").text.split('class="trabalho"', 1)[1]
    assert LOCAL_GUSTAVO not in dela.lower()


def test_recusado_continua_sem_mostrar_email(app_teste, cliente):
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    m = _outra(app_teste)
    pid = pedidos.recebidos(app_teste.cfg.sqlite_path, MARISTELA)[0]["id"]
    m.post(f"/pedidos/{pid}/responder",
           data={"acao": "recusar", "resposta": "Só temos DNA extraído."},
           follow_redirects=False)

    meu = cliente.get("/pedidos").text.split('class="trabalho"', 1)[1]
    assert "Só temos DNA extraído." in meu, "a justificativa não chegou"
    assert LOCAL_MARISTELA not in meu.lower(), "recusar liberou o e-mail"
    dela = m.get("/pedidos").text.split('class="trabalho"', 1)[1]
    assert LOCAL_GUSTAVO not in dela.lower(), "recusar liberou o e-mail"


def test_o_aceite_libera_o_email_dos_DOIS_lados(app_teste, cliente):
    """A única travessia de e-mail entre contas neste site, e ela é deliberada:
    aceitar É o consentimento. Sem isto o recurso não termina — a amostra viaja
    de correio, e alguém tem de saber para quem escrever."""
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    m = _outra(app_teste)
    pid = pedidos.recebidos(app_teste.cfg.sqlite_path, MARISTELA)[0]["id"]
    r = m.post(f"/pedidos/{pid}/responder",
               data={"acao": "aceitar", "resposta": "Temos alíquota de 2021."},
               follow_redirects=False)
    assert r.status_code == 303

    assert MARISTELA in cliente.get("/pedidos").text, "quem pediu não vê o contato"
    assert GUSTAVO in m.get("/pedidos").text, "quem aceitou não vê o contato"


def test_o_email_liberado_nao_vaza_para_o_labs_nem_para_terceiro(app_teste, cliente):
    """Aceitar abre o endereço NAQUELE pedido, e não no site.

    É a trava que impede o recurso de virar um caminho lateral para o que a ADR
    0066 fechou: o diretório continua sem e-mail de terceiro, e uma conta que
    não é parte do pedido não vê nada.
    """
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    m = _outra(app_teste)
    pid = pedidos.recebidos(app_teste.cfg.sqlite_path, MARISTELA)[0]["id"]
    m.post(f"/pedidos/{pid}/responder",
           data={"acao": "aceitar", "resposta": "Pode vir buscar."},
           follow_redirects=False)

    labs = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert LOCAL_MARISTELA not in labs.lower(), (
        "o aceite vazou o e-mail para o diretório)".replace(")", ""))

    terceiro = _outra(app_teste, "zulmira@ufrrj.br")
    caixa = terceiro.get("/pedidos").text
    assert GUSTAVO not in caixa and MARISTELA not in caixa, (
        "uma conta de fora do pedido viu os endereços)".replace(")", ""))
    assert pedidos.pegar(app_teste.cfg.sqlite_path, pid, "zulmira@ufrrj.br") is None


# ───────────────────────────────────────── o ciclo é final
def test_quem_nao_recebeu_nao_responde(app_teste, cliente):
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    pid = pedidos.recebidos(app_teste.cfg.sqlite_path, MARISTELA)[0]["id"]
    # o próprio autor do pedido tentando aceitá-lo
    r = cliente.post(f"/pedidos/{pid}/responder",
                     data={"acao": "aceitar", "resposta": "eu aceito o meu"},
                     headers={"accept": "application/json"},
                     follow_redirects=False)
    assert r.status_code == 404
    assert pedidos.pegar(app_teste.cfg.sqlite_path, pid, GUSTAVO)["estado"] == "pendente"


def test_respondido_nao_se_responde_de_novo(app_teste, cliente):
    """A condição `estado=pendente` está no UPDATE, não num SELECT antes: é o
    que impede dois cliques (ou duas abas) de responderem duas vezes."""
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    banco = app_teste.cfg.sqlite_path
    pid = pedidos.recebidos(banco, MARISTELA)[0]["id"]

    assert pedidos.responder(banco, pid, MARISTELA, aceitar=False,
                             resposta="não temos") is not None
    assert pedidos.responder(banco, pid, MARISTELA, aceitar=True,
                             resposta="mudei de ideia") is None
    assert pedidos.pegar(banco, pid, MARISTELA)["estado"] == "recusado"


def test_cancelar_e_so_de_quem_pediu_e_so_enquanto_pendente(app_teste, cliente):
    banco = app_teste.cfg.sqlite_path
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    pid = pedidos.recebidos(banco, MARISTELA)[0]["id"]

    assert pedidos.cancelar(banco, pid, MARISTELA) is False, "quem recebeu cancelou"
    assert pedidos.cancelar(banco, pid, GUSTAVO) is True
    assert pedidos.cancelar(banco, pid, GUSTAVO) is False, "cancelou duas vezes"
    # some da caixa de quem recebeu: quem pediu desistiu antes de ela olhar
    assert pedidos.recebidos(banco, MARISTELA) == []
    assert pedidos.enviados(banco, GUSTAVO)[0]["estado"] == "cancelado"


def test_pedir_de_novo_e_permitido_depois_da_recusa(app_teste, cliente):
    """Decisão do autor: estado final é final, mas um pedido novo sobre o mesmo
    assunto é outro pedido, com histórico próprio. Recusa não é banimento."""
    banco = app_teste.cfg.sqlite_path
    chave = _chave(app_teste, MARISTELA)
    _pedir(cliente, chave, itens=["Anaplasma platys"])
    pid = pedidos.recebidos(banco, MARISTELA)[0]["id"]
    pedidos.responder(banco, pid, MARISTELA, aceitar=False, resposta="agora não")

    r = _pedir(cliente, chave, itens=["Anaplasma platys"],
               motivo="Voltei porque conseguimos a autorização do SisGen.")
    assert r.headers["location"] == "/pedidos?enviado=1"
    assert len(pedidos.enviados(banco, GUSTAVO)) == 2


# ─────────────────────────────── o que se pode escrever, e quanto
def test_itens_e_filtrado_contra_o_portfolio_declarado(app_teste, cliente):
    """As caixas de seleção saem do portfólio que o servidor desenhou — mas o
    formulário é do navegador, e um POST feito à mão manda o que quiser. Sem o
    filtro, `itens` viraria um campo de texto livre extra, sem rótulo e sem teto
    declarado, atravessando de uma conta para outra. O texto livre existe e tem
    nome: é o `outro`."""
    banco = app_teste.cfg.sqlite_path
    _pedir(cliente, _chave(app_teste, MARISTELA),
           itens=["Anaplasma platys", "Trypanosoma cruzi <script>", "16S rRNA"])
    p = pedidos.recebidos(banco, MARISTELA)[0]
    assert p["itens"] == ["Anaplasma platys", "16S rRNA"]


def test_pedido_sem_motivo_nao_entra(app_teste, cliente):
    banco = app_teste.cfg.sqlite_path
    r = _pedir(cliente, _chave(app_teste, MARISTELA),
               itens=["Anaplasma platys"], motivo="   ")
    assert r.status_code == 303 and r.headers["location"] == "/labs"
    assert "diga para que serve a amostra" in cliente.get("/labs").text
    assert pedidos.recebidos(banco, MARISTELA) == []


def test_pedido_sem_item_nem_texto_livre_nao_entra(app_teste, cliente):
    banco = app_teste.cfg.sqlite_path
    _pedir(cliente, _chave(app_teste, MARISTELA))
    assert pedidos.recebidos(banco, MARISTELA) == []


def test_resposta_sem_justificativa_nao_passa(app_teste, cliente):
    """"Recusar sem dizer por quê" é o comportamento que faria o recurso morrer
    de desuso — quem pede fica sem saber com o que contar."""
    banco = app_teste.cfg.sqlite_path
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    pid = pedidos.recebidos(banco, MARISTELA)[0]["id"]
    with pytest.raises(pedidos.NaoPode):
        pedidos.responder(banco, pid, MARISTELA, aceitar=False, resposta="  ")
    assert pedidos.pegar(banco, pid, MARISTELA)["estado"] == "pendente"


def test_nao_da_para_pedir_a_si_mesmo(app_teste, cliente):
    r = _pedir(cliente, _chave(app_teste, GUSTAVO), outro="minha própria amostra")
    assert "um pedido é para outro laboratório" in cliente.get("/labs").text
    assert pedidos.recebidos(app_teste.cfg.sqlite_path, GUSTAVO) == []


def test_o_cartao_do_proprio_perfil_nao_tem_botao_de_pedir(app_teste, cliente):
    chave = _chave(app_teste, GUSTAVO)
    corpo = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert f'action="/labs/{chave}/pedido"' not in corpo


def test_teto_de_pendentes_com_o_mesmo_lab(app_teste, cliente):
    """O teto de taxa conta por hora e esquece; este conta o que está VIVO na
    caixa de quem recebe. Sem ele, 30 pedidos por hora, hora após hora, entopem
    a caixa de um lab só, e quem recebe não tem como fazer parar a não ser
    respondendo um por um."""
    banco = app_teste.cfg.sqlite_path
    chave = _chave(app_teste, MARISTELA)
    for i in range(pedidos.MAX_PENDENTES_POR_PAR):
        _pedir(cliente, chave, outro=f"amostra {i}")
    r = _pedir(cliente, chave, outro="mais uma")
    assert "aguarde a resposta antes de mandar outro" in cliente.get("/labs").text
    assert len(pedidos.recebidos(banco, MARISTELA)) == pedidos.MAX_PENDENTES_POR_PAR

    # responder libera a vaga — insistir exige que o outro responda antes
    pid = pedidos.recebidos(banco, MARISTELA)[0]["id"]
    pedidos.responder(banco, pid, MARISTELA, aceitar=False, resposta="não")
    assert _pedir(cliente, chave, outro="agora vai"
                  ).headers["location"] == "/pedidos?enviado=1"


# ─────────────────────────────── o aviso na tela e o contador na lateral
def test_a_tela_avisa_do_email_antes_de_o_botao_ser_apertado(app_teste, cliente):
    """A troca de endereço é a única deste site: quem manda tem de saber disso
    na hora de mandar, não numa página seguinte."""
    corpo = cliente.get("/labs").text.split('class="trabalho"', 1)[1]
    assert "se ele aceitar" in corpo
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])
    caixa = _outra(app_teste).get("/pedidos").text
    assert "seu e-mail passa a aparecer" in caixa


def test_o_contador_da_lateral_conta_so_o_que_espera_esta_conta(app_teste, cliente):
    """O app não manda e-mail: se o número não estiver na lateral de toda
    página, um pedido pode ficar meses sem ninguém saber que chegou."""
    banco = app_teste.cfg.sqlite_path
    _pedir(cliente, _chave(app_teste, MARISTELA), itens=["Anaplasma platys"])

    # quem MANDOU não tem tarefa nenhuma pendente
    assert pedidos.contar_pendentes(banco, GUSTAVO) == 0
    assert '<span class="cont">' not in cliente.get("/").text

    assert pedidos.contar_pendentes(banco, MARISTELA) == 1
    assert '<span class="cont">1</span>' in _outra(app_teste).get("/").text


def test_o_link_de_pedidos_esta_na_lateral(cliente):
    assert 'href="/pedidos"' in cliente.get("/").text
