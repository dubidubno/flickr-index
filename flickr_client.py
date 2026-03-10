"""
Flickr API wrapper — fetches albums, photos, and downloads image files.
"""

import urllib.request
from pathlib import Path

import flickrapi

from config import settings


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
        resp = flickr.photosets.getList(user_id=user_id, page=page, per_page=100)
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
        resp = flickr.photosets.getPhotos(
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
    resp = flickr.photos.getInfo(photo_id=photo_id)
    return resp["photo"]


def download_photo(url: str, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
