"""NOAA CORS (National Geodetic Survey, USA): the open data bucket on AWS, no account.

Anonymous HTTPS on `https://noaa-cors-pds.s3.amazonaws.com/` (the NOAA Open Data Dissemination bucket, checked 08/11/2026):
  * `rinex/YYYY/DDD/ssss/ssssDDD0.YYd.gz`: one RINEX 2 file per day, Hatanaka compressed then gzip. The sampling depends on the
    station (30 s, 15 s, 5 s or 1 s); only the file header says, so the survey reads it;
  * `rinex/YYYY/DDD/brdcDDD0.YYn.gz` and `.YYg.gz`: GPS and GLONASS broadcast ephemerides of the day (no Galileo);
  * `coord/coord_20/ssss_20.coord.txt`: the station coordinates (ITRF2020 and NAD 83 (2011)), the source of the snapshot.
Frame: NAD 83 (2011), epoch 2010.0, the positions NGS publishes for the antenna reference point. The frame is fixed to the North
American plate and the published position is used as it is, whatever the date of the flight. Licence: data distributed free of
charge by NOAA / NGS, used at the risk of the user; credit NOAA, NGS and the station operators.
"""

from .. import gnsstime
from . import archive

ROOT = "https://noaa-cors-pds.s3.amazonaws.com/rinex"


def day_folder(slot):
    return "%s/%04d/%03d" % (ROOT, slot.year, gnsstime.day_of_year(slot))


def nav_file(slot, letter):
    name = "brdc%03d0.%02d%s.gz" % (gnsstime.day_of_year(slot), slot.year % 100, letter)
    return "%s/%s" % (day_folder(slot), name), name


class NoaaCors(archive.DailyRinex2Provider):
    id = "noaa"
    name = "NOAA CORS (National Geodetic Survey)"
    short = "CORS"
    color = "#f08c2e"
    licence = ("NOAA / NGS data, distributed free of charge, used at your own risk; credit NOAA, the National Geodetic Survey and "
               "the station operators (https://geodesy.noaa.gov/CORS/)")
    licence_short = "free of charge, credit NOAA / NGS"
    site = "https://noaa-cors-pds.s3.amazonaws.com"
    frame_group = "NAD83(2011)"
    frame_label = "NAD 83 (2011), epoch 2010.0"
    frame_short = "NAD83(2011)"
    snapshot = "noaa_stations.json"
    snapshot_source = "https://noaa-cors-pds.s3.amazonaws.com/coord/coord_20/"
    daily_dir = staticmethod(day_folder)
    nav_url = staticmethod(nav_file)


PROVIDER = NoaaCors()
