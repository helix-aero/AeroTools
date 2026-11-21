"""Which chunk Aero-PPK works on. Pure logic, no Qt and no Metashape (tested without either).

Aero-PPK is bound to ONE chunk, the active chunk of Metashape, found again by its key (the key never changes, the label may, and two
chunks may have the same label). `entries` makes the lines of the drop-down of the header, `decide` says what the window does when the
active chunk of Metashape is not the bound one.
"""

from ... import i18n

NONE, REBIND, KEEP = "none", "rebind", "keep"


def entries(raw):
    """`raw` = [(key, label, photo count)] -> [{"key", "label", "photos", "text"}] for the drop-down.

    The text is the name and a small photo count. Chunks with the same label get " #2", " #3"... so they can be told apart."""
    seen = {}
    out = []
    for key, label, photos in raw:
        name = str(label or "").strip() or i18n.t("aeroppk.chunk.unnamed")
        seen[name] = seen.get(name, 0) + 1
        shown = name if seen[name] == 1 else "%s #%d" % (name, seen[name])
        out.append({"key": key, "label": name, "photos": int(photos or 0),
                    "text": i18n.t("aeroppk.chunk.item", name=shown, n=int(photos or 0))})
    return out


def decide(bound, active, locked, has_results, bound_exists, kept=None, after_job=False):
    """What to do about the active chunk of Metashape.

    NONE    nothing: it is the bound chunk, or a calculation / download is running (the bound chunk stays until it ends).
    REBIND  the window starts again on the active chunk.
    KEEP    the active chunk is another one but the results on screen are those of the bound chunk, which still exists: they are kept
            (apply and undo go to the bound chunk, said in their messages) until the user picks another chunk or changes it again.
            That only happens when a job has just ended, or for the active chunk already kept.
    """
    if active == bound or locked:
        return NONE
    if has_results and bound_exists and (after_job or kept == active):
        return KEEP
    return REBIND
