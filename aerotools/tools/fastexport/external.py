"""Fast Export : les logiciels externes qu'on peut utiliser (Global Mapper, AutoCAD, GDAL), detectes sur la machine.

Rien n'est suppose : aucun chemin n'est ecrit en dur. On cherche dans le registre et dans les dossiers
d'installation habituels ; l'utilisateur peut aussi designer l'executable (le chemin est alors garde dans les reglages).

  Global Mapper  ecrit l'ECW (le seul moyen de l'obtenir sans cle OEM Hexagon) ; il lit un script .gms
  AutoCAD        `accoreconsole.exe` (moteur sans fenetre) transforme un DXF en DWG et verifie que les images se chargent
  GDAL           (QGIS / OSGeo4W) ecrit le JPEG 2000 quand on prefere ne pas utiliser celui de Metashape

Rien n'est telecharge ni installe ici. Les liens d'achat sont des adresses officielles, ouvertes par l'utilisateur.
"""

import glob
import os
import re
import subprocess
import tempfile
import time

try:                                        # Windows seulement
    import winreg
except ImportError:                         # pragma: no cover
    winreg = None

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GLOBAL_MAPPER_URL = "https://www.bluemarblegeo.com/global-mapper/"
AUTOCAD_URL = "https://www.autodesk.com/products/autocad/overview"
QGIS_URL = "https://qgis.org/download/"
GM_NAMES = ("global_mapper.exe",)
ACCORE = "accoreconsole.exe"
ECW_RATIO = 10                              # rapport de compression ECW demande a Global Mapper (10:1, qualite visuelle)
JP2_QUALITY = 5                             # OpenJPEG : ~9:1. Mesure sur CITADELLE a poids voisin de l'ECW 10:1 : 42 dB contre 37 dB
JP2_GM_RATIO = 20                           # Global Mapper en JPEG 2000 : 11:1, 41 dB (meme mesure)
GM_TIMEOUT_S = 3600
ACAD_TIMEOUT_S = 600


def _program_dirs():
    found = []
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value and value not in found:
            found.append(value)
    return found


def _uninstall_locations(pattern):
    """InstallLocation des programmes dont le nom contient `pattern` (registre Windows)."""
    out = []
    if winreg is None:
        return out
    roots = ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(count):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, index)) as sub:
                            name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                            if re.search(pattern, name, re.I):
                                location = str(winreg.QueryValueEx(sub, "InstallLocation")[0])
                                if location:
                                    out.append(location)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _version_key(path):
    """Cle de tri : les nombres du chemin (GlobalMapper26.0, AutoCAD 2023) pour prendre la version la plus recente."""
    return [int(n) for n in re.findall(r"\d+", path)]


def _newest(paths):
    paths = [p for p in paths if os.path.isfile(p)]
    return sorted(paths, key=_version_key)[-1] if paths else ""


def find_global_mapper(saved=""):
    """Chemin de global_mapper.exe ("" si absent). `saved` : chemin deja choisi par l'utilisateur."""
    if saved and os.path.isfile(saved) and os.path.basename(saved).lower() in GM_NAMES:
        return saved
    candidates = []
    for location in _uninstall_locations(r"global\s*mapper"):
        candidates += [os.path.join(location, name) for name in GM_NAMES]
    for base in _program_dirs():
        for name in GM_NAMES:
            candidates += glob.glob(os.path.join(base, "GlobalMapper*", name))
            candidates += glob.glob(os.path.join(base, "Global Mapper*", name))
    return _newest(candidates)


def find_autocad(saved=""):
    """Chemin d'accoreconsole.exe ("" si AutoCAD est absent). `saved` peut etre acad.exe ou accoreconsole.exe."""
    if saved:
        folder = saved if os.path.isdir(saved) else os.path.dirname(saved)
        core = os.path.join(folder, ACCORE)
        if os.path.isfile(core):
            return core
    candidates = []
    for location in _uninstall_locations(r"^AutoCAD\b"):
        candidates.append(os.path.join(location, ACCORE))
    for base in _program_dirs():
        candidates += glob.glob(os.path.join(base, "Autodesk", "AutoCAD*", ACCORE))
    return _newest(candidates)


