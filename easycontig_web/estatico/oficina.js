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

  const est = { d: null, seq: [], sel: null, pilha: [], removidas: 0, exportado: true };

  fetch(raiz.dataset.traco, { headers: { accept: "application/json" } })
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(d => {
      if (!d.disponivel) { semTraco(d.motivo); return; }
      est.d = d;
      est.seq = d.leituras.map(l => l.alinhada.split(""));
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
      + motivo + ". As medidas ao lado continuam valendo.</p>";
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
          <span class="nome">${l.nome}</span>
          <span class="meta">sentido ${l.sentido}${l.primer ? " · primer " + l.primer : ""}
            ${l.q_medio ? " · Q" + l.q_medio + " " + l.q_rotulo : ""}</span>
          <span class="leg">
            <s><i style="background:var(--verde)"></i>A</s>
            <s><i style="background:var(--azul)"></i>C</s>
            <s><i style="background:#C6CBE0"></i>G</s>
            <s><i style="background:var(--vermelho)"></i>T</s>
          </span>
        </div>
        <div class="tela-tr">
          <svg id="svg-${i}" preserveAspectRatio="none"
               aria-label="Cromatograma de ${l.nome}"></svg>
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
          ? (l.primer || l.sentido + (i + 1))
          : "só do " + l.sentido
      }</button>`).join("")
      + `<button class="ac perigo" id="b-todas" type="button" disabled>${
          est.d.leituras.length > 2 ? "de todas" : "dos dois"}</button>`;
    bs.querySelectorAll("[data-l]").forEach(b =>
      b.onclick = () => remover([+b.dataset.l]));
    document.getElementById("b-todas").onclick = () =>
      remover(est.d.leituras.map((_, i) => i));
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
        + ` data-col="${col}">${b === "-" ? "·" : b}</span>`;
    }).join("");
    let h = "";
    est.d.leituras.forEach((l, i) => {
      h += `<div class="aln-l"><span class="aln-n">${l.sentido === "F" ? "→" : "←"} `
         + `${l.nome}</span><span>${linha(est.seq[i])}</span></div>`;
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

  function desenhaTracos() {
    const [i0, i1] = janela(), mm = mismatches();
    est.d.leituras.forEach((l, i) => {
      const svg = document.getElementById("svg-" + i);
      if (!svg) return;
      svg.setAttribute("viewBox", `${i0} 0 ${i1 - i0} 132`);
      let s = "";
      mm.filter(c => c >= i0 && c < i1).forEach(c =>
        s += `<rect class="faixa-mm" x="${c - .5}" y="0" width="1" height="104"/>`);
      if (est.sel !== null && est.sel >= i0 && est.sel < i1)
        s += `<rect class="faixa-sel" x="${est.sel - .5}" y="0" width="1" height="104"`
           + ` vector-effect="non-scaling-stroke"/>`;
      "ACGT".split("").forEach(ch =>
        (l.canais[ch] || "").split("|").filter(Boolean).forEach(p =>
          s += `<polyline class="tr ${ch}" points="${p}"/>`));
      svg.innerHTML = s;

      // As letras NÃO vão dentro do SVG: com `preserveAspectRatio="none"` o
      // eixo x é esticado ~13× e cada letra virava um traço horizontal
      // ilegível. Numa faixa HTML posicionada em %, elas saem redondas.
      const faixa = document.getElementById("bases-" + i);
      faixa.innerHTML = l.bases.filter(([c]) => c >= i0 && c < i1)
        .map(([col, base]) => {
          const morta = est.seq[i][Math.round(col)] === "-";
          return `<span class="bl ${morta ? "morta " : ""}${cls(base)}"`
               + ` style="left:${(col - i0 + .5) / (i1 - i0) * 100}%"`
               + ` data-col="${Math.round(col)}">${morta ? "·" : base}</span>`;
        }).join("");
      faixa.querySelectorAll(".bl").forEach(e =>
        e.onclick = () => selecionar(+e.dataset.col));

      // Painel vazio lê como "não montou", e é falso: a leitura simplesmente não
      // alcança esta região do contig. Dizer isso é o conserto — foi o que fez a
      // leitura reversa parecer quebrada quando a janela abria antes do início
      // dela.
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
      svg.onclick = ev => {
        const r = svg.getBoundingClientRect();
        selecionar(Math.round(i0 + (ev.clientX - r.left) / r.width * (i1 - i0)));
      };
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
    selecionar(est.sel);
  }
  $("#b-undo").onclick = () => {
    const s = est.pilha.pop(); if (!s) return;
    est.seq = s.seq; est.removidas = s.rem; est.exportado = false;
    $("#b-undo").disabled = !est.pilha.length;
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
    render();
  }
  $("#b-fasta").onclick = () => exportar("fasta");
  $("#b-txt").onclick = () => exportar("txt");

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
