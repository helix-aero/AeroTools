"""IGS (International GNSS Service), the worldwide reference network, through the open mirror of the BKG (Germany).

Anonymous HTTPS, no account (the main IGS data centre, CDDIS, wants a login and is not used). Checked 08/11/2026:
  * daily 30 s files `obs/YYYY/DDD/SSSSMRCCC_R_YYYYDDD0000_01D_30S_MO.crx.gz`, available about a day later;
  * 1 s files of 15 minutes `highrate/YYYY/DDD/<hour letter>/SSSSMRCCC_S_YYYYDDDHHMM_15M_01S_MO.crx.gz`, available within hours;
  * navigation: one worldwide broadcast file per day `BRDC/YYYY/DDD/BRDC00IGS_R_YYYYDDD0000_01D_MN.rnx.gz` (mixed constellations).
Stations: the IGS reference frame stations (IGc20), with positions and velocities in IGS20, an ITRF2020 realisation. See
`sources.py` for how the snapshot is made and docs/AEROPPK.md for what was verified.
"""

from .. import gnsstime
from . import archive

ROOT = "https://igs.bkg.bund.de/root_ftp/IGS"
BRDC_NAME = "BRDC00IGS_R_%04d%03d0000_01D_MN.rnx.gz"


def day_folder(slot):
    return "%04d/%03d" % (slot.year, gnsstime.day_of_year(slot))


def hour_letter(slot):
    return chr(ord("a") + slot.hour)


def broadcast(slot):
    """The worldwide broadcast ephemeris file of the day of `slot` on the BKG server (shared by IGS and EPN)."""
    name = BRDC_NAME % (slot.year, gnsstime.day_of_year(slot))
    return "%s/BRDC/%s/%s" % (ROOT, day_folder(slot), name), name


class Igs(archive.Rinex3Provider):
    id = "igs"
    name = "IGS (International GNSS Service)"
    short = "IGS"
    color = "#e0529c"
    licence = ("Open data, no restriction; cite and credit the IGS and the station operators "
               "(IGS Data and Product Disclaimer and Terms of Use, 5 August 2020, https://igs.org)")
    licence_short = "open data, credit the IGS"
    site = ROOT
    frame_group = "IGS20"
    frame_label = "IGS20 (ITRF2020)"
    frame_short = "IGS20"
    snapshot = "igs_stations.json"
    snapshot_source = "https://files.igs.org/pub/station/coord/IGc20/IGc20.ssc"
    templates = (
        archive.Template("hr_1s", "1 s, RINEX 3, 15 min files", 1.0, 15,
                         lambda s: "%s/highrate/%s/%s" % (ROOT, day_folder(s), hour_letter(s)), "15M", "01S"),
        archive.Template("day_30s", "30 s, RINEX 3, daily files", 30.0, 1440, lambda s: "%s/obs/%s" % (ROOT, day_folder(s)), "01D",
                         "30S"),
    )
    nav_dir = staticmethod(lambda s: "%s/obs/%s" % (ROOT, day_folder(s)))
    nav_global = staticmethod(broadcast)


PROVIDER = Igs()
