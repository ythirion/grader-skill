#!/usr/bin/env python3
"""Construit la synthèse de promo : notes agrégées, moyennes, liens vers chaque rapport.

Usage:
    python3 -I build_index.py <racine>/correction --rendus <dossier des rendus>

Lit <racine>/correction/bareme.json, la synthèse facultative <racine>/correction/synthese.json
({"commentaire": "…", "constats_communs": [{"niveau": "ok|warning|critical", "texte": "…"}]})
et, pour chaque rendu, <rendu>/correction/notes.json et totaux.json (écrit par render_report.py).
Facultatifs : <racine>/correction/exemples.md (section « meilleurs exemples », code coloré) et
<racine>/correction/oral.json (page oral.html des oraux de contre-évaluation, voir oral.py).
Les notes affichées sont celles du correcteur (correcteur.json ou ajustements.json, par défaut égales à
la note IA) ; la note IA reste visible à côté. La page embarque les notes IA de chaque rendu : grader.js
recalcule en direct les notes ajustées dans les rapports (stockage du navigateur), exporte ajustements.json
et, sous Chrome / Edge, écrit les correcteur.json dans les dossiers des rendus. Écrit <racine>/correction/index.html, notes.csv, les assets et,
si oral.json existe, oral.html.
"""
from __future__ import annotations

import csv
import os
import re
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (GRADES, E, copy_assets, dial, fmt, gauge, grade, grade_box, ia_values, level, live_attrs, live_num, load, load_correction, markdown,  # noqa: E402
                    correcteur_of, embed_json, page, pill, signals, slim_bareme)
import oral as oral_page  # noqa: E402
from common import grade_base, oral_anchor, oral_coef, oral_demande, oral_prevu, oral_seuil  # noqa: E402


