"""Aero-Facade drawing sheet: the words written INTO the drawing (data, not interface text).

The interface texts of the tool live in `lang/en.py`. These ones are different: they end up inside the DXF / DWG for the person who opens the
drawing, in the language chosen on the export screen ("fr" is the default: the drawing is usually handed to a French-speaking client).
Everything here is plain ASCII on purpose (the DXF is R2000, ASCII, no code page); a letter with an accent would be written with the
DXF escape `\\U+00C7` by `dxfout`, and none is needed. `%%d` is AutoCAD's code for the degree sign.

One table per language. `{...}` fields are filled by `sheet.py`.
"""

DEFAULT_LANGUAGE = "fr"
LANGUAGES = ("fr", "en")

# The cardinal point the wall FACES (the outward normal), in 45 degree sectors, clockwise from north.
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

TABLE = {
    "fr": {
        "title": "FACADE {direction}",
        "dir": {"N": "NORD", "NE": "NORD-EST", "E": "EST", "SE": "SUD-EST", "S": "SUD", "SW": "SUD-OUEST", "W": "OUEST",
                "NW": "NORD-OUEST"},
        "detail": "azimut {azimuth}%%d - {width} x {height} m",
        "pixel": "pixel {pixel} cm",
        "axis_relative": "Y = hauteur (m)",
        "axis_altitude": "Y = altitude (m)",
        "note_relative": "Hauteurs relatives : zero = point le plus bas des facades retenues",
        "note_altitude": "Altimetrie : {datum}",
        "note_units": "Aero-Facade{site} - unites : metres, echelle 1:1 - X le long des facades, Y = {y}",
        "y_relative": "hauteur",
        "y_altitude": "altitude",
    },
    "en": {
        "title": "{direction} FACADE",
        "dir": {"N": "NORTH", "NE": "NORTH-EAST", "E": "EAST", "SE": "SOUTH-EAST", "S": "SOUTH", "SW": "SOUTH-WEST", "W": "WEST",
                "NW": "NORTH-WEST"},
        "detail": "azimuth {azimuth}%%d - {width} x {height} m",
        "pixel": "pixel {pixel} cm",
        "axis_relative": "Y = height (m)",
        "axis_altitude": "Y = altitude (m)",
        "note_relative": "Relative heights: zero = lowest point of the selected facades",
        "note_altitude": "Altimetry: {datum}",
        "note_units": "Aero-Facade{site} - units: metres, scale 1:1 - X along the facades, Y = {y}",
        "y_relative": "height",
        "y_altitude": "altitude",
    },
}


def table(language):
    """The label table of a language ("fr" or "en"); anything else gives the default one."""
    return TABLE.get(str(language or "").lower(), TABLE[DEFAULT_LANGUAGE])
