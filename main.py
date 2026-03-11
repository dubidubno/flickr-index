"""
flickr-index — generate static HTML pages from your public Flickr photos.

Usage:
    python main.py                    # full sync (skips already-downloaded files)
    python main.py --force            # re-download everything, re-fetch EXIF/location
    python main.py --cron             # suppress console output (log to file only)
    python main.py --debug            # verbose DEBUG output
    python main.py --test-api-connection
    python main.py --get-nsid <username>
"""

import argparse
import hashlib
import json
import logging
import math
import os
import smtplib
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

# Always run from the directory where main.py lives
os.chdir(Path(__file__).parent)

from slugify import slugify

import flickr_client
import generator
import state
from config import settings


def setup_logging(cron: bool, debug: bool) -> logging.Logger:
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s %(levelname)-8s %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    log = logging.getLogger()
    log.setLevel(level)

    # Always log to file with rotation (1 MB × 8 backups)
    Path("logs").mkdir(exist_ok=True)
    fh = RotatingFileHandler("logs/sync.log", maxBytes=1_000_000, backupCount=8, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    log.addHandler(fh)

    # Console handler — suppressed in --cron mode
    if not cron:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(fmt, datefmt))
        log.addHandler(ch)

    return log


def send_email(success: bool, summary: str, settings) -> None:
    to_addr = settings.get("notify_email_to", "")
    if not to_addr:
        return
    from_addr = settings.get("notify_email_from", "")
    smtp_host = settings.get("notify_smtp_host", "localhost")
    smtp_port = int(settings.get("notify_smtp_port", 25))

    subject = f"[flickr-index] {'OK' if success else 'FAILED'} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(summary)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.send_message(msg)
        logging.info("Email notification sent to %s", to_addr)
    except Exception as exc:
        logging.warning("Failed to send email: %s", exc)


def _photo_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)


def _hash_dir(photo_id: str) -> str:
    return hashlib.md5(photo_id.encode()).hexdigest()[:2]


def build_album_meta(raw: dict, photos_by_id: dict) -> dict:
    primary_id = raw["primary"]
    primary_photo = photos_by_id.get(primary_id)
    if primary_photo:
        thumb_url = primary_photo["thumb_url"]
        thumb_local = primary_photo["thumb_local"]
    else:
        thumb_url = ""
        thumb_local = None
    return {
        "id": raw["id"],
        "title": raw["title"]["_content"],
        "description": raw["description"]["_content"],
        "slug": slugify(raw["title"]["_content"]),
        "photos_count": int(raw["photos"]),
        "thumb_url": thumb_url,
        "thumb_local": thumb_local,
    }


def build_photo_meta(raw: dict, flickr, st: dict, force: bool) -> dict:
    tags_raw = raw.get("tags", "")
    tags = [t.strip() for t in tags_raw.split() if t.strip()] if isinstance(tags_raw, str) else []

    pid = raw["id"]
    thumb_url = raw.get("url_q", "")
    large_url = (
        raw.get("url_b")
        or f"https://live.staticflickr.com/{raw['server']}/{raw['id']}_{raw['secret']}_b.jpg"
    )
    hash_dir = _hash_dir(pid)
    thumb_local = f"/photo-files/{hash_dir}/{_photo_filename(thumb_url)}" if thumb_url else ""
    large_local = f"/photo-files/{hash_dir}/{_photo_filename(large_url)}"

    # Detect if photo was updated on Flickr since last sync
    cached = st["photos"].get(pid, {})
    lastupdate = raw.get("lastupdate", "")
    updated = lastupdate and lastupdate != cached.get("lastupdate")

    if force or updated or "exif" not in cached:
        exif = flickr_client.get_exif(flickr, pid)
        cached["exif"] = exif
        state.mark_photo(st, pid, cached)
    else:
        exif = cached["exif"]

    if force or updated or "location" not in cached:
        location = flickr_client.get_location(flickr, pid)
        cached["location"] = location
        state.mark_photo(st, pid, cached)
    else:
        location = cached["location"]

    if updated:
        cached["lastupdate"] = lastupdate
        state.mark_photo(st, pid, cached)

    license_id = str(raw.get("license", "0"))
    license_name, license_url = flickr_client.LICENSES.get(license_id, ("Unknown", None))

    tz_offset = exif.pop("_tz_offset", "")
    date_taken = raw.get("datetaken", "")
    date_taken_iso = date_taken.replace(" ", "T") + tz_offset if date_taken else ""

    return {
        "id": pid,
        "title": raw.get("title", pid),
        "description": raw.get("description", {}).get("_content", "") if isinstance(raw.get("description"), dict) else raw.get("description", ""),
        "date_taken": date_taken,
        "date_taken_iso": date_taken_iso,
        "tags": tags,
        "owner": raw.get("owner", ""),
        "thumb_url": thumb_url,
        "large_url": large_url,
        "thumb_local": thumb_local,
        "large_local": large_local,
        "exif": exif,
        "location": location,
        "license_name": license_name,
        "license_url": license_url,
        "updated": updated,
    }


