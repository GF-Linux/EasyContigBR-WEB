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

  const est = { d: null, seq: [], sel: null, pilha: [], removidas: 0, exportado: true };

  // Rascunho guardado no navegador, por amostra. O consenso só vira definitivo
  // na exportação (ADR 0052) — isto não muda essa regra, só evita que recarregar
  // a página perca a correção. O `.ab1` em disco continua intocado, e o rascunho
  // é do navegador de quem editou, não do servidor.
  const CHAVE = "easycontig:rascunho:" + location.pathname;

  function guardarRascunho() {
    try {
      if (!est.removidas) { localStorage.removeItem(CHAVE); return; }
      localStorage.setItem(CHAVE, JSON.stringify({
        seq: est.seq.map(s => s.join("")), removidas: est.removidas,
        quando: new Date().toISOString(),
      }));
    } catch (e) { /* modo anônimo ou cota cheia: seguir sem rascunho */ }
  }

  function lerRascunho() {
    try { return JSON.parse(localStorage.getItem(CHAVE) || "null"); }
    catch (e) { return null; }
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
  function consenso() {
    const out = [];
    for (let c = 0; c < est.d.largura; c++) {
      const col = est.seq.map(s => s[c]).filter(b => b && b !== "-" && b !== "N");
      out.push(col.length ? col[0] : "-");
    }
    return out;
  }
  function mismatches() {
    const m = [];
    for (let c = 0; c < est.d.largura; c++) {
      const col = est.seq.map(s => s[c]).filter(b => b && b !== "-" && b !== "N");
      if (col.length > 1 && col.some(b => b !== col[0])) m.push(c);
    }
    return m;
  }
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
          <svg id="svg-${i}" preserveAspectRatio="none"
               aria-label="Cromatograma de ${esc(l.nome)}"></svg>
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
  function janela() {
    const meio = est.sel === null ? Math.floor(JAN / 2) : est.sel;
    let i = Math.max(0, Math.min(meio - Math.floor(JAN / 2), est.d.largura - JAN));
    return [Math.max(0, i), Math.min(est.d.largura, Math.max(0, i) + JAN)];
  }

  function desenhaAln() {
    const [i0, i1] = janela(), mm = mismatches(), c = consenso();
    const linha = (arr, extra) => arr.slice(i0, i1).map((b, k) => {
      const col = i0 + k;
      return `<span class="b ${cls(b)}${mm.includes(col) ? " mm" : ""}`
        + `${est.sel === col ? " sel" : ""}${b === "-" ? " morta" : ""}"`
        + ` data-col="${col}">${b === "-" ? "·" : esc(b)}</span>`;
    }).join("");
    let h = "";
    est.d.leituras.forEach((l, i) => {
      h += `<div class="aln-l"><span class="aln-n">${l.sentido === "F" ? "→" : "←"} `
         + `${esc(l.nome)}</span><span>${linha(est.seq[i])}</span></div>`;
    });
    h += `<div class="aln-l"><span class="aln-n regua">régua (×10)</span><span class="regua">`
       + Array.from({ length: i1 - i0 }, (_, k) =>
           `<span class="b">${(i0 + k + 1) % 10 === 0 ? "|" : "·"}</span>`).join("")
       + `</span></div>`;
    h += `<div class="aln-l"><span class="aln-n forte">Contig</span><span>${linha(c)}</span></div>`;
    const el = $("#aln"); el.innerHTML = h;
    el.querySelectorAll(".b[data-col]").forEach(s =>
      s.onclick = () => selecionar(+s.dataset.col));
    $("#m-mm").textContent = mm.length;
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
      const svg = document.getElementById("svg-" + i);
      if (!svg) return;
      svg.innerHTML = '<g class="faixas"></g><g class="linhas"></g>';
      svg.onclick = ev => {
        const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
        selecionar(Math.round(vb.x + (ev.clientX - r.left) / r.width * vb.width));
      };
    });
  }

  /* Recorta os pontos da JANELA visível. É a correção que resolveu o atraso.
     Duas tentativas antes desta não ajudaram, e vale registrar por quê:
       — desenhar a polilinha uma vez só e trocar o viewBox: 33 → 36 ms, nada.
         Reescrever o atributo não era o custo.
       — o DOM do alinhamento: medido em 0,4 ms, não era ele.
     O custo era RASTERIZAR: 16.940 pontos por cromatograma continuavam no
     documento e eram repintados inteiros a cada mudança de janela, mesmo os
     ~95% fora da vista. Recortando, sobram ~500 por canal. */
  function recorte(plano, i0, i1) {
    const partes = [];
    let atual = [];
    for (let k = 0; k < plano.length; k += 2) {
      const x = plano[k];
      if (x === null) {                       // quebra vinda do gap
        if (atual.length > 1) partes.push(atual.join(" "));
        atual = [];
        continue;
      }
      if (x < i0 - 1 || x > i1 + 1) {
        if (atual.length > 1) partes.push(atual.join(" "));
        atual = [];
        continue;
      }
      // y = 100 na base; ampliar é puxar o pico para cima sem sair do quadro
      const y = plano[k + 1];
      atual.push(x + "," + (amp === 1 ? y : Math.max(6, 100 - (100 - y) * amp).toFixed(1)));
    }
    if (atual.length > 1) partes.push(atual.join(" "));
    return partes;
  }

  function desenhaTracos() {
    const [i0, i1] = janela(), mm = mismatches();
    est.d.leituras.forEach((l, i) => {
      const svg = document.getElementById("svg-" + i);
      if (!svg) return;
      svg.setAttribute("viewBox", `${i0} 0 ${i1 - i0} 112`);

      svg.querySelector(".linhas").innerHTML = "ACGT".split("").map(ch =>
        recorte(l.canais[ch] || [], i0, i1)
          .map(pt => `<polyline class="tr ${ch}" points="${pt}"/>`).join("")
      ).join("");

      let s = mm.filter(c => c >= i0 && c < i1)
        .map(c => `<rect class="faixa-mm" x="${c - .5}" y="0" width="1" height="104"/>`)
        .join("");
      if (est.sel !== null && est.sel >= i0 && est.sel < i1) {
        s += `<rect class="faixa-sel" x="${est.sel - .5}" y="0" width="1" height="104"`
           + ` vector-effect="non-scaling-stroke"/>`;
      }
      svg.querySelector(".faixas").innerHTML = s;

      // As letras ficam FORA do SVG: com `preserveAspectRatio="none"` o eixo x é
      // esticado ~13× e cada letra virava um traço horizontal ilegível.
      const faixa = document.getElementById("bases-" + i);
      faixa.innerHTML = l.bases.filter(([c]) => c >= i0 && c < i1)
        .map(([col, base]) => {
          const morta = est.seq[i][Math.round(col)] === "-";
          return `<span class="bl ${morta ? "morta " : ""}${cls(base)}"`
               + ` style="left:${(col - i0 + .5) / (i1 - i0) * 100}%"`
               + ` data-col="${Math.round(col)}">${morta ? "·" : esc(base)}</span>`;
        }).join("");
      faixa.querySelectorAll(".bl").forEach(e =>
        e.onclick = () => selecionar(+e.dataset.col));

      // Painel vazio lê como "não montou", e é falso: a leitura simplesmente
      // não alcança esta região do contig.
      const nada = !l.bases.some(([c]) => c >= i0 && c < i1);
      let aviso = svg.parentElement.querySelector(".fora");
      if (nada && !aviso) {
        aviso = document.createElement("div");
        aviso.className = "fora";
        aviso.textContent = "esta leitura não cobre esta região do contig";
        svg.parentElement.appendChild(aviso);
      } else if (!nada && aviso) {
        aviso.remove();
      }
    });
  }

  // ── seleção: sempre nas DUAS leituras (é o acoplamento pedido) ────────────
  function selecionar(col) {
    est.sel = Math.max(0, Math.min(col, est.d.largura - 1));
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
    est.exportado = false;
    guardarRascunho();
    selecionar(est.sel);
  }
  $("#b-undo").onclick = () => {
    const s = est.pilha.pop(); if (!s) return;
    est.seq = s.seq; est.removidas = s.rem; est.exportado = false;
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
    try { localStorage.removeItem(CHAVE); } catch (e) {}
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
  let amp = 1;
  const btAmp = document.getElementById("b-amp");
  if (btAmp) {
    btAmp.onclick = () => {
      amp = amp >= 8 ? 1 : amp * 2;
      btAmp.textContent = "amp " + amp + "×";
      render();
    };
  }

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
