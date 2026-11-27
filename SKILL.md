---
name: grader
description: >
  Correction générique de rendus étudiants à partir d'un énoncé / barème fourni.
  S'exécute dans un dossier qui contient le barème et les rendus (un sous-dossier par
  étudiant·e ou binôme). Après confirmation du fichier de barème et du dossier des rendus,
  note chaque rendu critère par critère, écrit dans chaque rendu un rapport HTML à jauges
  (style wfs-grader), puis une synthèse de promo à la racine : moyennes,
  répartition, moyennes par critère, export CSV et navigation vers chaque rapport.
  Chaque note a une note IA et une note correcteur (égale par défaut) que le correcteur
  ajuste directement dans le rapport HTML, avec un commentaire facultatif (JavaScript embarqué, sans serveur).
  Triggers : "corrige les rendus", "corrige ce TP", "note les projets avec ce barème",
  "correction de l'évaluation", "grader", "/grader".
  $ARGUMENTS (facultatif) = chemin du barème et/ou du dossier des rendus.
outputs:
  - correction/index.html
  - correction/notes.csv
  - correction/bareme.json
  - correction/synthese.json
  - <rendu>/correction/index.html
  - <rendu>/correction/notes.json
  - <rendu>/correction/correcteur.json
allowed-tools:
  - Read
  - Glob
  - Grep
  - Write
  - AskUserQuestion
  - Bash(git -C *)
  - Bash(git config user.name)
  - Bash(ls *)
  - Bash(python3 *)
---

# Grader : correction générique de rendus étudiants

## Rôle

Tu es un correcteur expérimenté, rigoureux et bienveillant. Tu appliques **le barème fourni et lui seul** : tu n'ajoutes aucun critère, tu ne changes aucune pondération. Chaque note est justifiée par des preuves tirées du rendu (fichier, ligne, extrait, commit).

`<SKILL_DIR>` désigne le dossier qui contient ce `SKILL.md`, `<RACINE>` le dossier courant (ou celui donné en argument), `<RENDUS>` le dossier des rendus et `<BAREME>` le fichier d'énoncé / barème.

---

## Étape 0 : repérage et confirmation (obligatoire, avant toute correction)

