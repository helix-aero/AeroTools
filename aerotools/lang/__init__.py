"""Catalogues de langue d'AeroTools : un module par langue (fr.py, en.py).

Chaque module definit deux dictionnaires :
  TEXTES   cle -> texte, avec des champs `{nom}` pour les valeurs variables
  FORMATS  "decimal", "date" (motif strftime) et "csv_delimiter"

Les modules ne sont importes que par aerotools.i18n, a la demande. Pour ajouter
une langue : copier fr.py en <code>.py, traduire, puis ajouter une ligne a
`i18n.LANGUES`.
"""