def download_photos(flickr, photos: list[dict], st: dict, force: bool) -> None:
    out = Path(settings.output_dir)
    for photo in photos:
        pid = photo["id"]
        thumb_dest = out / "photo-files" / _hash_dir(pid) / _photo_filename(photo["thumb_url"]) if photo["thumb_url"] else None
        large_dest = out / "photo-files" / _hash_dir(pid) / _photo_filename(photo["large_url"])

        thumb_needed = thumb_dest and (force or photo.get("updated") or not thumb_dest.exists())
        large_needed = force or photo.get("updated") or not large_dest.exists()

        if not thumb_needed and not large_needed:
            continue

        logging.debug("download %s: %s", pid, photo["title"])

        if photo["thumb_url"] and thumb_needed:
            flickr_client.download_photo(photo["thumb_url"], thumb_dest)
        if photo["large_url"] and large_needed:
            flickr_client.download_photo(photo["large_url"], large_dest)

        cached = st["photos"].get(pid, {})
        cached["title"] = photo["title"]
        state.mark_photo(st, pid, cached)


NSID_FILE = Path("nsid.json")


def resolve_user_id() -> str | None:
    configured = settings.get("flickr_user_id", "")

    # Already an NSID
    if configured and "@" in configured:
        return configured

    # Username configured — check nsid.json cache first
    username = configured or None
    if NSID_FILE.exists():
        data = json.loads(NSID_FILE.read_text())
        if not username or data.get("username") == username:
            return data.get("nsid")

    # Look up via API
    if username:
        logging.info("Looking up NSID for username '%s'...", username)
        flickr = flickr_client.get_api()
        try:
            resp = flickr_client._api_call(flickr.people.findByUsername, username=username)
        except Exception as exc:
            logging.error("Error looking up NSID: %s", exc)
            return None
        nsid = resp["user"]["nsid"]
        NSID_FILE.write_text(json.dumps({"username": username, "nsid": nsid}, indent=2))
        logging.info("NSID: %s (saved to %s)", nsid, NSID_FILE)
        return nsid

    return None


def get_nsid(username: str) -> None:
    flickr = flickr_client.get_api()
    try:
        resp = flickr_client._api_call(flickr.people.findByUsername, username=username)
    except Exception as exc:
        logging.error("Error: %s", exc)
        sys.exit(1)

    nsid = resp["user"]["nsid"]
    data = {"username": username, "nsid": nsid}
    NSID_FILE.write_text(json.dumps(data, indent=2))
    print(f"Username : {username}")
    print(f"NSID     : {nsid}")
    print(f"Saved to : {NSID_FILE}")
    print(f"\nAdd to settings.yaml:\n  flickr_user_id: \"{nsid}\"")


def test_api_connection():
    print("Checking config...")
    ok = True
    if not settings.get("api_key"):
        print("  FAIL: FLICKR_INDEX_API_KEY not set in .env")
        ok = False
    else:
        print("  OK:   API key present")

    user_id = resolve_user_id()
    if not user_id:
        print("  WARN: flickr_user_id not set — run --get-nsid <username>")
    else:
        print(f"  OK:   flickr_user_id = {user_id}")

    if not ok:
        sys.exit(1)

    print("Contacting Flickr API...")
    try:
        flickr = flickr_client.get_api()
        resp = flickr_client._api_call(flickr.test.echo, foo="bar")
        if resp.get("stat") == "ok":
            print("  OK:   flickr.test.echo succeeded")
        else:
            print(f"  FAIL: unexpected response: {resp}")
            sys.exit(1)
    except Exception as exc:
        print(f"  FAIL: {exc}")
        sys.exit(1)

    user_id = resolve_user_id()
    if user_id:
        print(f"Fetching user info for {user_id}...")
        try:
            resp = flickr_client._api_call(flickr.people.getInfo, user_id=user_id)
            username = resp["person"]["username"]["_content"]
            photos_count = resp["person"]["photos"]["count"]["_content"]
            print(f"  OK:   user '{username}', {photos_count} public photos")
        except Exception as exc:
            print(f"  FAIL: {exc}")
            sys.exit(1)

    print("\nAll checks passed.")


