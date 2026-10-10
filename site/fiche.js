// ══════════════════════════════════════════════════════════════════════════════
// SOURCÉ — fiche d'un débat
//
// Ce que chaque débatteur a affirmé, ce qui était exact, sur quels thèmes —
// lisible sans regarder la vidéo. Met en forme sessions/<id>.fiche.json,
// produit par publish_session.py (server/summary.py) : tous les chiffres y
// sont déjà calculés, rien n'est recalculé ni jugé ici.
// ══════════════════════════════════════════════════════════════════════════════

(function () {
  'use strict';

  const ID_RE = /^[\w-]{11}$/;
  const $ = (id) => document.getElementById(id);

  const VERDICTS = {
    vrai: { label: 'Vrai', short: 'vrai', cls: 'v-vrai', color: 'var(--v-vrai)' },
    partiellement_vrai: { label: 'Partiellement vrai', short: 'partiel', cls: 'v-partiel', color: 'var(--v-partiel)' },
    trompeur: { label: 'Trompeur', short: 'trompeur', cls: 'v-trompeur', color: 'var(--v-trompeur)' },
    faux: { label: 'Faux', short: 'faux', cls: 'v-faux', color: 'var(--v-faux)' },
    non_recoupe: { label: 'Non recoupé', short: 'non recoupé', cls: 'v-nr', color: 'var(--v-nr)' },
    non_verifiable: { label: 'Non vérifiable', short: 'non vérifiable', cls: 'v-nv', color: 'var(--v-nv)' },
  };
  const TRANCHES = ['vrai', 'partiellement_vrai', 'trompeur', 'faux'];

  let fiche = null, sid = '', affById = new Map(), themeLabel = {};

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const fmtT = (t) => {
    t = Math.max(0, Math.floor(Number(t) || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  };
  const fmtDur = (s) => {
    const m = Math.round((Number(s) || 0) / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${pad(m % 60)}` : `${m} min`;
  };
  const fmtDate = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || '')
    ? new Date(`${iso}T12:00:00`).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }) : '');
  const plural = (n, word, many = `${word}s`) => `${n} ${n > 1 ? many : word}`;
  // Lien vers la relecture, quelques secondes avant le propos
  const replayUrl = (t) => `relecture.html?s=${encodeURIComponent(sid)}${t != null ? `&t=${Math.max(0, Math.floor(t - 3))}` : ''}`;
  const tsLink = (t) => (t != null ? `<a class="f-ts" href="${replayUrl(t)}" title="Revoir ce passage dans la vidéo">▶ ${fmtT(t)}</a>` : '');
  const badge = (v) => (VERDICTS[v] ? `<span class="verdict ${VERDICTS[v].cls}">${VERDICTS[v].label}</span>` : '');
  const swatch = (theme) => `<i class="sw th-${esc(theme)}" aria-hidden="true"></i>`;
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');

  function bar(counts, small = false) {
    const total = TRANCHES.reduce((n, v) => n + (counts?.[v] || 0), 0);
    if (!total) return '';
    const label = TRANCHES.filter((v) => counts[v]).map((v) => `${counts[v]} ${VERDICTS[v].short}`).join(', ');
    return `<div class="f-bar${small ? ' sm' : ''}" role="img" aria-label="${esc(label)}">${TRANCHES.filter((v) => counts[v])
      .map((v) => `<i style="flex-grow:${Number(counts[v])};background:${VERDICTS[v].color}"></i>`).join('')}</div>`;
  }

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    let index = { sessions: [] };
    try {
      index = await fetch('sessions/index.json', { cache: 'no-cache' }).then((r) => r.json());
    } catch (_) { /* liste vide */ }
    const list = (index.sessions || []).filter((s) => ID_RE.test(s.id || '') && s.fiche);
    sid = new URLSearchParams(location.search).get('s') || '';
    if (!list.some((s) => s.id === sid)) {
      renderList(list, sid ? "Cette fiche n'existe pas ou plus." : '');
      return;
    }
    try {
      // no-cache : une fiche régénérée sous le même identifiant remplace l'ancienne
      const r = await fetch(`sessions/${sid}.fiche.json`, { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      fiche = await r.json();
    } catch (_) {
      renderList(list, 'Impossible de charger cette fiche.');
      return;
    }
    render();
  }

  // ── Liste des fiches ──

  function renderList(list, error) {
    $('fiche-list').hidden = false;
    if (error) {
      $('list-error').textContent = error;
      $('list-error').hidden = false;
    }
    if (!list.length) {
      $('fiches').outerHTML = '<p class="muted">Aucune fiche publiée pour le moment.</p>';
      return;
    }
    $('fiches').innerHTML = list.map((s) => {
      const meta = [fmtDate(s.debat), s.duration ? fmtDur(s.duration) : '',
        s.affirmations ? plural(s.affirmations, 'affirmation vérifiée', 'affirmations vérifiées') : ''].filter(Boolean);
      return `<li><a href="fiche.html?s=${encodeURIComponent(s.id)}"><span class="debate-title">${esc(s.title || s.id)}</span>
        <span class="debate-meta">${esc(meta.join(' · '))}</span></a></li>`;
    }).join('');
  }

  // ── Fiche ──

  function render() {
    for (const a of fiche.affirmations || []) affById.set(a.id, a);
    for (const t of fiche.themes || []) themeLabel[t.id] = t.label;
    document.title = `${fiche.titre || 'Débat'} — fiche SOURCÉ`;
    $('fiche').hidden = false;
    renderHead();
    renderDebaters();
    renderMoments();
    renderThemes();
    renderChiffres();
    renderContradictions();
    renderPropositions();
    renderAffirmations();
    renderSources();
    renderToc();
    // Lien direct vers une section ou une affirmation (#a-…) : le contenu
    // vient d'être créé, le navigateur n'a pas pu s'y placer
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }

  function renderHead() {
    const t = fiche.totaux || {};
    $('f-title').textContent = fiche.titre || 'Débat';
    $('f-meta').textContent = [fmtDate(fiche.date), fiche.duree ? fmtDur(fiche.duree) : '',
      t.affirmations ? plural(t.affirmations, 'affirmation vérifiée', 'affirmations vérifiées') : ''].filter(Boolean).join(' · ');
    $('f-replay').href = replayUrl(null);
    $('f-resume-link').href = `resume.html?s=${encodeURIComponent(sid)}`;
    $('f-generated').textContent = fmtDate(fiche.genere) || fiche.genere || '';
    $('f-min').textContent = fiche.min_verdicts || 15;
    const resume = fiche.redaction?.resume;
    if (resume) {
      $('f-resume').innerHTML = `<p class="lede">${esc(resume)}</p>
        <p class="f-ai">Résumé rédigé automatiquement (Mistral) à partir des affirmations et de leurs verdicts.</p>`;
    }
  }

  function renderToc() {
    const sections = [['debatteurs', 'Débatteurs'], ['moments', 'Moments forts'], ['themes', 'Thèmes'],
      ['chiffres', 'Chiffres'], ['contradictions', 'Contradictions'], ['propositions', 'Propositions'],
      ['sources-utilisees', 'Sources'], ['affirmations', 'Toutes les affirmations']];
    $('f-toc').innerHTML = sections.filter(([id]) => !$(id).hidden)
      .map(([id, label]) => `<a href="#${id}">${label}</a>`).join('');
  }

  function renderDebaters() {
    const min = fiche.min_verdicts || 15;
    $('f-debs').innerHTML = (fiche.debatteurs || []).map((d) => {
      const c = d.verdicts || {};
      const idx = d.suffisant && d.exactitude != null
        ? `<div class="f-idx"><span class="f-idx-n">${Number(d.exactitude)} %</span><span class="f-idx-m">± ${Number(d.marge)} points</span></div>
           <p class="f-idx-l">indice d'exactitude, sur ${plural(d.tranches, 'verdict tranché', 'verdicts tranchés')}</p>`
        : `<p class="f-idx-na">Trop peu de verdicts tranchés (${Number(d.tranches)}, il en faut ${min}) pour un indice d'exactitude.</p>`;
      const counts = TRANCHES.filter((v) => c[v])
        .map((v) => `<li><i class="sw" style="background:${VERDICTS[v].color}"></i>${Number(c[v])} ${VERDICTS[v].short}</li>`).join('');
      const other = [c.non_recoupe ? `${c.non_recoupe} non recoupée${c.non_recoupe > 1 ? 's' : ''}` : '',
        c.non_verifiable ? `${c.non_verifiable} non vérifiable${c.non_verifiable > 1 ? 's' : ''}` : ''].filter(Boolean);
      const meta = [d.temps_parole ? `${fmtDur(d.temps_parole)} de parole` : '',
        plural(d.affirmations, 'affirmation vérifiée', 'affirmations vérifiées')].filter(Boolean).join(' · ');
      return `<article class="f-deb">
        <h3>${esc(d.nom)}</h3>
        <p class="f-deb-meta">${esc(meta)}</p>
        ${idx}${bar(c)}<ul class="f-counts">${counts}</ul>
        ${other.length ? `<p class="f-deb-other">Non tranchées : ${other.join(', ')}</p>` : ''}
        <a class="f-deb-link" href="#affirmations" data-qui="${esc(d.nom)}">Voir ses affirmations</a>
      </article>`;
    }).join('');
    $('f-debs').addEventListener('click', (e) => {
      const a = e.target.closest('[data-qui]');
      if (a) setFilters({ qui: a.dataset.qui });
    });
    $('f-comps').innerHTML = (fiche.comparaisons || []).map((c) => `<li><strong>${esc(c.a)}</strong> et <strong>${esc(c.b)}</strong> :
      écart de ${plural(c.ecart, 'point')} — ${c.significatif ? "au-delà de la marge d'erreur" : "dans la marge d'erreur : pas de différence établie"}.</li>`).join('');
    const notes = [];
    if (fiche.non_identifies) {
      const one = fiche.non_identifies === 1;
      notes.push(`${plural(fiche.non_identifies, 'affirmation n’a pas pu être attribuée', 'affirmations n’ont pas pu être attribuées')} à un débatteur (voix non reconnue) : ${one ? 'elle ne compte' : 'elles ne comptent'} pour personne.`);
    }
    const hosts = fiche.animateurs || [];
    if (hosts.length) {
      const many = hosts.length > 1;
      // « A, B et C »
      const names = many ? `${hosts.slice(0, -1).join(', ')} et ${hosts[hosts.length - 1]}` : hosts[0];
      notes.push(`${names} ${many ? 'mènent' : 'mène'} le débat : ${many ? 'leurs' : 'ses'} propos ne comptent pas dans la fiche.`);
    }
    if (notes.length) {
      $('f-unknown').textContent = notes.join(' ');
      $('f-unknown').hidden = false;
    }
  }

  function affCard(m) {
    const a = affById.get(m.id);
    if (!a) return '';
    const src = safeUrl(a.url) ? ` <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.source || 'source')} ↗</a>` : '';
    return `<article class="f-card" style="--vc:${(VERDICTS[a.verdict] || {}).color || 'var(--rule)'}">
      <p class="f-card-head">${badge(a.verdict)}${tsLink(a.t)}</p>
      <blockquote>« ${esc(a.texte)} »</blockquote>
      ${m.pourquoi ? `<p class="f-why">${esc(m.pourquoi)}</p>` : ''}
      <p class="f-expl">${esc(a.explication)}${src}</p>
    </article>`;
  }

  function renderMoments() {
    const moments = fiche.redaction?.moments || [];
    if (!moments.length) return;
    $('moments').hidden = false;
    $('f-moments').innerHTML = (fiche.debatteurs || []).map((d) => {
      const mine = moments.filter((m) => m.qui === d.nom);
      return mine.length ? `<h3 class="f-group">${esc(d.nom)}</h3><div class="f-cards">${mine.map(affCard).join('')}</div>` : '';
    }).join('');
  }

  function renderThemes() {
    const frise = fiche.frise || [];
    const total = fiche.duree || (frise.length ? frise[frise.length - 1].fin : 0);
    $('f-frise').innerHTML = frise.map((s, i) => {
      const label = `${themeLabel[s.theme] || s.theme}, ${fmtT(s.debut)} – ${fmtT(s.fin)}`;
      return `<a role="listitem" class="th-${esc(s.theme)}" style="flex-grow:${Math.max(1, s.fin - s.debut)}" href="#ch-${i}" title="${esc(label)}" aria-label="${esc(label)}"></a>`;
    }).join('');
    $('f-axis').innerHTML = total ? `<span>0:00</span><span>${fmtT(total / 2)}</span><span>${fmtT(total)}</span>` : '';
    const seen = [...new Set(frise.map((s) => s.theme))];
    $('f-legend').innerHTML = seen.map((t) => `<span>${swatch(t)}${esc(themeLabel[t] || t)}</span>`).join('');

    // Tableau débatteur × thème
    const names = (fiche.debatteurs || []).map((d) => d.nom);
    const rows = (fiche.themes || []).filter((t) => t.affirmations > 0 || t.duree >= 60);
    $('f-matrix').innerHTML = `<thead><tr><th scope="col">Thème</th><th scope="col">Durée</th>${names.map((n) => `<th scope="col">${esc(n)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map((t) => `<tr><th scope="row">${swatch(t.id)}${esc(t.label)}</th><td>${t.duree >= 60 ? fmtDur(t.duree) : '—'}</td>${names.map((n) => {
        const c = t.par_debatteur?.[n] || {};
        const v = c.verdicts || {};
        const talk = c.temps_parole >= 60 ? `<span class="f-cell-sub">${fmtDur(c.temps_parole)} de parole</span>` : '';
        if (!c.tranches) return `<td><span class="muted">—</span>${talk}</td>`;
        const best = t.plus_exact === n ? ' <span class="f-best" title="Meilleur indice d’exactitude sur ce thème">★</span>' : '';
        return `<td>${bar(v, true)}${cellCounts(v)} <span class="muted">sur ${Number(c.tranches)}</span>${best}${talk}</td>`;
      }).join('')}</tr>`).join('')}</tbody>`;

    // Déroulé : chapitres, avec les verdicts tombés pendant chacun
    const affs = fiche.affirmations || [];
    $('f-chapters').innerHTML = frise.map((s, i) => {
      const inside = affs.filter((a) => a.t != null && a.t >= s.debut && a.t < s.fin);
      const counts = {};
      for (const a of inside) counts[a.verdict] = (counts[a.verdict] || 0) + 1;
      const stats = Object.keys(VERDICTS).filter((v) => counts[v])
        .map((v) => `<span class="${VERDICTS[v].cls}">${counts[v]} ${VERDICTS[v].short}</span>`).join(' · ');
      return `<li id="ch-${i}"><span class="f-ch-time">${fmtT(s.debut)} – ${fmtT(s.fin)}</span>
        <span class="f-ch-theme">${swatch(s.theme)}${esc(themeLabel[s.theme] || s.theme)}</span>
        <a class="f-ts" href="${replayUrl(s.debut + 3)}">Revoir ▶</a>
        ${stats ? `<span class="f-ch-stats">${stats}</span>` : ''}</li>`;
    }).join('');
  }

  // « 7 vraies, 2 partielles » : toutes les catégories de la case, pas
  // seulement les vraies (6 vraies sur 6 semblait perdre face à 7 sur 9)
  const CELL_WORDS = { vrai: ['vraie', 'vraies'], partiellement_vrai: ['partielle', 'partielles'],
    trompeur: ['trompeuse', 'trompeuses'], faux: ['fausse', 'fausses'] };
  function cellCounts(v) {
    return TRANCHES.filter((k) => v[k]).map((k) => `${Number(v[k])} ${CELL_WORDS[k][v[k] > 1 ? 1 : 0]}`).join(', ');
  }

  function renderChiffres() {
    const rows = fiche.redaction?.chiffres || [];
    if (!rows.length) return;
    $('chiffres').hidden = false;
    $('f-chiffres').innerHTML = rows.map((c) => {
      const a = affById.get(c.id);
      if (!a) return '';
      const src = safeUrl(a.url) ? ` <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.source || 'source')} ↗</a>` : '';
      return `<article class="f-chiffre">
        <p class="f-card-head"><span>${badge(a.verdict)} <strong>${esc(c.qui)}</strong></span>${tsLink(a.t)}</p>
        <p class="f-quote">« ${esc(a.texte)} »</p>
        <div class="f-vs"><div><span class="f-vs-l">Annoncé</span><span class="f-vs-n">${esc(c.annonce)}</span></div>
          <div><span class="f-vs-l">Selon la source</span><span class="f-vs-n">${esc(c.selon_source)}</span></div></div>
        <p class="f-expl">${esc(a.explication)}${src}</p>
      </article>`;
    }).join('');
  }

  function renderContradictions() {
    const rows = fiche.redaction?.contradictions || [];
    if (!rows.length) return;
    $('contradictions').hidden = false;
    $('f-contras').innerHTML = rows.map((c) => `<article class="f-contra"><h3>${esc(c.sujet)}</h3><div class="f-contra-sides">${c.ids.map((id) => {
      const a = affById.get(id);
      return a ? `<div><p class="f-card-head"><strong>${esc(a.qui)}</strong>${badge(a.verdict)}</p>
        <blockquote>« ${esc(a.texte)} »</blockquote><p class="f-expl">${esc(a.explication)} <a href="#a-${esc(a.id)}">Détail</a></p></div>` : '';
    }).join('')}</div></article>`).join('');
  }

  function renderPropositions() {
    const props = fiche.redaction?.propositions || [];
    if (!props.length) return;
    $('propositions').hidden = false;
    $('f-props').innerHTML = (fiche.debatteurs || []).map((d) => {
      const mine = props.filter((p) => p.qui === d.nom);
      return mine.length ? `<div><h3 class="f-group">${esc(d.nom)}</h3><ul class="f-props">${mine
        .map((p) => `<li>${esc(p.intitule)} ${tsLink(p.t)}</li>`).join('')}</ul></div>` : '';
    }).join('');
  }

  // ── Toutes les affirmations, filtrables ──

  function setFilters({ qui = '', verdict = '', theme = '' }) {
    $('flt-qui').value = qui;
    $('flt-verdict').value = verdict;
    $('flt-theme').value = theme;
    renderAffList();
  }

  function renderAffirmations() {
    const affs = fiche.affirmations || [];
    const names = (fiche.debatteurs || []).map((d) => d.nom);
    const opt = (value, label) => `<option value="${esc(value)}">${esc(label)}</option>`;
    $('flt-qui').innerHTML = opt('', 'Tous') + names.map((n) => opt(n, n)).join('')
      + (affs.some((a) => !a.qui) ? opt('?', 'Non attribuées') : '');
    $('flt-verdict').innerHTML = opt('', 'Tous') + Object.keys(VERDICTS)
      .filter((v) => affs.some((a) => a.verdict === v)).map((v) => opt(v, VERDICTS[v].label)).join('');
    $('flt-theme').innerHTML = opt('', 'Tous') + (fiche.themes || [])
      .filter((t) => affs.some((a) => a.theme === t.id)).map((t) => opt(t.id, t.label)).join('');
    ['flt-qui', 'flt-verdict', 'flt-theme'].forEach((id) => $(id).addEventListener('change', renderAffList));
    renderAffList();
  }

  function renderAffList() {
    const qui = $('flt-qui').value, verdict = $('flt-verdict').value, theme = $('flt-theme').value;
    const shown = (fiche.affirmations || []).filter((a) => (!qui || (qui === '?' ? !a.qui : a.qui === qui))
      && (!verdict || a.verdict === verdict) && (!theme || a.theme === theme));
    $('f-count').textContent = plural(shown.length, 'affirmation');
    $('f-affs').innerHTML = shown.map((a) => {
      const src = safeUrl(a.url) ? ` <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.source || 'source')} ↗</a>`
        : (a.source ? ` <span class="muted">(${esc(a.source)})</span>` : '');
      return `<li id="a-${esc(a.id)}"><p class="f-aff-head">${badge(a.verdict)}<strong>${esc(a.qui || 'Non attribuée')}</strong>
        <span>${swatch(a.theme)}${esc(themeLabel[a.theme] || '')}</span>${tsLink(a.t)}</p>
        <p class="f-quote">« ${esc(a.texte)} »</p><p class="f-expl">${esc(a.explication)}${src}</p></li>`;
    }).join('') || '<li class="muted">Aucune affirmation pour ces filtres.</li>';
  }

  // Chaque source se déplie sur les vérifications qui la citent, avec le
  // lien vers l'article ou la donnée
  function renderSources() {
    const src = fiche.sources || [];
    if (!src.length) return;
    $('sources-utilisees').hidden = false;
    const cited = (name) => (fiche.affirmations || []).filter((a) => (a.source || '').trim() === name && safeUrl(a.url)
      && TRANCHES.includes(a.verdict));
    $('f-sources').innerHTML = src.map((s) => `<details class="f-source"><summary><span>${esc(s.nom)}</span>
      <span class="muted">${plural(s.n, 'vérification')}</span></summary><ul>${cited(s.nom).map((a) => `<li>${badge(a.verdict)}
      <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">« ${esc(a.texte)} » ↗</a>
      <span class="muted">${esc(a.qui || 'Non attribuée')}</span></li>`).join('')}</ul></details>`).join('');
  }
})();
