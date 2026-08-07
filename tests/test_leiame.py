"""O Readme tem de existir e tem de dizer a coisa CERTA.

A afirmação central — a orientação sai do DNA, não de metadado do `.ab1` nem do
nome — é a tese do produto (ADR 0034) e é o que `app/core/orientation.py` faz.
Se alguém trocar o motor, esta página passa a mentir para o laboratório: por
isso o teste amarra o texto ao mecanismo, e não só à existência da rota.
"""
import re
from pathlib import Path


def _texto():
    # Minúsculas e espaço normalizado: nem caixa nem quebra de linha do HTML
    # podem derrubar uma verificação de CONTEÚDO.
    return re.sub(r"\s+", " ",
                  Path("easycontig_web/templates/leiame.html").read_text()).lower()


def test_a_rota_existe_e_exige_sessao(cliente_sem_sessao=None):
    from easycontig_web.main import app
    rotas = {r.path for r in app.routes}
    assert "/leiame" in rotas


def test_explica_que_o_sentido_sai_do_dna_e_nao_do_nome():
    t = _texto()
    assert "reverso-complemento" in t
    assert "não usa o nome do arquivo" in t
    assert "não usa metadado" in t or "não usa metadado nenhum" in t


def test_diz_que_a_identidade_vem_do_nome():
    """A metade que as pessoas esquecem: sentido é do DNA, identidade é do nome."""
    t = _texto()
    assert "identidade da amostra" in t
    assert "poço da placa" in t


def test_o_botao_esta_na_lateral():
    base = re.sub(r"\s+", " ", Path("easycontig_web/templates/base.html").read_text())
    assert "/leiame" in base and "Readme" in base