def find_gdal(saved=""):
    """Dossier des programmes GDAL (QGIS, OSGeo4W) qui contient gdal_translate.exe ("" si absent)."""
    if saved and os.path.isfile(os.path.join(saved, "gdal_translate.exe")):
        return saved
    candidates = []
    for base in _program_dirs() + ["C:\\OSGeo4W64", "C:\\OSGeo4W"]:
        candidates += glob.glob(os.path.join(base, "QGIS*", "bin", "gdal_translate.exe"))
        candidates += glob.glob(os.path.join(base, "bin", "gdal_translate.exe"))
    best = _newest(candidates)
    return os.path.dirname(best) if best else ""


# --------------------------------------------------------------------------------------------------
# Global Mapper : ECW
# --------------------------------------------------------------------------------------------------

def gm_script(src, dst, ratio=ECW_RATIO):
    """Texte du script .gms : un GeoTIFF devient un ECW (verifie avec Global Mapper 26)."""
    return "\n".join(["GLOBAL_MAPPER_SCRIPT VERSION=1.00", "UNLOAD_ALL",
                      'IMPORT FILENAME="%s" TYPE=GEOTIFF' % src.replace("/", "\\"),
                      'EXPORT_RASTER FILENAME="%s" TYPE=ECW TARGET_COMPRESSION=%d' % (dst.replace("/", "\\"), ratio),
                      "UNLOAD_ALL"]) + "\n"


def _wait(process, timeout, tick=None):
    """Attend la fin d'un processus en rendant la main a l'interface. Renvoie True si fini, False si delai depasse."""
    started = time.time()
    while process.poll() is None:
        if time.time() - started > timeout:
            process.kill()
            return False
        if tick:
            tick()
        time.sleep(0.2)
    return True


def _run_gm(gm_exe, script_text, dst, tick=None):
    """Lance Global Mapper sur un script et controle le fichier produit. Renvoie (ok, message)."""
    with tempfile.TemporaryDirectory(prefix="aero_gm_") as work:
        script = os.path.join(work, "run.gms")
        with open(script, "w", encoding="ascii", errors="replace") as handle:
            handle.write(script_text)
        try:
            process = subprocess.Popen([gm_exe, script], cwd=work)
        except OSError as exc:
            return False, "Global Mapper : %s" % exc
        if not _wait(process, GM_TIMEOUT_S, tick):
            return False, "Global Mapper : delai depasse"
    if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        return False, "Global Mapper n'a pas ecrit le fichier (licence ou export refuse ?)"
    return True, ""


def to_ecw(gm_exe, src, dst, ratio=ECW_RATIO, tick=None):
    """GeoTIFF -> ECW par Global Mapper. Renvoie (ok, message). Le fichier produit est controle (existe, non vide)."""
    if os.path.exists(dst):
        os.remove(dst)
    return _run_gm(gm_exe, gm_script(src, dst, ratio), dst, tick)


# --------------------------------------------------------------------------------------------------
# GDAL : JPEG 2000 (OpenJPEG)
# --------------------------------------------------------------------------------------------------

def gdal_env(gdal_dir):
    env = dict(os.environ)
    env["PATH"] = gdal_dir + os.pathsep + env.get("PATH", "")
    return env


