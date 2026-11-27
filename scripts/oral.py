"""Page d'oral de contre-évaluation (réservée au formateur ou à la formatrice), éditable pendant l'oral.

Appelé par build_index.py dès qu'un rendu a un score d'indices IA ≥ seuil (3 par défaut, ou "seuil" de
<racine>/correction/oral.json). Écrit <racine>/correction/oral.html, liée seulement depuis la synthèse de promo :
elle n'est ni exportée en PDF ni liée depuis les rapports individuels.

Pendant l'oral, le correcteur note dans la page ce qui est dit (réponse à chaque question, exercice), coche les
niveaux d'auto-évaluation et sa propre lecture, choisit le niveau retenu par axe et marque l'oral comme réalisé.
assets/grader.js enregistre ces saisies avec les ajustements du rendu ("oral" dans correcteur.json) et calcule
la note conservée = note correcteur × moyenne des trois axes, reportée dans le rapport et la synthèse.

Format de oral.json (facultatif ; HTML inline <code>, <strong>, <em> autorisé dans les textes) :
{
  "seuil": 3,                                   # score IA à partir duquel un oral est prévu (défaut 3)
  "descriptions": {"consignes": "…"},           # facultatif : remplace la description d'un axe
  "rendus": {
    "<nom du dossier du rendu>": {
      "a_creuser": ["…"],
      "axes": {"vocabulaire": [{"question": "…", "attendu": "…", "alerte": "…"}], "demarche": […], "consignes": […]},
      "exercice": {"consigne": "…", "attendu": "…"}
    }
  }
}
Sans questions préparées pour un rendu, sa section est générée quand même (axes, niveaux, notes libres).
"""
from __future__ import annotations

import os
from pathlib import Path

from common import (E, embed_json, fmt, ia_values, oral_anchor, oral_coef, oral_demande, oral_prevu, page, slim_bareme)

AXES = [
    ("vocabulaire", "Axe 1 : le vocabulaire", "« Que veut dire ce mot ? »",
     "Les termes du code, des commentaires et des documents rendus.",
     ["J'explique chaque terme avec mes propres mots, et je sais dire si mon code correspond vraiment à ce que le mot décrit.",
      "J'explique la plupart des termes, mais certains restent flous.",
      "Je connais les mots, mais je les répète sans pouvoir expliquer ce qu'ils font dans mon code.",
      "Je ne sais pas ce que veulent dire certains mots que j'ai écrits."]),
    ("demarche", "Axe 2 : la démarche", "« Comment as-tu procédé, et pourquoi ? »",
     "L'ordre dans lequel le projet a été construit, les commits, les choix faits et ceux changés en cours de route.",
     ["Je retrace chaque étape de mon travail et j'explique pourquoi j'ai fait ces choix, dans cet ordre.",
      "Je retrace les étapes, mais j'ai du mal à justifier certains choix.",
      "Mon récit reste vague, il me manque des étapes.",
      "Je ne sais pas expliquer comment le travail a été fait."]),
    ("consignes", "Axe 3 : les consignes", "« Que demandait le sujet ? »",
     "Les règles et exigences de l'énoncé, les livrables attendus, ce qu'il ne fallait pas faire.",
     ["Je restitue les consignes et je montre, dans mon code, comment j'y réponds (et où je n'y réponds pas).",
      "Je restitue les consignes, mais je ne sais pas toujours montrer où mon code y répond.",
      "Je n'en restitue qu'une partie.",
      "Je ne sais pas ce que demandait le sujet."]),
]
LEVEL_LABEL = {"absent": "absent", "faible": "faible", "modere": "modéré", "fort": "fort", "tres-fort": "très fort"}
LEVEL_PILL = {"absent": "ok", "faible": "na", "modere": "warning", "fort": "critical", "tres-fort": "critical"}
NIVEAUX = [("Maîtrise", 100), ("L'essentiel", 75), ("Hésite", 50), ("Ne sait pas", 0)]


def _field(label: str, attrs: str, rows: int = 2, placeholder: str = "") -> str:
    return (f'<label class="oral-field">{E(label)}<textarea rows="{rows}" {attrs} '
            f'placeholder="{E(placeholder)}"></textarea></label>')


