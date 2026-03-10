"""
Flickr API wrapper — fetches photos, EXIF, location, and downloads image files.
"""

import time
import urllib.error
import urllib.request
from pathlib import Path

import flickrapi

from config import settings

_DOWNLOAD_DELAY = 0.1   # seconds between image downloads
_API_RETRY_DELAYS = [5, 15, 60]  # seconds to wait on successive API failures


def _api_call(fn, **kwargs):
    """Call a Flickr API function with exponential-ish backoff on failure."""
    for attempt, delay in enumerate([0] + _API_RETRY_DELAYS):
        if delay:
            print(f"  API error, retrying in {delay}s...")
            time.sleep(delay)
        try:
            return fn(**kwargs)
        except flickrapi.exceptions.FlickrError as exc:
            if attempt == len(_API_RETRY_DELAYS):
                raise
            print(f"  FlickrError: {exc}")
    raise RuntimeError("unreachable")


def get_api() -> flickrapi.FlickrAPI:
    return flickrapi.FlickrAPI(
        settings.api_key,
        settings.api_secret,
        format="parsed-json",
        store_token=True,
    )


def authenticate() -> None:
    """Run the OAuth flow interactively. Tokens are stored in ~/.flickr/."""
    flickr = get_api()
    if flickr.token_valid(perms="read"):
        print("Already authenticated.")
        return

    flickr.get_request_token(oauth_callback="oob")
    url = flickr.auth_url(perms="read")
    print(f"\nOpen this URL in your browser:\n\n  {url}\n")
    verifier = input("Enter the verifier code from Flickr: ").strip()
    flickr.get_access_token(verifier)
    print("Authentication successful. Token stored in ~/.flickr/")


def get_public_photos(flickr: flickrapi.FlickrAPI, user_id: str) -> list[dict]:
    """Fetch all public photos for a user, newest first."""
    photos = []
    page = 1
    while True:
        resp = _api_call(
            flickr.photos.search,
            user_id=user_id,
            privacy_filter=1,
            extras="url_q,date_taken,description,tags",
            sort="date-posted-desc",
            page=page,
            per_page=500,
        )
        photos.extend(resp["photos"]["photo"])
        if page >= resp["photos"]["pages"]:
            break
        page += 1
    return photos


def get_exif(flickr: flickrapi.FlickrAPI, photo_id: str) -> dict:
    """Return a dict of selected EXIF fields. Returns {} if unavailable."""
    try:
        resp = _api_call(flickr.photos.getExif, photo_id=photo_id)
    except flickrapi.exceptions.FlickrError:
        return {}

    tag_map = {}
    for entry in resp.get("photo", {}).get("exif", []):
        tag = entry.get("tag")
        value = entry.get("clean", entry.get("raw", {})).get("_content", "")
        tag_map[tag] = value

    make = tag_map.get("Make", "")
    model = tag_map.get("Model", "")
    camera = f"{make} {model}".strip() if make or model else ""

    result = {}
    if camera:
        result["Camera"] = camera
    for label, tag in [
        ("Lens", "LensModel"),
        ("Aperture", "FNumber"),
        ("Focal length", "FocalLength"),
        ("Exposure", "ExposureTime"),
        ("ISO", "ISO"),
        ("Flash", "Flash"),
    ]:
        if tag in tag_map and tag_map[tag]:
            result[label] = tag_map[tag]

    return result


def get_location(flickr: flickrapi.FlickrAPI, photo_id: str) -> dict:
    """Return location dict with lat, lon, and place name parts. Returns {} if not set."""
    try:
        resp = _api_call(flickr.photos.getInfo, photo_id=photo_id)
    except flickrapi.exceptions.FlickrError:
        return {}

    loc = resp.get("photo", {}).get("location", {})
    if not loc:
        return {}

    result = {}
    if loc.get("latitude"):
        result["lat"] = loc["latitude"]
    if loc.get("longitude"):
        result["lon"] = loc["longitude"]
    for field in ("locality", "county", "region", "country"):
        val = loc.get(field, {})
        if isinstance(val, dict):
            val = val.get("_content", "")
        if val:
            result[field] = val

    return result


def get_albums(flickr: flickrapi.FlickrAPI, user_id: str) -> list[dict]:
    albums = []
    page = 1
    while True:
        resp = _api_call(flickr.photosets.getList, user_id=user_id, page=page, per_page=100)
        sets = resp["photosets"]["photoset"]
        albums.extend(sets)
        if page >= resp["photosets"]["pages"]:
            break
        page += 1
    return albums


def get_album_photos(flickr: flickrapi.FlickrAPI, album_id: str, user_id: str) -> list[dict]:
    photos = []
    page = 1
    while True:
        resp = _api_call(
            flickr.photosets.getPhotos,
            photoset_id=album_id,
            user_id=user_id,
            privacy_filter=1,
            extras="url_q,url_b,url_o,date_taken,description,tags",
            page=page,
            per_page=500,
        )
        photos.extend(resp["photoset"]["photo"])
        if page >= resp["photoset"]["pages"]:
            break
        page += 1
    return photos


def download_photo(url: str, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt, delay in enumerate([0] + _API_RETRY_DELAYS):
        if delay:
            print(f"  download error, retrying in {delay}s...")
            time.sleep(delay)
        try:
            urllib.request.urlretrieve(url, dest)
            time.sleep(_DOWNLOAD_DELAY)
            return
        except urllib.error.URLError as exc:
            if attempt == len(_API_RETRY_DELAYS):
                raise
            print(f"  URLError: {exc}")
