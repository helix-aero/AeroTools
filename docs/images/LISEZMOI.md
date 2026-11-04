# Captures d'écran de la documentation

Les images de ce dossier illustrent `docs/TUTORIAL.md`, `docs/TUTORIAL.fr.md` et les propositions de README.
Elles viennent des captures Greenshot du 04/11/2026 (fenêtres « Review panel »). Les originaux restent dans
`Desktop\Screenshots_greenshot` ; ici ce sont des copies.

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

> Ces captures ont été prises avant les derniers changements de l'interface : le Review panel est resté identique, mais la carte
> Détection IA et le panneau principal visibles dans certaines captures originales n'ont plus la même disposition. Seules les huit
> images ci-dessus, qui ne montrent que le Review panel, sont utilisées.

## Encore à faire

Les `.md` les référencent déjà sous forme de commentaire `<!-- TODO screenshot: ... -->`. Quand un fichier existe, remplacez le
commentaire par `![texte](images/nom.png)`.

| Fichier attendu | Ce qu'il doit montrer |
|---|---|
| `panel-overview.png` | Le panneau FastGCP complet accroché à droite de Metashape : en-tête, Play/Pause, carte Détection IA, carte marqueur actif, tableau, Contrôle des résidus, Exporter, crédit helix.aero |
| `marker-list.png` | Le tableau avec une multi-sélection (Maj + clic) et le menu du clic droit « Supprimer les projections » / « Supprimer les marqueurs » |
| `ai-card.png` | La carte Détection IA : Lancer (vert), Effacer les propositions, Réviser, et les trois listes |
| `refine-window.png` | La fenêtre d'affinage : titre « Affiner le marqueur … sur … », croix rouge et verte, panneau de curseurs, barre `n/N pointées`, Annuler / Suivant / Valider et quitter, et idéalement une seconde capture avec le bouton vert Valider |
| `settings-window.png` | La fenêtre Paramètres : cases, Dossier de stockage, Modèle IA (Aero V1.0 + un Local), Affiner mon modèle avec mes données |
| `settings-training-warning.png` | L'avertissement avant le réentraînement |
| `report-ai-page.png` | Une page du rapport PDF « Photos pointées par l'IA » avec au moins une photo exclue (grande croix rouge) |
| `validate-busy.png` | La petite fenêtre « Application en cours » avec sa barre de progression, pendant Valider |

Pour en ajouter : prenez la capture avec Greenshot, copiez-la ici sous le nom attendu, puis remplacez le commentaire dans les
quatre `.md` (`TUTORIAL.md`, `TUTORIAL.fr.md`, `README.proposition.md`, `README.proposition.fr.md`).