def _axe(cle: str, titre: str, question: str, desc: str, reperes: list[str], items: list[dict], idx: str) -> str:
    out = f'<h3 id="{idx}-{cle}">{E(titre)}</h3><p><em>{E(question)}</em> {desc}</p>'
    for n, q in enumerate(items, 1):
        out += f'<article class="item"><div class="item__head"><h4 class="item__title">Q{n}. {q["question"]}</h4></div>'
        if q.get("attendu"):
            out += f'<p><span class="pill pill--ok">Attendu</span> {q["attendu"]}</p>'
        if q.get("alerte"):
            out += f'<p><span class="pill pill--critical">Alerte</span> {q["alerte"]}</p>'
        out += _field("Ce qui a été dit", f'data-oral="reponse" data-key="{cle}:{n}"', 2, "Réponse de l'étudiant·e, hésitations, précisions…")
        out += "</article>"
    if not items:
        out += _field("Questions posées et réponses", f'data-oral="reponse" data-key="{cle}:libre"', 3,
                      "Aucune question préparée : note ici les questions posées et ce qui a été répondu.")
    rows = "".join(
        f'<tr><td class="nowrap"><strong>{E(lib)} ({pct}&nbsp;%)</strong></td><td>{E(txt)}</td>'
        f'<td class="num"><input type="radio" name="{idx}-{cle}-auto" value="{pct}" data-oral="auto" data-axe="{cle}" '
        f'aria-label="Auto-évaluation : {E(lib)}"></td>'
        f'<td class="num"><input type="radio" name="{idx}-{cle}-ens" value="{pct}" data-oral="enseignant" data-axe="{cle}" '
        f'aria-label="Lecture de l\'enseignant·e : {E(lib)}"></td></tr>'
        for (lib, pct), txt in zip(NIVEAUX, reperes))
    options = "".join(f'<option value="{pct}">{E(lib)} ({pct} %)</option>' for lib, pct in NIVEAUX)
    out += (f'<div class="table-wrap"><table class="oral-levels"><thead><tr><th>Niveau</th><th>Je me reconnais si…</th>'
            f'<th class="num">Auto-éval.</th><th class="num">Enseignant·e</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<div class="oral-grid">'
            f'{_field("Ce qui me fait dire ça (réponse de l'étudiant·e)", f"data-oral=justification data-axe={cle}")}'
            f'{_field("Notes du correcteur", f"data-oral=notes data-axe={cle}")}'
            f'<label class="oral-field oral-field--select">Niveau retenu<select data-oral="retenu" data-axe="{cle}">'
            f'<option value="">= lecture de l\'enseignant·e</option>{options}</select></label></div>')
    return out


def _calc(idx: str, note: float, sur: float) -> str:
    lignes = "".join(f'<tr><td>{lib}</td><td class="num" data-out="auto:{cle}">—</td><td class="num" data-out="enseignant:{cle}">—</td>'
                     f'<td class="num"><strong data-out="niveau:{cle}">—</strong></td></tr>'
                     for cle, lib in (("vocabulaire", "Vocabulaire"), ("demarche", "Démarche"), ("consignes", "Consignes")))
    return (f'<h3 id="{idx}-calcul">Confrontation et calcul</h3><div class="table-wrap"><table><thead><tr><th>Axe</th>'
            '<th class="num">Auto-évaluation</th><th class="num">Lecture de l\'enseignant·e</th><th class="num">Niveau retenu</th></tr></thead>'
            f'<tbody>{lignes}<tr><td><strong>Moyenne</strong></td><td></td><td></td><td class="num"><strong data-out="moyenne">—</strong></td></tr>'
            f'</tbody></table></div>'
            f'<p class="oral-result"><strong>Note conservée</strong> = <span data-out="devoir">{fmt(note)}</span> / {fmt(sur)} × '
            f'<span data-out="coef">____ %</span> = <strong data-out="conservee">____</strong> / {fmt(sur)}</p>')


