# AeroTools

Petits scripts pour Agisoft Metashape, écrits par [HELIX AERO](https://helix.aero) en Corse.

[English version](README.md)

<!-- Capture du panneau FastGCP : docs/images/fastgcp.png -->

## Pourquoi ce projet

Nous faisons de la cartographie par drone et nous pointons beaucoup de points d'appui (GCP).
Metashape sait le faire, mais le geste n'est pas pratique : on fait défiler des centaines de photos pour trouver celles
qui voient un marqueur, on les ouvre une à une, on zoome à la main, on clique, et on recommence.

Nous avons donc écrit un script pour nous. Il nous a fait gagner assez de temps pour que nous le partagions.
Ce n'est pas un gros logiciel. Il remplace quelques fonctions de Metashape peu ergonomiques,
et il le fait assez bien pour que nous l'utilisions sur tous nos projets.

Si vous faites de la photogrammétrie, il peut vous servir aussi.

## Ce qu'il y a dedans

AeroTools ajoute un menu **Aero** à Metashape. Les outils se chargent quand on clique dessus.

**FastGCP** est le premier outil. C'est un panneau pour pointer les GCP vite :

- On choisit un marqueur et le panneau Photos se filtre sur les photos qui le voient.
- Un clic dans la vue photo pose ou déplace le marqueur exactement là où l'on a cliqué.
- La photo suivante qui voit le marqueur s'ouvre toute seule, zoomée autour du marqueur.
- Ctrl+Z (ou le bouton annuler) défait le dernier point.
- Un tableau liste chaque marqueur avec son erreur X, Y et Z et son nombre de projections. Un clic sur l'en-tête trie.
- Un clic exporte un rapport PDF (chiffres clés, graphiques d'erreur, captures) ou un tableau CSV.
- Le panneau existe en français et en anglais, en thème clair ou sombre.
- En pause, Metashape se comporte comme d'habitude.
- Un interrupteur « AI Training » (facultatif) enregistre où l'on a cliqué, pour entraîner des modèles de détection plus tard.

**AIGCP** vient ensuite. Voir la feuille de route ci-dessous.

## Installation

Il faut Metashape Pro 2.x sous Windows. Nous l'utilisons tous les jours avec Metashape 2.2.

1. Téléchargez ou clonez ce dépôt où vous voulez.
2. Double-cliquez sur `install\installer_aerotools.bat`.
3. Relancez Metashape. Le menu **Aero** est là.

L'installeur écrit un petit lanceur dans `%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\`. Rien d'autre n'est touché.
`install\desinstaller_aerotools.bat` le retire. Sous macOS ou Linux, le même lanceur devrait fonctionner si vous le copiez
vous-même dans le dossier de scripts de Metashape, mais nous n'avons pas essayé.

## Utiliser FastGCP

1. **Aero > FastGCP** (raccourci F8) ouvre le panneau à droite.
2. Choisissez un marqueur dans la liste ou dans le panneau Reference.
3. Cliquez sur la cible dans la vue photo.
4. Recommencez. L'outil passe tout seul à la photo suivante.

On se trompe parfois. Ctrl+Z défait le dernier point, et le Journal (bouton engrenage) dit pourquoi un clic a été refusé.

## Feuille de route

- **AIGCP.** Nous entraînons des poids de détection pour les marques à la craie, quelle que soit la forme de la cible.
  Les points proposés apparaîtront en bleu, pour que vous les acceptiez ou les corrigiez. Nous comptons le publier d'ici quelques semaines.
- D'autres petits outils, au gré de nos besoins sur nos propres projets.

## État du projet

Nous utilisons AeroTools sur des projets réels, mais seulement sur quelques configurations pour l'instant.
L'API de la vue photo de Metashape est peu documentée, il reste donc quelques aspérités.
Si quelque chose casse, ouvrez une issue et collez les lignes du Journal. Cela accélère beaucoup la correction.

## Contribuer

Issues, idées et pull requests sont les bienvenues.

- `main` et `dev` sont protégées. Faites un fork, travaillez sur une branche et ouvrez une pull request vers `dev`.
- Le code tourne dans le Python 3.9 embarqué de Metashape avec PySide2, et aussi sous PySide6. Pas de dépendance en plus, s'il vous plaît.
- Les tests se lancent avec `python -m pytest tests -q`. Ils n'ont pas besoin de Metashape.
- En envoyant une pull request, vous acceptez que votre contribution soit publiée sous AGPL-3.0.
- Plus de détails dans [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Licence

GNU AGPL-3.0, voir [LICENSE](LICENSE). Copyright 2026 HELIX AERO.

Vous pouvez utiliser et modifier AeroTools librement, y compris au travail. Si vous diffusez une version modifiée,
ou si vous la mettez à disposition d'autres personnes via un réseau, vous devez publier vos modifications sous la même
licence. Le plus simple est de nous les renvoyer ici par pull request. Si cela ne vous convient pas, contactez-nous
et nous pourrons discuter d'autres conditions.
