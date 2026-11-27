#!/usr/bin/env python3
"""Rend le rapport HTML d'un rendu à partir de sa notation JSON.

Usage:
    python3 -I render_report.py <rendu>/correction/notes.json --bareme <racine>/correction/bareme.json \
        --promo <racine>/correction

Les notes de section, le total, le bonus, le malus et la note finale sont calculés ici à partir des
notes par critère : le JSON ne porte que les notes élémentaires. Toute incohérence avec le
barème (critère manquant ou inconnu, note hors bornes) fait échouer le rendu (code 1).

Note IA / note correcteur : notes.json porte la note IA. Le rapport embarque le barème et les notes IA,
et assets/grader.js permet au correcteur d'ajuster chaque note dans le navigateur (commentaire facultatif) :
les totaux sont recalculés en direct et enregistrés dans le navigateur, exportables en correcteur.json.
Si <dossier du JSON>/correcteur.json (ou l'entrée du rendu dans <promo>/ajustements.json) existe, ses notes
sont intégrées au rendu statique et au PDF. Par défaut, note correcteur = note IA.

Écrit <dossier du JSON>/index.html et copie les assets à côté.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (KINDS, E, copy_assets, correcteur_of, dial, embed_json, excerpt, fmt, gauge, grade,  # noqa: E402
                    grade_box, ia_values, level, live_attrs, live_num, load, load_correction, page, pill,
                    pill_text, signals, slim_bareme)
from common import apply_oral, grade_base, oral_anchor, oral_demande, oral_prevu, oral_seuil  # noqa: E402

IA_NIVEAUX = {"absent": (0, "Absent"), "faible": (1, "Faible"), "modere": (2, "Modéré"),
              "fort": (3, "Fort"), "tres-fort": (4, "Très fort")}


def compute(bareme: dict, notes: dict) -> tuple[dict, list[str]]:
    """Calcule les totaux et liste les incohérences."""
    errors: list[str] = []
    given = notes.get("notes", {})
    known = set()
    sections = []
    for s in bareme["sections"]:
        sur = sum(c["sur"] for c in s["criteres"])
        total = 0.0
        for c in s["criteres"]:
            known.add(c["id"])
            n = given.get(c["id"])
            if n is None:
                errors.append(f"critère non noté : {c['id']}")
                continue
            if not 0 <= n["note"] <= c["sur"]:
                errors.append(f"{c['id']} : note {n['note']} hors de [0, {c['sur']}]")
            total += n["note"]
        sections.append({"id": s["id"], "titre": s["titre"], "sur": sur, "note": total})
    errors += [f"critère inconnu du barème : {k}" for k in given if k not in known]
    bonus = 0.0
    bonus_sur = 0.0
    for b in bareme.get("bonus", []):
        bonus_sur += b["sur"]
        n = notes.get("bonus", {}).get(b["id"])
        if n is not None:
            if not 0 <= n["note"] <= b["sur"]:
                errors.append(f"bonus {b['id']} : note {n['note']} hors de [0, {b['sur']}]")
            bonus += n["note"]
    malus = 0.0
    for m in bareme.get("malus", []):
        n = notes.get("malus", {}).get(m["id"])
        if n is not None:
            if not 0 <= n["note"] <= m["sur"]:
                errors.append(f"malus {m['id']} : retrait {n['note']} hors de [0, {m['sur']}]")
            malus += n["note"]
    known_malus = {m["id"] for m in bareme.get("malus", [])}
    errors += [f"malus inconnu du barème : {k}" for k in notes.get("malus", {}) if k not in known_malus]
    sur = sum(s["sur"] for s in sections)
    if abs(sur - bareme["sur"]) > 1e-9:
        errors.append(f"le barème totalise {sur} points au lieu de {bareme['sur']}")
    note = sum(s["note"] for s in sections)
    finale = max(0.0, note + bonus - malus)
    if bareme.get("plafonner", True):
        finale = min(finale, bareme["sur"])
    totals = {"sections": sections, "sur": bareme["sur"], "note": note, "bonus": bonus,
              "bonus_sur": bonus_sur, "malus": malus, "finale": finale}
    # Facultatif : "ramener_sur": 20 dans le barème affiche aussi la note finale convertie (ex. /100 → /20)
    if bareme.get("ramener_sur"):
        totals["ramener_sur"] = bareme["ramener_sur"]
        totals["finale_ramenee"] = round(finale * bareme["ramener_sur"] / bareme["sur"], 2)
    return totals, errors


def adjust_box(kind: str, cid: str, sur: float, ia_note: float, final: float, adj: dict, correcteur: str) -> str:
    """Note IA (fixe) et note correcteur (modifiable dans le navigateur via grader.js), avec commentaire facultatif."""
    signe = "−" if kind == "malus" else ""
    changed = abs(final - ia_note) > 1e-9
    comment = (adj or {}).get("commentaire", "")
    who = E(correcteur) if correcteur else "le correcteur"
    out = (f'<div class="adjust{" adjust--changed" if changed else ""}" data-kind="{kind}" data-id="{E(cid)}" '
           f'data-sur="{sur}" data-ia="{ia_note}" data-note="{final}">'
           f'<div class="adjust__row"><span class="adjust__ia">Note IA <strong>{signe}{fmt(ia_note)}/{fmt(sur)}</strong></span>'
           f'<label class="adjust__field">Note correcteur <input type="number" min="0" max="{sur}" step="0.25" value="{final}" '
           f'disabled><span>/{fmt(sur)}</span></label>'
           f'<span class="adjust__state pill pill--{"warning" if changed else "na"}">{"ajustée" if changed else "= IA"}</span></div>'
           f'<label class="adjust__comment">Commentaire du correcteur (facultatif)'
           f'<textarea rows="2" disabled placeholder="Pourquoi cette note ?">{E(comment)}</textarea></label>')
    if changed or comment:
        txt = (f'Note ajustée par {who} : {signe}{fmt(ia_note)} → <strong>{signe}{fmt(final)}/{fmt(sur)}</strong>.' if changed
               else f'Commentaire de {who} :')
        out += f'<p class="adjust__print">{txt}{" " + E(comment) if comment else ""}</p>'
    return out + "</div>"


def malus_block(m: dict, n: dict, ia_note: float | None = None, adj: dict | None = None, correcteur: str = "") -> str:
    """Un malus : le retrait est affiché en négatif, sans jauge."""
    k = "critical" if n["note"] else "ok"
    out = (f'<article class="item" id="{E(m["id"])}"><div class="item__head"><h3 class="item__title">{E(m["titre"])}</h3>'
           f'<span class="pill pill--{k}"{live_attrs("malus:" + m["id"], None, "malus")}>{"−" + fmt(n["note"]) if n["note"] else "0"}</span></div>')
    if m.get("description"):
        out += f'<p class="item__loc">{E(m["description"])} (jusqu\'à −{fmt(m["sur"])})</p>'
    out += adjust_box("malus", m["id"], m["sur"], n["note"] if ia_note is None else ia_note, n["note"], adj or {}, correcteur)
    out += f'<p>{n.get("justification", "")}</p>'
    out += "".join(excerpt(x) for x in n.get("extraits", []))
    out += signals(n.get("constats", []))
    return out + "</article>"


def criterion_block(c: dict, n: dict, kind: str = "notes", ia_note: float | None = None, adj: dict | None = None,
                    correcteur: str = "") -> str:
    out = (f'<article class="item" id="{E(c["id"])}"><div class="item__head"><h3 class="item__title">{E(c["titre"])}</h3>'
           f'{pill(n["note"], c["sur"], f"{kind}:{c['id']}")}</div>')
    if c.get("description"):
        out += f'<p class="item__loc">{E(c["description"])}</p>'
    out += gauge("Note", n["note"], c["sur"], f'{c["titre"]} : {n["note"]} sur {c["sur"]}', live=f"{kind}:{c['id']}")
    out += adjust_box(kind, c["id"], c["sur"], n["note"] if ia_note is None else ia_note, n["note"], adj or {}, correcteur)
    out += f'<p>{n.get("justification", "")}</p>'
    out += "".join(excerpt(x) for x in n.get("extraits", []))
    out += signals(n.get("constats", []))
    return out + "</article>"


ADJUST_BAR = """
<div class="adjust-bar" id="adjust-bar" data-mode="readonly" role="region" aria-label="Ajustement des notes">
  <p class="adjust-bar__readonly">Activez JavaScript pour ajuster les notes.</p>
  <div class="adjust-bar__edit">
    <label>Correcteur <input id="adj-who" type="text" autocomplete="name" placeholder="Prénom Nom"></label>
    <span id="adj-status" aria-live="polite"></span>
    <button type="button" id="adj-export" class="btn">Exporter correcteur.json</button>
    <button type="button" id="adj-reset" class="btn">Revenir aux notes IA</button>
    <button type="button" id="adj-print" class="btn btn--primary">Imprimer / PDF</button>
  </div>
