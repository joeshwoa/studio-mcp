"""One adapter per free stock provider that has an OFFICIAL API. Every adapter turns the
provider's JSON into the same normalised item:

  {id: "provider:native_id", provider, source, kind (video|photo|illustration|vector|music|sfx|icon),
   title, page_url, thumb, width, height, duration, fps, license, license_url, commercial_ok,
   modify_ok, attribution_required, share_alike, attribution (ready to paste), creator, creator_url,
   tags, notes, files: [{url, width, height, quality, mime, size}], extra: {...provider bits}}

`files` may be empty at search time for providers whose file list costs an extra request
(NASA, Internet Archive); `resolve()` fills it at download time.
Keyed providers read their key via core.keys.get_key (env first, then STUDIO_HOME/keys.json).
Sites without an official API (Mixkit, Videvo, Pixabay Music pages…) are deliberately absent."""
from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass, field

from ...core.keys import get_key
from . import licenses as L
from ._http import HTTPFail, get_json, request

KINDS = ("video", "photo", "illustration", "vector", "music", "sfx", "icon")


@dataclass
class Opts:
    orientation: str = ""      # landscape | portrait | square | ""
    count: int = 12
    page: int = 1
    min_duration: float = 0
    max_duration: float = 0
    min_width: int = 0
    commercial: bool = True


class ProviderError(Exception):
    pass


def make_item(provider: str, native_id, kind: str, lic: dict, **kw) -> dict:
    it = {"id": f"{provider}:{native_id}", "provider": provider, "source": kw.pop("source", provider), "kind": kind,
          "title": "", "page_url": "", "thumb": "", "width": 0, "height": 0, "duration": 0.0, "fps": 0,
          "creator": "", "creator_url": "", "tags": [], "notes": [], "files": [], "extra": {}, "attribution": ""}
    it.update(lic)
    for k, v in kw.items():
        if v is not None:
            it[k] = v
    it["title"] = clean_text(it["title"])[:160] or f"{provider} {native_id}"
    it["width"], it["height"] = int(it["width"] or 0), int(it["height"] or 0)
    try:
        it["duration"] = round(float(it["duration"] or 0), 2)
    except (TypeError, ValueError):
        it["duration"] = 0.0
    if isinstance(it["tags"], str):
        it["tags"] = [t.strip() for t in it["tags"].split(",") if t.strip()]
    it["tags"] = [str(t) for t in it["tags"]][:20]
    return it


def clean_text(s) -> str:
    if s is None:
        return ""
    if isinstance(s, list):
        s = ", ".join(str(x) for x in s)
    s = re.sub(r"<[^>]+>", "", str(s))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fmt_dur(d: float) -> str:
    d = float(d or 0)
    if d <= 0:
        return ""
    m, s = divmod(int(round(d)), 60)
    return f"{m}:{s:02d}" if m else f"{d:.1f}s"


def mime_of(url: str, default: str = "") -> str:
    ext = urllib.parse.urlsplit(url).path.rsplit(".", 1)[-1].lower() if "." in urllib.parse.urlsplit(url).path else ""
    return {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "ogv": "video/ogg",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            "svg": "image/svg+xml", "tif": "image/tiff", "tiff": "image/tiff", "mp3": "audio/mpeg", "ogg": "audio/ogg",
            "oga": "audio/ogg", "flac": "audio/flac", "wav": "audio/wav", "m4a": "audio/mp4", "opus": "audio/ogg"}.get(ext, default)


def orient_of(w: int, h: int) -> str:
    if not w or not h:
        return ""
    r = w / h
    return "square" if 0.9 <= r <= 1.1 else ("landscape" if r > 1 else "portrait")


class Provider:
    name = ""
    label = ""
    kinds: tuple[str, ...] = ()
    key_names: tuple[str, ...] = ()       # first is the canonical name written to keys.json
    home = ""
    gives = ""
    licence_note = ""
    attribution_rule = ""
    rate_note = ""
    get_key_steps: list[str] = []
    untested = False

    def key(self) -> str:
        return get_key(*self.key_names) if self.key_names else ""

    def active(self) -> bool:
        return not self.key_names or bool(self.key())

    def search(self, q: str, kind: str, o: Opts) -> list[dict]:
        raise NotImplementedError

    def resolve(self, it: dict) -> dict:
        return it

    def on_download(self, it: dict) -> str:
        """Provider-required download ping (Unsplash). Returns a note or ''."""
        return ""

    def auth_headers(self) -> dict:
        return {}


# ─────────────────────────────── keyed providers ───────────────────────────────

