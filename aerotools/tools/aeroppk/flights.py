"""Flights of step 1: find the photo folders, read what is next to the photos, describe a flight. Pure logic, no Qt, no Metashape."""

import os

import numpy as np

from . import drones, gnsstime

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".dng", ".tif", ".tiff")


def folders_of(paths):
    """Distinct folders of a list of photo paths, in order of appearance."""
    out = []
    for path in paths:
        folder = os.path.dirname(path)
        if folder and folder not in out:
            out.append(folder)
    return out


def image_count(folder):
    try:
        return sum(1 for n in os.listdir(folder) if n.lower().endswith(IMAGE_EXTENSIONS))
    except OSError:
        return 0


def make_flight(reader, folder):
    """The dictionary the rest of the tool works with: reader, files, events, needs, warnings."""
    files = reader.scan(folder)
    return refresh(reader, files)


def refresh(reader, files):
    """Read the events and the checklist again (after the user pointed at a file by hand)."""
    events, warnings = reader.read_events(files) if files.events_file else ([], [])
    return {"reader": reader, "files": files, "events": events, "needs": reader.needs(files), "warnings": warnings}


def scan_folders(folders, reader_id=None):
    """(flights, unread): `flights` for the folders a reader understands, `unread` = [(folder, image count)] for the others.

    With `reader_id` that reader is used for every folder that holds images (the user chose the device); without it each folder goes to
    the first reader that recognises it."""
    flights, unread = [], []
    for folder in folders:
        if reader_id:
            reader = drones.reader_by_id(reader_id)
        else:
            reader = drones.reader_for(folder)
        if reader is None or (not reader_id and not reader.accepts(folder)):
            count = image_count(folder)
            if count:
                unread.append((folder, count))
            continue
        flights.append(make_flight(reader, folder))
    return flights, unread


def expand_folder(folder):
    """A folder the user picked: itself, then its sub folders (a card with several flight folders)."""
    out = [folder]
    try:
        out += [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if os.path.isdir(os.path.join(folder, n))]
    except OSError:
        pass
    return out


def describe(flight):
    """Plain facts of a flight for the property grid: name, photos, events, start, end (GPS datetimes), duration (s), rover interval."""
    files, events = flight["files"], flight["events"]
    out = {"name": files.name, "photos": len(files.photos), "events": len(events), "start": None, "end": None,
           "duration": None, "folder": files.folder}
    if events:
        times = [e.time for e in events]
        out["start"], out["end"] = gnsstime.from_seconds(min(times)), gnsstime.from_seconds(max(times))
        out["duration"] = float(max(times) - min(times))
    return out


def site_of(flights):
    """(lat, lon, footprint) of the photos from the drone's own positions: the mean position and a decimated list of points. None if none."""
    points = [e.onboard[:2] for f in flights for e in f["events"] if e.onboard and abs(e.onboard[0]) > 0.0]
    if not points:
        return None
    arr = np.array(points)
    return float(arr[:, 0].mean()), float(arr[:, 1].mean()), [tuple(p) for p in arr[::max(1, len(arr) // 400)]]