def main():
    parser = argparse.ArgumentParser(description="Generate static Flickr photo pages")
    parser.add_argument("--force", action="store_true", help="Re-download all photos, re-fetch EXIF/location")
    parser.add_argument("--cron", action="store_true", help="Suppress console output (log to file only)")
    parser.add_argument("--debug", action="store_true", help="Set log level to DEBUG")
    parser.add_argument("--authenticate", action="store_true", help="Run Flickr OAuth flow and store token, then exit")
    parser.add_argument("--test-api-connection", action="store_true", help="Verify config and API connectivity, then exit")
    parser.add_argument("--get-nsid", metavar="USERNAME", help="Look up Flickr NSID for a username and save to nsid.json")
    args = parser.parse_args()

    setup_logging(cron=args.cron, debug=args.debug)

    if args.authenticate:
        flickr_client.authenticate()
        return

    if args.test_api_connection:
        test_api_connection()
        return

    if args.get_nsid:
        get_nsid(args.get_nsid)
        return

    user_id = resolve_user_id()
    if not user_id:
        logging.error("flickr_user_id not set — run: python main.py --get-nsid <username>")
        sys.exit(1)

    if not settings.api_key:
        logging.error("FLICKR_INDEX_API_KEY is not set in .env")
        sys.exit(1)

    run_start = datetime.now()
    warnings_count = [0]

    # Patch logging to count warnings
    original_warning = logging.warning
    def counting_warning(msg, *args, **kwargs):
        warnings_count[0] += 1
        original_warning(msg, *args, **kwargs)
    logging.warning = counting_warning

    summary_lines = [f"Run: {run_start.strftime('%Y-%m-%dT%H:%M:%S')}"]

    try:
        st = state.load()
        flickr = flickr_client.get_api()

        if not flickr.token_valid(perms="read"):
            logging.error("Not authenticated — run: python main.py --authenticate")
            sys.exit(1)

        per_page = settings.photos_per_page

        logging.info("Fetching public photostream...")
        raw_photos = flickr_client.get_public_photos(flickr, user_id)
        logging.info("%d public photos found", len(raw_photos))

        photos = []
        for i, raw in enumerate(raw_photos, 1):
            logging.debug("[%d/%d] %s", i, len(raw_photos), raw.get("title", raw["id"]))
            photo = build_photo_meta(raw, flickr, st, args.force)
            photos.append(photo)
            if i % 10 == 0:
                state.save(st)

        logging.info("Downloading images...")
        download_photos(flickr, photos, st, args.force)

        state.save(st)

        photos_by_id = {p["id"]: p for p in photos}

        logging.info("Fetching albums...")
        raw_albums = flickr_client.get_albums(flickr, user_id)

        photo_to_album = {}
        albums_meta = []
        total_album_pages = 0
        for raw_album in raw_albums:
            album = build_album_meta(raw_album, photos_by_id)
            albums_meta.append(album)
            raw_album_photos = flickr_client.get_album_photos(flickr, raw_album["id"], user_id)
            album_photos = [photos_by_id[p["id"]] for p in raw_album_photos if p["id"] in photos_by_id]
            for p in album_photos:
                photo_to_album.setdefault(p["id"], album)
            album_pages = max(1, math.ceil(len(album_photos) / per_page))
            total_album_pages += album_pages
            for page in range(1, album_pages + 1):
                s = (page - 1) * per_page
                logging.debug("  %s page %d/%d", album["title"], page, album_pages)
                generator.render_album(album, album_photos[s:s + per_page], page, album_pages)

        generator.render_albums(albums_meta)
        generator.render_home(photos[0], albums_meta)

        logging.info("Rendering photostream pages...")
        total_pages = max(1, math.ceil(len(photos) / per_page))
        for page in range(1, total_pages + 1):
            slice_start = (page - 1) * per_page
            page_photos = photos[slice_start: slice_start + per_page]
            logging.debug("  page %d/%d", page, total_pages)
            generator.render_photostream_page(page_photos, page, total_pages)

        logging.info("Rendering photo detail pages...")
        for i, photo in enumerate(photos, 1):
            logging.debug("[%d/%d] %s", i, len(photos), photo["title"])
            generator.render_photo(photo, album=photo_to_album.get(photo["id"]))

        state.save(st)

        duration = datetime.now() - run_start
        total_secs = int(duration.total_seconds())
        duration_str = f"{total_secs // 60}m {total_secs % 60}s"

        logging.info(
            "Done. %d photos, %d albums, %d pages. Output in '%s/'. Duration: %s",
            len(photos), len(albums_meta), total_pages, settings.output_dir, duration_str,
        )

        summary_lines += [
            "Status: OK",
            f"Photos: {len(photos)}",
            f"Albums: {len(albums_meta)}",
            f"Pages: {total_pages}",
            f"Duration: {duration_str}",
            f"Warnings: {warnings_count[0]}",
        ]
        send_email(success=True, summary="\n".join(summary_lines), settings=settings)

    except Exception:
        duration = datetime.now() - run_start
        total_secs = int(duration.total_seconds())
        duration_str = f"{total_secs // 60}m {total_secs % 60}s"
        tb = traceback.format_exc()
        logging.exception("Sync failed with unhandled exception")
        summary_lines += [
            "Status: FAILED",
            f"Duration: {duration_str}",
            f"Warnings: {warnings_count[0]}",
            "",
            tb,
        ]
        send_email(success=False, summary="\n".join(summary_lines), settings=settings)
        sys.exit(2)
    finally:
        logging.warning = original_warning


if __name__ == "__main__":
    main()