class Pexels(Provider):
    name, label = "pexels", "Pexels"
    kinds = ("video", "photo")
    key_names = ("PEXELS_API_KEY",)
    home = "https://www.pexels.com"
    gives = "HD/4K stock videos and photos"
    licence_note = "Pexels License — free for commercial use, no attribution required (credit appreciated); don't sell unaltered copies or imply endorsement."
    attribution_rule = "Not required, but the API terms ask you to credit: \"Video by NAME on Pexels\" with a link."
    rate_note = "200 requests/hour, 20 000/month (free key)"
    get_key_steps = ["Create a free account at https://www.pexels.com/join/",
                     "Open https://www.pexels.com/api/new/ and fill the short form (project name + description)",
                     "Copy the key shown on https://www.pexels.com/api/ (\"Your API Key\")",
                     "Paste it into keys.json as PEXELS_API_KEY"]
    LICENSE = L.fixed("Pexels License", "https://www.pexels.com/license/")

    def auth_headers(self):
        return {"Authorization": self.key()}

    def search(self, q, kind, o):
        params = {"query": q, "per_page": min(80, max(1, o.count)), "page": o.page,
                  "orientation": o.orientation if o.orientation in ("landscape", "portrait", "square") else ""}
        if kind == "video":
            if o.min_duration:
                params["min_duration"] = int(o.min_duration)
            if o.max_duration:
                params["max_duration"] = int(o.max_duration + 0.999)
            j = get_json("https://api.pexels.com/videos/search", params, self.auth_headers())
            return [self._video(v) for v in j.get("videos", [])]
        j = get_json("https://api.pexels.com/v1/search", params, self.auth_headers())
        return [self._photo(p) for p in j.get("photos", [])]

    def _video(self, v):
        user = v.get("user") or {}
        files = []
        for f in v.get("video_files") or []:
            if not f.get("link"):
                continue
            files.append({"url": f["link"], "width": f.get("width") or 0, "height": f.get("height") or 0,
                          "quality": f.get("quality") or "", "mime": f.get("file_type") or "video/mp4",
                          "size": f.get("size") or 0, "fps": f.get("fps") or 0})
        fps = max([f.get("fps") or 0 for f in files] or [0])
        name = user.get("name") or "Pexels creator"
        title = re.sub(r"[-\d]+$", "", (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").strip()
        return make_item("pexels", v["id"], "video", self.LICENSE, title=title or f"Pexels video {v['id']}",
                         page_url=v.get("url", ""), thumb=v.get("image", ""), width=v.get("width"), height=v.get("height"),
                         duration=v.get("duration"), fps=round(fps, 3) if fps else 0, creator=name,
                         creator_url=user.get("url", ""), files=files,
                         attribution=f"Video by {name} on Pexels — {v.get('url', '')}")

    def _photo(self, p):
        src = p.get("src") or {}
        w, h = p.get("width") or 0, p.get("height") or 0
        files = []
        if src.get("original"):
            files.append({"url": src["original"], "width": w, "height": h, "quality": "original",
                          "mime": "image/jpeg", "size": 0})
        if src.get("large2x") and w:
            k = min(1.0, 1880 / w)   # large2x = 940 px @2x
            files.append({"url": src["large2x"], "width": int(w * k), "height": int(h * k), "quality": "large2x",
                          "mime": "image/jpeg", "size": 0})
        name = p.get("photographer") or "Pexels photographer"
        extra = {"resize": src["original"] + "?auto=compress&cs=tinysrgb&w={w}"} if src.get("original") else {}
        return make_item("pexels", p["id"], "photo", self.LICENSE, title=p.get("alt") or f"Pexels photo {p['id']}",
                         page_url=p.get("url", ""), thumb=src.get("medium") or src.get("small", ""), width=w, height=h,
                         creator=name, creator_url=p.get("photographer_url", ""), files=files, extra=extra,
                         attribution=f"Photo by {name} on Pexels — {p.get('url', '')}")


class Pixabay(Provider):
    name, label = "pixabay", "Pixabay"
    kinds = ("video", "photo", "illustration", "vector")
    key_names = ("PIXABAY_API_KEY",)
    home = "https://pixabay.com"
    gives = "photos, illustrations, vector art (as raster previews ≤1280 px) and videos (film + animation)"
    licence_note = "Pixabay Content License — free for commercial use, no attribution required; not for standalone resale, not for trademarks/logos, people/brands need care."
    attribution_rule = "Not required (\"Image by NAME from Pixabay\" appreciated). API terms: show a Pixabay credit where results are displayed."
    rate_note = "100 requests/60 s; responses MUST be cached 24 h (done); no hotlinking — files are downloaded"
    get_key_steps = ["Create a free account at https://pixabay.com/accounts/register/",
                     "While logged in open https://pixabay.com/api/docs/ — your key is shown in the 'key (required)' parameter row",
                     "Paste it into keys.json as PIXABAY_API_KEY"]
    LICENSE = L.fixed("Pixabay Content License", "https://pixabay.com/service/license-summary/")

    def search(self, q, kind, o):
        params = {"key": self.key(), "q": q[:100], "per_page": min(200, max(3, o.count)), "page": o.page,
                  "safesearch": "true"}
        if o.min_width:
            params["min_width"] = o.min_width
        if kind == "video":
            j = get_json("https://pixabay.com/api/videos/", params)
            return [self._video(h) for h in j.get("hits", [])]
        params["image_type"] = {"photo": "photo", "illustration": "illustration", "vector": "vector"}[kind]
        if o.orientation in ("landscape", "portrait"):
            params["orientation"] = "horizontal" if o.orientation == "landscape" else "vertical"
        j = get_json("https://pixabay.com/api/", params)
        return [self._image(h, kind) for h in j.get("hits", [])]

    def _video(self, h):
        vids = h.get("videos") or {}
        files, thumb = [], ""
        for q in ("large", "medium", "small", "tiny"):
            v = vids.get(q) or {}
            if v.get("url"):
                files.append({"url": v["url"], "width": v.get("width") or 0, "height": v.get("height") or 0,
                              "quality": q, "mime": "video/mp4", "size": v.get("size") or 0})
                thumb = thumb or v.get("thumbnail", "")
        if not thumb and h.get("picture_id"):
            thumb = f"https://i.vimeocdn.com/video/{h['picture_id']}_640x360.jpg"
        top = max(files, key=lambda f: f["width"] * f["height"], default={"width": 0, "height": 0})
        user = h.get("user") or "Pixabay user"
        tags = h.get("tags", "")
        kind_note = "animation" if h.get("type") == "animation" else ""
        return make_item("pixabay", h["id"], "video", self.LICENSE, title=tags or f"Pixabay video {h['id']}",
                         page_url=h.get("pageURL", ""), thumb=thumb, width=top["width"], height=top["height"],
                         duration=h.get("duration"), creator=user,
                         creator_url=f"https://pixabay.com/users/{user}-{h.get('user_id', '')}/", tags=tags,
                         files=files, notes=[kind_note] if kind_note else [],
                         attribution=f"Video by {user} from Pixabay — {h.get('pageURL', '')}")

    def _image(self, h, kind):
        W, H = h.get("imageWidth") or 0, h.get("imageHeight") or 0
        files = []
        for key, maxw in (("imageURL", W), ("fullHDURL", 1920), ("largeImageURL", 1280)):
            if h.get(key):
                k = min(1.0, maxw / W) if W else 1.0
                files.append({"url": h[key], "width": int(W * k), "height": int(H * k), "quality": key,
                              "mime": mime_of(h[key], "image/jpeg"), "size": 0})
        notes = []
        if kind == "vector" and not h.get("vectorURL"):
            notes.append("Pixabay gives a raster preview (≤1280 px) of this vector; the .svg/.ai source needs Pixabay full API access")
        if h.get("vectorURL"):
            files.insert(0, {"url": h["vectorURL"], "width": W, "height": H, "quality": "vector",
                             "mime": mime_of(h["vectorURL"], "application/octet-stream"), "size": 0})
        user = h.get("user") or "Pixabay user"
        top = max(files, key=lambda f: f["width"], default={"width": 0, "height": 0})
        return make_item("pixabay", h["id"], kind, self.LICENSE, title=h.get("tags") or f"Pixabay {kind} {h['id']}",
                         page_url=h.get("pageURL", ""), thumb=h.get("webformatURL") or h.get("previewURL", ""),
                         width=top["width"] or W, height=top["height"] or H, creator=user, tags=h.get("tags", ""),
                         creator_url=f"https://pixabay.com/users/{user}-{h.get('user_id', '')}/", files=files,
                         notes=notes, extra={"original_size": [W, H]},
                         attribution=f"Image by {user} from Pixabay — {h.get('pageURL', '')}")


class Unsplash(Provider):
    name, label = "unsplash", "Unsplash"
    kinds = ("photo",)
    key_names = ("UNSPLASH_ACCESS_KEY",)
    home = "https://unsplash.com"
    gives = "high-resolution photos"
    licence_note = "Unsplash License — free for commercial use, attribution not required by the licence; no compiling into a competing service, no selling unaltered copies."
    attribution_rule = "API guidelines REQUIRE crediting where shown: \"Photo by NAME on Unsplash\" with links to the photographer and Unsplash. Download pings are sent automatically."
    rate_note = "50 requests/hour in demo mode (apply for production for 5000/h)"
    get_key_steps = ["Create a free account at https://unsplash.com/join",
                     "Register as a developer and open https://unsplash.com/oauth/applications → 'New Application', accept the API guidelines",
                     "On the app page copy the 'Access Key' (NOT the Secret key)",
                     "Paste it into keys.json as UNSPLASH_ACCESS_KEY"]
    LICENSE = L.fixed("Unsplash License", "https://unsplash.com/license", attribution_required=True)
    UTM = "utm_source=studio-mcp&utm_medium=referral"

    def auth_headers(self):
        return {"Authorization": f"Client-ID {self.key()}", "Accept-Version": "v1"}

    def search(self, q, kind, o):
        params = {"query": q, "per_page": min(30, max(1, o.count)), "page": o.page, "content_filter": "high"}
        if o.orientation:
            params["orientation"] = {"landscape": "landscape", "portrait": "portrait", "square": "squarish"}.get(o.orientation, "")
        j = get_json("https://api.unsplash.com/search/photos", params, self.auth_headers())
        return [self._photo(p) for p in j.get("results", [])]

    def _photo(self, p):
        urls, links, user = p.get("urls") or {}, p.get("links") or {}, p.get("user") or {}
        w, h = p.get("width") or 0, p.get("height") or 0
        files = []
        if urls.get("full"):
            files.append({"url": urls["full"], "width": w, "height": h, "quality": "full", "mime": "image/jpeg", "size": 0})
        if urls.get("regular") and w:
            files.append({"url": urls["regular"], "width": 1080, "height": int(h * 1080 / w), "quality": "regular",
                          "mime": "image/jpeg", "size": 0})
        name = user.get("name") or "Unsplash photographer"
        uname = user.get("username", "")
        prof = f"https://unsplash.com/@{uname}?{self.UTM}" if uname else (user.get("links") or {}).get("html", "")
        extra = {"download_location": links.get("download_location", "")}
        if urls.get("raw"):
            extra["resize"] = urls["raw"] + ("&" if "?" in urls["raw"] else "?") + "w={w}&fm=jpg&q=85"
        return make_item("unsplash", p["id"], "photo", self.LICENSE,
                         title=p.get("description") or p.get("alt_description") or f"Unsplash photo {p['id']}",
                         page_url=links.get("html", ""), thumb=urls.get("small") or urls.get("thumb", ""), width=w,
                         height=h, creator=name, creator_url=prof, files=files, extra=extra,
                         tags=[t.get("title", "") for t in p.get("tags") or [] if isinstance(t, dict)],
                         attribution=f"Photo by {name} ({prof}) on Unsplash (https://unsplash.com/?{self.UTM})")

    def on_download(self, it):
        loc = (it.get("extra") or {}).get("download_location")
        if not loc:
            return "Unsplash download_location missing — download not registered"
        try:
            request(loc, headers=self.auth_headers(), timeout=15, retries=1)
            return ""
        except HTTPFail as e:
            return f"Unsplash download ping failed: {e}"


class Coverr(Provider):
    name, label = "coverr", "Coverr"
    kinds = ("video",)
    key_names = ("COVERR_API_KEY",)
    home = "https://coverr.co"
    gives = "free HD stock videos (lifestyle, b-roll)"
    licence_note = "Coverr License — free for commercial use, no attribution required; don't resell unmodified clips."
    attribution_rule = "Not required (\"Video from Coverr\" appreciated)."
    rate_note = "free tier ~1000 calls/month in development; files come via signed mp4_download URLs (which also register the download as Coverr's API requires)"
    get_key_steps = ["Open https://coverr.co/developers and press 'Get API Key' (sign up / log in)",
                     "Create an app (name + short use-case); the key appears under your account → API Keys",
                     "(Coverr's docs also say you can request a key by emailing team@coverr.co)",
                     "Paste it into keys.json as COVERR_API_KEY"]
    untested = True   # written from https://api.coverr.co/docs — no key available while building
    LICENSE = L.fixed("Coverr License", "https://coverr.co/license")

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.key()}"}

    def search(self, q, kind, o):
        params = {"query": q, "page": max(0, o.page - 1), "page_size": min(100, max(1, o.count)), "urls": "true"}
        j = get_json("https://api.coverr.co/videos", params, self.auth_headers())
        hits = j.get("hits") if isinstance(j, dict) else j
        return [self._video(v) for v in hits or [] if isinstance(v, dict)]

    def _video(self, v):
        urls = v.get("urls") or {}
        mw = v.get("max_width") or v.get("maxWidth") or 0
        mh = v.get("max_height") or v.get("maxHeight") or 0
        if not (mw and mh):
            ar = str(v.get("aspect_ratio") or v.get("aspectRatio") or "16:9")
            vertical = bool(v.get("is_vertical"))
            mw, mh = (1080, 1920) if vertical else (1920, 1080)
            if ar == "1:1":
                mw = mh = 1080
        files = []
        dl = urls.get("mp4_download") or urls.get("mp4Download")
        mp4 = urls.get("mp4")
        prev = urls.get("mp4_preview") or urls.get("mp4Preview")
        if dl:
            files.append({"url": dl, "width": mw, "height": mh, "quality": "download", "mime": "video/mp4", "size": 0})
        elif mp4:
            files.append({"url": mp4, "width": mw, "height": mh, "quality": "mp4", "mime": "video/mp4", "size": 0})
        if prev:
            k = 360 / min(mw, mh)
            files.append({"url": prev, "width": int(mw * k), "height": int(mh * k), "quality": "preview",
                          "mime": "video/mp4", "size": 0})
        slug = v.get("slug") or v.get("id")
        return make_item("coverr", v.get("id"), "video", self.LICENSE, title=v.get("title") or f"Coverr {v.get('id')}",
                         page_url=f"https://coverr.co/videos/{slug}", thumb=v.get("thumbnail") or v.get("poster", ""),
                         width=mw, height=mh, duration=v.get("duration"), tags=v.get("tags") or [], files=files,
                         creator="Coverr", notes=["Coverr adapter untested against the live API (no key at build time)"],
                         attribution=f"Video from Coverr — https://coverr.co/videos/{slug}")


class Freesound(Provider):
    name, label = "freesound", "Freesound"
    kinds = ("sfx", "music")
    key_names = ("FREESOUND_API_KEY", "FREESOUND_TOKEN")
    home = "https://freesound.org"
    gives = "sound effects, ambiences, foley, loops (HQ MP3 previews)"
    licence_note = "Per sound: CC0, CC BY, CC BY-NC (NOT commercial) or Sampling+. Originals need an OAuth login; we download the HQ MP3 preview."
    attribution_rule = "CC BY: credit '\"SOUND\" by USER (freesound.org/s/ID) — CC BY x.0'. CC0: none."
    rate_note = "60 requests/minute, 2000/day"
    get_key_steps = ["Create a free account at https://freesound.org/home/register/",
                     "Open https://freesound.org/apiv2/apply and create a credential (name + description; callback URL can be http://localhost)",
                     "Copy the long 'Client secret/Api key' value shown in the table",
                     "Paste it into keys.json as FREESOUND_API_KEY"]

    def search(self, q, kind, o):
        flt = []
        if o.min_duration or o.max_duration:
            flt.append(f"duration:[{o.min_duration or 0} TO {o.max_duration or '*'}]")
        if o.commercial:
            flt.append('license:("Creative Commons 0" OR "Attribution")')
        if kind == "music":
            flt.append("(tag:music OR tag:loop OR tag:melody)")
        params = {"query": q, "token": self.key(), "page_size": min(150, max(1, o.count)), "page": o.page,
                  "fields": "id,name,tags,license,username,duration,previews,images,url,filesize,type,samplerate",
                  "filter": " ".join(flt), "sort": "score"}
        j = get_json("https://freesound.org/apiv2/search/text/", params)
        return [self._sound(s, kind) for s in j.get("results", [])]

    def _sound(self, s, kind):
        lic = L.cc(code=s.get("license", "")) if not str(s.get("license", "")).startswith("http") else L.cc(url=s["license"])
        prev = s.get("previews") or {}
        files = []
        for key, q, mime in (("preview-hq-mp3", "hq-mp3", "audio/mpeg"), ("preview-hq-ogg", "hq-ogg", "audio/ogg"),
                             ("preview-lq-mp3", "lq-mp3", "audio/mpeg")):
            if prev.get(key):
                files.append({"url": prev[key], "width": 0, "height": 0, "quality": q, "mime": mime, "size": 0})
        imgs = s.get("images") or {}
        user = s.get("username") or "freesound user"
        page = s.get("url") or f"https://freesound.org/s/{s['id']}/"
        return make_item("freesound", s["id"], kind, lic, title=s.get("name"), page_url=page,
                         thumb=imgs.get("waveform_m") or imgs.get("waveform_l", ""), duration=s.get("duration"),
                         creator=user, creator_url=f"https://freesound.org/people/{user}/", tags=s.get("tags") or [],
                         files=files, notes=["HQ MP3 preview (~128 kbps); original file needs Freesound OAuth"],
                         extra={"waveform": True},
                         attribution=f"\"{clean_text(s.get('name'))}\" by {user} ({page}) — {lic['license']} {lic['license_url']}")


class Jamendo(Provider):
    name, label = "jamendo", "Jamendo"
    kinds = ("music",)
    key_names = ("JAMENDO_CLIENT_ID",)
    home = "https://www.jamendo.com"
    gives = "independent music tracks under Creative Commons"
    licence_note = "Per track CC licence — most are BY-NC-* (NOT commercial; Jamendo Licensing sells commercial licences). The free API itself is for non-commercial use."
    attribution_rule = "Credit '\"TRACK\" by ARTIST (jamendo link) — CC licence'."
    rate_note = "35 000 requests/month (free non-commercial API)"
    get_key_steps = ["Create a free developer account at https://devportal.jamendo.com/signup",
                     "Open 'My apps' → 'Create a new app' (name, website can be your GitHub)",
                     "Copy the 'Client ID' (the Client secret is NOT needed)",
                     "Paste it into keys.json as JAMENDO_CLIENT_ID"]

    def search(self, q, kind, o):
        params = {"client_id": self.key(), "format": "json", "limit": min(200, max(1, o.count * (3 if o.commercial else 1))),
                  "offset": (o.page - 1) * o.count, "search": q, "include": "licenses musicinfo",
                  "audioformat": "mp32", "imagesize": 300, "order": "relevance"}
        if o.min_duration or o.max_duration:
            params["durationbetween"] = f"{int(o.min_duration or 0)}_{int(o.max_duration or 3600)}"
        j = get_json("https://api.jamendo.com/v3.0/tracks/", params)
        head = j.get("headers") or {}
        if head.get("status") not in (None, "success"):
            raise ProviderError(f"Jamendo: {head.get('error_message') or head.get('status')}")
        return [self._track(t) for t in j.get("results", []) if t.get("audiodownload_allowed", True)]

    def _track(self, t):
        lic = L.cc(url=t.get("license_ccurl", ""))
        files = []
        if t.get("audiodownload"):
            files.append({"url": t["audiodownload"], "width": 0, "height": 0, "quality": "download", "mime": "audio/mpeg", "size": 0})
        if t.get("audio"):
            files.append({"url": t["audio"], "width": 0, "height": 0, "quality": "stream", "mime": "audio/mpeg", "size": 0})
        tags = []
        mi = (t.get("musicinfo") or {}).get("tags") or {}
        for k in ("genres", "vartags", "instruments"):
            tags += mi.get(k) or []
        artist = t.get("artist_name") or "Jamendo artist"
        page = t.get("shareurl") or f"https://www.jamendo.com/track/{t['id']}"
        return make_item("jamendo", t["id"], "music", lic, title=t.get("name"), page_url=page,
                         thumb=t.get("album_image") or t.get("image", ""), duration=t.get("duration"), creator=artist,
                         creator_url=t.get("artist_idstr") and f"https://www.jamendo.com/artist/{t.get('artist_id')}" or "",
                         tags=tags, files=files,
                         attribution=f"\"{clean_text(t.get('name'))}\" by {artist} ({page}) — {lic['license']} {lic['license_url']}")


# ─────────────────────────────── keyless providers ───────────────────────────────

class Openverse(Provider):
    name, label = "openverse", "Openverse"
    kinds = ("photo", "illustration", "vector", "music", "sfx")
    home = "https://openverse.org"
    gives = "800M+ openly licensed images and audio aggregated from Flickr, Wikimedia, museums, Freesound, Jamendo…"
    licence_note = "Per item CC0 / PDM / CC BY(-SA/-ND/-NC); commercial=true asks Openverse for commercial+modifiable only."
    attribution_rule = "Ready-made attribution text is provided per item (required unless CC0/PDM)."
    rate_note = "anonymous: ~20 requests/minute burst, a few hundred per day — results cached 24 h; page_size ≤ 20"

    def search(self, q, kind, o):
        audio = kind in ("music", "sfx")
        params = {"q": q, "page_size": min(20, max(1, o.count)), "page": o.page, "mature": "false"}
        if o.commercial:
            params["license_type"] = "commercial,modification"
        # NB: Openverse's `category` is empty for most records, so filtering on it hides ~99 % of hits;
        # only illustrations use it. Audio kind (music vs sfx) is steered by duration in the ranking.
        if not audio:
            if kind == "illustration":
                params["category"] = "illustration,digitized_artwork"
            elif kind == "vector":
                params["extension"] = "svg"
            if o.orientation:
                params["aspect_ratio"] = {"landscape": "wide", "portrait": "tall", "square": "square"}.get(o.orientation, "")
            if o.min_width >= 1600:
                params["size"] = "large"
        j = get_json(f"https://api.openverse.org/v1/{'audio' if audio else 'images'}/", params, family="openverse")
        return [self._item(r, kind, audio) for r in j.get("results", [])]

    def _item(self, r, kind, audio):
        lic = L.cc(r.get("license", ""), r.get("license_version", ""), r.get("license_url", ""))
        if r.get("license_url"):
            lic["license_url"] = r["license_url"]
        url = r.get("url") or ""
        ftype = (r.get("filetype") or "").lower()
        mime = mime_of(url) or ({"mp3": "audio/mpeg", "jpg": "image/jpeg", "svg": "image/svg+xml"}.get(ftype, ""))
        files = [{"url": url, "width": r.get("width") or 0, "height": r.get("height") or 0, "quality": "original",
                  "mime": mime, "size": r.get("filesize") or 0}] if url else []
        for a in r.get("alt_files") or []:
            if a.get("url") and "freesound.org/apiv2" not in a["url"]:   # Freesound originals need OAuth
                files.append({"url": a["url"], "width": 0, "height": 0, "quality": a.get("filetype", "alt"),
                              "mime": mime_of(a["url"]), "size": a.get("filesize") or 0})
        dur = (r.get("duration") or 0) / 1000.0 if audio else 0
        thumb = r.get("thumbnail") or ""
        if not audio and "staticflickr.com" in url:   # Flickr's own 320 px thumb: spares the anonymous API quota
            thumb = re.sub(r"(_[a-z])?\.jpg$", "_n.jpg", url)
        src = r.get("source") or r.get("provider") or ""
        return make_item("openverse", r["id"], kind, lic, source=f"openverse/{src}" if src else "openverse",
                         title=r.get("title"), page_url=r.get("foreign_landing_url", ""), thumb=thumb,
                         width=r.get("width"), height=r.get("height"), duration=dur, creator=r.get("creator") or "",
                         creator_url=r.get("creator_url") or "", tags=[t.get("name", "") for t in r.get("tags") or []
                                                                         if t.get("accuracy") in (None, "") or (t.get("accuracy") or 0) > 0.9],
                         files=files, attribution=r.get("attribution") or "",
                         extra={"source": src, "detail_url": r.get("detail_url", "")})


class Wikimedia(Provider):
    name, label = "wikimedia", "Wikimedia Commons"
    kinds = ("video", "photo", "illustration", "vector", "music", "sfx")
    home = "https://commons.wikimedia.org"
    gives = "100M+ free media files: photos, SVG drawings, WebM/OGV video, audio — all freely licensed"
    licence_note = "Per file: Public Domain, CC0, CC BY, CC BY-SA (share-alike!), GFDL… Commons accepts no NC/ND content."
    attribution_rule = "Credit author + licence + link to the file page (done in the attribution text) unless PD/CC0."
    rate_note = "shared anonymous limits — we send a descriptive User-Agent, pace 1 request/s and cache 24 h; bursts get HTTP 429"
    FILETYPE = {"video": "filetype:video", "photo": "filetype:bitmap", "illustration": "filetype:drawing",
                "vector": "filetype:drawing", "music": "filetype:audio", "sfx": "filetype:audio"}
    THUMB_STEPS = (330, 500, 960, 1280, 1920, 3840)

    def search(self, q, kind, o):
        n = min(50, max(1, o.count))
        meta = "LicenseShortName|LicenseUrl|Artist|AttributionRequired|ObjectName|ImageDescription|UsageTerms|Copyrighted|Restrictions"
        params = {"action": "query", "format": "json", "formatversion": "2", "generator": "search",
                  "gsrsearch": f"{q} {self.FILETYPE[kind]}", "gsrnamespace": 6, "gsrlimit": n,
                  "gsroffset": (o.page - 1) * n, "prop": "videoinfo", "viprop": "url|size|mime|extmetadata|derivatives",
                  "viurlwidth": 500, "viextmetadatafilter": meta}
        j = get_json("https://commons.wikimedia.org/w/api.php", params, family="wikimedia")
        pages = (j.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):
            pages = list(pages.values())
        pages.sort(key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            it = self._item(p, kind)
            if it:
                out.append(it)
        return out

    def _item(self, p, kind):
        info = (p.get("videoinfo") or p.get("imageinfo") or [None])[0]
        if not info:
            return None
        mime = info.get("mime") or ""
        if kind == "video" and not (mime.startswith("video/") or mime == "application/ogg"):
            return None
        if kind in ("music", "sfx") and not (mime.startswith("audio/") or (mime == "application/ogg" and not info.get("width"))):
            return None
        if kind == "vector" and mime != "image/svg+xml":
            return None
        if kind == "photo" and mime not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            return None
        em = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (info.get("extmetadata") or {}).items()}
        short = clean_text(em.get("LicenseShortName", ""))
        lic = L.cc(code=short, url=em.get("LicenseUrl", "") or "")
        if short and lic["license"] in ("unknown licence", short.upper()):
            low = short.lower()
            if "public domain" in low or low.startswith("pd"):
                lic = L.cc("pd")
            elif "gfdl" in low or "gpl" in low or "free art" in low:
                lic = {"license": short, "license_url": em.get("LicenseUrl", "") or "", "commercial_ok": True,
                       "modify_ok": True, "attribution_required": True, "share_alike": True}
        if str(em.get("AttributionRequired", "")).lower() == "false":
            lic["attribution_required"] = False
        artist = clean_text(em.get("Artist", "")) or "unknown author"
        title = clean_text(em.get("ObjectName") or p.get("title", "").replace("File:", "").rsplit(".", 1)[0])
        page = info.get("descriptionurl", "")
        files = [{"url": info["url"], "width": info.get("width") or 0, "height": info.get("height") or 0,
                  "quality": "original", "mime": mime if mime != "application/ogg" else "video/ogg", "size": info.get("size") or 0}]
        for d in info.get("derivatives") or []:
            if d.get("transcodekey"):
                files.append({"url": d["src"], "width": d.get("width") or 0, "height": d.get("height") or 0,
                              "quality": d["transcodekey"], "mime": (d.get("type") or "").split(";")[0], "size": 0})
        extra = {}
        thumb = info.get("thumburl", "")
        if thumb and mime.startswith("image/") and mime != "image/svg+xml":
            m = re.search(r"/(\d+)px-", thumb)
            if m:
                extra["resize"] = thumb[:m.start()] + "/{w}px-" + thumb[m.end():]
                extra["resize_steps"] = list(self.THUMB_STEPS)
        notes = []
        if lic.get("share_alike"):
            notes.append("share-alike: a work that adapts this must carry the same licence")
        lic_txt = f"{lic['license']} {lic['license_url']}".strip()
        return make_item("wikimedia", p.get("pageid"), kind, lic, title=title, page_url=page, thumb=thumb,
                         width=info.get("width"), height=info.get("height"), duration=info.get("duration") or 0,
                         creator=artist, files=files, extra=extra, notes=notes,
                         attribution=f"\"{title}\" by {artist}, {lic_txt}, via Wikimedia Commons — {page}")


class InternetArchive(Provider):
    name, label = "archive", "Internet Archive"
    kinds = ("video", "music", "sfx")
    home = "https://archive.org"
    gives = "public-domain and CC films, newsreels, home movies, music and recordings"
    licence_note = "Only items whose licenseurl is Public Domain / CC are searched (commercial=true: PD, CC0, CC BY, CC BY-SA). Uploaders set licences themselves — check the item page."
    attribution_rule = "Credit creator + licence + archive.org link unless public domain."
    rate_note = "no key; be gentle (we pace requests and cache 24 h)"

    def search(self, q, kind, o):
        qq = q.replace('"', " ")
        lic = ("(licenseurl:*publicdomain* OR licenseurl:*\\/by\\/* OR licenseurl:*\\/by-sa\\/*)" if o.commercial
               else "(licenseurl:*publicdomain* OR licenseurl:*creativecommons.org*)")
        mt = "movies" if kind == "video" else "audio"
        query = f"(title:({qq}) OR subject:({qq}) OR description:({qq})) AND mediatype:({mt}) AND {lic}"
        fields = ["identifier", "title", "creator", "licenseurl", "subject", "runtime", "description"]
        params = [("q", query)] + [("fl[]", f) for f in fields] + [("rows", str(min(100, max(1, o.count)))),
                                                                    ("page", str(o.page)), ("output", "json")]
        j = get_json("https://archive.org/advancedsearch.php", params, family="archive")
        return [self._doc(d, kind) for d in (j.get("response") or {}).get("docs", [])]

    @staticmethod
    def _runtime(s) -> float:
        if isinstance(s, list):
            s = s[0] if s else ""
        s = str(s or "").strip()
        try:
            if ":" in s:
                parts = [float(x) for x in s.split(":")]
                t = 0.0
                for x in parts:
                    t = t * 60 + x
                return t
            m = re.match(r"([\d.]+)\s*(min|minutes|m)?", s)
            if m:
                return float(m.group(1)) * (60 if m.group(2) else 1)
        except ValueError:
            pass
        return 0.0

    def _doc(self, d, kind):
        ident = d["identifier"]
        lic = L.cc(url=d.get("licenseurl") or "")
        creator = clean_text(d.get("creator")) or "unknown creator"
        page = f"https://archive.org/details/{ident}"
        return make_item("archive", ident, kind, lic, title=clean_text(d.get("title")) or ident, page_url=page,
                         thumb=f"https://archive.org/services/img/{ident}", duration=self._runtime(d.get("runtime")),
                         creator=creator, tags=d.get("subject") or [], files=[],
                         notes=["licence set by the uploader — check the item page before commercial use"],
                         attribution=f"\"{clean_text(d.get('title')) or ident}\" by {creator} — {lic['license']} "
                                     f"{lic['license_url']} — via Internet Archive {page}")

    def resolve(self, it):
        if it.get("files"):
            return it
        ident = it["id"].split(":", 1)[1]
        j = get_json(f"https://archive.org/metadata/{ident}", family="archive")
        files = []
        for f in j.get("files") or []:
            name, fmt = f.get("name", ""), (f.get("format") or "")
            url = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
            if it["kind"] == "video" and re.search(r"\.(mp4|m4v|mov|webm|ogv|mpe?g|avi)$", name, re.I):
                if fmt.lower() in ("thumbnail",):
                    continue
                files.append({"url": url, "width": int(float(f.get("width") or 0)), "height": int(float(f.get("height") or 0)),
                              "quality": fmt, "mime": mime_of(url, "video/mp4"), "size": int(f.get("size") or 0),
                              "duration": float(f.get("length") or 0) if re.match(r"^[\d.]+$", str(f.get("length") or "")) else 0})
            elif it["kind"] in ("music", "sfx") and re.search(r"\.(mp3|ogg|flac|wav|m4a|opus)$", name, re.I):
                files.append({"url": url, "width": 0, "height": 0, "quality": fmt, "mime": mime_of(url, "audio/mpeg"),
                              "size": int(f.get("size") or 0), "duration": self._runtime(f.get("length")), "name": name})
        if it["kind"] in ("music", "sfx") and files:
            n_tracks = len({re.sub(r"\.[a-z0-9]+$", "", f["name"], flags=re.I) for f in files})
            if n_tracks > 1:
                it.setdefault("notes", []).append(f"item holds {n_tracks} audio tracks — the first one is downloaded")
                first = re.sub(r"\.[a-z0-9]+$", "", sorted(f["name"] for f in files)[0], flags=re.I)
                files = [f for f in files if re.sub(r"\.[a-z0-9]+$", "", f["name"], flags=re.I) == first]
        it["files"] = files
        vids = [f for f in files if f.get("width")]
        if vids:
            top = max(vids, key=lambda f: f["width"] * f["height"])
            it["width"], it["height"] = top["width"], top["height"]
        durs = [f.get("duration") or 0 for f in files]
        if max(durs or [0]) and not it.get("duration"):
            it["duration"] = round(max(durs), 2)
        return it


class Nasa(Provider):
    name, label = "nasa", "NASA Image and Video Library"
    kinds = ("photo", "video", "sfx")
    home = "https://images.nasa.gov"
    gives = "space, Earth, launches, aircraft — photos, videos and audio"
    licence_note = ("Generally public domain (US government work). NOT allowed: the NASA insignia/logo/'meatball', "
                    "implying NASA endorsement; identifiable people need consent for commercial use; items credited "
                    "to a non-NASA photographer/partner may be copyrighted.")
    attribution_rule = "Not required, but 'Courtesy NASA/CENTER' (and the photographer) is requested."
    rate_note = "no key; generous"
    PD = L.fixed("Public Domain (NASA)", "https://www.nasa.gov/nasa-brand-center/images-and-media/")
    NASA_RE = re.compile(r"nasa|jpl|caltech|goddard|kennedy|johnson|marshall|ames|langley|glenn|armstrong|stennis|esa|noaa", re.I)

    def search(self, q, kind, o):
        mt = {"photo": "image", "video": "video", "sfx": "audio"}[kind]
        params = {"q": q, "media_type": mt, "page": o.page, "page_size": min(100, max(1, o.count))}
        j = get_json("https://images-api.nasa.gov/search", params)
        return [x for x in (self._item(i, kind) for i in (j.get("collection") or {}).get("items", [])) if x]

    def _item(self, i, kind):
        d = (i.get("data") or [{}])[0]
        nid = d.get("nasa_id")
        if not nid:
            return None
        thumb, tw, th = "", 0, 0
        for l in i.get("links") or []:
            if l.get("render") == "image" and l.get("href"):
                if not thumb or "~medium" in l["href"] or "~thumb" in l["href"]:
                    thumb, tw, th = l["href"], l.get("width") or 0, l.get("height") or 0
                if "~medium" in l["href"]:
                    break
        lic = dict(self.PD)
        notes = ["NASA: no insignia/logo use, no implied endorsement; people need consent for commercial use"]
        credit_bits = [x for x in (d.get("photographer"), d.get("secondary_creator")) if x]
        credit = ", ".join(clean_text(c) for c in credit_bits)
        if credit and not self.NASA_RE.search(credit):
            notes.append(f"credited to {credit} — may be third-party copyrighted; verify before commercial use")
            lic["attribution_required"] = True
        center = d.get("center") or "NASA"
        creator = credit or f"NASA/{center}"
        page = f"https://images.nasa.gov/details/{urllib.parse.quote(nid)}"
        attribution = f"\"{clean_text(d.get('title'))}\" — Courtesy NASA/{center}" + (f" ({credit})" if credit else "") + f" — {page}"
        # thumbnail size is not the media size: width/height are filled by resolve()
        return make_item("nasa", nid, kind, lic, title=d.get("title"), page_url=page, thumb=thumb.replace("http://", "https://"), creator=creator, tags=d.get("keywords") or [], notes=notes, files=[],
                         extra={"collection": (i.get("href") or "").replace("http://", "https://"),
                                "thumb_aspect": round(tw / th, 4) if tw and th else 0},
                         attribution=attribution)

    ORDER = {"orig": 0, "large": 1, "medium": 2, "small": 3, "mobile": 4, "preview": 5, "thumb": 6}

    def resolve(self, it):
        if it.get("files"):
            return it
        coll = (it.get("extra") or {}).get("collection")
        if not coll:
            return it
        urls = get_json(coll)
        files = []
        for u in urls if isinstance(urls, list) else []:
            u = u.replace("http://", "https://")
            m = re.search(r"~(\w+)\.(\w+)$", u)
            ext = u.rsplit(".", 1)[-1].lower()
            if it["kind"] == "photo" and ext in ("jpg", "jpeg", "png", "tif", "tiff"):
                q = m.group(1) if m else "orig"
            elif it["kind"] == "video" and ext in ("mp4", "mov", "m4v"):
                q = m.group(1) if m else "orig"
            elif it["kind"] == "sfx" and ext in ("mp3", "wav", "m4a"):
                q = m.group(1) if m else "orig"
            else:
                continue
            if q == "thumb":
                continue
            files.append({"url": u, "width": 0, "height": 0, "quality": q, "mime": mime_of(u), "size": 0,
                          "rank": self.ORDER.get(q, 3)})
        files.sort(key=lambda f: f["rank"])
        # sizes: metadata.json describes the original
        try:
            meta = get_json(coll.rsplit("/", 1)[0] + "/metadata.json")
            w = meta.get("File:ImageWidth") or meta.get("EXIF:ImageWidth") or meta.get("QuickTime:ImageWidth") or meta.get("Composite:ImageWidth")
            h = meta.get("File:ImageHeight") or meta.get("EXIF:ImageHeight") or meta.get("QuickTime:ImageHeight") or meta.get("Composite:ImageHeight")
            if not (w and h) and meta.get("Composite:ImageSize"):
                w, h = (int(float(x)) for x in re.split(r"[x ]", str(meta["Composite:ImageSize"]))[:2])
            dur = meta.get("QuickTime:Duration") or meta.get("Composite:Duration") or 0
            if w and h:
                it["width"], it["height"] = int(w), int(h)
                for f in files:
                    if f["quality"] == "orig":
                        f["width"], f["height"] = int(w), int(h)
            if dur:
                d = str(dur)
                it["duration"] = InternetArchive._runtime(d.split(" ")[0]) if ":" in d else float(re.sub(r"[^\d.]", "", d) or 0)
        except Exception:
            pass
        # NASA's derivative ladder (typical): large≈1920, medium≈1280, small≈640, mobile≈480 wide
        W, H = it.get("width") or 0, it.get("height") or 0
        for f in files:
            if not f["width"] and W and H:
                target = {"large": 1920, "medium": 1280, "small": 640, "mobile": 480, "preview": 480}.get(f["quality"], W)
                k = min(1.0, target / W)
                f["width"], f["height"] = int(W * k), int(H * k)
                f["estimated"] = True
        it["files"] = files
        return it


class Iconify(Provider):
    name, label = "iconify", "Iconify"
    kinds = ("icon",)
    home = "https://icon-sets.iconify.design"
    gives = "200 000+ open-source SVG icons from 150+ sets (Material, Tabler, Phosphor, Lucide…)"
    licence_note = "Per icon set (MIT, Apache-2.0, ISC, CC BY 4.0, OFL, GPL…) — carried with every icon."
    attribution_rule = "Only CC BY sets need visible credit; MIT/Apache/ISC need the licence text kept with redistributed source files."
    rate_note = "no key; public API"
    _sets: dict = {}

    def search(self, q, kind, o):
        j = get_json("https://api.iconify.design/search", {"query": q, "limit": max(32, min(999, o.count * 3))})
        cols = j.get("collections") or {}
        self._sets.update(cols)
        out = []
        for full in j.get("icons") or []:
            prefix, _, nm = full.partition(":")
            it = self.icon_item(prefix, nm, cols.get(prefix) or {})
            if o.commercial and not it["commercial_ok"]:
                continue
            out.append(it)
        out.sort(key=lambda it: it["extra"]["animated"])   # static sets first (stable: keeps Iconify's order)
        start = (o.page - 1) * o.count
        return out[start:start + o.count]

    def collection(self, prefix: str) -> dict:
        if prefix not in self._sets:
            j = get_json("https://api.iconify.design/collections", {"prefixes": prefix})
            self._sets.update(j or {})
        return self._sets.get(prefix) or {}

    def icon_item(self, prefix: str, nm: str, col: dict) -> dict:
        lic_d = col.get("license") or {}
        lic = L.spdx(lic_d.get("spdx", ""), lic_d.get("title", ""), lic_d.get("url", ""))
        author = (col.get("author") or {}).get("name", "") or prefix
        setname = col.get("name") or prefix
        svg = f"https://api.iconify.design/{prefix}/{nm}.svg"
        notes = []
        if lic.get("share_alike"):
            notes.append(f"{lic['license']}: copyleft — fine inside images/videos, but redistributed icon source must keep the licence")
        animated = "Contains Animations" in (col.get("tags") or [])
        if animated:
            notes.append("animated SVG (SMIL): browsers/motion tools play it; the PNG is rendered from its final, settled frame")
        hgt = col.get("height") or 24
        hgt = hgt if isinstance(hgt, int) else 24
        return make_item("iconify", f"{prefix}:{nm}", "icon", lic, title=f"{nm.replace('-', ' ')} ({setname})",
                         page_url=f"https://icon-sets.iconify.design/{prefix}/{nm}/", thumb=svg, width=hgt, height=hgt,
                         creator=author, creator_url=(col.get("author") or {}).get("url", ""),
                         files=[{"url": svg, "width": hgt, "height": hgt, "quality": "svg", "mime": "image/svg+xml", "size": 0}],
                         notes=notes, extra={"prefix": prefix, "name": nm, "set": setname, "palette": bool(col.get("palette")),
                                "animated": animated},
                         attribution=f"\"{nm}\" icon from {setname} by {author} — {lic['license']} {lic['license_url']}".strip())


def static_svg(text: str) -> str:
    """Settle an animated (SMIL) icon to its final look for static rendering: drop <animate>/<set>
    and the 'hidden at start' attributes they animate (dash offsets, zero opacity)."""
    t = re.sub(r"<(animate|animateTransform|animateMotion|set)\b[^>]*/>", "", text)
    t = re.sub(r"<(animate|animateTransform|animateMotion|set)\b[^>]*>.*?</\1>", "", t, flags=re.S)
    t = re.sub(r'\sstroke-dashoffset="[^"]*"', "", t)
    t = re.sub(r'\s(fill-opacity|opacity|stroke-opacity)="0"', "", t)
    return t


PROVIDERS: dict[str, Provider] = {p.name: p for p in (Pexels(), Pixabay(), Unsplash(), Coverr(), Freesound(), Jamendo(),
                                                       Openverse(), Wikimedia(), InternetArchive(), Nasa(), Iconify())}

# provider weight per kind: order of trust/quality for that kind (also the fan-out list)
WEIGHTS: dict[str, dict[str, float]] = {
    "video": {"pexels": 1.0, "coverr": 0.95, "pixabay": 0.92, "wikimedia": 0.62, "nasa": 0.58, "archive": 0.5},
    "photo": {"unsplash": 1.0, "pexels": 1.0, "pixabay": 0.9, "openverse": 0.72, "wikimedia": 0.68, "nasa": 0.58},
    "illustration": {"pixabay": 1.0, "openverse": 0.78, "wikimedia": 0.62},
    "vector": {"openverse": 0.9, "wikimedia": 0.88, "pixabay": 0.75},
    "music": {"jamendo": 1.0, "freesound": 0.78, "openverse": 0.72, "archive": 0.55, "wikimedia": 0.45},
    "sfx": {"freesound": 1.0, "openverse": 0.88, "nasa": 0.55, "wikimedia": 0.5, "archive": 0.45},
    "icon": {"iconify": 1.0},
}
KEY_NAMES = [p.key_names[0] for p in PROVIDERS.values() if p.key_names]
