"""Geoscience Australia GNSS Data Centre (AUSCORS and the ARGN): the open S3 bucket, no account.

Anonymous HTTPS on `https://ga-gnss-data-rinex-v1.s3.amazonaws.com/public/` (documented by GA, checked 08/11/2026):
  * `daily/YYYY/DDD/` 30 s files and the daily navigation file of each station (`..._01D_MN.rnx.gz`);
  * `hourly/YYYY/DDD/HH/` 30 s files of one hour (`..._01H_30S_MO.crx.gz`);
  * `highrate/YYYY/DDD/HH/` 1 s files of 15 minutes, kept for years.
Licence: Creative Commons Attribution 3.0 Australia (the data centre pages carry the CC BY mark).
Stations: those of the APREF weekly solution that are Australian (country code AUS), in IGS20 at the epoch of the solution, with a
velocity. GDA2020 (the Australian legal datum, fixed to the plate) is NOT used: Aero-PPK does not convert frames, so positions
come out in IGS20 at the date of the flight and the user converts them with the official GDA2020 transformation.
"""

from .. import gnsstime
from . import archive, igs

ROOT = "https://ga-gnss-data-rinex-v1.s3.amazonaws.com/public"


def day_folder(slot):
    return "%04d/%03d" % (slot.year, gnsstime.day_of_year(slot))


class GeoscienceAustralia(archive.Rinex3Provider):
    id = "ga"
    name = "Geoscience Australia (AUSCORS, ARGN)"
    short = "GA"
    color = "#84cc16"
    licence = ("Creative Commons Attribution 3.0 Australia; credit Geoscience Australia, GNSS Data Centre "
               "(https://data.gnss.ga.gov.au)")
    licence_short = "CC BY 3.0 AU"
    site = ROOT
    frame_group = "IGS20"
    frame_label = "IGS20 (ITRF2020)"
    frame_short = "IGS20"
    snapshot = "ga_stations.json"
    snapshot_source = "https://ga-gnss-products-v1.s3.amazonaws.com/public/2440/AUS24407.SNX.gz"
    templates = (
        archive.Template("hr_1s", "1 s, RINEX 3, 15 min files", 1.0, 15,
                         lambda s: "%s/highrate/%s/%02d" % (ROOT, day_folder(s), s.hour), "15M", "01S"),
        archive.Template("hour_30s", "30 s, RINEX 3, hourly files", 30.0, 60,
                         lambda s: "%s/hourly/%s/%02d" % (ROOT, day_folder(s), s.hour), "01H", "30S"),
        archive.Template("day_30s", "30 s, RINEX 3, daily files", 30.0, 1440, lambda s: "%s/daily/%s" % (ROOT, day_folder(s)), "01D",
                         "30S"),
    )
    nav_dir = staticmethod(lambda s: "%s/daily/%s" % (ROOT, day_folder(s)))
    nav_global = staticmethod(igs.broadcast)


PROVIDER = GeoscienceAustralia()
