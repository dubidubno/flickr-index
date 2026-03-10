"""
HTML generator — renders Jinja2 templates into the output directory.
"""

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import settings


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape(["html"]),
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_path() -> str:
    return settings.get("base_path", "").rstrip("/")


def _author() -> str:
    return settings.get("author", settings.site_title)


def _site_url() -> str:
    return settings.get("site_url", "").rstrip("/")


def _generated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_albums(albums: list[dict]) -> None:
    out = Path(settings.output_dir)
    env = _env()
    html = env.get_template("albums.html").render(
        site_title=settings.site_title,
        author=_author(),
        base_path=_base_path(),
        generated_at=_generated_at(),
        albums=albums,
    )
    _write(out / "index.html", html)


def render_album(album: dict, photos: list[dict], page: int, total_pages: int) -> None:
    out = Path(settings.output_dir)
    env = _env()
    html = env.get_template("album.html").render(
        site_title=settings.site_title,
        author=_author(),
        base_path=_base_path(),
        generated_at=_generated_at(),
        album=album,
        photos=photos,
        page=page,
        total_pages=total_pages,
    )
    slug = album["slug"]
    if page == 1:
        _write(out / "albums" / slug / "index.html", html)
    else:
        _write(out / "albums" / slug / f"page{page}.html", html)


def render_photostream_page(photos: list[dict], page: int, total_pages: int) -> None:
    out = Path(settings.output_dir)
    env = _env()
    html = env.get_template("photostream.html").render(
        site_title=settings.site_title,
        author=_author(),
        base_path=_base_path(),
        generated_at=_generated_at(),
        photos=photos,
        page=page,
        total_pages=total_pages,
    )
    if page == 1:
        _write(out / "index.html", html)
    else:
        _write(out / f"page{page}.html", html)


def render_photo(photo: dict, album: dict | None) -> None:
    out = Path(settings.output_dir)
    env = _env()
    page_url = f"{_site_url()}{_base_path()}/photos/{photo['id']}/"
    html = env.get_template("photo.html").render(
        site_title=settings.site_title,
        author=_author(),
        base_path=_base_path(),
        generated_at=_generated_at(),
        site_url=_site_url(),
        page_url=page_url,
        photo=photo,
        album=album,
    )
    _write(out / "photos" / photo["id"] / "index.html", html)