</div>
"""


def render(notes_path: Path, bareme_path: Path, promo_dir: Path | None) -> int:
    bareme = load(bareme_path)
    notes_ia, notes, adj = load_correction(notes_path.parent, promo_dir)
    correcteur = correcteur_of(notes_ia, adj)
    totals_ia, errors = compute(bareme, notes_ia)
    totals, errors_adj = compute(bareme, notes)
    apply_oral(totals, adj, bareme)  # oral de contre-évaluation réalisé : note × moyenne des axes
    prevu = oral_prevu(notes_ia, oral_seuil(promo_dir))
    errors += [f"ajustement correcteur : {e}" for e in errors_adj if e not in errors]
    if errors:
        for e in errors:
            print(f"FAIL {notes_path}: {e}", file=sys.stderr)
        return 1
    out_dir = notes_path.parent
    copy_assets(out_dir)

    finale, sur = totals["finale"], totals["sur"]
    finale_ia = totals_ia["finale"]
    n_adj = sum(1 for k in KINDS for cid, a in (adj.get(k) or {}).items()
                if abs(a.get("note", 0) - (notes_ia.get(k, {}).get(cid) or {"note": 0})["note"]) > 1e-9)
    lettre, libelle = grade(grade_base(totals), sur)
    ia = notes.get("ia")

    # Tableau de bord
    main = ('<div class="dashboard"><div class="card card--hero">'
            f'{dial(finale, sur, f"Note finale sur {fmt(sur)}", live="finale")}{grade_box(grade_base(totals), sur, live="grade")}'
            f'<div><p class="card__label">Verdict</p><p class="card__number"{live_attrs("grade", sur, "libelle")}>{E(libelle)}</p>'
            f'<p class="card__desc">{notes.get("verdict", "")}</p></div></div>')
    kc = "warning" if n_adj else "na"
    main += (f'<div class="card"><span class="card__label">Note IA → note correcteur</span>'
             f'<p class="card__number">{fmt(finale_ia)} → {live_num(finale, "finale")}<small>/{fmt(sur)}</small></p>'
             f'<span class="card__desc"><span class="pill pill--{kc}" id="adj-count">{n_adj} note{"s" if n_adj > 1 else ""} ajustée{"s" if n_adj > 1 else ""}</span>'
             f'<span id="adj-by">{" par " + E(correcteur) if correcteur and n_adj else ""}</span></span></div>')
    # Oral : prévu (indices IA ≥ seuil) ou déclenché par le correcteur depuis ce rapport, la synthèse ou la page des oraux
    demande = oral_demande(adj)
    coef = totals.get("coef_oral")
    if coef is not None:
        o_pill, o_kind = f"{round(coef * 100)} %", "ok"
        etat = f'Oral réalisé : {round(coef * 100)} % de maîtrise, note avant oral {fmt(totals["avant_oral"])}/{fmt(sur)}.'
    elif prevu:
        o_pill, o_kind, etat = "à faire", "warning", "Indices d'usage IA élevés : un oral de contre-évaluation est prévu."
    elif demande:
        o_pill, o_kind, etat = "à faire", "warning", "Oral de contre-évaluation demandé par le correcteur."
    else:
        o_pill, o_kind, etat = "non prévu", "na", "Pas d'indice d'usage IA suffisant. Le correcteur peut tout de même demander un oral."
    oral_href = (os.path.relpath(promo_dir / "oral.html", out_dir).replace(os.sep, "/") + "#" + oral_anchor(out_dir.parent.name)
                 if promo_dir else None)
    main += (f'<div class="card card--oral-report"><span class="card__label">Oral de contre-évaluation</span>'
             f'<p class="card__number"><span class="pill pill--{o_kind}" id="oral-report-pill">{o_pill}</span></p>'
             f'<span class="card__desc" id="oral-report-desc">{etat}</span>'
             + (f'<span class="oral-actions"><a class="btn" id="oral-report-link" href="{E(oral_href)}"{"" if prevu or demande else " hidden"}>'
                f'Ouvrir l\'oral</a><button type="button" class="btn" id="oral-trigger"{" hidden" if prevu else ""} disabled>'
                f'{"Annuler la demande" if demande else "Déclencher un oral"}</button></span>' if oral_href else "")
             + '</div>')
    if "finale_ramenee" in totals:
        rs = totals["ramener_sur"]
        main += (f'<div class="card"><span class="card__label">Note ramenée sur {fmt(rs)}</span>'
                 f'<p class="card__number">{live_num(totals["finale_ramenee"], "ramenee")}<small>/{fmt(rs)}</small></p>'
                 f'<span class="card__desc">note finale {live_num(finale, "finale")}/{fmt(sur)} × {fmt(rs)} / {fmt(sur)}</span></div>')
    for s in totals["sections"]:
        main += (f'<a class="card" href="#{E(s["id"])}"><span class="card__label">{E(s["titre"])}</span>'
                 f'{gauge("Note", s["note"], s["sur"], s["titre"] + " sur " + fmt(s["sur"]), live="sec:" + s["id"])}</a>')
    if totals["bonus_sur"]:
        main += (f'<a class="card" href="#bonus"><span class="card__label">Bonus</span>'
                 f'<p class="card__number">{live_num(totals["bonus"], "bonus", "bonus")}</p>'
                 f'<span class="card__desc">sur +{fmt(totals["bonus_sur"])} possible</span></a>')
    if bareme.get("malus"):
        km = "critical" if totals["malus"] else "ok"
        main += (f'<a class="card" href="#malus"><span class="card__label">Malus</span>'
                 f'<p class="card__number"><span class="pill pill--{km}"{live_attrs("malus", None, "malus")}>{"−" + fmt(totals["malus"]) if totals["malus"] else "0"}</span></p>'
                 f'<span class="card__desc">sous-total {live_num(totals["note"], "note")}/{fmt(sur)} avant malus</span></a>')
    if ia:
        k = "ok" if ia["score"] <= 2 else "warning" if ia["score"] == 3 else "critical"
        main += (f'<a class="card" href="#ia"><span class="card__label">Usage IA</span>'
                 f'<div class="gauge gauge--{k}" role="meter" aria-valuemin="1" aria-valuemax="5" aria-valuenow="{ia["score"]}" '
                 f'aria-label="Score usage IA sur 5" style="--value:{ia["score"] * 20}"><div class="gauge__head"><span>Score</span>'
                 f'<span class="gauge__value">{ia["score"]}/5</span></div><div class="gauge__track"><div class="gauge__fill"></div></div></div>'
                 f'<span class="card__desc">indices, pas preuves</span></a>')
    main += "</div>"
    toc = []

    if notes.get("synthese"):
        main += (f'<section class="section" id="synthese" aria-labelledby="synthese-title"><h2 class="section__title" id="synthese-title">'
                 f'<span class="section__num">00</span> Synthèse {pill(finale, sur, "finale")}</h2><p class="section__summary">{notes["synthese"]}</p>')
        rows = "".join(f'<tr><td><a href="#{E(s["id"])}">{E(s["titre"])}</a></td><td class="num">{fmt(s["sur"])}</td>'
                       f'<td class="num">{live_num(s["note"], "sec:" + s["id"])}</td><td>{pill(s["note"], s["sur"], "sec:" + s["id"])}</td></tr>'
                       for s in totals["sections"])
        if totals["bonus_sur"]:
            rows += f'<tr><td><a href="#bonus">Bonus</a></td><td class="num">+{fmt(totals["bonus_sur"])}</td><td class="num">{live_num(totals["bonus"], "bonus", "bonus")}</td><td></td></tr>'
        if bareme.get("malus"):
            rows += (f'<tr><td>Sous-total</td><td class="num">{fmt(sur)}</td><td class="num">{live_num(totals["note"] + totals["bonus"], "brut")}</td><td></td></tr>'
                     f'<tr><td><a href="#malus">Malus</a></td><td></td><td class="num">{live_num(totals["malus"], "malus", "malus")}</td><td></td></tr>')
        rows += (f'<tr><td>Note IA</td><td class="num">{fmt(sur)}</td><td class="num">{fmt(finale_ia)}</td>'
                 f'<td>{pill(finale_ia, sur)}</td></tr>')
        rows += (f'<tr><td><strong>Note correcteur</strong></td><td class="num"><strong>{fmt(sur)}</strong></td>'
                 f'<td class="num"><strong>{live_num(finale, "finale")}</strong></td><td>{pill(finale, sur, "finale")}</td></tr>')
        if "finale_ramenee" in totals:
            rs = totals["ramener_sur"]
            rows += (f'<tr><td><strong>Note ramenée sur {fmt(rs)}</strong></td><td class="num"><strong>{fmt(rs)}</strong></td>'
                     f'<td class="num"><strong>{live_num(totals["finale_ramenee"], "ramenee")}</strong></td><td>{pill(totals["finale_ramenee"], rs, "ramenee")}</td></tr>')
        main += (f'<div class="table-wrap"><table><thead><tr><th>Partie</th><th class="num">Barème</th><th class="num">Note</th>'
                 f'<th>Niveau</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
        toc.append(("synthese", "Synthèse", pill(finale, sur, "finale")))

    by_id = {s["id"]: s for s in totals["sections"]}
    for i, s in enumerate(bareme["sections"], 1):
        t = by_id[s["id"]]
        main += (f'<section class="section" id="{E(s["id"])}" aria-labelledby="{E(s["id"])}-title">'
                 f'<h2 class="section__title" id="{E(s["id"])}-title"><span class="section__num">{i:02d}</span> {E(s["titre"])} '
                 f'{pill(t["note"], t["sur"], "sec:" + s["id"])}</h2>{gauge("Note", t["note"], t["sur"], s["titre"], live="sec:" + s["id"])}')
        main += "".join(criterion_block(c, notes["notes"][c["id"]], "notes", notes_ia["notes"][c["id"]]["note"],
                                        (adj.get("notes") or {}).get(c["id"]), correcteur) for c in s["criteres"])
        main += "</section>"
        toc.append((s["id"], s["titre"], pill(t["note"], t["sur"], "sec:" + s["id"])))

    if bareme.get("bonus"):
        main += (f'<section class="section" id="bonus" aria-labelledby="bonus-title"><h2 class="section__title" id="bonus-title">'
                 f'<span class="section__num">+</span> Bonus <span class="pill pill--{"ok" if totals["bonus"] else "na"}"{live_attrs("bonus", None, "bonus")}>+{fmt(totals["bonus"])}</span></h2>')
        for b in bareme["bonus"]:
            n = notes.get("bonus", {}).get(b["id"], {"note": 0, "justification": "Non attribué."})
            ia_n = (notes_ia.get("bonus", {}).get(b["id"]) or {"note": 0})["note"]
            main += criterion_block(b, n, "bonus", ia_n, (adj.get("bonus") or {}).get(b["id"]), correcteur)
        main += "</section>"
        toc.append(("bonus", "Bonus", f'<span class="pill pill--{"ok" if totals["bonus"] else "na"}"{live_attrs("bonus", None, "bonus")}>+{fmt(totals["bonus"])}</span>'))

    if bareme.get("malus"):
        km = "critical" if totals["malus"] else "ok"
        lib = "−" + fmt(totals["malus"]) if totals["malus"] else "0"
        main += (f'<section class="section" id="malus" aria-labelledby="malus-title"><h2 class="section__title" id="malus-title">'
                 f'<span class="section__num">−</span> Malus <span class="pill pill--{km}"{live_attrs("malus", None, "malus")}>{lib}</span></h2>')
        for m in bareme["malus"]:
            n = notes.get("malus", {}).get(m["id"], {"note": 0, "justification": "Non appliqué."})
            ia_n = (notes_ia.get("malus", {}).get(m["id"]) or {"note": 0})["note"]
            main += malus_block(m, n, ia_n, (adj.get("malus") or {}).get(m["id"]), correcteur)
        main += "</section>"
        toc.append(("malus", "Malus", f'<span class="pill pill--{km}"{live_attrs("malus", None, "malus")}>{lib}</span>'))

    if notes.get("points_forts") or notes.get("axes"):
        items = [{"niveau": "ok", "texte": t} for t in notes.get("points_forts", [])]
        items += [{"niveau": "warning", "texte": t} for t in notes.get("axes", [])]
        main += (f'<section class="section" id="retours" aria-labelledby="retours-title"><h2 class="section__title" id="retours-title">'
                 f'<span class="section__num">✓</span> Points forts et axes de progrès</h2>{signals(items)}</section>')
        toc.append(("retours", "Points forts / axes", ""))

    if notes.get("recommandations"):
        lis = "".join(f'<li>{pill_text(r.get("priorite", "warning"), "prioritaire" if r.get("priorite") == "critical" else "conseillé")} {r["texte"]}</li>'
                      for r in notes["recommandations"])
        main += (f'<section class="section" id="recommandations" aria-labelledby="recommandations-title"><h2 class="section__title" id="recommandations-title">'
                 f'<span class="section__num">→</span> Recommandations</h2><ol class="steps">{lis}</ol></section>')
        toc.append(("recommandations", "Recommandations", ""))

    if ia:
        k = "ok" if ia["score"] <= 2 else "warning" if ia["score"] == 3 else "critical"
        main += (f'<section class="section" id="ia" aria-labelledby="ia-title"><h2 class="section__title" id="ia-title">'
                 f'<span class="section__num">IA</span> Indices d\'usage IA <span class="pill pill--{k}">{ia["score"]}/5</span></h2>'
                 f'<p class="callout">Indices, pas preuves : à confronter à l\'oral.</p><p class="section__summary">{ia.get("justification", "")}</p>')
        rows = ""
        for ind in ia.get("indices", []):
            v, lib = IA_NIVEAUX.get(ind["niveau"], (0, ind["niveau"]))
            kk = "ok" if v <= 1 else "warning" if v == 2 else "critical"
            rows += f'<tr><td>{E(ind["titre"])}</td><td><span class="pill pill--{kk}">{lib}</span></td><td>{ind["texte"]}</td></tr>'
        if rows:
            main += (f'<div class="table-wrap"><table><thead><tr><th>Indice</th><th>Niveau</th><th>Observations</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>')
        main += "</section>"
        toc.append(("ia", "Usage IA", f'<span class="pill pill--{k}">{ia["score"]}/5</span>'))

    nav = []
    if promo_dir:
        rel = os.path.relpath(promo_dir / "index.html", out_dir)
        nav.append((rel, "Promo", False))
    nav += [("index.html", "Rapport", True), ("#imprimer", "PDF", False)]
    data = {"page": "report", "bareme": slim_bareme(bareme), "rendu": notes_ia.get("rendu", str(out_dir.parent.name)),
            "ia": ia_values(notes_ia), "baked": adj or None, "correcteur": correcteur,
            "oral_prevu": prevu}
    meta = (f'Corrigé le {E(notes.get("date", ""))} · {E(bareme["titre"])} · barème sur {fmt(sur)} · '
            f'rendu <code>{E(notes.get("rendu", ""))}</code> · correcteur : <span id="meta-correcteur">{E(correcteur) if correcteur else "non renseigné"}</span>'
            + (f' (notes ajustées le {E(adj["date"])})' if adj.get("date") and n_adj else ""))
    doc = page(title=notes["etudiant"], projet=bareme["titre"], eyebrow="Correction", h1=notes["etudiant"],
               meta=meta, main=main + ADJUST_BAR + embed_json(data), nav=nav, toc=toc,
               footer=f'Correction assistée par IA, à relire par le formateur ou la formatrice. Générée le {E(notes.get("date", ""))}.')
    (out_dir / "index.html").write_text(doc, encoding="utf-8")
    # Totaux recalculés, relus par build_index.py
    totals["ia"] = {k: totals_ia[k] for k in ("note", "bonus", "malus", "finale", "finale_ramenee", "sections") if k in totals_ia}
    totals["ajustements"] = n_adj
    totals["correcteur"] = correcteur
    (out_dir / "totaux.json").write_text(json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK   {out_dir / 'index.html'} : IA {fmt(finale_ia)} → correcteur {fmt(finale)}/{fmt(sur)} "
          f"(grade {lettre}, {n_adj} note(s) ajustée(s))")
    return 0


def main() -> int:
    args = sys.argv[1:]
    opts = {}
    for flag in ("--bareme", "--promo"):
        if flag in args:
            i = args.index(flag)
            opts[flag] = Path(args[i + 1])
            del args[i:i + 2]
    if not args or "--bareme" not in opts:
        print(__doc__, file=sys.stderr)
        return 2
    return max(render(Path(a), opts["--bareme"], opts.get("--promo")) for a in args)


if __name__ == "__main__":
    sys.exit(main())
