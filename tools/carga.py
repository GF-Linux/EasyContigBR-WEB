#!/usr/bin/env python3
"""
carga.py — quanta gente e quantas corridas esta máquina aguenta.

POR QUE EXISTE: em 2026-08-06 mediu-se carga num arnês ad hoc, dentro de uma
worktree que depois sumiu. Os números (inicial 508 → 112 ms) não podem ser
repetidos na máquina nova nem comparados com ela — e é exatamente a comparação
que escolhe a VPS. Este arquivo é aquele teste, commitado.

SEM DEPENDÊNCIA FORA DA BIBLIOTECA PADRÃO, de propósito: ele precisa rodar numa
VPS recém-instalada, antes de existir venv, e dentro do contêiner se preciso.

O QUE MEDE, nesta ordem:

  1. LEITURA — rampa de leitores simultâneos nas quatro páginas quentes.
     Responde "quantas pessoas ao mesmo tempo antes de a tela ficar lenta".
  2. ENVIO — corridas reais (80 `.ab1`, 26 MB) de contas distintas ao mesmo
     tempo, do POST até o lote ficar `pronto`. Responde "quantas corridas por
     hora esta máquina monta", que é o número que escolhe o número de vCPU.

⚠️ RECUSA 429/413 NÃO É FALHA. A cota e o teto de taxa existem, estão certos e
   foram medidos. Contá-los como erro faria o arnês reprovar uma máquina por ela
   estar se defendendo. Saem em coluna própria, e o relatório diz quantas foram.

⚠️ POR PADRÃO O ALVO DEVE SUBIR COM OS TETOS DESLIGADOS
   (`EASYCONTIG_LIM_LEITURA=0`, `EASYCONTIG_LIM_ENVIO=0`) — é o que o
   `carga.sh` faz. Com eles ligados, uma rampa de 50 leitores mede o
   limitador, não a máquina: são 300 leituras por minuto POR CONTA, e 50
   leitores a 100 ms estouram isso em meio minuto. O relatório avisa quando
   detecta teto ligado, porque um número medido contra o limitador parece
   apenas "a máquina é ruim".

⚠️ NUNCA APONTE PARA PRODUÇÃO. O arnês entra por `EASYCONTIG_AUTH=dev`, cria
   contas e escreve corridas. Para caracterizar a máquina de produção, suba uma
   instância isolada NELA (é o que o `carga.sh` faz) — o que se quer medir é o
   hardware, não o banco de dados de quem usa.

USO:
    tools/carga.sh                       # sobe instância isolada e mede (o caminho normal)
    python3 tools/carga.py --alvo http://127.0.0.1:8123 --amostras <pasta>

O relatório sai em texto na tela e em JSON (`--json arquivo`), para comparar
duas máquinas depois.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from pathlib import Path

EXT_AB1 = {".ab1", ".abi", ".scf"}

# A rampa. Sobe até achar o joelho; para sozinha quando a máquina começa a
# recusar ou a passar do teto de paciência.
RAMPA_PADRAO = [1, 2, 5, 10, 20, 50]

# Acima disto a página deixou de ser "instantânea" para quem usa. Não é um
# limite do software — é o critério de parada da rampa, para o arnês não passar
# dez minutos medindo uma máquina que já respondeu a pergunta.
TETO_P95_MS = 3000.0


# --------------------------------------------------------------------------
# Cliente HTTP
# --------------------------------------------------------------------------

class _NaoSegue(urllib.request.HTTPRedirectHandler):
    """Não seguir redirecionamento é requisito de medição, não preferência.

    Sem isto, uma sessão que caiu faz `GET /` responder 303 → `/entrar`, o
    urllib segue, devolve 200 e o arnês cronometra alegremente a PÁGINA DE
    LOGIN achando que é a inicial. O número sairia ótimo e não significaria
    nada. Aqui um 3xx vira erro visível.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Medida:
    ms: float
    codigo: int          # 0 = não houve resposta (timeout, conexão recusada)
    bytes: int = 0
    erro: str = ""
    # O caminho feliz do POST /lotes é **303**, não 200. Sem isto o aceite do
    # upload saía como "0 ok" e o p50 aparecia como "—" num envio que deu certo.
    bons: tuple[int, ...] = (200, 201, 204)

    @property
    def ok(self) -> bool:
        return self.codigo in self.bons

    @property
    def recusa_legitima(self) -> bool:
        """429 (teto de taxa) e 413 (cota) são o software funcionando."""
        return self.codigo in (429, 413)


