# News

AeroTools can show a short note when Metashape starts: a new tool, a fixed problem, an event. The notes come from a small file,
`news.json`, that is published next to `manifest.json` in the public repository. It is read every time Metashape starts, and it is **not**
part of the manifest: changing it never triggers an update and never needs a new release.

## Where the file lives

The file follows the channel (`main` or `dev`), like the updater does. For the channel `main`:

```
https://raw.githubusercontent.com/helix-aero/AeroTools/main/news.json
```

The `dev` branch has its own `news.json`, so each channel can carry different news. A file that is missing, unreachable or not valid JSON
means no news, silently.

## Format

```json
{
  "items": [
    {
      "id": "2026-11-14-new-tool",
      "date": "2026-11-14",
      "title": "A new tool is available",
      "body": "One or two short sentences.",
      "url": "https://docs.helix.aero",
      "channels": ["main", "dev"],
      "min_version": "0.5.0",
      "max_version": "0.9.9"
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique and never reused: it is what "already seen" is remembered with. A date and a short word work well. |
| `title` | yes | One line, shown in bold (cut after 100 characters). |
| `date` | no | `YYYY-MM-DD`. Items are listed newest first. |
| `body` | no | Plain text, a sentence or two (cut after 400 characters). |
| `url` | no | `https://` address behind a "Learn more" button. Any other kind of address is ignored. |
| `channels` | no | `["main"]`, `["dev"]` or both. Without it the item is for every channel. |
| `min_version`, `max_version` | no | The item is shown only to installed versions in this range, both ends included. |

Items that cannot be read are skipped; the others are still shown.

## What the user sees

A few seconds after Metashape starts, a small window lists the items that were not shown yet (the three newest; "More news" opens the
documentation for the rest). Closing it marks the items as seen, so each one appears once. "Don't show news again" turns the startup news off.

The choice lives in **Aero > Install plugins**, under Notifications ("Show news at startup"), and can be changed there at any time.
**Aero > News** shows the current items on demand, even when the startup news is off.
