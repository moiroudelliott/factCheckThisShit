// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — résumé d'un débat en un écran
//
// Une page au format paysage (1600 × 900, mise à l'échelle de la fenêtre,
// sans défilement) : l'exactitude de chaque débatteur, les thèmes, les
// chiffres contestés et les sources. Met en forme sessions/<id>.fiche.json
// (publish_session.py) — rien n'est recalculé ici.
// ══════════════════════════════════════════════════════════════════════════════

(function () {
  'use strict';

  const W = 1600, H = 900, MARGIN = 16;
  const ID_RE = /^[\w-]{11}$/;
  const TRANCHES = ['vrai', 'partiellement_vrai', 'trompeur', 'faux'];
  const VERDICTS = {
    vrai: { one: 'vraie', many: 'vraies', color: 'var(--v-vrai)' },
    partiellement_vrai: { one: 'partielle', many: 'partielles', color: 'var(--v-partiel)' },
    trompeur: { one: 'trompeuse', many: 'trompeuses', color: 'var(--v-trompeur)' },
    faux: { one: 'fausse', many: 'fausses', color: 'var(--v-faux)' },
  };
  const canvas = document.getElementById('r-canvas');

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const fmtT = (t) => {
    t = Math.max(0, Math.floor(Number(t) || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
    return h ? `${h}:${pad(m)}:${pad(t % 60)}` : `${m}:${pad(t % 60)}`;
  };
  const fmtDur = (s) => {
    const m = Math.round((Number(s) || 0) / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${pad(m % 60)}` : `${m} min`;
  };
  const fmtDate = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || '')
    ? new Date(`${iso}T12:00:00`).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }) : '');

  // Le canevas garde ses proportions et tient toujours dans la fenêtre
  function fit() {
    const s = Math.min((innerWidth - 2 * MARGIN) / W, (innerHeight - 2 * MARGIN) / H);
    canvas.style.setProperty('--r-scale', String(Math.max(0.1, s)));
    document.body.classList.toggle('r-portrait', innerHeight > innerWidth * 1.1);
  }
  addEventListener('resize', fit);
  fit();

  init();

  async function init() {
    const id = new URLSearchParams(location.search).get('s') || '';
    let index = { sessions: [] };
    try {
      index = await fetch('sessions/index.json', { cache: 'no-cache' }).then((r) => r.json());
    } catch (_) { /* liste vide */ }
    const list = (index.sessions || []).filter((s) => ID_RE.test(s.id || '') && s.fiche);
    const entry = list.find((s) => s.id === id) || list[list.length - 1];
    if (!entry) {
      canvas.innerHTML = '<p class="r-loading">Aucun débat publié.</p>';
      return;
    }
    document.getElementById('r-back').href = `fiche.html?s=${encodeURIComponent(entry.id)}`;
    try {
      const r = await fetch(`sessions/${entry.id}.fiche.json`, { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      render(await r.json(), entry.id);
    } catch (_) {
      canvas.innerHTML = '<p class="r-loading">Impossible de charger ce débat.</p>';
    }
  }

  // « +19,3 % entre 2017 et 2024, selon l'Insee » → gros : « +19,3 % »,
  // petit : le reste. Une fourchette reste entière (« 300 000 à 400 000 »).
  const NUM = String.raw`[+−-]?\d[\d\s  .,]*`;
  const UNIT = String.raw`(?:\s?(?:%|€|M€|Md€|milliards?|millions?|euros?|ans|an|points?|postes?|élèves?))?`;
  const FIGURE_RE = new RegExp(String.raw`^((?:environ|près de|plus de|moins de)\s)?(${NUM}${UNIT}(?:\s?(?:à|-|–)\s?${NUM}${UNIT})?)(.*)$`, 'i');
  function splitFigure(text) {
    const m = String(text || '').trim().match(FIGURE_RE);
    if (!m) return { big: '', rest: text };
    return { big: `${m[1] || ''}${m[2]}`.trim(), rest: m[3].replace(/^[\s,;:]+/, '') };
  }

  function bar(counts) {
    const total = TRANCHES.reduce((n, v) => n + (counts?.[v] || 0), 0);
    return total ? `<div class="r-bar">${TRANCHES.filter((v) => counts[v])
      .map((v) => `<i style="flex-grow:${Number(counts[v])};background:${VERDICTS[v].color}"></i>`).join('')}</div>`
      : '<div class="r-bar r-bar--empty"></div>';
  }

  function debater(d, min) {
    const c = d.verdicts || {};
    const idx = d.suffisant && d.exactitude != null
      ? `<p class="r-idx"><b>${Number(d.exactitude)}<small>%</small></b><span>± ${Number(d.marge)} pts</span></p>
         <p class="r-idx-l">Indice d'exactitude · ${Number(d.tranches)} verdicts tranchés</p>`
      : `<p class="r-idx r-idx--na"><span>Indice non calculé</span></p>
         <p class="r-idx-l">Pas d'indice : ${Number(d.tranches)} verdict${d.tranches > 1 ? 's' : ''} tranché${d.tranches > 1 ? 's' : ''}, ${min} requis</p>`;
    const counts = TRANCHES.map((v) => `<div><b style="color:${VERDICTS[v].color}">${Number(c[v] || 0)}</b>
      <span>${c[v] > 1 ? VERDICTS[v].many : VERDICTS[v].one}</span></div>`).join('');
    const other = (c.non_recoupe || 0) + (c.non_verifiable || 0);
    return `<section class="r-deb">
      <p class="r-name">${esc(d.nom)}</p>
      <p class="r-sub">${d.temps_parole ? `${fmtDur(d.temps_parole)} de parole · ` : ''}${Number(d.affirmations)} affirmations vérifiées${other ? ` · ${other} non tranchée${other > 1 ? 's' : ''}` : ''}</p>
      ${idx}${bar(c)}<div class="r-counts">${counts}</div>
    </section>`;
  }

  function comparisons(f) {
    const comps = f.comparaisons || [];
    if (!comps.length) return '';
    return comps.map((c) => `<span><strong>${esc(c.a)} / ${esc(c.b)}</strong> : ${Number(c.ecart)} pts, ${c.significatif
      ? "<em class=\"r-sig\">au-delà de la marge d'erreur</em>" : "dans la marge d'erreur"}</span>`).join('');
  }

  function themes(f) {
    const frise = f.frise || [];
    const total = f.duree || (frise.length ? frise[frise.length - 1].fin : 0);
    const label = Object.fromEntries((f.themes || []).map((t) => [t.id, t.label]));
    const top = (f.themes || []).filter((t) => t.id !== 'autre' && t.duree >= 60).slice(0, 6);
    return `<section class="r-block">
      <p class="r-h">Le débat, thème par thème</p>
      <div class="r-frise">${frise.map((s) => `<i class="th-${esc(s.theme)}" style="flex-grow:${Math.max(1, s.fin - s.debut)}"
        title="${esc(label[s.theme] || s.theme)}"></i>`).join('')}</div>
      <div class="r-axis"><span>0:00</span><span>${fmtT(total / 2)}</span><span>${fmtT(total)}</span></div>
      <ul class="r-themes">${top.map((t) => `<li class="th-${esc(t.id)}"><i class="r-sw"></i><span>${esc(t.label)}</span>
        <b>${fmtDur(t.duree)}</b></li>`).join('')}</ul>
    </section>`;
  }

  // Les chiffres contestés les plus lisibles en gros (les plus courts),
  // un par débatteur d'abord
  function figures(f) {
    const rows = (f.redaction?.chiffres || []).map((c) => ({ ...c, said: splitFigure(c.annonce), found: splitFigure(c.selon_source) }))
      .filter((c) => c.said.big && c.found.big)
      .sort((a, b) => (a.said.big.length + a.found.big.length) - (b.said.big.length + b.found.big.length));
    const picked = [];
    for (const c of rows) if (picked.length < 2 && !picked.some((p) => p.qui === c.qui)) picked.push(c);
    for (const c of rows) if (picked.length < 2 && !picked.includes(c)) picked.push(c);
    if (!picked.length) return sources(f, 8);
    // Le propos d'origine : sans lui, « 10 ans → 20 à 25 ans » ne dit pas de quoi on parle
    const claims = new Map((f.affirmations || []).map((a) => [a.id, a.texte]));
    return `<section class="r-block">
      <p class="r-h">Chiffres contestés</p>
      <div class="r-nums"><span class="r-num-l">Annoncé</span><span></span><span class="r-num-l">Selon la source</span>
        ${picked.map((c) => `<p class="r-num-who"><strong>${esc(c.qui)}</strong> — <em>« ${esc(claims.get(c.id) || c.annonce)} »</em></p>
          <div><b class="r-num-big r-num-said" title="${esc(c.annonce)}">${esc(c.said.big)}</b><span class="r-num-rest">${esc(c.said.rest)}</span></div>
          <span class="r-num-arrow" aria-hidden="true">→</span>
          <div><b class="r-num-big" title="${esc(c.selon_source)}">${esc(c.found.big)}</b><span class="r-num-rest">${esc(c.found.rest)}</span></div>`).join('')}</div>
    </section>`;
  }

  function sources(f, n) {
    const src = (f.sources || []).slice(0, n);
    return src.length ? `<section class="r-block">
      <p class="r-h">Sources les plus citées</p>
      <ol class="r-sources">${src.map((s) => `<li><span>${esc(s.nom)}</span><b>${Number(s.n)}</b></li>`).join('')}</ol>
    </section>` : '';
  }

  function render(f, id) {
    document.title = `${f.titre || 'Débat'} — résumé SOURCÉ`;
    const t = f.totaux || {};
    const v = t.verdicts || {};
    const debs = f.debatteurs || [];
    const date = fmtDate(f.date);
    const topSources = (f.sources || []).slice(0, 5).map((s) => s.nom).join(', ');
    canvas.style.setProperty('--n', String(Math.max(1, debs.length)));
    canvas.innerHTML = `
      <header class="r-head">
        <div>
          <p class="r-kicker"><span class="r-brand">SOURC<span>É</span></span><span>Fiche du débat</span>${date ? `<span>${esc(date)}</span>` : ''}</p>
          <h1 class="r-title">${esc(f.titre || 'Débat').replace(/(\w)-(\w)/g, '$1\u2011$2')}</h1>
        </div>
        <div class="r-kpis">
          <div class="r-kpi"><b>${Number(t.affirmations || 0)}</b><span>affirmations<br>vérifiées</span></div>
          <div class="r-kpi"><b style="color:var(--v-faux)">${Number(v.faux || 0) + Number(v.trompeur || 0)}</b><span>fausses ou<br>trompeuses</span></div>
          <div class="r-kpi"><b>${esc(fmtDur(f.duree))}</b><span>de<br>débat</span></div>
        </div>
      </header>
      <div class="r-debs">${debs.map((d) => debater(d, f.min_verdicts || 15)).join('')}</div>
      <p class="r-comps">${comparisons(f)}</p>
      <div class="r-bottom">${themes(f)}${figures(f)}</div>
      <footer class="r-foot">
        <span>Indice d'exactitude : vrai 1, partiel ½, trompeur et faux 0, sur les verdicts tranchés ; ± : marge d'erreur à 95 %.
          Calculé par le programme à partir de verdicts automatiques, qui peuvent se tromper.${topSources ? ` Sources les plus citées : ${esc(topSources)}.` : ''}</span>
        <span class="r-url">source.codeminds.fr/fiche.html?s=${esc(id)}</span>
      </footer>`;
  }
})();
