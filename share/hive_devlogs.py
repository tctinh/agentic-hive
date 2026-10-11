"""Read ordinary Markdown dev-log bundles from Hive's shared artifacts."""
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
import re
try:
    import tomllib
except ModuleNotFoundError:  # Portable installs may use Python 3.10.
    import tomli as tomllib
from urllib.parse import quote, unquote, urlencode, urlsplit

HANDLE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
ARTICLE_LIMIT = 256 * 1024


def folder(root, identifier):
    parts = identifier.split("/")
    if len(parts) != 2 or not all(HANDLE.fullmatch(p) and p not in (".", "..") for p in parts):
        raise ValueError("invalid dev-log identifier")
    base = Path(root) / "artifacts/devlogs"
    target = base.joinpath(*parts)
    if not base.resolve().is_relative_to(Path(root).resolve()) or not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("dev log leaves shared artifacts")
    return target


def media_url(identifier, name):
    return "/api/devlog-media?" + urlencode({"id": identifier, "path": name})


def media(root, identifier, name):
    base = folder(root, identifier)
    parts = name.split("/")
    if not name or any(p in ("", ".", "..") for p in parts) or "\\" in name or "\0" in name:
        raise ValueError("invalid dev-log media path")
    target = base.joinpath(*parts)
    if not target.resolve().is_relative_to(base.resolve()) or not target.is_file():
        raise ValueError("dev-log media not found")
    with target.open("rb") as stream:
        header = stream.read(32)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif header.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        mime = "image/webp"
    elif target.suffix.lower() == ".mp4" and header[4:8] == b"ftyp":
        mime = "video/mp4"
    elif target.suffix.lower() == ".webm" and header.startswith(b"\x1aE\xdf\xa3"):
        mime = "video/webm"
    else:
        raise ValueError("use PNG, JPG, GIF, WebP, MP4 or WebM media")
    return target, mime


def local_media(root, identifier, source):
    url = urlsplit(source)
    if url.scheme or url.netloc or url.query or url.fragment or url.path.startswith("/"):
        raise ValueError("media must be a relative file in this dev-log bundle")
    name = unquote(url.path)
    target, mime = media(root, identifier, name)
    return name, target, mime


def render(root, identifier, body, cover=None):
    # js-default disables raw HTML and includes tables/strikethrough. Keep
    # arbitrary article content out of the dashboard's executable HTML surface.
    from markdown_it import MarkdownIt
    parser = MarkdownIt("js-default")

    def image(tokens, index, options, env):
        token = tokens[index]
        caption = escape(token.content)
        try:
            name, _, mime = local_media(root, identifier, token.attrGet("src") or "")
        except (OSError, ValueError):
            return f'<span class="devlog-missing">Media unavailable: {caption}</span>'
        source = escape(media_url(identifier, name), quote=True)
        if mime.startswith("video/"):
            poster = f' poster="{escape(cover, quote=True)}"' if cover else ""
            element = f'<video controls playsinline preload="metadata"{poster}><source src="{source}" type="{mime}">Your browser cannot play this video.</video>'
        else:
            element = f'<a href="{source}" target="_blank" rel="noopener noreferrer"><img src="{source}" alt="{caption}" loading="lazy"></a>'
        return f'<span class="devlog-media">{element}<span class="devlog-caption">{caption}</span></span>'

    parser.renderer.rules["image"] = image
    return parser.render(body)


def article(root, identifier, with_body=True):
    base = folder(root, identifier)
    source = base / "index.md"
    if not source.resolve().is_relative_to(base.resolve()) or not source.is_file():
        raise ValueError("article not found in its dev-log bundle")
    with source.open("rb") as stream:
        data = stream.read(ARTICLE_LIMIT + 1)
    if len(data) > ARTICLE_LIMIT:
        raise ValueError("dev-log article exceeds 256 KiB")
    body = data.decode("utf-8-sig")
    metadata = {}
    if body.startswith("+++\n") or body.startswith("+++\r\n"):
        lines = body.splitlines(keepends=True)
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "+++"), None)
        if end is None:
            raise ValueError("close dev-log metadata with +++")
        metadata = tomllib.loads("".join(lines[1:end]))
        body = "".join(lines[end + 1:])
    heading = re.match(r"\s*# ([^\n]+)\n?", body)

    def text(key, default="", limit=500):
        value = metadata.get(key, default)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"dev-log {key} must be text of at most {limit} characters")
        return value.strip()

    title = text("title", heading[1].strip() if heading else identifier.split("/")[1].replace("-", " "), 200)
    if not title:
        raise ValueError("dev-log title cannot be empty")
    if heading and heading[1].strip() == title:
        body = body[heading.end():].lstrip()
    summary = text("summary")
    if not summary:
        paragraph = next((p for p in re.split(r"\n\s*\n", body) if p.strip() and not p.lstrip().startswith(("#", "!", "```", "|", ">"))), "")
        summary = re.sub(r"\s+", " ", paragraph).strip()[:240]
    stamp = metadata.get("date")
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    elif isinstance(stamp, date) and not isinstance(stamp, datetime):
        stamp = datetime.combine(stamp, datetime.min.time())
    elif stamp is not None and not isinstance(stamp, datetime):
        raise ValueError("dev-log date must be an ISO date or timestamp")
    if stamp is None:
        stamp = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
    elif stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    cover = None
    cover_name = text("cover")
    if not cover_name:
        match = re.search(r"!\[[^\]]*\]\(([^\s)]+)\)", body)
        cover_name = match[1] if match else ""
    if cover_name:
        try:
            name, _, mime = local_media(root, identifier, cover_name)
            if mime.startswith("image/"):
                cover = media_url(identifier, name)
        except (OSError, ValueError):
            pass
    stamp = stamp.astimezone(timezone.utc)
    result = {"id": identifier, "project": identifier.split("/")[0], "title": title,
              "summary": summary, "author": text("author", "", 100), "date": stamp.isoformat(),
              "commit": text("commit", "", 200), "cover": cover,
              "url": "/#devlog=" + quote(identifier, safe=""),
              "source": f"artifacts/devlogs/{identifier}/index.md"}
    if with_body:
        result["html"] = render(root, identifier, body, cover)
    return result


def articles(root):
    base = Path(root) / "artifacts/devlogs"
    found, errors = [], []
    if not base.resolve().is_relative_to(Path(root).resolve()):
        return {"articles": [], "errors": [{"id": "", "error": "dev logs leave shared artifacts"}]}
    for source in sorted(base.glob("*/*/index.md")):
        identifier = source.parent.relative_to(base).as_posix()
        try:
            found.append(article(root, identifier, with_body=False))
        except (OSError, ValueError) as error:
            errors.append({"id": identifier, "error": str(error)})
    found.sort(key=lambda item: item["date"], reverse=True)
    return {"articles": found, "errors": errors}
