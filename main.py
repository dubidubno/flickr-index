"""
flickr-index — generate static HTML pages from your public Flickr photos.

Usage:
    python main.py                    # full sync
    python main.py --force            # re-download everything (ignore state)
    python main.py --test-api-connection
    python main.py --get-nsid <username>
"""

import argparse
import json
import math
import sys
from pathlib import Path

from slugify import slugify

import flickr_client
import generator
import state
from config import settings


def build_album_meta(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "title": raw["title"]["_content"],
        "description": raw["description"]["_content"],
        "slug": slugify(raw["title"]["_content"]),
        "photos_count": int(raw["photos"]),
        "thumb_url": None,  # filled in below
        "thumb_local": None,
    }


def build_photo_meta(raw: dict, album_slug: str) -> dict:
    tags_raw = raw.get("tags", "")
    tags = [t.strip() for t in tags_raw.split() if t.strip()] if isinstance(tags_raw, str) else []

    return {
        "id": raw["id"],
        "title": raw.get("title", raw["id"]),
        "description": raw.get("description", {}).get("_content", "") if isinstance(raw.get("description"), dict) else raw.get("description", ""),
        "date_taken": raw.get("datetaken", ""),
        "tags": tags,
        "owner": raw.get("owner", ""),
        "thumb_url": raw.get("url_q", ""),
        "large_url": raw.get("url_b", ""),
        "thumb_local": f"/photos/{raw['id']}/thumb.jpg",
        "large_local": f"/photos/{raw['id']}/large.jpg",
        "album_slug": album_slug,
    }


def download_photos(flickr, photos: list[dict], st: dict, force: bool) -> None:
    out = Path(settings.output_dir)
    for photo in photos:
        pid = photo["id"]
        if not force and state.photo_done(st, pid):
            print(f"  skip {pid}")
            continue

        print(f"  download {pid}: {photo['title']}")

        if photo["thumb_url"]:
            flickr_client.download_photo(photo["thumb_url"], out / "photos" / pid / "thumb.jpg")
        if photo["large_url"]:
            flickr_client.download_photo(photo["large_url"], out / "photos" / pid / "large.jpg")

        state.mark_photo(st, pid, {"title": photo["title"]})


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
            resp = flickr_client._api_call(
                flickr.people.getInfo, user_id=user_id
            )
            username = resp["person"]["username"]["_content"]
            photos_count = resp["person"]["photos"]["count"]["_content"]
            print(f"  OK:   user '{username}', {photos_count} public photos")
        except Exception as exc:
            print(f"  FAIL: {exc}")
            sys.exit(1)

    print("\nAll checks passed.")


def main():
    parser = argparse.ArgumentParser(description="Generate static Flickr photo pages")
    parser.add_argument("--force", action="store_true", help="Re-download all photos, ignore state")
    parser.add_argument("--test-api-connection", action="store_true", help="Verify config and API connectivity, then exit")
    parser.add_argument("--get-nsid", metavar="USERNAME", help="Look up Flickr NSID for a username and save to nsid.json")
    args = parser.parse_args()

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

    print("Fetching albums...")
    raw_albums = flickr_client.get_albums(flickr, user_id)

    albums_meta = []
    for raw_album in raw_albums:
        album = build_album_meta(raw_album)
        print(f"\nAlbum: {album['title']} ({album['photos_count']} photos)")

        raw_photos = flickr_client.get_album_photos(flickr, album["id"], user_id)
        photos = [build_photo_meta(p, album["slug"]) for p in raw_photos]

        if not photos:
            print(f"  skip (no public photos)")
            continue

        # Use first photo as album cover thumbnail
        if photos and photos[0]["thumb_url"]:
            album["thumb_url"] = photos[0]["thumb_url"]
            album["thumb_local"] = photos[0]["thumb_local"]

        download_photos(flickr, photos, st, args.force)

        # Paginate album pages
        total_pages = max(1, math.ceil(len(photos) / per_page))
        for page in range(1, total_pages + 1):
            slice_start = (page - 1) * per_page
            page_photos = photos[slice_start: slice_start + per_page]
            generator.render_album(album, page_photos, page, total_pages)

        # Render individual photo pages
        for photo in photos:
            generator.render_photo(photo, album)

        albums_meta.append(album)
        state.save(st)

    print("\nRendering album index...")
    generator.render_albums(albums_meta)

    state.save(st)
    print(f"\nDone. Output in '{settings.output_dir}/'")


if __name__ == "__main__":
    main()