class Sessao:
    """Uma conta. Cookie próprio, como um navegador de uma pessoa."""

    def __init__(self, alvo: str, email: str, timeout: float = 120.0):
        self.alvo = alvo.rstrip("/")
        self.email = email
        self.timeout = timeout
        self.jar = CookieJar()
        biscoitos = urllib.request.HTTPCookieProcessor(self.jar)
        self.segue = urllib.request.build_opener(biscoitos)
        self.direto = urllib.request.build_opener(biscoitos, _NaoSegue())

    def entrar(self) -> bool:
        corpo = urllib.parse.urlencode(
            {"email": self.email, "proximo": "/"}).encode()
        req = urllib.request.Request(
            self.alvo + "/entrar", data=corpo,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.segue.open(req, timeout=self.timeout) as r:
                r.read()
        except urllib.error.HTTPError as e:
            e.read()
            return False
        return any(c.name == "session" for c in self.jar)

    def pegar(self, caminho: str) -> Medida:
        t0 = time.perf_counter()
        try:
            with self.direto.open(self.alvo + caminho, timeout=self.timeout) as r:
                dados = r.read()
                return Medida((time.perf_counter() - t0) * 1000, r.status, len(dados))
        except urllib.error.HTTPError as e:
            try:
                e.read()
            except Exception:                                   # noqa: BLE001
                pass
            return Medida((time.perf_counter() - t0) * 1000, e.code)
        except Exception as e:                                  # noqa: BLE001
            return Medida((time.perf_counter() - t0) * 1000, 0, erro=repr(e))

    def enviar(self, corpo: bytes, tipo: str) -> tuple[Medida, str]:
        """POST /lotes. Devolve a medida e o id do lote (vazio se não entrou)."""
        req = urllib.request.Request(
            self.alvo + "/lotes", data=corpo, headers={"Content-Type": tipo})
        t0 = time.perf_counter()
        try:
            with self.direto.open(req, timeout=self.timeout) as r:
                r.read()
                # 200 aqui seria estranho: o caminho feliz é 303.
                return Medida((time.perf_counter() - t0) * 1000, r.status,
                              bons=(303,)), ""
        except urllib.error.HTTPError as e:
            try:
                e.read()
            except Exception:                                   # noqa: BLE001
                pass
            ms = (time.perf_counter() - t0) * 1000
            if e.code == 303:
                destino = e.headers.get("Location", "")
                return Medida(ms, 303, bons=(303,)), destino.rsplit("/", 1)[-1]
            return Medida(ms, e.code, bons=(303,)), ""
        except Exception as e:                                  # noqa: BLE001
            return Medida((time.perf_counter() - t0) * 1000, 0,
                          erro=repr(e), bons=(303,)), ""

    def estado_do_lote(self, lote_id: str) -> str:
        """UMA requisição, não duas: consultar o estado é o laço de espera da
        fase 2, e dobrá-lo põe carga do arnês em cima da carga medida.

        ⚠️ O campo é `status`, NÃO `estado`. Escrevi `estado` na primeira versão
        e o arnês esperou 300 s por um lote que ficara pronto em 11,7 s, para
        então dizer "não terminou; há trabalhador rodando?" — a mentira mais
        cara que um arnês pode contar, porque acusa a MÁQUINA de lenta. Quem
        desmentiu foi o log do trabalhador, não o arnês. Por isso o `?` nunca é
        silencioso: um campo ausente devolve o dicionário inteiro no relatório.
        """
        try:
            with self.direto.open(self.alvo + f"/api/lotes/{lote_id}",
                                  timeout=self.timeout) as r:
                d = json.loads(r.read())
                if "status" not in d:
                    return "?sem-campo-status:" + ",".join(sorted(d))[:120]
                return d["status"]
        except urllib.error.HTTPError as e:
            try:
                e.read()
            except Exception:                                   # noqa: BLE001
                pass
            return f"?{e.code}"
        except Exception:                                       # noqa: BLE001
            return "?"


# --------------------------------------------------------------------------
# Corpo do envio
# --------------------------------------------------------------------------

def montar_multipart(arquivos: list[Path], nome: str,
                     referencia: str) -> tuple[bytes, str]:
    """O corpo é montado UMA VEZ e reusado por todas as contas.

    São 26 MB; remontar por conta faria o arnês medir a si mesmo em vez de medir
    o servidor, e com 8 simultâneos seriam 208 MB de RAM do lado errado.
    """
    limite = "----carga" + os.urandom(8).hex()
    partes: list[bytes] = []
    for campo, valor in (("nome", nome), ("referencia", referencia)):
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{campo}"'
            f"\r\n\r\n{valor}\r\n".encode())
    for caminho in arquivos:
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="arquivos"; '
            f'filename="{caminho.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode())
        partes.append(caminho.read_bytes())
        partes.append(b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


# --------------------------------------------------------------------------
# Estatística
# --------------------------------------------------------------------------

def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return float("nan")
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p
    baixo, alto = int(k), min(int(k) + 1, len(ordenados) - 1)
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (k - baixo)


@dataclass
class Resumo:
    rotulo: str
    medidas: list[Medida] = field(default_factory=list)

    def linha(self) -> dict:
        bons = [m.ms for m in self.medidas if m.ok]
        recusas = sum(1 for m in self.medidas if m.recusa_legitima)
        falhas = [m for m in self.medidas
                  if not m.ok and not m.recusa_legitima]
        return {
            "rotulo": self.rotulo,
            "n": len(self.medidas),
            "ok": len(bons),
            "p50": round(percentil(bons, 0.50), 1) if bons else None,
            "p95": round(percentil(bons, 0.95), 1) if bons else None,
            "max": round(max(bons), 1) if bons else None,
            "recusas": recusas,
            "falhas": len(falhas),
            "codigos_de_falha": sorted({m.codigo for m in falhas}),
            "erros": sorted({m.erro for m in falhas if m.erro})[:3],
        }


# --------------------------------------------------------------------------
# Identificação da máquina — sem isto os números não são comparáveis
# --------------------------------------------------------------------------

def identificar_maquina() -> dict:
    info = {
        "sistema": platform.platform(),
        "python": platform.python_version(),
        "nucleos": os.cpu_count(),
    }
    try:
        for linha in Path("/proc/cpuinfo").read_text().splitlines():
            if linha.startswith("model name"):
                info["cpu"] = linha.split(":", 1)[1].strip()
                break
    except Exception:                                           # noqa: BLE001
        pass
    try:
        for linha in Path("/proc/meminfo").read_text().splitlines():
            if linha.startswith("MemTotal"):
                kb = int(linha.split()[1])
                info["ram_gb"] = round(kb / 1024 / 1024, 1)
                break
    except Exception:                                           # noqa: BLE001
        pass
    return info


def conferir_tetos(sessao: Sessao) -> list[str]:
    """Avisa se o alvo subiu com os tetos ligados.

    Não dá para ler a configuração do servidor de fora, então isto é o que dá:
    o `/saude` com sessão traz o diagnóstico. O aviso importa porque um número
    medido contra o limitador parece só 'a máquina é ruim'.
    """
    avisos = []
    if os.environ.get("EASYCONTIG_LIM_LEITURA", "") not in ("0",):
        avisos.append(
            "EASYCONTIG_LIM_LEITURA não está em 0 NESTE shell — se o alvo subiu "
            "com o mesmo ambiente, a rampa acima de ~5 leitores vai medir o "
            "limitador de taxa e não a máquina (300 leituras/min por conta).")
    return avisos


# --------------------------------------------------------------------------
# Fase 1 — leitura
# --------------------------------------------------------------------------

def fase_leitura(alvo: str, email: str, caminhos: list[tuple[str, str]],
                 rampa: list[int], voltas: int, saida: dict) -> None:
    """Todos os leitores entram na MESMA conta, e é de propósito.

    Duas razões, as duas medidas. (1) `/lotes/<id>` e o traço recusam lote de
    outra conta — `_lote_do_usuario` devolve 404, e está certo: o link é
    compartilhável por descuido. Com contas distintas, metade da rampa media
    404 em vez de página. (2) O custo da inicial cresce com quantas corridas a
    conta TEM — foi esse o N+1 de disco de 06/08 (a cota percorria cada pasta).
    Cinquenta contas vazias renderizariam a página barata e a máquina pareceria
    melhor do que é. O que se quer é uma conta com acervo realista, lida por
    muita gente ao mesmo tempo.
    """
    print("\n=== FASE 1 — LEITURA (rampa de leitores simultâneos) ===\n")
    print(f"{'simultâneos':>12} {'página':<28} {'p50':>8} {'p95':>8} "
          f"{'max':>8} {'recusas':>8} {'falhas':>7}")
    print("-" * 84)

    saida["leitura"] = []
    for n in rampa:
        sessoes = []
        for _ in range(n):
            s = Sessao(alvo, email)
            if not s.entrar():
                print(f"  ⚠️  {email} não entrou — o alvo está em "
                      f"EASYCONTIG_AUTH=dev e o domínio bate?")
                return
            sessoes.append(s)

        pior_p95 = 0.0
        for rotulo, caminho in caminhos:
            resumo = Resumo(f"{rotulo} @{n}")

            def bater(s: Sessao, c: str = caminho) -> list[Medida]:
                return [s.pegar(c) for _ in range(voltas)]

            with ThreadPoolExecutor(max_workers=n) as pool:
                for lote in pool.map(bater, sessoes):
                    resumo.medidas.extend(lote)

            L = resumo.linha()
            saida["leitura"].append({"simultaneos": n, **L})
            print(f"{n:>12} {rotulo:<28} "
                  f"{_f(L['p50']):>8} {_f(L['p95']):>8} {_f(L['max']):>8} "
                  f"{L['recusas']:>8} {L['falhas']:>7}"
                  + (f"   {L['codigos_de_falha']}" if L["falhas"] else ""))
            if L["p95"]:
                pior_p95 = max(pior_p95, L["p95"])

        if pior_p95 > TETO_P95_MS:
            print(f"\n  ⏹  parando a rampa: p95 passou de {TETO_P95_MS:.0f} ms "
                  f"com {n} simultâneos. O joelho está aqui.")
            saida["joelho_leitura"] = n
            return
    saida["joelho_leitura"] = None


def _f(v) -> str:
    return "—" if v is None else f"{v:.0f}"


# --------------------------------------------------------------------------
# Fase 2 — envio (o trabalho real)
# --------------------------------------------------------------------------

def fase_envio(alvo: str, dominio: str, arquivos: list[Path], quantas: int,
               referencia: str, paciencia: float, saida: dict) -> None:
    print(f"\n=== FASE 2 — ENVIO ({quantas} corridas simultâneas, "
          f"{len(arquivos)} arquivos cada) ===\n")

    corpo, tipo = montar_multipart(arquivos, "carga", referencia)
    mb = len(corpo) / 1024 / 1024
    print(f"  corpo montado uma vez: {mb:.1f} MB × {quantas} contas\n")

    sessoes = []
    for i in range(quantas):
        s = Sessao(alvo, f"envio{i:03d}@{dominio}")
        if not s.entrar():
            print(f"  ⚠️  conta envio{i:03d}@{dominio} não entrou.")
            return
        sessoes.append(s)

    t_zero = time.perf_counter()

    def subir(s: Sessao) -> tuple[str, Medida, str]:
        m, lote = s.enviar(corpo, tipo)
        return s.email, m, lote

    resultados = []
    with ThreadPoolExecutor(max_workers=quantas) as pool:
        resultados = list(pool.map(subir, sessoes))

    aceitos = [(e, m, lote) for e, m, lote in resultados if lote]
    recusados = [(e, m) for e, m, lote in resultados if not lote]

    aceite = Resumo("aceite do POST /lotes")
    aceite.medidas = [m for _, m, _ in resultados]
    L = aceite.linha()
    print(f"  aceite do POST (upload inteiro recebido): "
          f"p50 {_f(L['p50'])} ms · p95 {_f(L['p95'])} ms · max {_f(L['max'])} ms")
    print(f"  aceitos: {len(aceitos)} · recusados (cota/teto, legítimo): "
          f"{len(recusados)}")
    for email, m in recusados:
        print(f"      {email}: HTTP {m.codigo}"
              + (f" {m.erro}" if m.erro else ""))

    if not aceitos:
        print("\n  ⚠️  nenhum lote entrou — não há o que cronometrar.")
        saida["envio"] = {"aceitos": 0, "recusados": len(recusados)}
        return

    print(f"\n  esperando os {len(aceitos)} lotes ficarem `pronto` "
          f"(paciência: {paciencia:.0f} s)")
    # Pelo e-mail, e não pela ordem: quem consulta o estado do lote tem de ser
    # a sessão DONA dele — `_lote_do_usuario` recusa lote de outra conta, e
    # parear por posição devolveria 404 assim que a ordem de conclusão mudasse.
    por_email = {s.email: s for s in sessoes}
    pendentes = {lote: (por_email[email], email) for email, _, lote in aceitos}

    tempos: dict[str, float] = {}
    falhados: dict[str, str] = {}
    while pendentes and (time.perf_counter() - t_zero) < paciencia:
        time.sleep(1.0)
        for lote in list(pendentes):
            s, email = pendentes[lote]
            estado = s.estado_do_lote(lote)
            if estado == "pronto":
                tempos[lote] = time.perf_counter() - t_zero
                del pendentes[lote]
                print(f"      {lote} pronto em {tempos[lote]:6.1f} s   ({email})")
            elif estado == "falhou":
                falhados[lote] = email
                del pendentes[lote]
                print(f"      {lote} FALHOU   ({email})")

    for lote, (_, email) in pendentes.items():
        print(f"      {lote} ainda não terminou ao fim da paciência   ({email})")

    total_amostras = len(tempos) * (len(arquivos) // 2)
    duracao = max(tempos.values()) if tempos else 0.0
    saida["envio"] = {
        "corridas_simultaneas": quantas,
        "arquivos_por_corrida": len(arquivos),
        "mb_por_corrida": round(mb, 1),
        "aceite_p50_ms": L["p50"],
        "aceite_p95_ms": L["p95"],
        "aceitos": len(aceitos),
        "recusados_legitimos": len(recusados),
        "prontos": len(tempos),
        "falhados": len(falhados),
        "nao_terminaram": len(pendentes),
        "segundos_ate_o_ultimo": round(duracao, 1),
        "tempos_por_lote": {k: round(v, 1) for k, v in tempos.items()},
    }

    if tempos and duracao > 0:
        por_hora = len(tempos) / duracao * 3600
        s_amostra = duracao / total_amostras if total_amostras else float("nan")
        saida["envio"]["corridas_por_hora"] = round(por_hora, 1)
        saida["envio"]["segundos_de_parede_por_amostra"] = round(s_amostra, 2)
        print(f"\n  ⏱  {len(tempos)} corridas em {duracao:.1f} s  →  "
              f"{por_hora:.0f} corridas/hora nesta máquina")
        print(f"     {s_amostra:.2f} s de parede por amostra "
              f"(referência: 0,65 s de CPU/amostra medido no Deck em 04/08)")


# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Arnês de carga do EasyContig BR (biblioteca padrão só).")
    p.add_argument("--alvo", default="http://127.0.0.1:8123",
                   help="endereço da instância ISOLADA (nunca produção)")
    p.add_argument("--dominio", default="carga.local",
                   help="domínio das contas de teste; tem que estar em "
                        "EASYCONTIG_DOMINIO do alvo")
    p.add_argument("--amostras", type=Path, default=None,
                   help="pasta com os .ab1 de uma corrida (padrão: procura)")
    p.add_argument("--rampa", default=",".join(str(x) for x in RAMPA_PADRAO),
                   help="níveis de leitores simultâneos, separados por vírgula")
    p.add_argument("--voltas", type=int, default=6,
                   help="requisições por leitor em cada nível")
    p.add_argument("--corridas", type=int, default=3,
                   help="corridas simultâneas na fase 2")
    p.add_argument("--semente", type=int, default=5,
                   help="corridas semeadas na conta de leitura antes da fase 1; "
                        "use 40 para reproduzir o acervo medido em 06/08")
    p.add_argument("--referencia", default="curado")
    p.add_argument("--paciencia", type=float, default=900.0,
                   help="segundos de espera pelos lotes na fase 2")
    p.add_argument("--so-leitura", action="store_true")
    p.add_argument("--so-envio", action="store_true")
    p.add_argument("--json", type=Path, default=None,
                   help="grava o relatório em JSON, para comparar máquinas")
    args = p.parse_args()

    saida: dict = {
        "quando": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "alvo": args.alvo,
        "maquina_do_arnes": identificar_maquina(),
    }

    print("=" * 84)
    print("ARNÊS DE CARGA — EasyContig BR")
    print("=" * 84)
    m = saida["maquina_do_arnes"]
    print(f"  máquina : {m.get('cpu', '?')}")
    print(f"            {m.get('nucleos', '?')} núcleos · "
          f"{m.get('ram_gb', '?')} GB RAM · {m.get('sistema', '?')}")
    print(f"  alvo    : {args.alvo}")

    piloto = Sessao(args.alvo, f"piloto@{args.dominio}")
    saude = piloto.pegar("/saude")
    if not saude.ok:
        print(f"\n  ⚠️  {args.alvo}/saude respondeu {saude.codigo} "
              f"{saude.erro}\n      O alvo não está de pé. "
              f"Suba com tools/carga.sh, ou confira QUEM atende a porta:\n"
              f"      ss -ltnp | grep {args.alvo.rsplit(':', 1)[-1]}")
        return 2
    if not piloto.entrar():
        print(f"\n  ⚠️  não consegui entrar como piloto@{args.dominio}.\n"
              f"      O alvo precisa de EASYCONTIG_AUTH=dev e de "
              f"EASYCONTIG_DOMINIO contendo '{args.dominio}'.")
        return 2

    for aviso in conferir_tetos(piloto):
        print(f"\n  ⚠️  {aviso}")

    # A pasta de .ab1
    pasta = args.amostras
    if pasta is None:
        raiz = Path(__file__).resolve().parent.parent
        candidatas = sorted((raiz / "dados" / "lotes").glob("*/entrada"),
                            key=lambda d: -len(list(d.iterdir())))
        pasta = candidatas[0] if candidatas else None
    if pasta is None or not pasta.is_dir():
        print("\n  ⚠️  não achei uma pasta de .ab1. Passe --amostras <pasta>.")
        return 2
    arquivos = sorted(x for x in pasta.iterdir()
                      if x.suffix.lower() in EXT_AB1)
    if not arquivos:
        print(f"\n  ⚠️  {pasta} não tem .ab1.")
        return 2
    print(f"  amostras: {len(arquivos)} arquivos de {pasta}")

    # A semente. O acervo da conta é o que faz a página inicial custar caro —
    # foi esse o N+1 de disco de 06/08 — então medir contra uma conta com uma
    # corrida só produziria um número bonito e falso. O padrão é modesto para o
    # arnês terminar em minutos; para a medição que escolhe a VPS, use
    # `--semente 40`, que é o acervo com que 06/08 mediu.
    lote_ref = ""
    conta_leitura = f"leitor@{args.dominio}"
    if not args.so_envio:
        leitor = Sessao(args.alvo, conta_leitura)
        if not leitor.entrar():
            print(f"\n  ⚠️  {conta_leitura} não entrou.")
            return 2
        corpo, tipo = montar_multipart(arquivos, "semente", args.referencia)
        print(f"\n  semeando {args.semente} corrida(s) em {conta_leitura} "
              f"— a página inicial custa em função do acervo")
        for i in range(args.semente):
            med, lote = leitor.enviar(corpo, tipo)
            if not lote:
                print(f"  ⚠️  semente {i + 1} devolveu HTTP {med.codigo} "
                      f"{med.erro}")
                return 2
            lote_ref = lote
        alvo_t = time.perf_counter()
        while time.perf_counter() - alvo_t < args.paciencia:
            e = leitor.estado_do_lote(lote_ref)
            if e in ("pronto", "falhou"):
                print(f"  semente pronta em {time.perf_counter() - alvo_t:.1f} s "
                      f"(último lote: {e})")
                break
            time.sleep(1.0)
        else:
            print("  ⚠️  a semente não terminou; há trabalhador rodando? "
                  "(confira o log do trabalhador — o arnês NÃO é a fonte)")
            return 2
        piloto = leitor

    caminhos = [("/ (inicial)", "/"), ("/perfil", "/perfil")]
    if lote_ref:
        caminhos.append(("/lotes/<id>", f"/lotes/{lote_ref}"))
        # A chave da amostra vem do RELATÓRIO (`samples[].key`), não do
        # `/api/lotes/<id>` — este devolve a linha do banco e não tem amostra
        # nenhuma. Na primeira versão eu procurei `amostras[]` ali, não achei, e
        # o arnês simplesmente PULOU o traço sem dizer nada: a rota mais pesada
        # do app (108 KB por par, ADR 0052) ficou de fora da medição em
        # silêncio. Agora, se não achar, ele reclama.
        try:
            rel = json.loads(piloto.direto.open(
                f"{args.alvo}/lotes/{lote_ref}/relatorio.json", timeout=60).read())
            chave = next((a["key"] for a in (rel.get("samples") or [])
                          if isinstance(a, dict) and a.get("key")), None)
        except Exception as e:                                  # noqa: BLE001
            chave = None
            print(f"  ⚠️  não li o relatório da semente: {e!r}")
        if chave:
            caminhos.append(
                ("traço (cromatograma)",
                 f"/api/lotes/{lote_ref}/amostras/{chave}/traco"))
        else:
            print("  ⚠️  sem chave de amostra — o traço, que é a rota mais "
                  "pesada, FICOU DE FORA desta medição.")

    rampa = [int(x) for x in args.rampa.split(",") if x.strip()]
    if not args.so_envio:
        saida["semente_corridas"] = args.semente
        fase_leitura(args.alvo, conta_leitura, caminhos, rampa,
                     args.voltas, saida)
    if not args.so_leitura:
        fase_envio(args.alvo, args.dominio, arquivos, args.corridas,
                   args.referencia, args.paciencia, saida)

    print("\n" + "=" * 84)
    if args.json:
        args.json.write_text(json.dumps(saida, indent=2, ensure_ascii=False))
        print(f"relatório em {args.json}")
        print("Para comparar duas máquinas: rode o mesmo comando nas duas e "
              "compare os JSON.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