1. **Repère les candidats** dans `<RACINE>` (et dans `$ARGUMENTS` s'il est fourni) :
   - barème : fichiers `.md`, `.pdf`, `.docx`, `.txt` à la racine dont le nom ou le contenu évoque un barème, un énoncé, une évaluation, des points (`bareme`, `barème`, `enonce`, `sujet`, `evaluation`, `grille`…) ;
   - rendus : dossiers dont les sous-dossiers ressemblent à des rendus (un dépôt git, du code, un `README`), par exemple `rendus/`, `projets/`, `submissions/`, ou la racine elle-même.
2. **Demande confirmation** avec `AskUserQuestion`, en une seule fois, trois questions :
   - « Quel fichier contient l'énoncé et le barème ? » : les candidats trouvés, le plus probable en premier avec « (recommandé) » ;
   - « Où se trouvent les rendus ? » : les dossiers candidats, chacun avec dans sa description le nombre de rendus et leurs noms (ex. « 3 rendus : Logan - Ororo, Scott - Jean - Kurt, Remy - Hank »).
   - « Qui est le correcteur ou la correctrice ? » : en premier, le nom donné par `git config user.name` s'il existe, avec « (recommandé) », puis « Ne pas renseigner ». Le nom est affiché dans les rapports et la synthèse, à côté des notes qu'il ou elle ajustera.
   L'utilisateur peut répondre « Other » et saisir un chemin ou un nom : utilise-le.
3. Si aucun barème n'est trouvé, demande le chemin. **Ne commence jamais la correction sans les confirmations du barème et des rendus.**
4. Annonce le plan en une ligne : barème retenu, nombre de rendus, correcteur, dossiers de sortie.

---

## Étape 1 : barème structuré → `<RACINE>/correction/bareme.json`

Lis `<BAREME>` en entier (énoncé compris : les règles métier servent à vérifier les rendus) et transcris le barème **fidèlement** :

```json
{
  "titre": "Kata EID — TDD",
  "source": "Bareme.md",
  "sur": 20,
  "plafonner": true,
  "sections": [
    {"id": "tdd", "titre": "Pratique TDD", "criteres": [
      {"id": "tdd-cycle", "titre": "Cycle Red / Green / Refactor respecté", "sur": 3,
       "description": "Chaque fonctionnalité est précédée d'un test qui échoue, puis rendue vert, puis refactorisée"}
    ]}
  ],
  "bonus": [
    {"id": "retro", "titre": "RETRO.md sincère et pertinent", "sur": 1, "description": "…"}
  ],
  "malus": [
    {"id": "m-compile", "titre": "Ne compile pas", "sur": 4, "description": "…"}
  ]
}
```

- `malus` (facultatif) : pénalités prévues par le barème ; `sur` = retrait maximal. La note finale vaut `max(0, note + bonus − malus)`, puis plafonnée ; le grade est calculé sur la note après malus, hors bonus.

- `id` en kebab-case, uniques. Points décimaux autorisés (`0.5`).
- `sur` = total annoncé par le barème (hors bonus) ; la somme des critères doit l'égaler (le script de rendu le vérifie).
- `ramener_sur` (facultatif) : si le barème convertit la note (ex. « score / 5 → note sur 20 »), la nouvelle échelle (`20`). Les rapports, la synthèse et `notes.csv` affichent alors aussi la note finale ramenée sur cette échelle.
- `plafonner` : `true` si la note finale bonus compris ne peut dépasser `sur` (cas par défaut), `false` si le barème autorise à dépasser.
- Si le barème n'a pas de parties, crée une section unique. S'il est ambigu (points manquants, total incohérent), **pose la question** avant de corriger.

---

## Étape 2 : correction de chaque rendu → `<rendu>/correction/notes.json`

Corrige les rendus **un par un**, dans l'ordre alphabétique. Pour chacun :

1. **Explore** : arborescence (hors `node_modules`, `target`, `build`, `.idea`, `dist`), README et documents demandés, code source, tests. Si l'énoncé impose quelque chose sur l'historique git (commits, préfixes, régularité), lis `git -C "<rendu>" log --reverse --format='%h %ad %s' --date=iso` (et `--stat` si utile). Le rendu peut contenir un sous-dossier de projet (`java/`, `app/`…) : cherche-le.
2. **Vérifie le fonctionnel** par lecture du code au regard des règles de l'énoncé : repère les règles implémentées, absentes ou fausses, avec des contre-exemples concrets. N'exécute pas le code du rendu (pas de build ni de tests) : c'est une correction par lecture, dis-le dans la synthèse si cela limite une note.
3. **Note chaque critère** du barème, puis chaque bonus :
   - une note entre 0 et le maximum du critère, au pas de 0,5 (ou plus fin si le barème le prévoit) ;
   - une justification de 1 à 4 phrases, factuelle, qui cite `<code>chemin/fichier:ligne</code>` ou un hash de commit ;
   - 0 à 2 extraits de code de 15 lignes maximum, uniquement s'ils expliquent la note ;
   - des constats `ok` (acquis), `warning` (à améliorer), `critical` (manque bloquant ou erreur).
4. **Rédige** un verdict d'une phrase, une synthèse d'un paragraphe, 2 à 4 points forts, 2 à 4 axes de progrès et 3 recommandations concrètes.
5. **Indices d'usage IA** (facultatif mais recommandé pour du code) : score 1→5 et indices (sur-documentation générique, sophistication incohérente avec le niveau, hétérogénéité de niveau, gestion d'erreurs trop générique, commits monolithiques ou style uniforme). Ce sont des indices, à confronter à l'oral.

