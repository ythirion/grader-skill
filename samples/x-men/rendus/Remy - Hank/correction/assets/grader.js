/* Grader : ajustement des notes par le correcteur, sans serveur.
 *
 * Chaque page embarque ses données (<script type="application/json" id="grader-data">) : barème, notes IA
 * et ajustements déjà intégrés au rendu ("baked", issus de correcteur.json). Le correcteur modifie une note
 * dans un rapport ; les totaux, jauges et grades sont recalculés ici et l'état est enregistré dans le
 * navigateur (localStorage, clé par barème et par rendu). La synthèse relit ces états pour afficher note IA
 * et note correcteur, exporte tout en ajustements.json et, sous Chrome / Edge, écrit les correcteur.json
 * directement dans les dossiers des rendus (File System Access API).
 * Règles de calcul identiques à render_report.py : note = Σ critères ; finale = max(0, note + bonus − malus),
 * plafonnée à "sur" sauf "plafonner": false ; grade sur (note − malus).
 */
(() => {
  'use strict';
  const dataEl = document.getElementById('grader-data');
  if (!dataEl) return;
  const D = JSON.parse(dataEl.textContent);
  const B = D.bareme;
  const EPS = 1e-9;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // ---------- Format et niveaux (mêmes seuils que common.py) ----------
  const fmt = (x) => (Math.round(x * 100) / 100).toFixed(2).replace(/\.?0+$/, '').replace('.', ',');
  const level = (n, sur) => { const r = sur ? n / sur : 0; return r >= 0.75 ? 'ok' : r >= 0.5 ? 'warning' : 'critical'; };
  const GRADES = [[85, 'A', 'Excellent'], [70, 'B', 'Bien'], [55, 'C', 'Correct'], [40, 'D', 'Insuffisant'], [25, 'E', 'Mauvais'], [0, 'F', 'Très insuffisant']];
  const grade = (n, sur) => { const pct = sur ? 100 * n / sur : 0; return (GRADES.find(([s]) => pct >= s) || GRADES[5]).slice(1); };
  const plural = (n, word) => `${n} ${word}${n > 1 ? 's' : ''}`;
  const now = () => new Date();
  const frDate = (d) => d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });

  // ---------- Stockage navigateur ----------
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch { /* stockage indisponible */ } },
  };
  const keyOf = (rendu) => `grader:${B.titre}:${rendu}`;
  /** État courant d'un rendu : le plus récent entre le navigateur et le fichier intégré au rendu. */
  const current = (rendu, baked) => {
    const local = store.get(keyOf(rendu));
    if (local && (!baked || (local.maj || '') >= (baked.maj || ''))) return local;
    return baked || {};
  };

  // ---------- Calcul ----------
  const noteOf = (ia, adj, kind, id) => {
    const a = adj && adj[kind] && adj[kind][id];
    return a && typeof a.note === 'number' ? a.note : ((ia[kind] || {})[id] || 0);
  };
  function compute(ia, adj) {
    const v = {};
    let note = 0, bonus = 0, malus = 0;
    for (const s of B.sections) {
      let t = 0;
      for (const c of s.criteres) { const n = noteOf(ia, adj, 'notes', c.id); v['notes:' + c.id] = n; t += n; }
      v['sec:' + s.id] = t; note += t;
    }
    for (const b of B.bonus || []) { const n = noteOf(ia, adj, 'bonus', b.id); v['bonus:' + b.id] = n; bonus += n; }
    for (const m of B.malus || []) { const n = noteOf(ia, adj, 'malus', m.id); v['malus:' + m.id] = n; malus += n; }
    let finale = Math.max(0, note + bonus - malus);
    if (B.plafonner !== false) finale = Math.min(finale, B.sur);
    let gradeBase = note - malus;
    const coef = oralCoef(adj && adj.oral);
    if (coef != null) { v.avant_oral = finale; v.coef = coef; finale = Math.round(finale * coef * 100) / 100; gradeBase *= coef; }
    Object.assign(v, { note, bonus, malus, brut: note + bonus, finale, grade: gradeBase });
    if (B.ramener_sur) v.ramenee = Math.round(finale * B.ramener_sur / B.sur * 100) / 100;
    return v;
  }
  // Oral de contre-évaluation : niveau d'un axe = retenu, sinon enseignant·e, sinon auto-évaluation
  const ORAL_AXES = ['vocabulaire', 'demarche', 'consignes'];
  const oralLevel = (ax) => ['retenu', 'enseignant', 'auto'].map((k) => ax && ax[k]).find((x) => typeof x === 'number');
  function oralCoef(oral) {
    if (!oral || !oral.fait) return null;
    const lv = ORAL_AXES.map((a) => oralLevel((oral.axes || {})[a]));
    return lv.some((x) => x === undefined) ? null : lv.reduce((s, x) => s + x, 0) / lv.length / 100;
  }
  /** Déclenche (on = true) ou annule l'oral de contre-évaluation d'un rendu à la demande du correcteur. */
  function setOralDemande(rendu, baked, on) {
    const state = { ...current(rendu, baked) };
    if (on) state.oral = { ...(state.oral || {}), demande: true };
    else delete state.oral;
    const d = now();
    return store.set(keyOf(rendu), { ...state, rendu, maj: d.toISOString(), date: state.date || frDate(d) });
  }
  /** Bouton à double clic dont le libellé change (« Confirmer ? » au premier clic). */
  const armClick = (btn, confirmLabel, action) => {
    let armed = false, timer, initial;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      if (!armed) { armed = true; initial = btn.textContent; btn.textContent = confirmLabel; timer = setTimeout(() => { armed = false; btn.textContent = initial; }, 4000); return; }
      clearTimeout(timer); armed = false; action();
    });
  };
  const countAdjusted = (ia, adj) => ['notes', 'bonus', 'malus'].reduce((n, k) =>
    n + Object.entries((adj && adj[k]) || {}).filter(([id, a]) => Math.abs(a.note - ((ia[k] || {})[id] || 0)) > EPS).length, 0);

  // ---------- Mise à jour du DOM : tout élément [data-live] ----------
  function paint(values, prefix = '') {
    for (const el of $$('[data-live]')) {
      let key = el.dataset.live;
      if (prefix) { if (!key.startsWith(prefix)) continue; key = key.slice(prefix.length); } else if (/^r\d+:/.test(key)) continue;
      if (!(key in values)) continue;
      const n = values[key], sur = el.dataset.sur ? +el.dataset.sur : 0, mode = el.dataset.fmt || '';
      const lvl = mode === 'malus' ? (n ? 'critical' : 'ok') : mode === 'bonus' ? (n ? 'ok' : 'na') : level(n, sur);
      const text = mode === 'malus' ? (n ? '−' + fmt(n) : '0') : mode === 'bonus' ? '+' + fmt(n)
        : mode === 'num' || !sur ? fmt(n) : `${fmt(n)}/${fmt(sur)}`;
      const cls = el.classList;
      if (cls.contains('pill')) { el.className = el.className.replace(/pill--\w+/, 'pill--' + lvl); el.textContent = text; }
      else if (cls.contains('gauge')) {
        el.style.setProperty('--value', Math.round(100 * (sur ? n / sur : 0)));
        el.className = el.className.replace(/gauge--\w+/, 'gauge--' + lvl);
        el.setAttribute('aria-valuenow', n);
        const g = $('.gauge__value', el); if (g && !el.dataset.keep) g.textContent = `${fmt(n)}/${fmt(sur)}`;
      } else if (cls.contains('dial')) {
        el.style.setProperty('--value', Math.round(100 * (sur ? n / sur : 0)));
        el.className = el.className.replace(/dial--\w+/, 'dial--' + lvl);
        el.setAttribute('aria-valuenow', n);
        $('.dial__value', el).textContent = fmt(n);
      } else if (cls.contains('grade')) {
        const [L, lib] = grade(n, sur);
        el.className = el.className.replace(/grade--\w/, 'grade--' + L.toLowerCase());
        el.textContent = L; el.setAttribute('aria-label', `Grade ${L}, ${lib}`);
      } else if (mode === 'libelle') el.textContent = grade(n, sur)[1];
      else el.textContent = text;
    }
  }

  const download = (name, obj) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2) + '\n'], { type: 'application/json' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  /** Bouton à double clic (évite les boîtes de dialogue bloquantes). */
  const confirmClick = (btn, label, action) => {
    const initial = btn.textContent;
    let armed = false, timer;
    btn.addEventListener('click', () => {
      if (!armed) { armed = true; btn.textContent = label; timer = setTimeout(() => { armed = false; btn.textContent = initial; }, 4000); return; }
      clearTimeout(timer); armed = false; btn.textContent = initial; action();
    });
  };

  if (D.page === 'report') report(); else if (D.page === 'promo') promo(); else if (D.page === 'oral') oralPage();

  // =====================================================================
  // Rapport d'un rendu
  // =====================================================================
  function report() {
    const bar = $('#adjust-bar'), who = $('#adj-who'), status = $('#adj-status');
    const boxes = $$('.adjust');
    const local = store.get(keyOf(D.rendu));
    const fromBrowser = !!local && (!D.baked || (local.maj || '') >= (D.baked.maj || ''));
    let adj = JSON.parse(JSON.stringify(fromBrowser ? local : (D.baked || {})));

    bar.dataset.mode = 'edit';
    document.body.classList.add('is-editing');
    who.value = adj.correcteur || D.correcteur || '';
    for (const b of boxes) {
      const a = adj[b.dataset.kind] && adj[b.dataset.kind][b.dataset.id];
      const input = $('input', b), area = $('textarea', b);
      input.disabled = false; area.disabled = false;
      if (a) { input.value = a.note; area.value = a.commentaire || ''; } else { input.value = b.dataset.ia; area.value = ''; }
    }

    const valueOf = (b) => parseFloat($('input', b).value.replace(',', '.'));
    function sync(save) {
      let invalid = false;
      const next = { correcteur: who.value.trim(), notes: {}, bonus: {}, malus: {} };
      for (const b of boxes) {
        const v = valueOf(b), sur = +b.dataset.sur, ia = +b.dataset.ia, c = $('textarea', b).value.trim();
        const bad = Number.isNaN(v) || v < 0 || v > sur + EPS;
        const changed = !bad && Math.abs(v - ia) > EPS;
        invalid ||= bad;
        b.classList.toggle('adjust--invalid', bad);
        b.classList.toggle('adjust--changed', changed);
        const state = $('.adjust__state', b);
        state.textContent = bad ? `hors barème (0 à ${fmt(sur)})` : changed ? 'ajustée' : '= IA';
        state.className = 'adjust__state pill pill--' + (bad ? 'critical' : changed ? 'warning' : 'na');
        // Texte imprimé (PDF) : note IA → note retenue et commentaire
        let p = $('.adjust__print', b);
        const sign = b.dataset.kind === 'malus' ? '−' : '';
        const name = next.correcteur || 'le correcteur';
        if (!bad && (changed || c)) {
          if (!p) { p = document.createElement('p'); p.className = 'adjust__print'; b.append(p); }
          p.textContent = (changed ? `Note ajustée par ${name} : ${sign}${fmt(ia)} → ${sign}${fmt(v)}/${fmt(sur)}.` : `Commentaire de ${name} :`) + (c ? ' ' + c : '');
        } else if (p) p.remove();
        if (!bad && (changed || c)) next[b.dataset.kind][b.dataset.id] = { note: v, commentaire: c };
      }
      if (invalid) { status.textContent = 'Note hors barème : non enregistrée'; return; }
      next.oral = current(D.rendu, D.baked).oral;
      const v = compute(D.ia, next);
      paintOral(v, next.oral);
      paint(v);
      const n = countAdjusted(D.ia, next);
      const cnt = $('#adj-count');
      if (cnt) { cnt.textContent = `${plural(n, 'note')} ajustée${n > 1 ? 's' : ''}`; cnt.className = 'pill pill--' + (n ? 'warning' : 'na'); }
      const by = $('#adj-by'); if (by) by.textContent = n && next.correcteur ? ' par ' + next.correcteur : '';
      const meta = $('#meta-correcteur'); if (meta) meta.textContent = next.correcteur || 'non renseigné';
      if (save) {
        const d = now();
        const fresh = current(D.rendu, D.baked);  // l'oral se saisit sur une autre page : on garde sa dernière version
        if (fresh.oral) next.oral = fresh.oral;
        Object.assign(next, { rendu: D.rendu, date: frDate(d), maj: d.toISOString() });
        adj = next;
        status.textContent = store.set(keyOf(D.rendu), next)
          ? `Enregistré dans ce navigateur à ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
          : 'Stockage du navigateur indisponible : pensez à exporter';
      }
    }

    function paintOral(v, oral) {
      const op = $('#oral-report-pill'), od = $('#oral-report-desc');
      if (!op) return;
      const demande = !!(oral && oral.demande), concerne = D.oral_prevu || demande;
      op.textContent = v.coef != null ? Math.round(v.coef * 100) + ' %' : concerne ? 'à faire' : 'non prévu';
      op.className = 'pill pill--' + (v.coef != null ? level(v.coef, 1) : concerne ? 'warning' : 'na');
      od.textContent = v.coef != null
        ? `Oral réalisé : ${Math.round(v.coef * 100)} % de maîtrise, note avant oral ${fmt(v.avant_oral)}/${fmt(B.sur)}.`
        : D.oral_prevu ? 'Indices d\'usage IA élevés : un oral de contre-évaluation est prévu.'
          : demande ? 'Oral de contre-évaluation demandé par le correcteur.'
            : 'Pas d\'indice d\'usage IA suffisant. Le correcteur peut tout de même demander un oral.';
      const link = $('#oral-report-link'), trig = $('#oral-trigger');
      if (link) link.hidden = !concerne;
      if (trig) { trig.textContent = demande ? 'Annuler la demande' : 'Déclencher un oral'; trig.disabled = false; }
    }
    const trig = $('#oral-trigger');
    if (trig && !D.oral_prevu) {
      armClick(trig, 'Confirmer ?', () => {
        const on = !(current(D.rendu, D.baked).oral || {}).demande;
        setOralDemande(D.rendu, D.baked, on);
        adj = current(D.rendu, D.baked);
        sync(false);
        status.textContent = on ? 'Oral demandé : il apparaît dans la synthèse et la page des oraux.' : 'Demande d\'oral annulée.';
      });
    }

    sync(false);
    status.textContent = fromBrowser ? `Ajustements repris de ce navigateur (${adj.date || ''})`
      : D.baked ? 'Ajustements repris de correcteur.json' : 'Note correcteur = note IA tant que rien n\'est modifié';
    let t;
    const onInput = () => { clearTimeout(t); t = setTimeout(() => sync(true), 250); };
    boxes.forEach((b) => b.addEventListener('input', onInput));
    who.addEventListener('input', onInput);
    $('#adj-export').addEventListener('click', () => { sync(true); download('correcteur.json', adj); });
    // Impression PDF : nom de fichier proposé = nom du rendu, notes ajustées comprises
    const opened = location.hash === '#imprimer';
    const printNow = () => {
      sync(true);
      const title = document.title;
      document.title = `${D.rendu.split('/').pop()} — ${B.titre}`;
      window.addEventListener('afterprint', () => { document.title = title; if (opened && window.opener) window.close(); }, { once: true });
      window.print();
    };
    $('#adj-print').addEventListener('click', printNow);
    $$('a.nav__link--pdf').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); printNow(); }));
    if (opened) { history.replaceState(null, '', location.pathname); setTimeout(printNow, 300); }
    confirmClick($('#adj-reset'), 'Confirmer le retour aux notes IA', () => {
      const keep = current(D.rendu, D.baked).oral;
      store.set(keyOf(D.rendu), { correcteur: who.value.trim(), rendu: D.rendu, maj: now().toISOString(), date: frDate(now()), ...(keep ? { oral: keep } : {}) });
      location.reload();
    });
  }

  // =====================================================================
  // Synthèse de promo
  // =====================================================================
  function promo() {
    const status = $('#promo-status');
    const mean = (a) => a.reduce((s, x) => s + x, 0) / (a.length || 1);
    const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
    const std = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((x) => (x - m) ** 2))); };

    function refresh() {
      const states = D.rows.map((r) => current(r.rendu, r.baked));
      const vals = D.rows.map((r, i) => compute(r.ia, states[i]));
      const agg = {};
      let ajustes = 0;
      const names = new Set();
      D.rows.forEach((r, i) => {
        const v = vals[i], n = countAdjusted(r.ia, states[i]);
        if (n) ajustes++;
        if (states[i].correcteur) names.add(states[i].correcteur);
        paint(v, `r${i}:`);
        const delta = $(`#delta-${i}`);
        if (delta) {
          const d = v.finale - r.ia_finale;
          delta.hidden = !n;
          delta.textContent = (d > EPS ? '+' : d < -EPS ? '−' : '±') + fmt(Math.abs(d));
          delta.title = `${plural(n, 'note')} ajustée${n > 1 ? 's' : ''}${states[i].correcteur ? ' par ' + states[i].correcteur : ''}`;
        }
      });
      let oraux = 0;
      const concernes = [];
      D.rows.forEach((r, i) => {
        const concerne = r.oral_prevu || !!((states[i] || {}).oral || {}).demande;
        const c = vals[i].coef, el = $(`#oral-${i}`);
        if (concerne) { concernes.push(r.etudiant); if (c != null) oraux++; }
        if (!el) return;
        el.dataset.concerne = concerne ? '1' : '';
        el.textContent = c != null ? `oral ${Math.round(c * 100)} %` : concerne ? 'oral à faire' : '＋ oral';
        el.className = 'pill delta pill--' + (c != null ? level(c, 1) : concerne ? 'warning' : 'na');
        el.title = concerne ? 'Oral de contre-évaluation' : 'Déclencher un oral de contre-évaluation pour ce rendu (double clic)';
      });
      const po = $('#promo-oral'), pn = $('#promo-oral-num'), pd = $('#promo-oral-desc');
      if (pn) pn.textContent = concernes.length ? `Oui · ${concernes.length}` : 'Non';
      if (pd) pd.textContent = concernes.length ? concernes.join(', ') + '.' : `Aucun rendu n'a un score d'indices IA ≥ ${D.oral_seuil}/5.`;
      if (po) {
        po.hidden = !concernes.length;
        po.textContent = `${oraux}/${concernes.length} réalisé${oraux > 1 ? 's' : ''}`;
        po.className = 'pill pill--' + (oraux === concernes.length ? 'ok' : 'warning');
      }
      const fin = vals.map((v) => v.finale);
      Object.assign(agg, {
        moy: mean(fin), med: median(fin), min: Math.min(...fin), max: Math.max(...fin), std: std(fin),
        reussite: fin.filter((f) => f >= B.sur / 2).length, gradeMoy: mean(fin),
      });
      if (B.ramener_sur) {
        const c = (x) => Math.round(x * B.ramener_sur / B.sur * 100) / 100;
        Object.assign(agg, { moy_r: c(agg.moy), med_r: c(agg.med), min_r: c(agg.min), max_r: c(agg.max) });
      }
      for (const s of B.sections) {
        agg['secmoy:' + s.id] = mean(vals.map((v) => v['sec:' + s.id]));
        for (const c of s.criteres) {
          const xs = vals.map((v) => v['notes:' + c.id]);
          Object.assign(agg, { ['critmoy:' + c.id]: mean(xs), ['critmin:' + c.id]: Math.min(...xs), ['critmax:' + c.id]: Math.max(...xs) });
        }
      }
      paint(agg);
      const pillAdj = $('#promo-adjusted');
      if (pillAdj) {
        pillAdj.textContent = `${ajustes}/${D.rows.length} rendu${ajustes > 1 ? 's' : ''} ajusté${ajustes > 1 ? 's' : ''}`;
        pillAdj.className = 'pill pill--' + (ajustes ? 'warning' : 'na');
      }
      const meta = $('#meta-correcteur'); if (meta && names.size) meta.textContent = [...names].join(', ');
      // Répartition des grades
      const counts = Object.fromEntries(GRADES.map(([, L]) => [L, 0]));
      vals.forEach((v) => { counts[grade(v.grade, B.sur)[0]]++; });
      const top = Math.max(1, ...Object.values(counts));
      for (const bar of $$('[data-histo]')) {
        const c = counts[bar.dataset.histo];
        bar.style.setProperty('--h', Math.round(100 * c / top));
        bar.setAttribute('aria-label', `${c} rendu(s) en ${bar.dataset.histo}`);
        $('span', bar).textContent = c;
      }
      return { states, ajustes };
    }

    let last = refresh();
    window.addEventListener('storage', (e) => { if (!e.key || e.key.startsWith(`grader:${B.titre}:`)) last = refresh(); });
    window.addEventListener('pageshow', () => { last = refresh(); });

    // Export PDF : un rapport par clic (les navigateurs bloquent l'ouverture de plusieurs fenêtres d'un coup)
    const pdfBtn = $('#promo-pdf');
    let next = 0;
    pdfBtn?.addEventListener('click', () => {
      const r = D.rows[next];
      window.open(r.href + '#imprimer', '_blank');
      next = (next + 1) % D.rows.length;
      if (next) {
        pdfBtn.textContent = `PDF suivant : ${D.rows[next].etudiant} (${next + 1}/${D.rows.length})`;
        status.textContent = `Impression de ${r.etudiant} : enregistrez en PDF, puis cliquez sur « PDF suivant ».`;
      } else {
        pdfBtn.textContent = `Exporter les PDF (${D.rows.length})`;
        status.textContent = `Dernier rapport ouvert (${r.etudiant}) : les ${D.rows.length} PDF ont été lancés.`;
      }
    });

    // « ＋ oral » : double clic pour déclencher un oral sur un rendu sans indices IA suffisants, puis ouverture de la page
    D.rows.forEach((r, i) => {
      const el = $(`#oral-${i}`);
      if (!el) return;
      let armed = false, timer;
      el.addEventListener('click', (e) => {
        if (el.dataset.concerne) return;  // lien normal vers l'oral
        e.preventDefault();
        if (!armed) { armed = true; el.textContent = 'déclencher ?'; timer = setTimeout(() => { armed = false; last = refresh(); }, 4000); return; }
        clearTimeout(timer); armed = false;
        setOralDemande(r.rendu, r.baked, true);
        last = refresh();
        status.textContent = `Oral demandé pour ${r.etudiant}.`;
        location.href = r.oral_href;
      });
    });

    const collect = () => {
      const rendus = {};
      D.rows.forEach((r, i) => { const s = last.states[i]; if (s && (countAdjusted(r.ia, s) || s.oral || ['notes', 'bonus', 'malus'].some((k) => Object.values(s[k] || {}).some((a) => a.commentaire)))) rendus[r.rendu] = s; });
      return rendus;
    };
    $('#promo-export')?.addEventListener('click', () => {
      last = refresh();
      download('ajustements.json', { bareme: B.titre, maj: now().toISOString(), rendus: collect() });
      status.textContent = `Exporté : placez ajustements.json dans le dossier correction/ (${Object.keys(collect()).length} rendu(s) ajusté(s)).`;
    });

    const input = $('#promo-import-file');
    $('#promo-import')?.addEventListener('click', () => input.click());
    input?.addEventListener('change', async () => {
      try {
        const data = JSON.parse(await input.files[0].text());
        const rendus = data.rendus || (data.rendu ? { [data.rendu]: data } : {});
        const known = new Set(D.rows.map((r) => r.rendu));
        let n = 0;
        for (const [rendu, adj] of Object.entries(rendus)) {
          if (!known.has(rendu)) continue;
          store.set(keyOf(rendu), { ...adj, rendu, maj: adj.maj || now().toISOString() }); n++;
        }
        last = refresh();
        status.textContent = `${plural(n, 'rendu')} importé${n > 1 ? 's' : ''}.`;
      } catch (e) { status.textContent = 'Import impossible : ' + e.message; }
      input.value = '';
    });

    const saveBtn = $('#promo-save');
    if (saveBtn && 'showDirectoryPicker' in window) {
      saveBtn.hidden = false;
      saveBtn.addEventListener('click', async () => {
        try {
          const root = await window.showDirectoryPicker({ id: 'grader-' + D.root_name, mode: 'readwrite' });
          if (root.name !== D.root_name) throw new Error(`choisissez le dossier « ${D.root_name} » (dossier choisi : « ${root.name} »)`);
          last = refresh();
          let written = 0, removed = 0;
          for (const [i, r] of D.rows.entries()) {
            if (!r.path) continue;
            let dir = root;
            for (const part of r.path.split('/')) dir = await dir.getDirectoryHandle(part);
            const s = last.states[i];
            const keep = s && (countAdjusted(r.ia, s) || s.oral || ['notes', 'bonus', 'malus'].some((k) => Object.values(s[k] || {}).some((a) => a.commentaire)));
            if (keep) {
              const f = await (await dir.getFileHandle('correcteur.json', { create: true })).createWritable();
              await f.write(JSON.stringify({ ...s, rendu: r.rendu }, null, 2) + '\n'); await f.close(); written++;
            } else {
              try { await dir.removeEntry('correcteur.json'); removed++; } catch { /* absent */ }
            }
          }
          status.textContent = `${plural(written, 'fichier')} correcteur.json écrit${written > 1 ? 's' : ''}`
            + (removed ? `, ${removed} supprimé${removed > 1 ? 's' : ''} (retour aux notes IA)` : '')
            + '. Demandez à Claude de régénérer la synthèse pour mettre à jour notes.csv.';
        } catch (e) {
          if (e.name !== 'AbortError') status.textContent = 'Enregistrement impossible : ' + e.message;
        }
      });
    }
  }

  // =====================================================================
  // Page d'oral de contre-évaluation (éditable pendant l'oral)
  // =====================================================================
  function oralPage() {
    const status = $('#oral-status');
    const pct = (x) => (typeof x === 'number' ? x + ' %' : '—');
    for (const r of D.rows) {
      const sec = document.getElementById(r.idx);
      if (!sec) continue;
      const o = current(r.rendu, r.baked).oral || {};
      const field = (sel) => $(sel, sec);
      const visible = r.prevu || !!o.demande;
      sec.hidden = !visible;
      const tocLi = $(`a[href="#${r.idx}"]`)?.closest('li');
      if (tocLi) tocLi.hidden = !visible;
      const cancel = $('[data-oral-cancel]', sec);
      if (cancel) {
        cancel.disabled = false;
        armClick(cancel, 'Confirmer : les saisies de cet oral seront effacées', () => { setOralDemande(r.rendu, r.baked, false); location.reload(); });
      }
      // Remplissage initial
      field('[data-oral="fait"]').checked = !!o.fait;
      $$('[data-oral="reponse"]', sec).forEach((t) => { t.value = (o.reponses || {})[t.dataset.key] || ''; });
      for (const axe of ORAL_AXES) {
        const ax = (o.axes || {})[axe] || {};
        for (const k of ['auto', 'enseignant']) {
          $$(`input[data-oral="${k}"][data-axe="${axe}"]`, sec).forEach((x) => { x.checked = +x.value === ax[k]; });
        }
        field(`select[data-oral="retenu"][data-axe="${axe}"]`).value = typeof ax.retenu === 'number' ? String(ax.retenu) : '';
        field(`[data-oral="justification"][data-axe="${axe}"]`).value = ax.justification || '';
        field(`[data-oral="notes"][data-axe="${axe}"]`).value = ax.notes || '';
      }
      field('[data-oral="exercice"]').value = o.exercice || '';
      field('[data-oral="commentaire"]').value = o.commentaire || '';

      const read = () => {
        const checked = (k, axe) => { const x = field(`input[data-oral="${k}"][data-axe="${axe}"]:checked`); return x ? +x.value : null; };
        const oral = { fait: field('[data-oral="fait"]').checked, axes: {}, reponses: {},
          exercice: field('[data-oral="exercice"]').value.trim(), commentaire: field('[data-oral="commentaire"]').value.trim() };
        $$('[data-oral="reponse"]', sec).forEach((t) => { if (t.value.trim()) oral.reponses[t.dataset.key] = t.value.trim(); });
        for (const axe of ORAL_AXES) {
          const sel = field(`select[data-oral="retenu"][data-axe="${axe}"]`).value;
          oral.axes[axe] = { auto: checked('auto', axe), enseignant: checked('enseignant', axe), retenu: sel === '' ? null : +sel,
            justification: field(`[data-oral="justification"][data-axe="${axe}"]`).value.trim(),
            notes: field(`[data-oral="notes"][data-axe="${axe}"]`).value.trim() };
        }
        return oral;
      };
      const show = (oral) => {
        const state = current(r.rendu, r.baked);
        const base = compute(r.ia, { ...state, oral: null }).finale;
        const out = (k) => $(`[data-out="${k}"]`, sec);
        const levels = ORAL_AXES.map((axe) => {
          const ax = oral.axes[axe];
          out('auto:' + axe).textContent = pct(ax.auto);
          out('enseignant:' + axe).textContent = pct(ax.enseignant);
          const lv = oralLevel(ax);
          out('niveau:' + axe).textContent = pct(lv);
          return lv;
        });
        const complete = levels.every((x) => x !== undefined);
        const moy = complete ? levels.reduce((s, x) => s + x, 0) / 3 : null;
        out('moyenne').textContent = moy == null ? '—' : fmt(moy) + ' %';
        out('devoir').textContent = fmt(base);
        out('devoir-pill').textContent = `${fmt(base)}/${fmt(B.sur)}`;
        out('coef').textContent = moy == null ? '____ %' : fmt(moy) + ' %';
        out('conservee').textContent = moy == null ? '____' : fmt(Math.round(base * moy) / 100);
        const coef = oralCoef(oral);
        const label = coef != null ? Math.round(coef * 100) + ' %' : oral.fait ? 'incomplet' : 'à faire';
        const kind = coef != null ? level(coef, 1) : 'warning';
        for (const el of [out('etat'), $(`[data-out-toc="${r.idx}"]`)]) {
          if (el) { el.textContent = label; el.className = 'pill pill--' + kind; }
        }
        sec.classList.toggle('oral-rendu--done', coef != null);
      };
      const save = () => {
        const oral = read();
        const d = now();
        oral.date = frDate(d);
        if (!r.prevu) oral.demande = true;
        const state = current(r.rendu, r.baked);
        const ok = store.set(keyOf(r.rendu), { ...state, rendu: r.rendu, oral, maj: d.toISOString(), date: state.date || frDate(d) });
        show(oral);
        const coef = oralCoef(oral);
        status.textContent = !ok ? 'Stockage du navigateur indisponible : exporte depuis la synthèse.'
          : `${r.etudiant} : enregistré à ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
            + (coef != null ? ` · note conservée ${fmt(compute(r.ia, { ...state, oral }).finale)}/${fmt(B.sur)}` : oral.fait ? ' · évalue les trois axes pour appliquer la note' : '');
      };
      show(read());
      let t;
      const onEdit = () => { clearTimeout(t); t = setTimeout(save, 300); };
      sec.addEventListener('input', onEdit);
      sec.addEventListener('change', onEdit);
    }
    // Déclencher un oral pour un rendu sans indices IA suffisants
    const sel = $('#oral-trigger-select'), btn = $('#oral-trigger-btn');
    if (sel && btn) {
      const visibleRows = () => D.rows.filter((r) => !document.getElementById(r.idx)?.hidden);
      [...sel.options].forEach((op) => { if (op.value && visibleRows().some((r) => r.rendu === op.value)) op.remove(); });
      sel.disabled = btn.disabled = sel.options.length <= 1;
      const empty = $('#oral-empty');
      if (empty) empty.hidden = visibleRows().length > 0;
      btn.addEventListener('click', () => {
        const r = D.rows.find((x) => x.rendu === sel.value);
        if (!r) { status.textContent = 'Choisis un rendu.'; return; }
        setOralDemande(r.rendu, r.baked, true);
        location.hash = r.idx;
        location.reload();
      });
    }    // Ancre vers une section révélée par le script (lien de la synthèse ou du rapport)
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }
})();
