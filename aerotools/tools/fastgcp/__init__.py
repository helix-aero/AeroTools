"""FastGCP - pointage GCP assiste dans la vue photo de Metashape.

Principe : un marqueur est « courant », un clic dans la vue photo (ou Maj+clic)
lui pose une projection, et le panneau Photos se filtre tout seul sur les photos qui
voient ce marqueur — y compris celles d'un GCP jamais pointe, calculees depuis
ses coordonnees de reference.

Modules :
  session.py    etat de la session (marqueur courant, options, references Qt)
  tool.py       install() / uninstall(), appeles par le menu Aero
  panel.py      le dock Qt
  marking.py    filtre d'evenements et pose de la projection
  photos.py     filtre du panneau Photos et passage a la photo suivante
  dataset.py    enregistrement des pointages pour l'apprentissage (optionnel)
  detection.py  point d'accroche de la detection automatique (AIGCP)
"""
