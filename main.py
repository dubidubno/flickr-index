"""
flickr-index — generate static HTML pages from your public Flickr photos.

Usage:
    python main.py           # full sync
    python main.py --force   # re-download everything (ignore state)
"""

import argparse
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


def main():
    parser = argparse.ArgumentParser(description="Generate static Flickr photo pages")
    parser.add_argument("--force", action="store_true", help="Re-download all photos, ignore state")
    args = parser.parse_args()

    if not settings.flickr_user_id:
        print("Error: flickr_user_id is not set in settings.yaml", file=sys.stderr)
        sys.exit(1)
    if not settings.api_key:
        print("Error: FLICKR_INDEX_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)

    st = state.load()
    flickr = flickr_client.get_api()
    per_page = settings.photos_per_page

    print("Fetching albums...")
    raw_albums = flickr_client.get_albums(flickr)

    albums_meta = []
    for raw_album in raw_albums:
        album = build_album_meta(raw_album)
        print(f"\nAlbum: {album['title']} ({album['photos_count']} photos)")

        raw_photos = flickr_client.get_album_photos(flickr, album["id"])
        photos = [build_photo_meta(p, album["slug"]) for p in raw_photos]

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