Format de `notes.json` (le HTML inline `<code>`, `<strong>`, `<em>` est autorisé dans les textes ; échappe `<` et `>` hors de ces balises ; le champ `code` des extraits est brut, le script l'échappe) :

```json
{
  "etudiant": "Logan & Ororo",
  "rendu": "rendus/Logan - Ororo",
  "date": "20 novembre 2026",
  "correcteur": "Prénom Nom",
  "verdict": "Une phrase.",
  "synthese": "Un paragraphe.",
  "notes": {
    "tdd-cycle": {
      "note": 2,
      "justification": "Texte avec <code>src/Eid.java:12</code>.",
      "extraits": [{"fichier": "src/Eid.java:12", "code": "if (eid.length() != 8) return false;"}],
      "constats": [{"niveau": "ok", "texte": "…"}, {"niveau": "warning", "texte": "…"}]
    }
  },
  "bonus": {"retro": {"note": 1, "justification": "…", "constats": []}},
  "points_forts": ["…"],
  "axes": ["…"],
  "recommandations": [{"priorite": "critical", "texte": "…"}, {"priorite": "warning", "texte": "…"}],
  "ia": {"score": 2, "justification": "…",
         "indices": [{"titre": "Commits monolithiques", "niveau": "faible", "texte": "…"}]}
}
```

- `notes` contient **exactement** les `id` des critères du barème ; `bonus` ceux des bonus attribués (absent = 0) ; `malus` ceux des malus appliqués, avec le retrait en positif (`{"m-compile": {"note": 4, "justification": "…", "constats": []}}`, absent = 0).
- Niveaux d'indice IA : `absent`, `faible`, `modere`, `fort`, `tres-fort`.
- Ne calcule aucun total : les sous-totaux, le bonus, le plafonnement et la note finale sont calculés par le script.
- `correcteur` : le nom confirmé à l'étape 0 (chaîne vide s'il n'est pas renseigné).
- Les notes de `notes.json` sont les **notes IA** : ne les modifie jamais après coup pour refléter un avis du correcteur. Ses ajustements vont dans `correcteur.json` (étape 5). Si un `correcteur.json` existe déjà lors d'une nouvelle correction, ne l'efface pas : il reste appliqué par-dessus les nouvelles notes IA, et `render_report.py` échoue si un ajustement ne correspond plus au barème.

Puis rends le rapport du rendu :

```bash
python3 -I <SKILL_DIR>/scripts/render_report.py "<rendu>/correction/notes.json" --bareme "<RACINE>/correction/bareme.json" --promo "<RACINE>/correction"
```

Le script écrit `<rendu>/correction/index.html`, `totaux.json` et les assets. Code 1 : il liste les incohérences (critère manquant ou inconnu, note hors bornes, total du barème faux) ; corrige le JSON et relance.

Écris dans le dossier du rendu uniquement le sous-dossier `correction/` : ne modifie jamais les fichiers de l'étudiant·e.

---

## Étape 3 : pas d'export PDF

Ne génère pas de PDF : les notes doivent d'abord être relues et ajustées par le correcteur (étape 5). C'est lui qui exporte les PDF, à jour de ses ajustements, depuis la synthèse (« Exporter les PDF ») ou depuis chaque rapport (« Imprimer / PDF »). Lance `export_pdf.py` uniquement si on te le demande explicitement.

---

## Étape 4 : synthèse de promo → `<RACINE>/correction/`

1. Écris `<RACINE>/correction/synthese.json` : un commentaire global de 2 phrases et 3 à 6 constats communs à la promo (réussites et erreurs récurrentes), sans nommer les étudiant·es.

   ```json
   {"commentaire": "…", "constats_communs": [{"niveau": "warning", "texte": "…"}]}
   ```

   **Oral de contre-évaluation** (à faire dès qu'un rendu a un score d'indices IA ≥ 3) : écris `<RACINE>/correction/oral.json`, à la racine et jamais dans le dossier d'un rendu, car c'est un document réservé au formateur ou à la formatrice. Pour chaque rendu concerné (clé = nom du dossier du rendu), donne :
   - 2 ou 3 points « à creuser » ;
   - 3 ou 4 questions par axe (`vocabulaire` : les termes que l'étudiant·e a écrits ; `demarche` : l'ordre réel du travail confronté à l'historique git ; `consignes` : ce que demandait l'énoncé et où le rendu y répond). Chaque question s'appuie sur **son** code (fichier:ligne, commit) et donne `attendu` (réponse de niveau Maîtrise) et `alerte` (ce qui fait baisser le niveau) ;
   - un `exercice` en direct de 5 minutes sur son propre code (souvent : écrire le test qui montre un bug qu'il ou elle a signalé).

   ```json
   {"seuil": 3, "descriptions": {"consignes": "Les exigences de l'énoncé…"},
    "rendus": {"charlie": {"a_creuser": ["…"],
      "axes": {"vocabulaire": [{"question": "…", "attendu": "…", "alerte": "…"}], "demarche": [], "consignes": []},
      "exercice": {"consigne": "…", "attendu": "…"}}}}
   ```

   Le but n'est pas de prouver l'usage d'une IA mais de mesurer la maîtrise : formule des questions ouvertes, à poser en montrant le code. **Rédige les questions, les points à creuser et l'exercice au tutoiement** (« Montre-moi… », « Comment as-tu… ? »), même pour un binôme : l'oral est individuel. La page `oral.html` reprend le déroulé (trois axes, auto-évaluation, confrontation, note conservée = note × moyenne des axes) et est **éditable pendant l'oral** (étape 5). Elle n'est liée que depuis la synthèse, n'est pas exportée en PDF, et `build_index.py` signale un rendu à score IA ≥ seuil qui n'a pas de questions (la page propose alors des notes libres). Le seuil vaut 3, ou `"seuil"` dans `oral.json`. Si le correcteur a déclenché un oral (`"oral": {"demande": true}` dans `correcteur.json` ou `ajustements.json`) et te demande de le préparer, ajoute les questions de ce rendu à `oral.json` puis relance `build_index.py`.

   Facultatif, si on te le demande : `<RACINE>/correction/exemples.md` (meilleurs exemples de code, avant / après, tests, builders), en indiquant pour chacun **qui** et **où** (fichier:ligne, commit). Le titre `#` devient le titre de la section, les `##` entrent dans la table des matières, et les blocs ```` ```java ```` sont colorés.

2. Construis la page :

   ```bash
   python3 -I <SKILL_DIR>/scripts/build_index.py "<RACINE>/correction" --rendus "<RENDUS>"
   ```

   Elle affiche la moyenne (cadran), la médiane, le min / max, l'écart-type, le nombre de rendus à la moyenne, le tableau des notes par rendu et par partie (chaque nom ouvre le rapport détaillé ; le lien « PDF » l'ouvre et lance son impression), la répartition des grades A→F, les moyennes par partie et par critère, les constats communs, les meilleurs exemples si `exemples.md` existe, et écrit `notes.csv` (séparateur `;`, UTF-8 avec BOM pour Excel). Elle indique si des oraux de contre-évaluation sont nécessaires (carte « Oui · N » / « Non ») et écrit toujours `oral.html`, où le correcteur peut aussi déclencher un oral pour un rendu sans indices IA.

---

## Étape 5 : ajustement des notes par le correcteur

Chaque critère, bonus et malus affiche deux notes : la **note IA** (celle de `notes.json`, figée) et la **note correcteur**, égale par défaut à la note IA. Les rapports embarquent le barème et les notes IA, et `assets/grader.js` (copié avec les assets) gère l'ajustement **sans serveur ni script à lancer** : il suffit d'ouvrir les fichiers HTML dans un navigateur.

- **Dans un rapport** : le correcteur modifie le champ « Note correcteur » ; dès qu'une note diffère de la note IA, un champ « Commentaire » facultatif apparaît. Les parties, le bonus, le malus, la note finale, le grade et les jauges sont recalculés en direct, et l'état est enregistré automatiquement dans le navigateur. La barre du bas permet de renseigner le nom du correcteur, d'exporter `correcteur.json`, de revenir aux notes IA et d'imprimer le rapport à jour en PDF (nom de fichier proposé : celui du rendu).
- **Dans la synthèse** : les notes ajustées dans les rapports apparaissent (note IA, note correcteur, écart, moyennes, grades). Actions : « Exporter les PDF » (ouvre chaque rapport à tour de rôle et lance son impression, notes ajustées comprises ; un clic par rapport, car les navigateurs bloquent l'ouverture de plusieurs fenêtres d'un coup), « Exporter ajustements.json » (tous les rendus), « Importer… » (un `ajustements.json` ou `correcteur.json`) et, sous Chrome / Edge, « Enregistrer dans « <RACINE> » », qui écrit `correcteur.json` directement dans chaque `<rendu>/correction/`.

Les totaux, le grade, la synthèse, `notes.csv` et l'oral suivent la note correcteur ; la note IA reste visible à côté. Le calcul JavaScript applique les mêmes règles que `render_report.py`.

### Oral de contre-évaluation : saisi pendant l'oral, il compense la note

La synthèse indique toujours si des oraux sont nécessaires : carte « Oraux de contre-évaluation » avec **Oui · N** (noms des rendus concernés, nombre d'oraux réalisés, lien vers la page) ou **Non** quand aucun rendu n'atteint le seuil, et une pastille « oral à faire » / « oral 75 % » à côté du score IA de chaque rendu concerné. Le rapport du rendu affiche aussi une carte « Oral de contre-évaluation ».

**Oral déclenché par le correcteur** : même sans indice d'usage IA suffisant, le correcteur peut demander un oral pour n'importe quel rendu, depuis le bouton « Déclencher un oral » de la carte du rapport, la pastille « ＋ oral » d'une ligne de la synthèse (double clic) ou la liste « Déclencher un oral pour » de la page des oraux. La demande est enregistrée comme les ajustements (`"oral": {"demande": true}`) et peut être annulée tant que l'oral n'a pas eu lieu (« Annuler la demande », ce qui efface ses saisies). Le rendu est alors compté dans « Oui · N » et suit la même règle de note.

`oral.html` est toujours écrite, avec une section par rendu : visible si l'oral est prévu ou demandé, masquée sinon. Sans questions préparées dans `oral.json`, la section propose des notes libres. Pendant l'oral, le correcteur saisit dans la page :

- ce qui a été dit, sous chaque question (ou dans un champ libre) et pour l'exercice en direct ;
- par axe : le niveau d'auto-évaluation, sa propre lecture, la justification de l'étudiant·e, ses notes, et un niveau retenu après discussion (par défaut : sa lecture, sinon l'auto-évaluation) ;
- la case **Oral réalisé** et un commentaire de synthèse.

Les saisies sont enregistrées au fil de l'eau avec les ajustements du rendu (champ `oral` de `correcteur.json` / `ajustements.json`, exportés et enregistrés par les mêmes boutons). Dès que l'oral est réalisé et les trois axes évalués, **note conservée = note correcteur × moyenne des trois axes** : elle remplace la note finale (et le grade, la note ramenée) dans le rapport, la synthèse, les PDF et `notes.csv` (colonnes « Note avant oral » et « Oral (maîtrise %) »). `render_report.py` et `build_index.py` appliquent la même règle.


**Intégrer les ajustements aux fichiers** (`notes.csv`, `totaux.json`, rendu statique des pages) : quand on te le demande, relance `render_report.py` et `build_index.py`. Ils lisent `<rendu>/correction/correcteur.json` ou, à défaut, l'entrée du rendu dans `<RACINE>/correction/ajustements.json` (le plus récent, champ `maj`, l'emporte). Ne modifie jamais `notes.json` pour y reporter un ajustement.

Format de `correcteur.json` (seules les notes ajustées ou commentées y figurent) :

```json
{"correcteur": "Prénom Nom", "rendu": "rendus/Logan - Ororo", "date": "27 novembre 2026", "maj": "2026-11-27T10:12:00.000Z",
 "notes": {"regle-cle": {"note": 0.5, "commentaire": "Découpage juste malgré la formule fausse."}},
 "bonus": {}, "malus": {},
 "oral": {"demande": true, "fait": true, "date": "27 novembre 2026",
          "axes": {"vocabulaire": {"auto": 75, "enseignant": 50, "retenu": null, "justification": "…", "notes": "…"},
                   "demarche": {…}, "consignes": {…}},
          "reponses": {"vocabulaire:1": "…"}, "exercice": "…", "commentaire": "…"}}
```

Niveaux possibles : 100 (maîtrise), 75 (l'essentiel), 50 (hésite), 0 (ne sait pas).

`ajustements.json` regroupe ces objets : `{"bareme": "…", "maj": "…", "rendus": {"<rendu>": {…}}}`.

L'état enregistré dans le navigateur dépend du navigateur et de la machine : rappelle au correcteur d'exporter ou d'enregistrer dans les dossiers pour garder ses ajustements.

Termine en affichant :

```
Synthèse → <chemin absolu>/correction/index.html
Rapports → N rendus (HTML) dans <RENDUS>/*/correction/
Moyenne : X/Y · médiane : X · min–max : X–Y
Ajuster  → ouvrir la synthèse, relire et ajuster les notes dans chaque rapport
PDF      → depuis la synthèse, « Exporter les PDF » une fois les notes relues
Oraux   → Oui : N rendus à score IA ≥ 3 → <chemin absolu>/correction/oral.html, à saisir pendant l'oral (sinon : « Oraux → Non »)
```

---

## Grades

Le grade dépend du pourcentage de la note hors bonus : **A** ≥ 85 %, **B** ≥ 70 %, **C** ≥ 55 %, **D** ≥ 40 %, **E** ≥ 25 %, **F** en dessous. Les couleurs des jauges suivent le ratio note / maximum : vert ≥ 75 %, ambre de 50 à 74 %, rouge en dessous.

## Règles de notation

- **Le barème fait foi** : un critère non couvert par le rendu vaut 0, avec une justification qui dit ce qui manque.
- **Preuves avant tout** : chaque note cite au moins un fichier, une ligne, un commit ou l'absence constatée d'un élément.
- **Même exigence pour tous** : relis les notes d'un même critère entre rendus avant la synthèse, et harmonise si deux rendus comparables ont des notes différentes.
- **Ne sur-note pas, ne sous-note pas** : reconnais les réussites, nomme les manques.
- **Information manquante** (fichier illisible, binaire, rendu vide) : dis-le dans la justification et note sur ce qui est vérifiable.
- **Confidentialité** : les rapports restent dans les dossiers locaux ; rien n'est publié ni envoyé.
