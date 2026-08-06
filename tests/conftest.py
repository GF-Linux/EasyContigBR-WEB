"""
Preparo comum dos testes.

O limitador de taxa guarda os instantes num registro de PROCESSO, e a suíte
inteira roda no mesmo processo: sem zerar entre casos, o 40º teste esbarra no
teto que os 39 anteriores consumiram, e a falha aparece longe da causa.
"""
import pytest


@pytest.fixture(autouse=True)
def _zerar_limites():
    from easycontig_web import limites
    limites.limpar()
    yield
    limites.limpar()
