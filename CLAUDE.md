# flickr-index — notes for Claude

## Config framework
- **Dynaconf** with `settings.yaml` (non-secrets) + `.env` (secrets)
- Env var prefix: `FLICKR_INDEX_`
- See `config.py`

## Key files
- `main.py` — CLI entry point (`--force`, `--cron`, `--debug` flags)
- `flickr_client.py` — Flickr API wrapper (albums, photos, download)
- `generator.py` — Jinja2 HTML rendering (atomic writes via os.replace)
- `state.py` — incremental sync state (`.state.json`, gitignored)
- `templates/` — base.html, albums.html, album.html, photo.html, home.html

## Photo sizes
- Thumbnail: size code `q` (150x150 square)
- Large: size code `b` (1024px longest edge)

## Output structure
```
output/
  index.html                      # homepage (3-card grid)
  photostream/index.html          # photo grid page 1
  photostream/page2.html
  albums/index.html               # album listing
  albums/<slug>/index.html        # album photo grid
  photos/<id>/index.html          # photo detail
```

## flickr_user_id setting
- Accepts either username or NSID — if username given, NSID is looked up via API and cached to nsid.json
- `photosets.getList` requires NSID (not username) — resolve_user_id() handles this automatically

## Flickr API quirks
- `datetaken` is local time with no timezone — use OffsetTimeOriginal from EXIF instead
- `primary_photo_extras` is NOT a valid parameter for `photosets.getList` (causes "User not found")
- Error 1 (User not found), 2, 100, 105, 111 are permanent — not retried
- `photos.search` extra for last update is `last_update` (underscore); returned field is `lastupdate` (no underscore)
- `lastupdate` changes on both image replacement and metadata edits (title, description, tags)
- `photosets.getList` returns `date_update` per album — used for album change detection

## Sync behaviour
- Logging: rotating `logs/sync.log` (1 MB × 8); `--cron` suppresses console; `--debug` enables DEBUG level
- Change detection: compares `lastupdate` (photos) and `date_update` (albums) against `.state.json` cache
- Incremental rendering: skips all HTML generation if no photos or albums changed
- Exit code 2 on unhandled exception; email summary sent if `notify_email_to` configured
- Email is HTML with key/value table; includes Rendered YES/NO, hostname, script path, timezone-aware timestamps
