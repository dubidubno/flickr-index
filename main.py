"""
flickr-index — generate static HTML pages from your public Flickr photos.

Usage:
    python main.py                    # full sync (skips already-downloaded files)
    python main.py --force            # re-download everything, re-fetch EXIF/location
    python main.py --test-api-connection
    python main.py --get-nsid <username>
"""

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Always run from the directory where main.py lives
os.chdir(Path(__file__).parent)

from slugify import slugify

import flickr_client
import generator
import state
from config import settings


def _photo_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)


def _hash_dir(photo_id: str) -> str:
    return hashlib.md5(photo_id.encode()).hexdigest()[:2]


def build_album_meta(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "title": raw["title"]["_content"],
        "description": raw["description"]["_content"],
        "slug": slugify(raw["title"]["_content"]),
        "photos_count": int(raw["photos"]),
        "thumb_url": None,
        "thumb_local": None,
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

    return {
        "id": pid,
        "title": raw.get("title", pid),
        "description": raw.get("description", {}).get("_content", "") if isinstance(raw.get("description"), dict) else raw.get("description", ""),
        "date_taken": raw.get("datetaken", ""),
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

        print(f"  download {pid}: {photo['title']}")

        if photo["thumb_url"] and thumb_needed:
            flickr_client.download_photo(photo["thumb_url"], thumb_dest)
        if photo["large_url"] and large_needed:
            flickr_client.download_photo(photo["large_url"], large_dest)

        cached = st["photos"].get(pid, {})
        cached["title"] = photo["title"]
        state.mark_photo(st, pid, cached)


NSID_FILE = Path("nsid.json")


def resolve_user_id() -> str | None:
    if settings.get("flickr_user_id"):
        return settings.flickr_user_id
    if NSID_FILE.exists():
        data = json.loads(NSID_FILE.read_text())
        return data.get("nsid")
    return None


def get_nsid(username: str) -> None:
    flickr = flickr_client.get_api()
    try:
        resp = flickr_client._api_call(flickr.people.findByUsername, username=username)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
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
    parser.add_argument("--authenticate", action="store_true", help="Run Flickr OAuth flow and store token, then exit")
    parser.add_argument("--test-api-connection", action="store_true", help="Verify config and API connectivity, then exit")
    parser.add_argument("--get-nsid", metavar="USERNAME", help="Look up Flickr NSID for a username and save to nsid.json")
    args = parser.parse_args()

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
        print("Error: flickr_user_id not set — run: python main.py --get-nsid <username>", file=sys.stderr)
        sys.exit(1)
    if not settings.api_key:
        print("Error: FLICKR_INDEX_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)

    st = state.load()
    flickr = flickr_client.get_api()
    per_page = settings.photos_per_page

    print("Fetching public photostream...")
    raw_photos = flickr_client.get_public_photos(flickr, user_id)
    print(f"  {len(raw_photos)} public photos found")

    photos = []
    for i, raw in enumerate(raw_photos, 1):
        print(f"  [{i}/{len(raw_photos)}] {raw.get('title', raw['id'])}")
        photo = build_photo_meta(raw, flickr, st, args.force)
        photos.append(photo)
        if i % 10 == 0:
            state.save(st)

    print("\nDownloading images...")
    download_photos(flickr, photos, st, args.force)

    state.save(st)

    print("\nRendering pages...")
    total_pages = max(1, math.ceil(len(photos) / per_page))
    for page in range(1, total_pages + 1):
        slice_start = (page - 1) * per_page
        page_photos = photos[slice_start: slice_start + per_page]
        print(f"  page {page}/{total_pages}")
        generator.render_photostream_page(page_photos, page, total_pages)

    for i, photo in enumerate(photos, 1):
        print(f"  [{i}/{len(photos)}] {photo['title']}")
        generator.render_photo(photo, album=None)

    state.save(st)
    print(f"\nDone. {len(photos)} photos across {total_pages} pages. Output in '{settings.output_dir}/'")


if __name__ == "__main__":
    main()
