"""EPN (EUREF Permanent GNSS Network, Europe), through the open mirror of the BKG (Germany).

Anonymous HTTPS, no account. Same layout as the IGS mirror, under `root_ftp/EUREF`. Checked 08/11/2026: daily 30 s files, 1 s files of
15 minutes, per-station navigation files (mixed or per constellation) and the worldwide broadcast file of the IGS mirror as a fallback.
Stations: the EPN stations with a current position and velocity in the EUREF reference frame solution, expressed in ETRF2000 (the
ETRS89 realisation RGF93 v2b of the RGP also belongs to; the positions are moved to the date of the flight with the EPN velocities).
Licence: EPN data are open, CC BY 4.0 unless a station operator states another open licence (EPN Data Access and Use Policy, 2 July 2026).
"""

from . import archive, igs

ROOT = "https://igs.bkg.bund.de/root_ftp/EUREF"


class Epn(archive.Rinex3Provider):
    id = "epn"
    name = "EPN (EUREF Permanent GNSS Network)"
    short = "EPN"
    color = "#14b8c4"
    licence = ("CC BY 4.0 unless the station operator states another open licence; credit EUREF, the EPN and the station operators "
               "(EPN Data Access and Use Policy, https://epncb.oma.be)")
    licence_short = "CC BY 4.0"
    site = ROOT
    frame_group = "ETRS89"
    frame_label = "ETRF2000"
    frame_short = "ETRF2000"
    snapshot = "epn_stations.json"
    snapshot_source = "https://epncb.oma.be/pub/product/referenceframe/C2415/EUR0OPSSNX_1996001_2026115_00U_SOL_ETRF2000.SSC"
    templates = (
        archive.Template("hr_1s", "1 s, RINEX 3, 15 min files", 1.0, 15,
                         lambda s: "%s/highrate/%s/%s" % (ROOT, igs.day_folder(s), igs.hour_letter(s)), "15M", "01S"),
        archive.Template("day_30s", "30 s, RINEX 3, daily files", 30.0, 1440, lambda s: "%s/obs/%s" % (ROOT, igs.day_folder(s)),
                         "01D", "30S"),
    )
    nav_dir = staticmethod(lambda s: "%s/obs/%s" % (ROOT, igs.day_folder(s)))
    nav_global = staticmethod(igs.broadcast)


PROVIDER = Epn()