def build(out: Path, bareme: dict, rows: list[dict], oral: dict) -> tuple[bool, list[str]]:
    """Écrit oral.html avec une section par rendu : visible si l'oral est prévu (score IA ≥ seuil) ou demandé par le
    correcteur, masquée sinon (le correcteur peut la déclencher depuis la page). Renvoie (page écrite, avertissements)."""
    seuil = oral.get("seuil", 3)
    desc = oral.get("descriptions", {})
    score = lambda r: (r["notes_ia"].get("ia") or {}).get("score", 0)  # noqa: E731
    prevu = lambda r: oral_prevu(r["notes_ia"], seuil)  # noqa: E731
    concernes = [r for r in rows if prevu(r) or oral_demande(r["adj"])]
    ordre = sorted(rows, key=lambda r: (r not in concernes, -score(r), r["notes"]["etudiant"]))
    warnings = [f"Questions d'oral non préparées pour <code>{E(r['dir'].name)}</code> ({'score IA ' + str(score(r)) if prevu(r) else 'demandé par le correcteur'}) : "
                "la page propose des notes libres" for r in concernes if r["dir"].name not in oral.get("rendus", {})]

    main = ('<section class="section" id="deroule" aria-labelledby="deroule-title"><h2 class="section__title" id="deroule-title">'
            '<span class="section__num">00</span> Comment ça se passe</h2><ol>'
            '<li>On ouvre le projet dans l\'IDE, <strong>sans IA active</strong>.</li>'
            '<li>On pose trois séries de questions sur <strong>ce qui a été rendu</strong> : le code, les documents, l\'historique git. '
            'Note dans chaque champ <em>Ce qui a été dit</em>.</li>'
            '<li>À la fin, <strong>l\'étudiant·e s\'évalue</strong> : un niveau par axe, avec une justification.</li>'
            '<li>On compare avec ta lecture ; si elles diffèrent, on en discute et on choisit le niveau retenu.</li>'
            '<li>Pour un rendu sans indice d\'usage IA suffisant, tu peux tout de même <strong>déclencher un oral</strong> '
            '(barre ci-dessous, « ＋ oral » dans la synthèse ou bouton du rapport).</li>'
            '<li>Coche <strong>Oral réalisé</strong> : la note conservée remplace la note du devoir dans le rapport et la synthèse.</li></ol>'
            + '<div class="callout"><p><strong>L\'objectif n\'est pas de prouver si une IA a été utilisée.</strong> '
              'On mesure la maîtrise de ce qui a été rendu : qui comprend et sait défendre chaque ligne garde sa note, '
              'quel que soit l\'outil utilisé.</p></div>'
            + f'<p>Oral prévu pour les rendus dont le score d\'indices IA est d\'au moins <strong>{seuil}/5</strong>. '
              '<strong>Note conservée = note correcteur × moyenne des trois axes.</strong> Niveau d\'un axe : le niveau retenu, '
              'sinon ta lecture, sinon l\'auto-évaluation. Les saisies sont enregistrées dans ce navigateur avec les ajustements '
              'du rendu : exporte-les ou enregistre-les depuis la synthèse.</p>'
              '<div class="adjust-bar adjust-bar--promo oral-trigger-bar"><label for="oral-trigger-select">Déclencher un oral pour</label>'
              '<select id="oral-trigger-select" disabled><option value="">Choisir un rendu…</option>'
              + "".join(f'<option value="{E(r["notes_ia"].get("rendu", r["dir"].name))}">{E(r["notes"]["etudiant"])}</option>'
                        for r in sorted(rows, key=lambda r: r["notes"]["etudiant"]) if not prevu(r))
              + '</select><button type="button" class="btn btn--primary" id="oral-trigger-btn" disabled>Déclencher</button></div>'
              f'<p id="oral-empty"{"" if not concernes else " hidden"}><em>Aucun oral prévu ni demandé pour l\'instant.</em></p></section>')
    toc = [("deroule", "Déroulé", "")]
    js_rows, hidden_toc = [], []

    for i, r in enumerate(ordre, 1):
        n, ia, t = r["notes"], r["notes_ia"].get("ia") or {"score": 0}, r["totaux"]
        concerne, auto = r in concernes, prevu(r)
        sur = t["sur"]
        avant = t.get("avant_oral", t["finale"])
        coef = oral_coef((r["adj"] or {}).get("oral"))
        idx = oral_anchor(r["dir"].name)
        data = oral.get("rendus", {}).get(r["dir"].name, {})
        rel = os.path.relpath(r["dir"] / "correction" / "index.html", out).replace(os.sep, "/")
        js_rows.append({"rendu": r["notes_ia"].get("rendu", r["dir"].name), "etudiant": n["etudiant"], "idx": idx, "prevu": auto,
                        "ia": ia_values(r["notes_ia"]), "baked": r["adj"] or None})
        etat = f"{round(coef * 100)} %" if coef is not None else "à faire"
        main += (f'<section class="section oral-rendu" id="{idx}" aria-labelledby="{idx}-title"{"" if concerne else " hidden"}>'
                 f'<h2 class="section__title" id="{idx}-title">'
                 f'<span class="section__num">{i:02d}</span> {E(n["etudiant"])} '
                 f'<span class="pill pill--na" data-out="devoir-pill">{fmt(avant)}/{fmt(sur)}</span> '
                 f'<span class="pill pill--{"critical" if ia["score"] >= 4 else "warning" if auto else "na"}">IA {ia["score"]}/5</span> '
                 + ("" if auto else '<span class="pill pill--warning">demandé par le correcteur</span> ')
                 + f'<span class="pill pill--{"ok" if coef is not None else "warning"}" data-out="etat">{etat}</span></h2>'
                 f'<p class="section__summary">{ia.get("justification", "")} <a href="{E(rel)}">Ouvrir le rapport</a>.</p>'
                 + ("" if auto else '<p><button type="button" class="btn" data-oral-cancel disabled>Annuler la demande d\'oral</button></p>')
                 + f'<label class="oral-done"><input type="checkbox" data-oral="fait"> Oral réalisé '
                 f'<span class="oral-done__hint">(la note conservée s\'applique dès que les trois axes sont évalués)</span></label>')
        if ia.get("indices"):
            main += "<h3>Indices relevés à la correction</h3><ul>" + "".join(
                f'<li><span class="pill pill--{LEVEL_PILL.get(x.get("niveau"), "na")}">{E(LEVEL_LABEL.get(x.get("niveau"), x.get("niveau", "")))}</span> '
                f'<strong>{E(x["titre"])}</strong> : {x.get("texte", "")}</li>' for x in ia["indices"]) + "</ul>"
        if data.get("a_creuser"):
            main += "<h3>À creuser</h3><ul>" + "".join(f"<li>{x}</li>" for x in data["a_creuser"]) + "</ul>"
        for cle, titre, question, d, reperes in AXES:
            main += _axe(cle, titre, question, desc.get(cle, d), reperes, data.get("axes", {}).get(cle, []), idx)
        ex = data.get("exercice") or {}
        main += f'<h3 id="{idx}-exercice">Exercice en direct (5 min)</h3>'
        if ex:
            main += (f'<div class="callout"><p>{ex["consigne"]}</p></div>'
                     + (f'<p><span class="pill pill--ok">Attendu</span> {ex["attendu"]}</p>' if ex.get("attendu") else ""))
        main += _field("Déroulé de l'exercice", 'data-oral="exercice"', 3, "Ce que l'étudiant·e a fait, en combien de temps, avec quelle aisance…")
        main += _calc(idx, avant, sur)
        main += _field("Commentaire de synthèse de l'oral", 'data-oral="commentaire"', 3) + "</section>"
        toc.append((idx, n["etudiant"], f'<span class="pill pill--{"ok" if coef is not None else "warning"}" data-out-toc="{idx}">{etat}</span>'))
        if not concerne:
            hidden_toc.append(idx)

    date = rows[0]["notes"].get("date", "")
    data_js = {"page": "oral", "bareme": slim_bareme(bareme), "rows": js_rows}
    doc = page(title="Oral de contre-évaluation", projet=bareme["titre"], eyebrow="Oral de contre-évaluation · usage réservé",
               h1=bareme["titre"], meta=f'Oral si score IA ≥ {seuil}/5 ou à la demande du correcteur · '
               'page non exportée, à ne pas diffuser aux étudiant·es',
               main=main + '<p class="adjust-bar adjust-bar--promo" id="oral-status" aria-live="polite">Saisies enregistrées dans ce navigateur au fil de l\'oral.</p>'
               + embed_json(data_js), nav=[("index.html", "Promo", False), ("oral.html", "Oral", True)], toc=toc,
               footer=f"Questions préparées par IA à partir du code rendu, à relire avant l'oral. Générée le {E(date)}.")
    for idx in hidden_toc:  # entrées de sommaire des rendus sans oral : masquées, réaffichées par grader.js
        doc = doc.replace(f'<li><a href="#{idx}">', f'<li hidden><a href="#{idx}">', 1)
    (out / "oral.html").write_text(doc, encoding="utf-8")
    return True, warnings
