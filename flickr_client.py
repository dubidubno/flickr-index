"""
Flickr API wrapper — fetches albums, photos, and downloads image files.
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
    )


def get_albums(flickr: flickrapi.FlickrAPI) -> list[dict]:
    user_id = settings.flickr_user_id
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


def get_album_photos(flickr: flickrapi.FlickrAPI, album_id: str) -> list[dict]:
    user_id = settings.flickr_user_id
    photos = []
    page = 1
    while True:
        resp = _api_call(
            flickr.photosets.getPhotos,
            photoset_id=album_id,
            user_id=user_id,
            extras="url_q,url_b,url_o,date_taken,description,tags",
            page=page,
            per_page=500,
        )
        photos.extend(resp["photoset"]["photo"])
        if page >= resp["photoset"]["pages"]:
            break
        page += 1
    return photos


def get_photo_info(flickr: flickrapi.FlickrAPI, photo_id: str) -> dict:
    resp = _api_call(flickr.photos.getInfo, photo_id=photo_id)
    return resp["photo"]


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
