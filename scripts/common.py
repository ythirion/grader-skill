"""Briques HTML partagées par render_report.py et build_index.py (style wfs-grader / outside-in)."""
from __future__ import annotations

import copy
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
E = html.escape

# Grade A→F sur le pourcentage de la note (hors bonus)
GRADES = [(85, "A", "Excellent"), (70, "B", "Bien"), (55, "C", "Correct"),
          (40, "D", "Insuffisant"), (25, "E", "Mauvais"), (0, "F", "Très insuffisant")]
NIVEAUX = {"ok": "acquis", "warning": "à améliorer", "critical": "critique"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- Note IA / note correcteur ----------
# notes.json porte la note IA ; correcteur.json (facultatif, à côté) porte les ajustements du correcteur :
# {"correcteur": "…", "date": "…", "notes": {"<id>": {"note": 2.5, "commentaire": "…"}}, "bonus": {…}, "malus": {…}}
# Par défaut, la note correcteur est la note IA.
KINDS = ("notes", "bonus", "malus")


def apply_adjustments(notes: dict, adj: dict) -> dict:
    """Copie de notes.json où chaque note ajustée par le correcteur remplace la note IA."""
    final = copy.deepcopy(notes)
    for kind in KINDS:
        for cid, a in (adj.get(kind) or {}).items():
            if "note" in a:
                final.setdefault(kind, {}).setdefault(cid, {"note": 0, "justification": ""})["note"] = a["note"]
    return final


def load_correction(corr_dir: Path, promo_dir: Path | None = None) -> tuple[dict, dict, dict]:
    """Renvoie (notes IA, notes correcteur, ajustements) d'un dossier <rendu>/correction.

    Les ajustements viennent de <rendu>/correction/correcteur.json ou, à défaut, de l'entrée du rendu
    dans <promo>/ajustements.json (export groupé de la synthèse) ; le plus récent (champ "maj") l'emporte."""
    notes = load(corr_dir / "notes.json")
    cands = []
    if (corr_dir / "correcteur.json").exists():
        cands.append(load(corr_dir / "correcteur.json"))
    if promo_dir and (promo_dir / "ajustements.json").exists():
        entry = (load(promo_dir / "ajustements.json").get("rendus") or {}).get(notes.get("rendu", ""))
        if entry:
            cands.append(entry)
    adj = max(cands, key=lambda a: a.get("maj", "")) if cands else {}
    return notes, apply_adjustments(notes, adj), adj


def slim_bareme(bareme: dict) -> dict:
    """Ce dont grader.js a besoin pour recalculer les notes."""
    ids = lambda items: [{"id": c["id"], "sur": c["sur"]} for c in items]  # noqa: E731
    return {"titre": bareme["titre"], "sur": bareme["sur"], "plafonner": bareme.get("plafonner", True),
            "ramener_sur": bareme.get("ramener_sur"),
            "sections": [{"id": s["id"], "criteres": ids(s["criteres"])} for s in bareme["sections"]],
            "bonus": ids(bareme.get("bonus", [])), "malus": ids(bareme.get("malus", []))}


def ia_values(notes_ia: dict) -> dict:
    return {k: {cid: v["note"] for cid, v in (notes_ia.get(k) or {}).items()} for k in KINDS}


def correcteur_of(notes: dict, adj: dict) -> str:
    return adj.get("correcteur") or notes.get("correcteur") or ""


def fmt(x: float) -> str:
    """15.0 -> 15 ; 2.5 -> 2,5 (notation française)."""
    return (f"{x:.2f}".rstrip("0").rstrip(".")).replace(".", ",")


def ratio(note: float, sur: float) -> float:
    return note / sur if sur else 0.0


def level(note: float, sur: float) -> str:
    r = ratio(note, sur)
    return "ok" if r >= 0.75 else "warning" if r >= 0.5 else "critical"


def grade(note: float, sur: float) -> tuple[str, str]:
    pct = 100 * ratio(note, sur)
    for seuil, lettre, libelle in GRADES:
        if pct >= seuil:
            return lettre, libelle
    return "F", "Très insuffisant"


def live_attrs(live: str | None, sur: float | None = None, mode: str | None = None) -> str:
    """Attributs lus par assets/grader.js pour recalculer la valeur dans le navigateur."""
    if not live:
        return ""
    return (f' data-live="{E(live)}"' + (f' data-sur="{sur}"' if sur is not None else "")
            + (f' data-fmt="{mode}"' if mode else ""))


def live_num(value: float, live: str, mode: str = "num") -> str:
    """Nombre mis à jour en direct ; mode : num (12,5), bonus (+1), malus (−2 ou 0)."""
    text = (f"+{fmt(value)}" if mode == "bonus" else ("−" + fmt(value) if value else "0") if mode == "malus" else fmt(value))
    return f'<span{live_attrs(live, None, mode)}>{text}</span>'


def embed_json(data: dict, element_id: str = "grader-data") -> str:
    """Données embarquées pour grader.js (</ échappé pour ne pas fermer la balise)."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" id="{element_id}">{raw}</script><script src="assets/grader.js"></script>'


def grade_box(note: float, sur: float, small: bool = False, live: str | None = None) -> str:
    lettre, libelle = grade(note, sur)
    sm = " grade--sm" if small else ""
    return (f'<div class="grade grade--{lettre.lower()}{sm}"{live_attrs(live, sur)} '
            f'aria-label="Grade {lettre}, {libelle}">{lettre}</div>')


def gauge(label: str, note: float, sur: float, aria: str, valeur: str | None = None, live: str | None = None) -> str:
    pct = round(100 * ratio(note, sur))
    valeur = valeur or f"{fmt(note)}/{fmt(sur)}"
    return (f'<div class="gauge gauge--{level(note, sur)}"{live_attrs(live, sur)} role="meter" aria-valuemin="0" aria-valuemax="{sur}" '
            f'aria-valuenow="{note}" aria-label="{E(aria)}" style="--value:{pct}">'
            f'<div class="gauge__head"><span>{E(label)}</span><span class="gauge__value">{valeur}</span></div>'
            f'<div class="gauge__track"><div class="gauge__fill"></div></div></div>')


def dial(note: float, sur: float, aria: str, label: str | None = None, live: str | None = None) -> str:
    pct = round(100 * ratio(note, sur))
    return (f'<div class="dial dial--{level(note, sur)}"{live_attrs(live, sur)} role="meter" aria-valuemin="0" aria-valuemax="{sur}" '
            f'aria-valuenow="{note}" aria-label="{E(aria)}" style="--value:{pct}">'
            f'<span class="dial__value">{fmt(note)}</span><span class="dial__label">{E(label or "/ " + fmt(sur))}</span></div>')


def pill(note: float, sur: float, live: str | None = None) -> str:
    return f'<span class="pill pill--{level(note, sur)}"{live_attrs(live, sur)}>{fmt(note)}/{fmt(sur)}</span>'


def pill_text(kind: str, text: str) -> str:
    return f'<span class="pill pill--{kind}">{E(text)}</span>'


def signals(items: list[dict]) -> str:
    """items : [{"niveau": "ok|warning|critical", "texte": "HTML inline"}]."""
    if not items:
        return ""
    lis = "".join(f'<li class="signal signal--{i["niveau"]}"><span class="pill pill--{i["niveau"]}">'
                  f'{NIVEAUX.get(i["niveau"], i["niveau"])}</span><div>{i["texte"]}</div></li>' for i in items)
    return f'<ul class="signals">{lis}</ul>'


def excerpt(ex: dict) -> str:
    """ex : {"fichier": "src/x.java:12", "code": "…", "lang": "java"} — le code est échappé et coloré ici."""
    lang = ex.get("lang") or lang_of(ex.get("fichier", ""))
    cmt = "#" if lang in HASH_COMMENT else "//"
    head = f'{cmt} {ex["fichier"]}\n' if ex.get("fichier") else ""
    return f'<pre class="hl"><code>{highlight(head + ex["code"], lang)}</code></pre>'


# ---------- Coloration syntaxique (sans dépendance, pour des rapports autonomes et imprimables) ----------

KEYWORDS = set("""
abstract assert boolean break byte case catch char class const continue default do double else enum extends
final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void
volatile while var record yield sealed permits non-sealed true false null
fun val when object companion data override open internal lateinit in is as
let function async await export from typeof undefined of
def elif except lambda pass raise with None True False and or not nonlocal global
""".split())
HASH_COMMENT = {"python", "py", "bash", "sh", "yaml", "yml", "toml"}
EXT_LANG = {"java": "java", "kt": "kotlin", "js": "js", "ts": "ts", "tsx": "ts", "jsx": "js", "py": "python",
            "cs": "csharp", "c": "c", "cpp": "cpp", "go": "go", "rs": "rust", "php": "php", "sh": "bash"}


def lang_of(path: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]+)(?::\d+(?:-\d+)?)?\s*$", path or "")
    return EXT_LANG.get(m.group(1).lower(), "") if m else ""


def highlight(code: str, lang: str = "") -> str:
    """Échappe et colore un extrait (commentaires, chaînes, annotations, nombres, mots-clés, types)."""
    lang = (lang or "").lower()
    comment = r"#[^\n]*" if lang in HASH_COMMENT else r"//[^\n]*|/\*.*?\*/"
    token = re.compile(rf'(?P<com>{comment})|(?P<str>"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
                       r'|(?P<ann>@[A-Za-z_][\w.]*)|(?P<num>\b\d[\d_]*(?:\.\d+)?[fFdDlL]?\b)|(?P<word>\b[A-Za-z_]\w*\b)', re.S)
    out, pos = [], 0
    for m in token.finditer(code):
        out.append(E(code[pos:m.start()]))
        kind, text = m.lastgroup, m.group()
        if kind == "word":
            kind = "kw" if text in KEYWORDS else "type" if text[0].isupper() else None
        out.append(f'<span class="hl-{kind}">{E(text)}</span>' if kind else E(text))
        pos = m.end()
    out.append(E(code[pos:]))
    return "".join(out)


# ---------- Markdown minimal (titres, paragraphes, listes, tableaux, blocs de code colorés) ----------

def _inline(text: str) -> str:
    """Code `…`, gras **…**, italique *…*, liens [t](u). Les spans de code sont protégés avant le reste."""
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(f"<code>{E(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    p = re.sub(r"`([^`]+)`", keep, text)
    p = E(p, quote=False)
    p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
    p = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", p)
    p = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', p)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], p)


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def markdown(md: str, offset: int = 0, id_prefix: str = "") -> tuple[str, list[tuple[int, str, str]]]:
    """Convertit un Markdown simple en HTML. Renvoie (html, titres [(niveau, id, texte)]).
    offset décale les niveaux de titre (ex. offset=1 : ## → h3)."""
    lines, out, heads, i = md.splitlines(), [], [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            lang, buf, i = line[3:].strip(), [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append(f'<pre class="hl"><code>{highlight(chr(10).join(buf), lang)}</code></pre>')
            i += 1
            continue
        m = re.match(r"(#{1,6})\s+(.*)", line)
        if m:
            lvl = min(6, len(m.group(1)) + offset)
            hid = id_prefix + slug(m.group(2))
            heads.append((len(m.group(1)), hid, m.group(2)))
            out.append(f'<h{lvl} id="{hid}">{_inline(m.group(2))}</h{lvl}>')
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body = [r for r in rows[1:] if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
            th = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
            trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            continue
        if re.match(r"\s*[-*]\s+", line) and not re.fullmatch(r"\s*[-*]{3,}\s*", line):
            items = []
            while i < len(lines) and re.match(r"\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(t)}</li>" for t in items) + "</ul>")
            continue
        if not line.strip() or re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            i += 1
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"(```|#{1,6}\s|\||\s*[-*]\s+)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "".join(out), heads


def page(*, title: str, projet: str, eyebrow: str, h1: str, meta: str, main: str, nav: list[tuple[str, str, bool]],
         toc: list[tuple[str, str, str]] | None = None, footer: str) -> str:
    """nav : [(href, libellé, courant)] ; toc : [(id, libellé, pastille HTML)]."""
    links = "".join(f'<li><a class="nav__link{" nav__link--pdf" if l == "PDF" else ""}" href="{E(h)}"'
                    f'{" aria-current=\"page\"" if cur else ""}>{E(l)}</a></li>' for h, l, cur in nav)
    if toc:
        lis = "".join(f'<li><a href="#{i}"><span>{E(l)}</span>{p}</a></li>' for i, l, p in toc)
        body = (f'<div class="layout layout--toc"><nav class="toc" aria-label="Sur cette page">'
                f'<p class="toc__title">Sur cette page</p><ol>{lis}</ol></nav><main id="main">{main}</main></div>')
    else:
        body = f'<div class="layout"><main id="main">{main}</main></div>'
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{E(title)} — Grader — {E(projet)}</title>
  <link rel="preload" href="assets/fonts/lexend.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="assets/report.css">
</head>
<body>
  <a class="skip-link" href="#main">Aller au contenu</a>
  <header class="site-header">
    <nav class="nav" aria-label="Rapport">
      <span class="nav__brand">Grader <span>{E(projet)}</span></span>
      <ul class="nav__menu">{links}</ul>
    </nav>
  </header>
  <div class="container">
    <div class="hero">
      <span class="hero__eyebrow">{E(eyebrow)}</span>
      <h1 class="hero__title">{E(h1)}</h1>
      <p class="hero__meta">{meta}</p>
    </div>
    {body}
  </div>
  <footer class="site-footer">
    <div class="container">{footer}</div>
  </footer>
</body>
</html>
"""


def copy_assets(dest: Path) -> None:
    """Copie report.css et les polices dans <dest>/assets/."""
    target = dest / "assets"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_DIR / "assets" / "report.css", target / "report.css")
    shutil.copy2(SKILL_DIR / "assets" / "grader.js", target / "grader.js")
    shutil.copytree(SKILL_DIR / "assets" / "fonts", target / "fonts", dirs_exist_ok=True)


# ---------- Oral de contre-évaluation ----------
# Stocké dans les ajustements du rendu (correcteur.json → "oral") :
# {"fait": true, "date": "…", "axes": {"vocabulaire": {"auto": 75, "enseignant": 50, "retenu": null,
#   "justification": "…", "notes": "…"}, …}, "reponses": {"vocabulaire:1": "…"}, "exercice": "…", "commentaire": "…"}
# Niveau d'un axe = retenu, sinon lecture de l'enseignant·e, sinon auto-évaluation (100, 75, 50 ou 0).
# Note conservée = note correcteur × moyenne des trois axes, dès que l'oral est réalisé et les trois axes évalués.
ORAL_AXES = ("vocabulaire", "demarche", "consignes")
ORAL_SEUIL = 3


def oral_level(axe: dict) -> float | None:
    for k in ("retenu", "enseignant", "auto"):
        v = (axe or {}).get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def oral_coef(oral: dict | None) -> float | None:
    """Coefficient de l'oral (0 à 1), ou None si l'oral n'est pas réalisé ou incomplet."""
    if not oral or not oral.get("fait"):
        return None
    levels = [oral_level((oral.get("axes") or {}).get(a)) for a in ORAL_AXES]
    if any(v is None for v in levels):
        return None
    return sum(levels) / len(levels) / 100


def apply_oral(totals: dict, adj: dict, bareme: dict) -> None:
    """Applique le résultat de l'oral aux totaux (note finale, grade, note ramenée)."""
    totals["grade_base"] = totals["note"] - totals.get("malus", 0)
    coef = oral_coef((adj or {}).get("oral"))
    if coef is None:
        return
    totals["avant_oral"] = totals["finale"]
    totals["coef_oral"] = coef
    totals["finale"] = round(totals["finale"] * coef, 2)
    totals["grade_base"] *= coef
    if bareme.get("ramener_sur"):
        totals["finale_ramenee"] = round(totals["finale"] * bareme["ramener_sur"] / bareme["sur"], 2)


def grade_base(totals: dict) -> float:
    """Base du grade : note après malus, hors bonus, compensée par l'oral."""
    return totals.get("grade_base", totals["note"] - totals.get("malus", 0))


def oral_seuil(promo_dir: Path | None) -> int:
    if promo_dir and (promo_dir / "oral.json").exists():
        return load(promo_dir / "oral.json").get("seuil", ORAL_SEUIL)
    return ORAL_SEUIL


def oral_prevu(notes_ia: dict, seuil: int) -> bool:
    return (notes_ia.get("ia") or {}).get("score", 0) >= seuil


def oral_demande(adj: dict | None) -> bool:
    """Oral déclenché par le correcteur (champ "demande" de l'oral), même sans indices d'usage IA."""
    return bool(((adj or {}).get("oral") or {}).get("demande"))


def oral_anchor(rendu_dir_name: str) -> str:
    """Ancre stable de la section d'un rendu dans oral.html."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", rendu_dir_name).encode("ascii", "ignore").decode().lower()
    return "oral-" + (re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "rendu")
