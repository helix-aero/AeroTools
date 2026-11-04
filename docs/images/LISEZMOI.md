# Captures d'écran de la documentation

Les images de ce dossier illustrent `docs/TUTORIAL.md`, `docs/TUTORIAL.fr.md` et les propositions de README.
Huit viennent des captures Greenshot du 04/11/2026 (fenêtres « Review panel ») ; les originaux restent dans
`Desktop\Screenshots_greenshot`, ici ce sont des copies. Cinq autres (panneau FastGCP, tableau, carte IA, Paramètres) ont été
prises le 04/11/2026 directement dans Metashape ouvert, interface en anglais, sur le projet CITADELLE_Pompeani.

## Déjà là

| Fichier | Ce qu'il montre |
|---|---|
| `review-panel-overview.png` | Review panel sur PS_1 : pastilles de statut (croix rouge, coches vertes), ligne de bilan, panneau de droite |
| `review-panel-markers.png` | Liste de tous les marqueurs à gauche, 573 propositions gardées |
| `review-panel-adjust-before.png` | Zoom 400 %, réglages d'affichage presque neutres |
| `review-panel-adjust-after.png` | Mêmes vignettes avec exposition -60, contraste +85, saturation +69 |
| `review-panel-excluded.png` | Vignettes au zoom 131 % sur PS_6 ; la ligne du bas compte 559 gardés, 10 exclus, 4 affinés (aucune pastille visible dans la zone défilée) |
| `review-panel-context-menu.png` | Menu du clic droit : Exclure, Remettre, Affiner… |
| `review-panel-selected.png` | PS_2 : une vignette sélectionnée (contour vert, confiance 0,66), menu du clic droit ouvert, boutons Exclure et Affiner… actifs, Remettre grisé |
| `review-panel-refined.png` | PS_11 à 400 % : première vignette affinée (croix verte, coche verte), les autres gardent la croix rouge ; 572 gardés, 1 affiné |
| `panel-overview.png` | Le panneau FastGCP entier : en-tête, bouton Play/Pause avec « Tool active », carte AI DETECTION, marqueur actif PS_4, recherche, tableau, Residual check, Export, crédit helix.aero. Utilisée dans `README.proposition.md`, `README.proposition.fr.md` et les deux `TUTORIAL` |
| `marker-list.png` | Le tableau des marqueurs : colonnes ID, Error X/Y/Z et Proj., marqueur actif PS_4 surligné. Elle ne montre ni multi-sélection ni menu du clic droit. Utilisée dans les deux `TUTORIAL` |
| `ai-card.png` | La carte AI DETECTION : Run (vert), Clear proposals et Review grisés, trois listes (Spray, All markers, Ignore green + blue). Utilisée dans les deux `TUTORIAL` |
| `settings-window.png` | La fenêtre Settings : General (Open the review panel automatically, Save marker picks, Log) et AI models (dossier de stockage, Aero V1.0, Refine my model using my data). Elle laisse voir le chemin local `C:\Users\LTB1\AppData\Local\HELIX\AeroTools`. Utilisée dans les deux `TUTORIAL` |
| `settings-training-warning.png` | L'avertissement « Refine the AI model » avec ses boutons Yes et No. Pris puis refusé par No : aucun entraînement lancé. Utilisée dans les deux `TUTORIAL` |

> Les huit captures `review-panel-*` ont été prises avant les derniers changements de l'interface : le Review panel est resté
> identique, mais la carte Détection IA et le panneau principal visibles dans certaines captures originales n'ont plus la même
> disposition. Elles ne servent donc que pour le Review panel ; le panneau principal et la carte IA viennent des cinq captures
> plus récentes.

## Encore à faire

Les `.md` les référencent déjà sous forme de commentaire `<!-- TODO screenshot: ... -->`. Quand un fichier existe, remplacez le
commentaire par `![texte](images/nom.png)`.

| Fichier attendu | Ce qu'il doit montrer | Pourquoi pas encore pris |
|---|---|---|
| `refine-window.png` | La fenêtre d'affinage : titre « Affiner le marqueur … sur … », croix rouge et verte, panneau de curseurs, barre `n/N pointées`, Annuler / Suivant / Valider et quitter, et idéalement une seconde capture avec le bouton vert Valider | Demande une vraie détection IA (Run) sur le projet ouvert, non lancée pour ne pas modifier le projet |
| `report-ai-page.png` | Une page du rapport PDF « Photos pointées par l'IA » avec au moins une photo exclue (grande croix rouge) | Demande une détection puis un export du rapport |
| `validate-busy.png` | La petite fenêtre « Application en cours » avec sa barre de progression, pendant Valider | Demande de valider des pointés de l'IA, donc de modifier le projet |

Pour en ajouter : prenez la capture avec Greenshot, copiez-la ici sous le nom attendu, puis remplacez le commentaire dans les
`.md` concernés (`TUTORIAL.md`, `TUTORIAL.fr.md`, `README.proposition.md`, `README.proposition.fr.md`).