def to_jp2_gdal(gdal_dir, src, dst, quality=JP2_QUALITY, tick=None):
    """GeoTIFF RVB -> JPEG 2000 par GDAL (OpenJPEG). `quality` : plus petit = fichier plus leger (5 : environ 9:1)."""
    if os.path.exists(dst):
        os.remove(dst)
    command = [os.path.join(gdal_dir, "gdal_translate.exe"), "-q", "-of", "JP2OpenJPEG", "-co", "QUALITY=%d" % quality,
               "-co", "REVERSIBLE=NO", "-co", "YCC=YES", src, dst]
    try:
        process = subprocess.Popen(command, env=gdal_env(gdal_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=NO_WINDOW)
    except OSError as exc:
        return False, "GDAL : %s" % exc
    if not _wait(process, GM_TIMEOUT_S, tick):
        return False, "GDAL : delai depasse"
    if process.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        return False, "GDAL : echec du JPEG 2000"
    return True, ""


def to_jp2_gm(gm_exe, src, dst, ratio=JP2_GM_RATIO, tick=None):
    """GeoTIFF -> JPEG 2000 par Global Mapper. Renvoie (ok, message)."""
    if os.path.exists(dst):
        os.remove(dst)
    lines = ["GLOBAL_MAPPER_SCRIPT VERSION=1.00", "UNLOAD_ALL", 'IMPORT FILENAME="%s" TYPE=GEOTIFF' % src.replace("/", "\\"),
             'EXPORT_RASTER FILENAME="%s" TYPE=JPEG2000 TARGET_COMPRESSION=%d' % (dst.replace("/", "\\"), ratio), "UNLOAD_ALL"]
    return _run_gm(gm_exe, "\n".join(lines) + "\n", dst, tick)


# --------------------------------------------------------------------------------------------------
# AutoCAD : DWG et controle des images
# --------------------------------------------------------------------------------------------------

def acad_script_check():
    """Script AutoCAD : recharge toutes les images du DXF puis les liste."""
    return "-IMAGE\nR\n*\n-IMAGE\n?\n*\n_QUIT\nY\n"


def acad_script_dwg(dwg, version="2018"):
    return '_SAVEAS\n%s\n"%s"\n_QUIT\nY\n' % (version, dwg.replace("/", "\\"))


def _run_core(core, dxf, script_text, tick=None):
    """Lance accoreconsole sur `dxf` avec le script ; renvoie le texte affiche par AutoCAD ("" si echec)."""
    with tempfile.TemporaryDirectory(prefix="aero_acad_") as work:
        script = os.path.join(work, "run.scr")
        with open(script, "w", encoding="ascii", errors="replace", newline="\n") as handle:
            handle.write(script_text)
        output = os.path.join(work, "out.txt")
        with open(output, "wb") as sink:
            try:
                process = subprocess.Popen([core, "/i", dxf, "/s", script, "/l", "en-US"], stdout=sink,
                                           stderr=subprocess.STDOUT, cwd=work, creationflags=NO_WINDOW)
            except OSError:
                return ""
            if not _wait(process, ACAD_TIMEOUT_S, tick):
                return ""
        try:
            raw = open(output, "rb").read()
        except OSError:
            return ""
    for encoding in ("utf-16", "utf-8", "cp1252"):
        try:
            text = raw.decode(encoding)
            if "AutoCAD" in text:
                return text
        except UnicodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_check(text):
    """Resultat du controle AutoCAD : {"images": nombre d'images chargees, "failed": [noms], "valid": le DXF s'est ouvert}."""
    loaded = re.findall(r"^(\S+) loaded and relinked", text, re.M)
    failed = re.findall(r"^(\S+) .*(?:not found|cannot|unable|failed)", text, re.M | re.I)
    valid = ("Invalid or incomplete DXF" not in text and "Reload image" in text) or bool(loaded)
    return {"images": len(loaded), "failed": [name for name in failed if name not in loaded], "valid": valid}


def check_dxf(core, dxf, tick=None):
    """Ouvre le DXF dans AutoCAD (sans fenetre) et verifie que chaque image se recharge. Renvoie parse_check()."""
    return parse_check(_run_core(core, dxf, acad_script_check(), tick))


def dxf_to_dwg(core, dxf, dwg, tick=None):
    """DXF -> DWG par AutoCAD. Renvoie (ok, message). Le DWG est controle (existe, non vide)."""
    if os.path.exists(dwg):
        os.remove(dwg)
    _run_core(core, dxf, acad_script_dwg(dwg), tick)
    if not os.path.isfile(dwg) or os.path.getsize(dwg) == 0:
        return False, "AutoCAD n'a pas ecrit le DWG"
    return True, ""
