"""Aero-Exports: the export queue (pure data: no Qt, no Metashape).

Each ticked format becomes one `Item` when it is added: the settings read from the window at that moment are copied into it, so
that changing an option afterwards never alters what is already queued. The window runs the items one after the other.
"""

MAX_ITEMS = 6                                         # rows the list shows: it never needs a scroll bar


class Item(object):
    """One planned export.

    `key`: the preset. `settings`: everything the export needs, frozen (chunk, raster info, folder, site, date, pixels, system, height
    grid, cad options, tiles, white background, look...). `jobs`: the plan.Job list (names and versions fixed). `rows`: [(relative file
    name, what it holds)] for the details. `failed`: the readable reason when the last run of this item failed, else ''."""

    def __init__(self, key, settings, jobs, rows):
        self.key, self.settings, self.jobs, self.rows = key, settings, jobs, rows
        self.failed = ""

    @property
    def files(self):
        return len(self.rows)


def total_files(items):
    return sum(item.files for item in items)



class Summary(object):
    """What a run of the list did: `done` [(item, pipeline.Result)] and `failed` [(item, readable reason)]."""

    def __init__(self):
        self.done, self.failed = [], []
