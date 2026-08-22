/* oficina.js — o workspace da amostra: alinhamento, dois cromatogramas
   acoplados e edição de base.

   O acoplamento entre F e R sai de graça e não é feito aqui: o servidor manda o
   traço em coordenada de COLUNA do consenso (app/core/assembly.py,
   chromatogram_data_columns), então as duas leituras já compartilham o eixo x.
   Este arquivo só desenha e trata o clique.

   A edição usa a mesma regra do núcleo: apagar uma base vira gap naquela
   leitura, e o consenso é o voto majoritário da coluna ignorando gaps — com uma
   leitura só, quem sobra decide. O consenso só vira definitivo na EXPORTAÇÃO
   (ADR 0052): até lá o `.ab1` em disco continua sendo a verdade. */
(function () {
  "use strict";
  const raiz = document.querySelector(".oficina");
  if (!raiz) return;
  const $ = s => document.querySelector(s);

  /* Escapa antes de virar HTML. Existe porque um XSS ARMAZENADO foi confirmado
     em 06/08 com prova de execução: um FASTA com o cabeçalho
     `>REF001 <img src=x onerror=…>` vira o `stitle` do BLAST, o `stitle` vira
     `titulo` na resposta da consulta, e o `titulo` caía em `innerHTML`.
     A mesma porta valia para o NOME DA LEITURA, que sai do nome do arquivo
     enviado — e vale para qualquer texto que venha do NCBI, que é de fora.
     Regra daqui em diante: nada que não seja literal do nosso código entra em
     `innerHTML` sem passar por aqui. */
  const esc = v => String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  /* `desloc` é a LENTE, em colunas: quanto a janela andou por arrasto, à parte
     da coluna marcada. Existe porque a janela era definida só por `sel`, e então
     não havia como caminhar pelo contig sem mudar a marca — e a marca é
     referência de trabalho (é nela que "remover base" age), não posição de
     rolagem. */
  const est = { d: null, seq: [], sel: null, desloc: 0, pilha: [], removidas: 0,
                exportado: true };

  // Rascunho guardado no navegador, por amostra. O consenso só vira definitivo
  // na exportação (ADR 0052) — isto não muda essa regra, só evita que recarregar
  // a página perca a correção. O `.ab1` em disco continua intocado, e o rascunho
  // é do navegador de quem editou, não do servidor.
  //
  // ⚠️ `sessionStorage`, NÃO `localStorage` — achado F3 da varredura de
  // 2026-08-21. Aqui dentro vão as leituras ALINHADAS de uma corrida ainda não
  // publicada, e no `localStorage` elas sobreviviam ao fim da sessão do
  // servidor: numa estação compartilhada de laboratório, a próxima pessoa a
  // sentar abria o devtools e lia a sequência de quem estava antes, sem
  // credencial nenhuma. `sessionStorage` mantém intacto o motivo de o rascunho
  // existir (recarregar a página não perde a correção — ele sobrevive ao F5),
  // e some quando a aba fecha, que é justamente quando a pessoa foi embora.
  const CHAVE = "easycontig:rascunho:" + location.pathname;
  const GUARDA = window.sessionStorage;
  // Prazo de validade, o que o comentário do `entrar.html` já pedia: aba
  // esquecida aberta a noite inteira não deve entregar a edição da manhã
  // anterior. 12 h cobre um dia de trabalho com folga.
  const VALIDADE_MS = 12 * 60 * 60 * 1000;

  function guardarRascunho() {
    try {
      if (!est.removidas) { GUARDA.removeItem(CHAVE); return; }
      GUARDA.setItem(CHAVE, JSON.stringify({
        seq: est.seq.map(s => s.join("")), removidas: est.removidas,
        quando: new Date().toISOString(),
      }));
    } catch (e) { /* modo anônimo ou cota cheia: seguir sem rascunho */ }
  }

  function lerRascunho() {
    try {
      const r = JSON.parse(GUARDA.getItem(CHAVE) || "null");
      if (!r) return null;
      // Vencido não volta — e sai do armazenamento em vez de ficar esperando.
      const t = Date.parse(r.quando || "");
      if (!t || (Date.now() - t) > VALIDADE_MS) {
        GUARDA.removeItem(CHAVE);
        return null;
      }
      return r;
    } catch (e) { return null; }
  }

  fetch(raiz.dataset.traco, { headers: { accept: "application/json" } })
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(d => {
      if (!d.disponivel) { semTraco(d.motivo); return; }
      est.d = d;
      est.seq = d.leituras.map(l => l.alinhada.split(""));

      // Rascunho anterior desta amostra: só entra se ainda casar com o traço
      // (mesmo nº de leituras e mesma largura). Se a corrida foi reprocessada,
      // aplicar edição velha em cima de alinhamento novo apagaria a base errada.
      const r = lerRascunho();
      if (r && r.seq && r.seq.length === est.seq.length
          && r.seq.every((s, i) => s.length === est.seq[i].length)) {
        est.seq = r.seq.map(s => s.split(""));
        est.removidas = r.removidas || 0;
        est.exportado = false;
        const av = document.getElementById("rascunho-antigo");
        if (av) {
          av.style.display = "flex";
          av.querySelector("time").textContent =
            new Date(r.quando).toLocaleString("pt-BR").slice(0, 16);
        }
      }
      montar();
      // Abre onde as leituras SE SOBREPÕEM, não na coluna 0. Numa amostra em que
      // a reversa começa na coluna 30, abrir no início deixava metade do painel
      // dela vazio — e um painel vazio lê como "não montou", que é falso.
      const cob = d.cobertura || [];
      const inicio = cob.findIndex(c => c >= 2);
      const mm = mismatches();
      const noMeio = mm.find(c => inicio < 0 || c >= inicio);
      selecionar(noMeio !== undefined ? noMeio
                 : (inicio >= 0 ? inicio + Math.floor(JAN / 2) : 0));
    })
    .catch(() => semTraco("não foi possível carregar o traço agora"));

  function semTraco(motivo) {
    $("#aln").innerHTML = '<p class="sub" style="margin:0">Alinhamento indisponível: '
      + esc(motivo) + ". As medidas ao lado continuam valendo.</p>";
  }

  // ── consenso e mismatches: mesma regra do núcleo ──────────────────────────
  /* Consenso e discordâncias varrem o contig INTEIRO (656 colunas na amostra
     real), e eram recalculados a cada interação — quatro passadas completas por
     clique, uma alocação de array por coluna, para responder a uma pergunta que
     só muda quando alguém EDITA uma base. Agora são calculados uma vez e
     guardados; `invalidar()` é chamado de todo caminho que mexe em `est.seq`.

     Uma passada só serve às duas respostas: elas leem exatamente as mesmas
     colunas. */
  let _cache = null;

  function invalidar() { _cache = null; }

  function calcular() {
    if (_cache) return _cache;
    const cons = [], mm = [];
    const n = est.seq.length;
    for (let c = 0; c < est.d.largura; c++) {
      let primeira = null, divergiu = false, vistas = 0;
      for (let i = 0; i < n; i++) {
        const b = est.seq[i][c];
        if (!b || b === "-" || b === "N") continue;
        vistas++;
        if (primeira === null) primeira = b;
        else if (b !== primeira) divergiu = true;
      }
      cons.push(primeira === null ? "-" : primeira);
      if (vistas > 1 && divergiu) mm.push(c);
    }
    _cache = { cons, mm, mmSet: new Set(mm) };
    return _cache;
  }

  function consenso() { return calcular().cons; }
  function mismatches() { return calcular().mm; }
  const cls = b => b === "-" || b === "." ? "bgap" : "b" + b.toUpperCase();

  // ── desenho ───────────────────────────────────────────────────────────────
  function montar() {
    const alvo = document.getElementById("cromatogramas");
    alvo.innerHTML = est.d.leituras.map((l, i) => `
      <section class="bloco" style="margin-bottom:11px">
        <div class="cr-topo">
          <span class="nome">${esc(l.nome)}</span>
          <span class="meta">sentido ${esc(l.sentido)}${
            l.primer ? " · primer " + esc(l.primer) : ""}${
            l.q_medio ? " · Q" + esc(l.q_medio) + " " + esc(l.q_rotulo) : ""}</span>
          <span class="leg">
            <s><i style="background:var(--verde)"></i>A</s>
            <s><i style="background:var(--azul)"></i>C</s>
            <s><i style="background:#C6CBE0"></i>G</s>
            <s><i style="background:var(--vermelho)"></i>T</s>
          </span>
        </div>
        <div class="tela-tr">
          <canvas id="cv-${i}" class="tr-cv"
                  aria-label="Cromatograma de ${esc(l.nome)}"></canvas>
          <div class="bases-lin" id="bases-${i}"></div>
        </div>
      </section>`).join("");
    // Um botão por leitura, gerado: a amostra pode ter 2 leituras (o par) ou 4
    // (dois pares de primers, como na pasta do Hepatozoon). Botões fixos "só do
    // F" / "só do R" só davam conta do primeiro caso.
    const bs = document.getElementById("botoes-remover");
    bs.innerHTML = est.d.leituras.map((l, i) =>
      `<button class="ac perigo" type="button" data-l="${i}" disabled>${
        est.d.leituras.length > 2
          ? esc(l.primer || l.sentido + (i + 1))
          : "só do " + esc(l.sentido)
      }</button>`).join("")
      + `<button class="ac perigo" id="b-todas" type="button" disabled>${
          est.d.leituras.length > 2 ? "de todas" : "dos dois"}</button>`;
    bs.querySelectorAll("[data-l]").forEach(b =>
      b.onclick = () => remover([+b.dataset.l]));
    document.getElementById("b-todas").onclick = () =>
      remover(est.d.leituras.map((_, i) => i));
    montarTracos();
    $("#acoes").style.display = "flex";
  }

  // janela visível: 74 colunas, como no desenho aprovado. Rolar move as DUAS.
  const JAN = 74;
  const meia = () => Math.floor(JAN / 2);
  const inicioNatural = () =>
    (est.sel === null ? meia() : est.sel) - meia();   // sem arrasto: marca no meio
  const tetoJanela = () => Math.max(0, est.d.largura - JAN);

  function janela() {
    const i = Math.max(0, Math.min(
      Math.round(inicioNatural() + est.desloc), tetoJanela()));
    return [i, Math.min(est.d.largura, i + JAN)];
  }

  /* O arrasto acumula em `desloc`, mas a janela é presa nas pontas do contig.
     Sem prender `desloc` também, puxar 5 telas além do fim guardaria 5 telas de
     dívida e o caminho de volta começaria por um trecho morto. */
  function limitarDesloc() {
    const base = inicioNatural();
    est.desloc = Math.max(-base, Math.min(tetoJanela() - base, est.desloc));
  }

  /* ── o alinhamento REAPROVEITA os nós ──────────────────────────────────────
     Antes esta função remontava o `innerHTML` inteiro a cada seleção. Medido no
     desktop: **3,0 ms** só para reescrever o alinhamento, mais 1,6 ms por faixa
     de letras — contra 0,1 ms do traço depois que ele virou canvas. Ou seja, o
     custo tinha migrado do desenho para o TEXTO, e insistir no traço não
     resolveria mais nada.

     A quantidade de colunas visíveis é fixa (JAN), então os nós podem ser
     criados uma vez e só atualizados: `textContent` e `className` em vez de
     analisar marcação e construir árvore de novo. O clique passa a ser tratado
     por delegação, num ouvinte só, em vez de um por span. */
  let alnPronto = false;
  const alnCel = [];        // [linha][coluna] → span

  function montarAln() {
    const el = $("#aln");
    const n = JAN;
    el.innerHTML = "";
    alnCel.length = 0;
    const faz = (rotulo, classeRot, guardar) => {
      const div = document.createElement("div");
      div.className = "aln-l";
      const r = document.createElement("span");
      r.className = "aln-n" + (classeRot ? " " + classeRot : "");
      r.textContent = rotulo;
      const corpo = document.createElement("span");
      if (classeRot === "regua") corpo.className = "regua";
      const linha = [];
      for (let k = 0; k < n; k++) {
        const s = document.createElement("span");
        s.className = "b";
        corpo.appendChild(s);
        linha.push(s);
      }
      div.append(r, corpo);
      el.appendChild(div);
      if (guardar) alnCel.push(linha); else alnCel.push(linha);
      return linha;
    };
    est.d.leituras.forEach(l =>
      faz((l.sentido === "F" ? "→ " : "← ") + l.nome, "", true));
    faz("régua (×10)", "regua", true);
    faz("Contig", "forte", true);

    // Delegação: um ouvinte para o bloco inteiro, e não um por célula.
    el.onclick = ev => {
      const alvo = ev.target.closest(".b[data-col]");
      if (alvo) selecionar(+alvo.dataset.col);
    };
    alnPronto = true;
  }

  function desenhaAln() {
    if (!alnPronto) montarAln();
    const [i0, i1] = janela(), mm = new Set(mismatches()), c = consenso();
    const n = i1 - i0;
    const linhas = est.d.leituras.length;

    const pinta = (celulas, arr) => {
      for (let k = 0; k < celulas.length; k++) {
        const s = celulas[k];
        if (k >= n) { s.style.display = "none"; continue; }
        s.style.display = "";
        const col = i0 + k, b = arr[col];
        s.textContent = b === "-" ? "·" : (b === undefined ? " " : b);
        s.className = "b " + cls(b)
          + (mm.has(col) ? " mm" : "")
          + (est.sel === col ? " sel" : "")
          + (b === "-" ? " morta" : "");
        s.dataset.col = col;
      }
    };
    for (let i = 0; i < linhas; i++) pinta(alnCel[i], est.seq[i]);

    const regua = alnCel[linhas];
    for (let k = 0; k < regua.length; k++) {
      if (k >= n) { regua[k].style.display = "none"; continue; }
      regua[k].style.display = "";
      regua[k].textContent = (i0 + k + 1) % 10 === 0 ? "|" : "·";
    }
    pinta(alnCel[linhas + 1], c);

    $("#m-mm").textContent = mm.size;
    $("#q-ed").textContent = est.removidas;
  }

  // O traço em si NUNCA muda: editar base mexe no rótulo e no consenso, não no
  // sinal medido. Então ele é desenhado UMA vez, e o clique só move o viewBox e
  // as faixas.
  //
  // Antes tudo era reescrito por innerHTML a cada seleção — 16.940 pontos e
  // 197 KB de atributo POR cromatograma, reanalisados e repintados do zero.
  // Medido: 33 ms do clique até o quadro pintado (2 frames), e no Deck pior.
  // O script custava 8 ms; o resto era o navegador repintando polilinha que
  // continuava idêntica.
  function montarTracos() {
    est.d.leituras.forEach((l, i) => {
      const cv = document.getElementById("cv-" + i);
      if (!cv) return;
      cv.onclick = ev => {
        const r = cv.getBoundingClientRect(), [i0, i1] = janela();
        selecionar(Math.round(i0 + (ev.clientX - r.left) / r.width * (i1 - i0)));
      };
      ligarArrasto(cv.parentElement, cv);
    });
    ajustarCanvas();
  }

  /* ── arrastar o traço para o lado ──────────────────────────────────────────
     Antes NÃO HAVIA arrasto: o gesto chegava ao fim como um `click` na coluna
     onde a mão soltou, e clicar recentra a janela ali. O efeito para quem usa é
     o desenho andando para o lado CONTRÁRIO da mão — medido: puxando de 25% a
     75% da largura, a primeira coluna visível ia de 211 para 230.

     Agora o traço acompanha a mão, como em qualquer mapa: puxar para a DIREITA
     traz o que está à esquerda. A marca não sai do lugar — quem anda é a lente
     (`est.desloc`), não a coluna escolhida.

     `pointer*` e não `mouse*` porque o alvo é o Deck: o mesmo código atende
     dedo, caneta e mouse, e `setPointerCapture` mantém o arrasto vivo quando a
     mão sai do quadro no meio do movimento. */
  const ARRASTO_MIN = 4;        // px: abaixo disto a pessoa quis clicar

  let quadro = 0;
  function pedirQuadro() {      // um render por quadro, não um por evento
    if (quadro) return;
    quadro = requestAnimationFrame(() => { quadro = 0; render(); });
  }

  function ligarArrasto(tela, cv) {
    let puxando = null, arrastou = false;

    tela.addEventListener("pointerdown", ev => {
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      puxando = {
        id: ev.pointerId, x: ev.clientX, desloc0: est.desloc,
        largura: cv.getBoundingClientRect().width || 1, andou: false,
      };
    });

    tela.addEventListener("pointermove", ev => {
      if (!puxando || ev.pointerId !== puxando.id) return;
      // Sem botão apertado não há arrasto: cobre o caso de a mão soltar fora do
      // quadro antes do limiar, quando ainda não há captura para nos avisar.
      if (ev.buttons === 0) { puxando = null; return; }
      const dx = ev.clientX - puxando.x;
      if (!puxando.andou) {
        if (Math.abs(dx) < ARRASTO_MIN) return;
        puxando.andou = true;
        tela.classList.add("puxando");
        /* A captura entra AQUI, e não no `pointerdown`: no Chromium ela
           redireciona também o `click` de compatibilidade para o elemento que
           capturou, e com ela no início o clique parava de chegar ao canvas —
           marcar uma base pelo traço deixava de funcionar. Achado pelo teste
           `test_clique_curto_dentro_do_traco_continua_marcando`. */
        tela.setPointerCapture(ev.pointerId);
      }
      // O sinal é o ponto do pedido: `- dx` faz o conteúdo seguir a mão.
      est.desloc = puxando.desloc0 - dx / puxando.largura * JAN;
      limitarDesloc();
      pedirQuadro();
    });

    const solta = ev => {
      if (!puxando || ev.pointerId !== puxando.id) return;
      arrastou = puxando.andou;
      puxando = null;
      tela.classList.remove("puxando");
      if (tela.hasPointerCapture(ev.pointerId)) tela.releasePointerCapture(ev.pointerId);
    };
    tela.addEventListener("pointerup", solta);
    tela.addEventListener("pointercancel", solta);

    /* Na captura, antes dos ouvintes do canvas e da faixa de letras: quem
       arrastou não escolheu a base onde soltou a mão. Sem isto todo arrasto
       terminaria marcando uma base ao acaso — e recentrando a janela, que é
       exatamente o defeito que este bloco existe para tirar. */
    tela.addEventListener("click", ev => {
      if (!arrastou) return;
      arrastou = false;
      ev.stopPropagation();
      ev.preventDefault();
    }, true);
  }

  /* ── por que CANVAS, e não SVG ──────────────────────────────────────────────
     O traço era SVG e cada mudança de janela reescrevia `innerHTML` de quatro
     polilinhas por leitura. Medido no desktop: **9,0 ms de trabalho síncrono**
     por clique — analisar a marcação, recalcular estilo e refazer o layout de
     nós que o navegador acabara de criar. No Deck isso multiplica, e é o
     "parece 30 quadros por segundo" relatado por quem usa.

     ⚠️ Antes de trocar, uma medição minha estava ERRADA e quase virou conclusão:
     eu cronometrava esperando dois quadros, e o navegador em modo headless roda
     a 30 Hz — então "não fazer nada" também dava 33 ms. O piso do arnês não é
     custo do código. O número que vale é o trabalho SÍNCRONO acima.

     Canvas é desenho imediato: sem nó, sem estilo, sem layout. O mesmo traço
     custa uma passada de `lineTo`. E a amplitude, que no SVG obrigava a
     recalcular todos os pontos em texto, vira uma multiplicação no desenho. */
  const ALTURA_LOGICA = 112;          // mesmo sistema de coordenadas de antes

  function ajustarCanvas() {
    // `devicePixelRatio`: sem isto o traço sai borrado em tela HiDPI — e o Deck
    // ligado a uma TV 4K é exatamente esse caso.
    const dpr = window.devicePixelRatio || 1;
    est.d.leituras.forEach((_l, i) => {
      const cv = document.getElementById("cv-" + i);
      if (!cv) return;
      const r = cv.getBoundingClientRect();
      const w = Math.max(1, Math.round(r.width * dpr));
      const h = Math.max(1, Math.round(r.height * dpr));
      if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    });
  }

  const COR = { A: "#3FB950", C: "#58A6FF", G: "#C6CBE0", T: "#F0524A" };

  function desenhaUmTraco(cv, leitura, i0, i1, mm) {
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    const cols = i1 - i0;
    const px = c => (c - i0) / cols * W;            // coluna → pixel
    const py = y => y / ALTURA_LOGICA * H;          // y lógico → pixel

    ctx.clearRect(0, 0, W, H);

    // faixas: discordância e coluna escolhida, atrás do traço
    ctx.fillStyle = "rgba(240,82,74,.13)";
    for (const c of mm) {
      if (c >= i0 && c < i1) ctx.fillRect(px(c - .5), 0, W / cols, py(104));
    }
    if (est.sel !== null && est.sel >= i0 && est.sel < i1) {
      ctx.fillStyle = "rgba(145,132,217,.20)";
      ctx.fillRect(px(est.sel - .5), 0, W / cols, py(104));
      ctx.strokeStyle = "#9184D9";
      ctx.lineWidth = Math.max(1, (window.devicePixelRatio || 1));
      ctx.strokeRect(px(est.sel - .5), 0, W / cols, py(104));
    }

    ctx.lineWidth = 1.25 * (window.devicePixelRatio || 1);
    ctx.lineJoin = "round";
    for (const ch of "ACGT") {
      const plano = leitura.canais[ch] || [];
      ctx.beginPath();
      ctx.strokeStyle = COR[ch];
      let caneta = false;
      for (let k = 0; k < plano.length; k += 2) {
        const x = plano[k];
        if (x === null) { caneta = false; continue; }     // quebra do gap
        if (x < i0 - 1 || x > i1 + 1) { caneta = false; continue; }
        // y=100 é a base; ampliar puxa o pico para cima sem sair do quadro
        const yl = plano[k + 1];
        const y = amp === 1 ? yl : Math.max(6, 100 - (100 - yl) * amp);
        if (caneta) ctx.lineTo(px(x), py(y));
        else { ctx.moveTo(px(x), py(y)); caneta = true; }
      }
      ctx.stroke();
    }
  }

  function desenhaTracos() {
    const [i0, i1] = janela(), mm = mismatches();
    est.d.leituras.forEach((l, i) => {
      const cv = document.getElementById("cv-" + i);
      if (!cv) return;
      desenhaUmTraco(cv, l, i0, i1, mm);

      // As letras ficam FORA do desenho, em DOM: precisam ser clicáveis, e pixel
      // no canvas não é. Mas os nós são REAPROVEITADOS pelo mesmo motivo do
      // alinhamento — reescrever `innerHTML` aqui custava 1,6 ms por faixa.
      const faixa = document.getElementById("bases-" + i);
      const visiveis = l.bases.filter(([c]) => c >= i0 && c < i1);
      if (!faixa._cel) {
        faixa._cel = [];
        faixa.onclick = ev => {
          const alvo = ev.target.closest(".bl[data-col]");
          if (alvo) selecionar(+alvo.dataset.col);
        };
      }
      while (faixa._cel.length < visiveis.length) {
        const s = document.createElement("span");
        s.className = "bl";
        faixa.appendChild(s);
        faixa._cel.push(s);
      }
      faixa._cel.forEach((s, k) => {
        if (k >= visiveis.length) { s.style.display = "none"; return; }
        const [col, base] = visiveis[k];
        const morta = est.seq[i][Math.round(col)] === "-";
        s.style.display = "";
        s.style.left = ((col - i0 + .5) / (i1 - i0) * 100) + "%";
        s.className = "bl " + (morta ? "morta " : "") + cls(base);
        s.textContent = morta ? "·" : base;
        s.dataset.col = Math.round(col);
      });

      // Painel vazio lê como "não montou", e é falso: a leitura simplesmente
      // não alcança esta região do contig.
      const nada = !l.bases.some(([c]) => c >= i0 && c < i1);
      let aviso = cv.parentElement.querySelector(".fora");
      if (nada && !aviso) {
        aviso = document.createElement("div");
        aviso.className = "fora";
        aviso.textContent = "esta leitura não cobre esta região do contig";
        cv.parentElement.appendChild(aviso);
      } else if (!nada && aviso) {
        aviso.remove();
      }
    });
  }

  // ── seleção: sempre nas DUAS leituras (é o acoplamento pedido) ────────────
  function selecionar(col) {
    const [antes] = janela();
    const tinha = est.sel !== null;
    est.sel = Math.max(0, Math.min(col, est.d.largura - 1));
    /* Marcar uma base que JÁ ESTÁ na tela não pode mexer na tela: quem arrastou
       até aqui e clicou no pico que veio ver perderia o lugar na hora do clique.
       Fora da janela (⚠ próximo erro, desfazer numa coluna distante) a janela
       vai atrás e recentra, que é o comportamento antigo. */
    if (tinha && est.sel >= antes && est.sel < antes + JAN) {
      est.desloc = antes - (est.sel - meia());
    } else {
      est.desloc = 0;
    }
    limitarDesloc();
    document.querySelectorAll("#botoes-remover [data-l]").forEach(b => {
      const v = est.seq[+b.dataset.l][est.sel];
      b.disabled = !(v && v !== "-");
    });
    const algum = est.seq.some(s => s[est.sel] && s[est.sel] !== "-");
    const todas = document.getElementById("b-todas");
    if (todas) todas.disabled = !algum;
    render();
  }

  function guarda() {
    est.pilha.push({ seq: est.seq.map(s => s.slice()), rem: est.removidas });
    if (est.pilha.length > 50) est.pilha.shift();
    $("#b-undo").disabled = false;
  }

  function remover(indices) {
    if (est.sel === null) return;
    guarda();
    indices.forEach(i => {
      if (est.seq[i][est.sel] && est.seq[i][est.sel] !== "-") {
        est.seq[i][est.sel] = "-"; est.removidas++;
      }
    });
    invalidar();                  // a sequência mudou: consenso/mismatch caem
    est.exportado = false;
    guardarRascunho();
    selecionar(est.sel);
  }
  $("#b-undo").onclick = () => {
    const s = est.pilha.pop(); if (!s) return;
    est.seq = s.seq; est.removidas = s.rem; est.exportado = false;
    invalidar();                  // desfazer também mexe em est.seq
    $("#b-undo").disabled = !est.pilha.length;
    guardarRascunho();
    selecionar(est.sel === null ? 0 : est.sel);
  };

  $("#b-erro").onclick = () => {
    const mm = mismatches(); if (!mm.length) return;
    const prox = mm.find(c => est.sel === null || c > est.sel);
    selecionar(prox === undefined ? mm[0] : prox);
  };

  // ── exportação: é aqui que o consenso vira definitivo ─────────────────────
  function exportar(formato) {
    const c = consenso().filter(b => b !== "-").join("");
    const chave = raiz.dataset.traco.split("/amostras/")[1].split("/")[0];
    const nota = est.removidas ? ` | ${est.removidas} base(s) removida(s) a mao` : "";
    const txt = formato === "fasta"
      ? `>${decodeURIComponent(chave)} | ${c.length} pb${nota}\n`
        + (c.match(/.{1,60}/g) || []).join("\n") + "\n"
      : c + "\n";
    const url = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = decodeURIComponent(chave) + (formato === "fasta" ? ".fasta" : ".txt");
    a.click();
    URL.revokeObjectURL(url);
    est.exportado = true;
    // Exportou: o rascunho cumpriu o papel e sai. Guardá-lo depois disso faria a
    // próxima visita ressuscitar uma edição que já virou arquivo.
    try { GUARDA.removeItem(CHAVE); } catch (e) {}
    const av = document.getElementById("rascunho-antigo");
    if (av) av.style.display = "none";
    render();
  }
  $("#b-fasta").onclick = () => exportar("fasta");
  $("#b-txt").onclick = () => exportar("txt");

  // ── consulta a outro banco: é segunda opinião, não veredito ──────────────
  const btConsulta = document.getElementById("b-consultar");
  if (btConsulta) {
    btConsulta.onclick = () => {
      const banco = document.getElementById("banco-consulta").value;
      const saida = document.getElementById("saida-consulta");
      btConsulta.disabled = true;
      saida.innerHTML = '<span class="sub">consultando…</span>';
      const base = raiz.dataset.traco.replace(/\/traco$/, "/consultar");
      fetch(base + "?banco=" + encodeURIComponent(banco),
            { headers: { accept: "application/json" } })
        .then(r => r.ok ? r.json() : Promise.reject(r))
        .then(d => {
          if (!d.hits.length) {
            saida.innerHTML = '<span class="sub">Nenhum acerto nesse banco. '
              + 'Isso descreve o banco consultado, não a amostra.</span>';
            return;
          }
          saida.innerHTML = d.hits.map(h =>
            `<div class="hit"><span class="org">${esc(h.titulo)}</span>`
            + `<span class="num">${esc(String(h.identidade).replace(".", ","))}%</span>`
            + ` · cobertura ${esc(String(h.cobertura).replace(".", ","))}%`
            + ` · <span class="acc">${esc(h.accession)}</span></div>`).join("");
        })
        .catch(() => { saida.innerHTML =
          '<span class="erro">Não foi possível consultar agora.</span>'; })
        .finally(() => { btConsulta.disabled = false; });
    };
  }

  // ── amplitude do traço ───────────────────────────────────────────────────
  // A ponta da leitura tem sinal fraco de verdade, e no zoom natural ela some.
  // Ampliar é o que o desktop chama de "Amp 1×": não muda o dado, muda a lente —
  // e o rótulo diz o fator, para ninguém ler pico ampliado como pico alto.
  //
  // Vai de 0,5× a 16× em passos de raiz de 2 — no SVG cada mudança obrigava a
  // reescrever todos os pontos em texto, e por isso os passos eram poucos e só
  // para cima. No canvas é uma multiplicação no desenho, então o passo pode ser
  // fino e nos dois sentidos, que é como se usa na bancada.
  const AMP_MIN = 0.5, AMP_MAX = 16, PASSO = Math.SQRT2;
  let amp = 1;
  const vAmp = document.getElementById("b-amp-valor");

  function mostraAmp() {
    if (vAmp) vAmp.textContent = amp.toFixed(1).replace(".", ",") + "×";
    const menos = document.getElementById("b-amp-menos");
    const mais = document.getElementById("b-amp-mais");
    // Botão que não tem mais para onde ir fica desabilitado em vez de não
    // responder: clicar e nada acontecer lê-se como travado.
    if (menos) menos.disabled = amp <= AMP_MIN + 1e-9;
    if (mais) mais.disabled = amp >= AMP_MAX - 1e-9;
  }

  function mudaAmp(fator) {
    const novo = Math.min(AMP_MAX, Math.max(AMP_MIN, amp * fator));
    if (Math.abs(novo - amp) < 1e-9) return;
    amp = novo;
    mostraAmp();
    desenhaTracos();          // só o traço muda; alinhamento e números não
  }

  const btMenos = document.getElementById("b-amp-menos");
  const btMais = document.getElementById("b-amp-mais");
  if (btMenos) btMenos.onclick = () => mudaAmp(1 / PASSO);
  if (btMais) btMais.onclick = () => mudaAmp(PASSO);
  mostraAmp();

  // A roda com Ctrl sobre o traço também ajusta — é o gesto que quem vem de
  // outro programa de cromatograma tenta primeiro. `passive:false` porque
  // precisamos impedir o zoom da página.
  document.addEventListener("wheel", ev => {
    if (!ev.ctrlKey) return;
    const alvo = ev.target.closest && ev.target.closest(".tela-tr");
    if (!alvo) return;
    ev.preventDefault();
    mudaAmp(ev.deltaY < 0 ? PASSO : 1 / PASSO);
  }, { passive: false });

  // O canvas depende do tamanho em pixels: mudou a janela, remede e redesenha.
  let redim;
  window.addEventListener("resize", () => {
    clearTimeout(redim);
    redim = setTimeout(() => { ajustarCanvas(); desenhaTracos(); }, 120);
  });

  // Sair com edição pendente perde trabalho em silêncio — o mesmo gênero de
  // defeito das "33 de 40 amostras". O aviso do navegador é a única trava real.
  window.addEventListener("beforeunload", ev => {
    if (!est.exportado) { ev.preventDefault(); ev.returnValue = ""; }
  });

  function render() {
    if (!est.d) return;
    desenhaAln(); desenhaTracos();
    $("#rascunho").style.display = est.exportado ? "none" : "flex";
    $("#m-pb").textContent = consenso().filter(b => b !== "-").length;
  }
})();