def collect(rendus: Path, out: Path) -> tuple[list[dict], list[str]]:
    rows, missing = [], []
    for d in sorted(p for p in rendus.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if d.resolve() == out.resolve():
            continue  # le dossier de sortie de la synthèse n'est pas un rendu
        corr = d / "correction"
        if not (corr / "notes.json").exists() or not (corr / "totaux.json").exists():
            missing.append(d.name)
            continue
        notes_ia, notes, adj = load_correction(corr, out)
        totaux = load(corr / "totaux.json")
        totaux.setdefault("ia", {"finale": totaux["finale"]})  # totaux d'une version antérieure
        rows.append({"dir": d, "notes": notes, "notes_ia": notes_ia, "adj": adj, "totaux": totaux,
                     "correcteur": correcteur_of(notes_ia, adj), "pdf": (corr / "rapport.pdf").exists()})
    return rows, missing


def main() -> int:
    args = sys.argv[1:]
    if "--rendus" not in args or not args:
        print(__doc__, file=sys.stderr)
        return 2
    i = args.index("--rendus")
    rendus = Path(args[i + 1])
    del args[i:i + 2]
    out = Path(args[0])
    bareme = load(out / "bareme.json")
    synthese = load(out / "synthese.json") if (out / "synthese.json").exists() else {}
    rows, missing = collect(rendus, out)
    if not rows:
        print(f"FAIL aucun rendu corrigé dans {rendus}", file=sys.stderr)
        return 1
    copy_assets(out)
    sur = bareme["sur"]
    # Oral de contre-évaluation : prévu dès qu'un rendu a un score d'indices IA ≥ seuil (3, ou "seuil" de oral.json).
    # oral.html (éditable pendant l'oral) est liée seulement depuis cette page ; oral.json apporte les questions préparées.
    seuil = oral_seuil(out)
    # Concernés : indices IA ≥ seuil, ou oral déclenché par le correcteur ("demande" dans ses ajustements)
    concernes = [r for r in rows if oral_prevu(r["notes_ia"], seuil) or oral_demande(r["adj"])]
    # oral.html est toujours écrite : le correcteur peut y déclencher un oral pour n'importe quel rendu
    oral_ok, oral_warn = oral_page.build(out, bareme, rows, load(out / "oral.json") if (out / "oral.json").exists() else {})
    oraux_faits = sum(1 for r in concernes if oral_coef((r["adj"] or {}).get("oral")) is not None)
    finales = [r["totaux"]["finale"] for r in rows]
    finales_ia = [r["totaux"]["ia"]["finale"] for r in rows]
    moy_ia = statistics.mean(finales_ia)
    ajustes = [r for r in rows if r["totaux"].get("ajustements")]
    correcteurs = sorted({r["correcteur"] for r in rows if r["correcteur"]})
    moy = statistics.mean(finales)
    med = statistics.median(finales)
    reussite = sum(1 for f in finales if f >= sur / 2)
    # Facultatif : "ramener_sur": 20 dans le barème ajoute la note convertie (carte, colonne, CSV)
    rs = bareme.get("ramener_sur")
    conv = (lambda x: round(x * rs / sur, 2)) if rs else None

    # Tableau de bord
    main_ = ('<div class="dashboard"><div class="card card--hero">'
             f'{dial(round(moy, 2), sur, f"Moyenne de la promo sur {fmt(sur)}", "moyenne", live="moy")}{grade_box(moy, sur, live="gradeMoy")}'
             f'<div><p class="card__label">Promo</p><p class="card__number">{len(rows)} rendu{"s" if len(rows) > 1 else ""}</p>'
             f'<p class="card__desc">{synthese.get("commentaire", "")}</p></div></div>'
             f'<div class="card"><span class="card__label">Moyenne IA → correcteur</span>'
             f'<p class="card__number">{fmt(round(moy_ia, 2))} → {live_num(round(moy, 2), "moy")}</p>'
             f'<span class="card__desc"><span id="promo-adjusted" class="pill pill--{"warning" if ajustes else "na"}">{len(ajustes)}/{len(rows)} rendu{"s" if len(ajustes) > 1 else ""} ajusté{"s" if len(ajustes) > 1 else ""}</span></span></div>'
             f'<div class="card"><span class="card__label">Médiane</span><p class="card__number">{live_num(round(med, 2), "med")}</p>'
             f'<span class="card__desc">sur {fmt(sur)}</span></div>'
             + (f'<div class="card"><span class="card__label">Moyenne sur {fmt(rs)}</span><p class="card__number">{live_num(conv(moy), "moy_r")}</p>'
                f'<span class="card__desc">médiane {live_num(conv(med), "med_r")} · min {live_num(conv(min(finales)), "min_r")} · max {live_num(conv(max(finales)), "max_r")}</span></div>' if rs else "")
             + f'<div class="card"><span class="card__label">Min · max</span><p class="card__number">{live_num(min(finales), "min")} · {live_num(max(finales), "max")}</p>'
             f'<span class="card__desc">écart-type {live_num(round(statistics.pstdev(finales), 2), "std")}</span></div>'
             f'<div class="card"><span class="card__label">Au-dessus de la moyenne</span>'
             f'{gauge("Rendus", reussite, len(rows), "Rendus à la moyenne ou plus", f"{reussite}/{len(rows)}", live="reussite")}</div>'
             + f'<a class="card card--oral" href="oral.html"><span class="card__label">Oraux de contre-évaluation</span>'
             f'<p class="card__number" id="promo-oral-num">{f"Oui · {len(concernes)}" if concernes else "Non"}</p>'
             f'<span class="card__desc"><span id="promo-oral-desc">'
             f'{E(", ".join(r["notes"]["etudiant"] for r in concernes)) + "." if concernes else f"Aucun rendu n&#39;a un score d&#39;indices IA ≥ {seuil}/5."}</span> '
             f'<span id="promo-oral" class="pill pill--{"ok" if oraux_faits == len(concernes) else "warning"}"{"" if concernes else " hidden"}>'
             f'{oraux_faits}/{len(concernes)} réalisé{"s" if oraux_faits > 1 else ""}</span> '
             f'Ouvrir la page des oraux, ou « ＋ oral » sur une ligne pour en déclencher un →</span></a>'
             + '</div>')

    # Classement des rendus
    head = "".join(f'<th class="num"><abbr title="{E(s["titre"])}">P{i}</abbr><br><small>/{fmt(sum(c["sur"] for c in s["criteres"]))}</small></th>'
                   for i, s in enumerate(bareme["sections"], 1))
    legend = " · ".join(f'<strong>P{i}</strong> {E(s["titre"])}' for i, s in enumerate(bareme["sections"], 1)) + " · <strong>IA</strong> note IA · <strong>Corr.</strong> note correcteur"
    has_bonus = bool(bareme.get("bonus"))
    has_malus = bool(bareme.get("malus"))
    has_ia = any(r["notes"].get("ia") for r in rows)
    body = ""
    racine = out.resolve().parent
    js_rows = []
    for ri, r in enumerate(rows):
        rel = os.path.relpath(r["dir"] / "correction" / "index.html", out)
        t = r["totaux"]
        px = f"r{ri}:"
        path = os.path.relpath((r["dir"] / "correction").resolve(), racine).replace(os.sep, "/")
        js_rows.append({"rendu": r["notes_ia"].get("rendu", r["dir"].name), "ia": ia_values(r["notes_ia"]),
                        "etudiant": r["notes"]["etudiant"], "href": rel.replace(os.sep, "/"),
                        "oral_prevu": oral_prevu(r["notes_ia"], seuil), "oral_href": "oral.html#" + oral_anchor(r["dir"].name),
                        "baked": r["adj"] or None, "ia_finale": t["ia"]["finale"],
                        "path": None if path.startswith("..") else path})
        cells = "".join(f'<td class="num"><span class="pill pill--{level(s["note"], s["sur"])}"{live_attrs(px + "sec:" + s["id"], s["sur"], "num")}>'
                        f'{fmt(s["note"])}</span></td>' for s in t["sections"])
        bonus = f'<td class="num">{live_num(t["bonus"], px + "bonus", "bonus")}</td>' if has_bonus else ""
        if has_malus:
            mal = t.get("malus", 0)
            bonus += (f'<td class="num"><span class="pill pill--{"critical" if mal else "ok"}"{live_attrs(px + "malus", None, "malus")}>'
                      f'{"−" + fmt(mal) if mal else "0"}</span></td>')
        ia = r["notes"].get("ia")
        concerne = oral_prevu(r["notes_ia"], seuil) or oral_demande(r["adj"])
        c_oral = oral_coef((r["adj"] or {}).get("oral"))
        o_txt, o_kind = (f"oral {round(c_oral * 100)} %", "ok") if c_oral is not None else ("oral à faire", "warning") if concerne else ("＋ oral", "na")
        oral_pill = (f'<br><a class="pill pill--{o_kind} delta" id="oral-{ri}" href="oral.html#{oral_anchor(r["dir"].name)}" '
                     f'title="{"Oral de contre-évaluation" if concerne else "Déclencher un oral de contre-évaluation pour ce rendu"}">{o_txt}</a>')
        ia_cell = (f'<td class="num">{ia["score"]}/5{oral_pill}</td>' if ia else '<td class="num">—</td>') if has_ia else ""
        pdf = (f'<br><a class="pill pill--na delta" href="{E(rel)}#imprimer" target="_blank" '
               f'title="Ouvre le rapport et lance l\'impression en PDF, notes ajustées comprises">PDF</a>')
        f_ia, n_adj = t["ia"]["finale"], t.get("ajustements", 0)
        delta = t["finale"] - f_ia
        adj_pill = (f'<br><span class="pill pill--warning delta" id="delta-{ri}"{"" if n_adj else " hidden"} '
                    f'title="{n_adj} note(s) ajustée(s) par {E(r["correcteur"] or "le correcteur")}">'
                    f'{"+" if delta > 0 else "−" if delta < 0 else "±"}{fmt(abs(delta))}</span>')
        body += (f'<tr><td class="nowrap"><a href="{E(rel)}">{E(r["notes"]["etudiant"])}</a>{pdf}</td>{cells}{bonus}'
                 f'<td class="num">{fmt(f_ia)}</td>'
                 f'<td class="num"><strong>{live_num(t["finale"], px + "finale")}</strong>{adj_pill}</td>'
                 + (f'<td class="num"><strong>{live_num(conv(t["finale"]), px + "ramenee")}</strong></td>' if rs else "")
                 + f'<td>{grade_box(grade_base(t), sur, small=True, live=px + "grade")}</td>{ia_cell}</tr>')
    main_ += (f'<section class="section" id="rendus" aria-labelledby="rendus-title"><h2 class="section__title" id="rendus-title">'
              f'<span class="section__num">01</span> Notes par rendu {pill(round(moy, 2), sur, "moy")}</h2>'
              f'<p class="section__summary">Cliquez sur un nom pour ouvrir son rapport détaillé. La note correcteur vaut la note IA tant qu\'elle n\'a pas été ajustée ; '
              f'les parties, le grade et les moyennes suivent la note correcteur. Une fois les notes relues, « Exporter les PDF » imprime chaque rapport à jour. Export tableur : <a href="notes.csv">notes.csv</a>.</p>'
              f'<div class="table-wrap"><table class="table--compact"><thead><tr><th>Rendu</th>{head}{"<th class=\"num\">Bonus</th>" if has_bonus else ""}{"<th class=\"num\">Malus</th>" if has_malus else ""}'
              f'<th class="num"><abbr title="Note IA">IA</abbr><br><small>/{fmt(sur)}</small></th><th class="num"><abbr title="Note correcteur">Corr.</abbr><br><small>/{fmt(sur)}</small></th>'
              + (f'<th class="num">Note<br><small>/{fmt(rs)}</small></th>' if rs else "")
              + f'<th>Grade</th>{"<th class=\"num\"><abbr title=\"Score d'indices d'usage IA\">Indices<br>IA</abbr></th>" if has_ia else ""}</tr></thead>'
              f'<tbody>{body}</tbody></table></div><p class="card__desc">{legend}</p>'
              '<div class="adjust-bar adjust-bar--promo" data-mode="edit"><div class="adjust-bar__edit">'
              '<span id="promo-status" aria-live="polite">Les notes ajustées dans les rapports s\'affichent ici (même navigateur).</span>'
              f'<button type="button" id="promo-pdf" class="btn btn--primary" title="Ouvre chaque rapport et lance son impression en PDF">'
              f'Exporter les PDF ({len(rows)})</button>'
              '<button type="button" id="promo-import" class="btn">Importer…</button>'
              '<input type="file" id="promo-import-file" accept="application/json,.json" hidden>'
              '<button type="button" id="promo-export" class="btn">Exporter ajustements.json</button>'
              f'<button type="button" id="promo-save" class="btn" hidden title="Écrit correcteur.json dans chaque rendu (Chrome, Edge)">'
              f'Enregistrer dans « {E(racine.name)} »</button></div></div>')
    if missing:
        main_ += signals([{"niveau": "warning", "texte": f"Non corrigé : <code>{E(m)}</code>"} for m in missing])
    if oral_warn:
        main_ += signals([{"niveau": "warning", "texte": w} for w in oral_warn])
    main_ += "</section>"

    # Répartition des grades
    counts = {lettre: 0 for _, lettre, _ in GRADES}
    for r in rows:
        counts[grade(grade_base(r["totaux"]), sur)[0]] += 1
    top = max(counts.values()) or 1
    bars = "".join(f'<div class="histo__bar" data-histo="{k}" style="--h:{round(100 * v / top)}" role="img" aria-label="{v} rendu(s) en {k}"><span>{v}</span></div>'
                   for k, v in counts.items())
    labels = "".join(f"<span>{k}</span>" for k in counts)
    main_ += (f'<section class="section" id="repartition" aria-labelledby="repartition-title"><h2 class="section__title" id="repartition-title">'
              f'<span class="section__num">02</span> Répartition des grades</h2>'
              f'<div class="histo" style="--cols:{len(counts)}">{bars}</div><div class="histo__labels" style="--cols:{len(counts)}">{labels}</div></section>')

    # Moyennes par partie et par critère
    sec = ""
    crit_rows = ""
    for s in bareme["sections"]:
        s_sur = sum(c["sur"] for c in s["criteres"])
        s_moy = statistics.mean(next(x["note"] for x in r["totaux"]["sections"] if x["id"] == s["id"]) for r in rows)
        sec += gauge(s["titre"], round(s_moy, 2), s_sur, f'Moyenne {s["titre"]}', live="secmoy:" + s["id"])
        crit_rows += f'<tr><th colspan="5" scope="rowgroup">{E(s["titre"])}</th></tr>'
        for c in s["criteres"]:
            vals = [r["notes"]["notes"][c["id"]]["note"] for r in rows]
            m = statistics.mean(vals)
            crit_rows += (f'<tr><td>{E(c["titre"])}</td><td class="num">{fmt(c["sur"])}</td><td class="num">{live_num(round(m, 2), "critmoy:" + c["id"])}</td>'
                          f'<td>{pill(round(m, 2), c["sur"], "critmoy:" + c["id"])}</td>'
                          f'<td class="num">{live_num(min(vals), "critmin:" + c["id"])} · {live_num(max(vals), "critmax:" + c["id"])}</td></tr>')
    main_ += (f'<section class="section" id="moyennes" aria-labelledby="moyennes-title"><h2 class="section__title" id="moyennes-title">'
              f'<span class="section__num">03</span> Moyennes par partie et par critère</h2>{sec}'
              f'<div class="table-wrap"><table><thead><tr><th>Critère</th><th class="num">Barème</th><th class="num">Moyenne</th>'
              f'<th>Niveau</th><th class="num">Min · max</th></tr></thead><tbody>{crit_rows}</tbody></table></div></section>')

    toc = [("rendus", "Notes par rendu", pill(round(moy, 2), sur, "moy")), ("repartition", "Répartition", ""),
           ("moyennes", "Moyennes", "")]
    if synthese.get("constats_communs"):
        main_ += (f'<section class="section" id="constats" aria-labelledby="constats-title"><h2 class="section__title" id="constats-title">'
                  f'<span class="section__num">04</span> Constats communs</h2>{signals(synthese["constats_communs"])}</section>')
        toc.append(("constats", "Constats communs", ""))

    # Meilleurs exemples (facultatif) : <racine>/correction/exemples.md, blocs de code colorés
    ex_path = out / "exemples.md"
    if ex_path.exists():
        md = ex_path.read_text(encoding="utf-8")
        titre = "Meilleurs exemples"
        h1 = re.match(r"#\s+(.+)\n", md)
        if h1:
            titre = h1.group(1).split(":")[-1].strip().capitalize() or titre
            md = md[h1.end():]
        body, heads = markdown(md, offset=1, id_prefix="ex-")
        num = 5 if synthese.get("constats_communs") else 4
        main_ += (f'<section class="section prose" id="exemples" aria-labelledby="exemples-title"><h2 class="section__title" id="exemples-title">'
                  f'<span class="section__num">{num:02d}</span> {E(titre)}</h2>{body}</section>')
        toc.append(("exemples", titre, ""))
        toc += [(hid, "· " + re.sub(r"`", "", txt), "") for lvl, hid, txt in heads if lvl == 2]

    date = rows[0]["notes"].get("date", "")
    doc = page(title="Synthèse de la promo", projet=bareme["titre"], eyebrow="Synthèse de la promo", h1=bareme["titre"],
               meta=f'Corrigé le {E(date)} · {len(rows)} rendus · barème sur {fmt(sur)} (source <code>{E(bareme.get("source", ""))}</code>)'
                    f' · correcteur : <span id="meta-correcteur">{E(", ".join(correcteurs)) if correcteurs else "non renseigné"}</span>',
               main=main_ + embed_json({"page": "promo", "bareme": slim_bareme(bareme), "root_name": racine.name, "oral_seuil": seuil, "rows": js_rows}), nav=[("index.html", "Promo", True), ("notes.csv", "CSV", False)] + ([("oral.html", "Oral", False)] if oral_ok else []), toc=toc,
               footer=f"Correction assistée par IA, à relire par le formateur ou la formatrice. Générée le {E(date)}.")
    (out / "index.html").write_text(doc, encoding="utf-8")

    with (out / "notes.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        crits = [c for s in bareme["sections"] for c in s["criteres"]]
        w.writerow(["Rendu"] + [f'{c["titre"]} /{fmt(c["sur"])}' for c in crits]
                   + [f'{s["titre"]} /{fmt(sum(c["sur"] for c in s["criteres"]))}' for s in bareme["sections"]]
                   + (["Bonus"] if has_bonus else []) + (["Malus"] if has_malus else []) + [f"Note IA /{fmt(sur)}", f"Note correcteur /{fmt(sur)}"] + ([f"Note correcteur /{fmt(rs)}"] if rs else []) + ["Grade", "Notes ajustées", "Correcteur"] + (["Usage IA /5"] if has_ia else [])
                   + [f"Note avant oral /{fmt(sur)}", "Oral (maîtrise %)"])
        for r in rows:
            t = r["totaux"]
            w.writerow([r["notes"]["etudiant"]] + [fmt(r["notes"]["notes"][c["id"]]["note"]) for c in crits]
                       + [fmt(s["note"]) for s in t["sections"]] + ([fmt(t["bonus"])] if has_bonus else [])
                       + ([fmt(-t.get("malus", 0)) if t.get("malus") else "0"] if has_malus else [])
                       + [fmt(t["ia"]["finale"]), fmt(t["finale"])] + ([fmt(conv(t["finale"]))] if rs else [])
                       + [grade(grade_base(t), sur)[0], str(t.get("ajustements", 0)), r["correcteur"]]
                       + ([str(r["notes"]["ia"]["score"]) if r["notes"].get("ia") else ""] if has_ia else [])
                       + [fmt(t.get("avant_oral", t["finale"])),
                           (str(round(t["coef_oral"] * 100)) if "coef_oral" in t else ("à faire" if r in concernes else ""))])
    print(f"OK   {out / 'index.html'} : {len(rows)} rendus, moyenne IA {fmt(round(moy_ia, 2))} → correcteur {fmt(round(moy, 2))}/{fmt(sur)}"
          f", {len(ajustes)} rendu(s) ajusté(s)"
          + (f" ({fmt(conv(moy))}/{fmt(rs)})" if rs else "")
          + (f", oraux : {oraux_faits}/{len(concernes)} réalisé(s) (oral.html)" if concernes else ", aucun oral nécessaire")
          + (f", non corrigés : {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
