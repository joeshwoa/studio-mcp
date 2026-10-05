"""Stock department tests. Fast ones mock every HTTP call with small realistic fixtures (shapes copied
from the providers' real responses); @slow ones hit the keyless providers for real and download one
small item each; keyed providers' live tests skip without keys."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio.core import keys as K
from studio.core.registry import call
from studio.core.result import ToolError
from studio.depts.stock import _http, _sheet, licenses as L, providers as P, tools as T


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    for n in K_NAMES:
        monkeypatch.delenv(n, raising=False)
    return tmp_path


ORIG_ENV = dict(os.environ)   # captured before the autouse fixture clears keys (live keyed tests use it)
K_NAMES = ["PEXELS_API_KEY", "PIXABAY_API_KEY", "UNSPLASH_ACCESS_KEY", "COVERR_API_KEY", "FREESOUND_API_KEY",
           "FREESOUND_TOKEN", "JAMENDO_CLIENT_ID"]

# ───────────────────────────── fixtures (trimmed real responses) ─────────────────────────────

PEXELS_VIDEOS = {"page": 1, "per_page": 2, "videos": [
    {"id": 3191572, "width": 3840, "height": 2160, "duration": 14, "url": "https://www.pexels.com/video/pouring-coffee-3191572/",
     "image": "https://images.pexels.com/videos/3191572/x.jpeg", "user": {"name": "Ana Lee", "url": "https://www.pexels.com/@ana"},
     "video_files": [
         {"id": 1, "quality": "uhd", "file_type": "video/mp4", "width": 3840, "height": 2160, "fps": 25, "link": "https://videos.pexels.com/a-uhd.mp4", "size": 90000000},
         {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1920, "height": 1080, "fps": 25, "link": "https://videos.pexels.com/a-hd.mp4", "size": 20000000},
         {"id": 3, "quality": "sd", "file_type": "video/mp4", "width": 960, "height": 540, "fps": 25, "link": "https://videos.pexels.com/a-sd.mp4", "size": 5000000}]},
    {"id": 999, "width": 1080, "height": 1920, "duration": 8, "url": "https://www.pexels.com/video/vertical-999/",
     "image": "https://images.pexels.com/videos/999/y.jpeg", "user": {"name": "Bo"},
     "video_files": [{"id": 4, "quality": "hd", "file_type": "video/mp4", "width": 1080, "height": 1920, "fps": 30, "link": "https://videos.pexels.com/b.mp4"}]}]}
PEXELS_PHOTOS = {"photos": [{"id": 1415131, "width": 6000, "height": 4000, "url": "https://www.pexels.com/photo/cup-1415131/",
                             "photographer": "Chevanon", "photographer_url": "https://www.pexels.com/@chevanon", "alt": "Cup of coffee",
                             "src": {"original": "https://images.pexels.com/photos/1415131/p.jpeg", "large2x": "https://images.pexels.com/photos/1415131/p.jpeg?w=940&dpr=2",
                                     "medium": "https://images.pexels.com/photos/1415131/p.jpeg?h=350"}}]}
PIXABAY_IMAGES = {"total": 1, "hits": [{"id": 736885, "pageURL": "https://pixabay.com/photos/tree-736885/", "type": "photo", "tags": "tree, sunset, nature",
                                        "previewURL": "https://cdn.pixabay.com/p_150.jpg", "webformatURL": "https://pixabay.com/get/w_640.jpg",
                                        "largeImageURL": "https://pixabay.com/get/l_1280.jpg", "imageWidth": 4000, "imageHeight": 2250,
                                        "user_id": 909086, "user": "Bessi"}]}
PIXABAY_VIDEOS = {"hits": [{"id": 125, "pageURL": "https://pixabay.com/videos/id-125/", "type": "film", "tags": "flowers, yellow", "duration": 12,
                            "videos": {"large": {"url": "https://cdn.pixabay.com/v/l.mp4", "width": 1920, "height": 1080, "size": 6615235, "thumbnail": "https://cdn.pixabay.com/v/l.jpg"},
                                       "medium": {"url": "https://cdn.pixabay.com/v/m.mp4", "width": 1280, "height": 720, "size": 3562083, "thumbnail": "https://cdn.pixabay.com/v/m.jpg"},
                                       "small": {"url": "https://cdn.pixabay.com/v/s.mp4", "width": 960, "height": 540, "size": 1828126}},
                            "user_id": 1281706, "user": "Coverr-Free-Footage"}]}
UNSPLASH = {"results": [{"id": "eOLpJytrbsQ", "width": 4000, "height": 6000, "description": None, "alt_description": "woman holding coffee",
                         "urls": {"raw": "https://images.unsplash.com/photo-1?ixid=x", "full": "https://images.unsplash.com/photo-1?q=85&fm=jpg",
                                  "regular": "https://images.unsplash.com/photo-1?w=1080", "small": "https://images.unsplash.com/photo-1?w=400"},
                         "links": {"html": "https://unsplash.com/photos/eOLpJytrbsQ", "download_location": "https://api.unsplash.com/photos/eOLpJytrbsQ/download?ixid=x"},
                         "user": {"name": "Jeff Sheldon", "username": "ugmonk", "links": {"html": "https://unsplash.com/@ugmonk"}}}]}
COVERR = {"page": 0, "pages": 1, "page_size": 20, "total": 1, "hits": [
    {"id": "S1YbPl1NfI", "title": "Pouring Coffee", "poster": "https://storage.coverr.co/p/Q", "thumbnail": "https://storage.coverr.co/t/Q",
     "is_vertical": False, "tags": ["coffee"], "aspect_ratio": "16:9", "duration": 11.625, "max_height": 1152, "max_width": 2048,
     "urls": {"mp4": "https://storage.coverr.co/videos/Q?token=t", "mp4_preview": "https://storage.coverr.co/videos/Q/preview?token=t",
              "mp4_download": "https://storage.coverr.co/videos/Q/download?token=t&filename=Pouring Coffee"}}]}
FREESOUND = {"count": 2, "results": [
    {"id": 351256, "name": "Deep Whoosh #1", "tags": ["whoosh", "swoosh"], "license": "http://creativecommons.org/publicdomain/zero/1.0/",
     "username": "Kinoton", "duration": 3.155, "url": "https://freesound.org/people/Kinoton/sounds/351256/",
     "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/351/351256_2247456-hq.mp3", "preview-lq-mp3": "https://cdn.freesound.org/previews/351/351256_2247456-lq.mp3"},
     "images": {"waveform_m": "https://cdn.freesound.org/displays/351/351256_2247456_wave_M.png"}},
    {"id": 2, "name": "Swish", "tags": ["swish"], "license": "https://creativecommons.org/licenses/by/4.0/", "username": "u2", "duration": 1.0,
     "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/0/2-hq.mp3"}, "images": {}}]}
JAMENDO = {"headers": {"status": "success", "results_count": 2}, "results": [
    {"id": "1204669", "name": "Coffee Morning", "duration": 183, "artist_name": "Lofi Duo", "album_image": "https://usercontent.jamendo.com/a.jpg",
     "audio": "https://prod-1.storage.jamendo.com/?trackid=1204669&format=mp31", "audiodownload": "https://prod-1.storage.jamendo.com/download/track/1204669/mp32/",
     "audiodownload_allowed": True, "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/", "shareurl": "https://www.jamendo.com/track/1204669",
     "musicinfo": {"tags": {"genres": ["lounge"], "instruments": ["piano"], "vartags": ["calm"]}}},
    {"id": "77", "name": "NC tune", "duration": 120, "artist_name": "X", "audiodownload": "https://j/77", "audiodownload_allowed": True,
     "license_ccurl": "http://creativecommons.org/licenses/by-nc-nd/3.0/", "shareurl": "https://www.jamendo.com/track/77"}]}
OPENVERSE_IMG = {"result_count": 2, "results": [
    {"id": "0cca3a36", "title": "Cairo skyline in the morning", "foreign_landing_url": "https://www.flickr.com/photos/98833136@N00/73638843",
     "url": "https://live.staticflickr.com/35/73638843_d348e4c946_b.jpg", "creator": "StartAgain", "creator_url": "https://www.flickr.com/photos/98833136@N00",
     "license": "by-sa", "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by-sa/2.0/", "provider": "flickr", "source": "flickr",
     "filetype": None, "tags": [{"name": "cairo", "accuracy": None}], "attribution": "\"Cairo skyline in the morning\" by StartAgain is licensed under CC BY-SA 2.0.",
     "height": 768, "width": 1024, "thumbnail": "https://api.openverse.org/v1/images/0cca3a36/thumb/"},
    {"id": "b4431803", "title": "Historic Cairo skyline", "foreign_landing_url": "https://www.rawpixel.com/image/1", "url": "https://images.rawpixel.com/1.jpg",
     "creator": "", "license": "cc0", "license_version": "1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "rawpixel",
     "tags": [], "attribution": "\"Historic Cairo skyline\" is marked with CC0 1.0.", "height": 3232, "width": 3887, "thumbnail": "https://api.openverse.org/v1/images/b44/thumb/"}]}
OPENVERSE_AUDIO = {"results": [
    {"id": "eab8a6e2", "title": "Deep Whoosh #1", "foreign_landing_url": "https://freesound.org/people/Kinoton/sounds/351256",
     "url": "https://cdn.freesound.org/previews/351/351256_2247456-hq.mp3", "creator": "Kinoton", "license": "cc0", "license_version": "1.0",
     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "provider": "freesound", "source": "freesound", "filesize": 69196, "filetype": "mp3",
     "tags": [{"name": "whoosh", "accuracy": None}], "alt_files": [{"url": "https://freesound.org/apiv2/sounds/351256/download/", "filetype": "wav"}],
     "attribution": "\"Deep Whoosh #1\" by Kinoton is marked with CC0 1.0.", "duration": 3155, "thumbnail": None}]}
WIKIMEDIA = {"batchcomplete": True, "query": {"pages": [
    {"pageid": 61813797, "ns": 6, "title": "File:A man pours Arabic coffee.ogv", "index": 2, "videoinfo": [{
        "size": 1037297, "width": 640, "height": 640, "duration": 4.2042, "mime": "application/ogg",
        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/A.ogv/500px--A.ogv.jpg", "thumbwidth": 400, "thumbheight": 400,
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/22/A_man_pours_Arabic_coffee.ogv",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:A_man_pours_Arabic_coffee.ogv",
        "extmetadata": {"ObjectName": {"value": "A man pours Arabic coffee"}, "Artist": {"value": "<a href=\"//commons.wikimedia.org/wiki/User:S\">Sarah Canbel</a>"},
                        "LicenseShortName": {"value": "CC BY-SA 4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                        "AttributionRequired": {"value": "true"}},
        "derivatives": [{"src": "https://upload.wikimedia.org/x.ogv", "type": "video/ogg; codecs=\"theora\"", "width": 640, "height": 640},
                        {"src": "https://upload.wikimedia.org/t/A.ogv.480p.vp9.webm", "type": "video/webm; codecs=\"vp9, opus\"", "transcodekey": "480p.vp9.webm", "width": 480, "height": 480}]}]},
    {"pageid": 5, "ns": 6, "title": "File:Photo.jpg", "index": 1, "videoinfo": [{"mime": "image/jpeg", "width": 100, "height": 100,
                                                                                  "url": "https://u/x.jpg", "extmetadata": {}}]}]}}
WIKIMEDIA_PHOTO = {"query": {"pages": [{"pageid": 121354651, "title": "File:Cairo skyline, Nile River, Egypt.jpg", "index": 1, "videoinfo": [{
    "size": 4245497, "width": 3072, "height": 2048, "mime": "image/jpeg",
    "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/54/Cairo.jpg/500px-Cairo.jpg", "thumbwidth": 400,
    "url": "https://upload.wikimedia.org/wikipedia/commons/5/54/Cairo.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cairo.jpg",
    "extmetadata": {"ObjectName": {"value": "Cairo skyline, Nile River, Egypt"}, "Artist": {"value": "Vyacheslav Argenberg"},
                    "LicenseShortName": {"value": "CC BY 4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"}}}]}]}}
ARCHIVE_SEARCH = {"response": {"numFound": 1, "docs": [{"identifier": "PouringCoffeeInSlowMotionCCBYNatureClip", "title": "Pouring Coffee In Slow Motion",
                                                        "licenseurl": "http://creativecommons.org/licenses/by/3.0/", "creator": "NatureClip", "runtime": "00:00:53"}]}}
ARCHIVE_META = {"metadata": {"identifier": "PouringCoffeeInSlowMotionCCBYNatureClip"}, "files": [
    {"name": "Pouring.mov", "format": "QuickTime", "size": "54240950", "width": "1920", "height": "1080", "length": "53.7", "source": "original"},
    {"name": "Pouring.mp4", "format": "h.264", "size": "2593422", "width": "1280", "height": "720", "length": "53.7", "source": "derivative"},
    {"name": "__ia_thumb.jpg", "format": "Item Tile", "size": "13158"}]}
NASA_SEARCH = {"collection": {"items": [
    {"href": "https://images-assets.nasa.gov/video/KSC-1/collection.json",
     "data": [{"nasa_id": "KSC-1", "title": "TROPICS Rocket Launch", "center": "KSC", "media_type": "video", "photographer": "Rocket Lab", "keywords": ["Rocket"]}],
     "links": [{"href": "https://images-assets.nasa.gov/video/KSC-1/KSC-1~medium.jpg", "render": "image", "width": 400, "height": 225}]},
    {"href": "https://images-assets.nasa.gov/video/GSFC 2/collection.json",
     "data": [{"nasa_id": "GSFC 2", "title": "OSIRIS launch", "center": "GSFC", "media_type": "video"}],
     "links": [{"href": "http://images-assets.nasa.gov/video/GSFC 2/GSFC 2~thumb.jpg", "render": "image", "width": 1920, "height": 1080}]}]}}
NASA_COLL = ["http://images-assets.nasa.gov/video/KSC-1/KSC-1~orig.mp4", "http://images-assets.nasa.gov/video/KSC-1/KSC-1~large.mp4",
             "http://images-assets.nasa.gov/video/KSC-1/KSC-1~small.mp4", "http://images-assets.nasa.gov/video/KSC-1/KSC-1~thumb.jpg",
             "http://images-assets.nasa.gov/video/KSC-1/KSC-1.vtt"]
NASA_META = {"QuickTime:ImageWidth": 3840, "QuickTime:ImageHeight": 2160, "QuickTime:Duration": "0:00:30"}
ICONIFY = {"icons": ["mdi:coffee", "tabler:coffee", "nc-set:coffee"], "total": 3, "collections": {
    "mdi": {"name": "Material Design Icons", "author": {"name": "Pictogrammers"}, "license": {"title": "Apache 2.0", "spdx": "Apache-2.0", "url": "https://x/LICENSE"}, "height": 24, "palette": False},
    "tabler": {"name": "Tabler Icons", "author": {"name": "Paweł Kuna"}, "license": {"title": "MIT", "spdx": "MIT", "url": "https://y/LICENSE"}, "height": 24},
    "nc-set": {"name": "NC set", "author": {"name": "Z"}, "license": {"title": "CC BY-NC 4.0", "spdx": "CC-BY-NC-4.0"}, "height": 24}}}


def router(url, params=None, headers=None, **kw):
    u = url
    table = [("api.pexels.com/videos", PEXELS_VIDEOS), ("api.pexels.com/v1", PEXELS_PHOTOS), ("pixabay.com/api/videos", PIXABAY_VIDEOS),
             ("pixabay.com/api", PIXABAY_IMAGES), ("api.unsplash.com", UNSPLASH), ("api.coverr.co", COVERR), ("freesound.org/apiv2", FREESOUND),
             ("api.jamendo.com", JAMENDO), ("openverse.org/v1/images", OPENVERSE_IMG), ("openverse.org/v1/audio", OPENVERSE_AUDIO),
             ("archive.org/advancedsearch", ARCHIVE_SEARCH), ("archive.org/metadata", ARCHIVE_META), ("images-api.nasa.gov", NASA_SEARCH),
             ("collection.json", NASA_COLL), ("metadata.json", NASA_META), ("api.iconify.design/search", ICONIFY)]
    if "commons.wikimedia.org" in u:
        gs = (params or {}).get("gsrsearch", "")
        return WIKIMEDIA_PHOTO if "bitmap" in gs else WIKIMEDIA
    for k, v in table:
        if k in u:
            return json.loads(json.dumps(v))
    raise AssertionError(f"unmocked url {u}")


@pytest.fixture
def mocked(monkeypatch, tmp_path):
    """All provider HTTP mocked; downloads write real tiny media; thumbnails are local images."""
    monkeypatch.setattr(P, "get_json", router)
    pings = []
    monkeypatch.setattr(P, "request", lambda url, **kw: pings.append(url) or b"{}")
    thumb = tmp_path / "thumb.jpg"
    Image.new("RGB", (320, 180), (180, 120, 60)).save(thumb)
    monkeypatch.setattr(_sheet, "cached_file", lambda url, ext=".jpg", **kw: thumb)

    def fake_download(url, dest, **kw):
        dest = Path(dest)
        ext = dest.suffix.lower()
        if ext in (".jpg", ".png", ".webp"):
            Image.new("RGB", (640, 427), (40, 90, 160)).save(dest, "JPEG" if ext == ".jpg" else None)
        elif ext == ".svg":
            dest.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 21h18v-2H2z"/></svg>')
        elif ext in (".mp4", ".mov", ".webm", ".ogv"):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=25", "-pix_fmt", "yuv420p", str(dest.with_suffix(".mp4"))], check=True)
            if dest.suffix != ".mp4":
                dest.with_suffix(".mp4").rename(dest)
        else:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", str(dest.with_suffix(".mp3"))], check=True)
            if dest.suffix != ".mp3":
                dest.with_suffix(".mp3").rename(dest)
        return dest
    monkeypatch.setattr(T, "download", fake_download)
    return {"pings": pings}


def all_keys(monkeypatch):
    for n in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "UNSPLASH_ACCESS_KEY", "COVERR_API_KEY", "FREESOUND_API_KEY", "JAMENDO_CLIENT_ID"):
        monkeypatch.setenv(n, "test-" + n.lower())


needs_ff = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")


# ───────────────────────────── keys ─────────────────────────────

def test_keys_env_first_then_file(home, monkeypatch):
    f = K.keys_file()
    f.write_text(json.dumps({"PEXELS_API_KEY": "from-file", "PIXABAY_API_KEY": ""}))
    assert K.get_key("PEXELS_API_KEY") == "from-file"
    monkeypatch.setenv("PEXELS_API_KEY", "from-env")
    assert K.get_key("PEXELS_API_KEY") == "from-env"
    assert K.get_key("PIXABAY_API_KEY") == ""
    st = K.key_status(["PEXELS_API_KEY", "PIXABAY_API_KEY"])
    assert st == {"PEXELS_API_KEY": "set (env)", "PIXABAY_API_KEY": "unset"}
    assert "from-env" not in json.dumps(st)


def test_keys_template_never_overwrites(home):
    f = K.keys_file()
    f.write_text(json.dumps({"PEXELS_API_KEY": "keep-me"}))
    path, added = K.ensure_keys_template(["PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY"])
    data = json.loads(path.read_text())
    assert data["PEXELS_API_KEY"] == "keep-me" and data["UNSPLASH_ACCESS_KEY"] == ""
    assert added == ["UNSPLASH_ACCESS_KEY"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    f.write_text("{ broken")
    K.ensure_keys_template(["X"])
    assert f.read_text() == "{ broken"


def test_sources_lists_and_hides_values(home, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "super-secret-value")
    r = call("stock_sources", {})
    txt = r.to_text()
    assert "super-secret-value" not in txt
    assert "Pexels" in txt and "PIXABAY_API_KEY" in txt and "pixabay.com/api/docs" in txt
    rows = {p["provider"]: p for p in r.data["providers"]}
    assert rows["pexels"]["active"] and not rows["unsplash"]["active"] and rows["openverse"]["active"]
    assert json.loads(K.keys_file().read_text())["JAMENDO_CLIENT_ID"] == ""


# ───────────────────────────── licences ─────────────────────────────

@pytest.mark.parametrize("kw,lic,com,attr,sa", [
    ({"code": "cc0"}, "CC0", True, False, False),
    ({"code": "by-sa", "version": "2.0"}, "CC BY-SA 2.0", True, True, True),
    ({"code": "CC BY-SA 4.0"}, "CC BY-SA 4.0", True, True, True),
    ({"url": "http://creativecommons.org/licenses/by-nc/3.0/"}, "CC BY-NC 3.0", False, True, False),
    ({"url": "http://creativecommons.org/publicdomain/zero/1.0/"}, "CC0", True, False, False),
    ({"url": "http://creativecommons.org/publicdomain/mark/1.0/"}, "Public Domain Mark", True, False, False),
    ({"code": "Attribution NonCommercial"}, "CC BY-NC 4.0", False, True, False),
    ({"code": "Public domain"}, "Public Domain", True, False, False),
])
def test_cc_parsing(kw, lic, com, attr, sa):
    d = L.cc(**kw)
    assert (d["license"], d["commercial_ok"], d["attribution_required"], d["share_alike"]) == (lic, com, attr, sa)


def test_unknown_licence_is_unsafe():
    d = L.cc("gfdl")
    assert not d["commercial_ok"] and d["attribution_required"]
    assert L.cc(url="http://creativecommons.org/licenses/by-nd/4.0/")["modify_ok"] is False


# ───────────────────────────── adapters ─────────────────────────────

REQUIRED = {"id", "provider", "kind", "title", "page_url", "thumb", "width", "height", "duration", "license", "license_url",
            "commercial_ok", "attribution_required", "attribution", "creator", "tags", "files"}


def check(it, provider, kind):
    assert REQUIRED <= set(it), REQUIRED - set(it)
    assert it["id"].startswith(provider + ":") and it["kind"] == kind and it["provider"] == provider
    assert it["attribution"] and it["license"]
    return it


def test_pexels(mocked, monkeypatch):
    all_keys(monkeypatch)
    v = P.PROVIDERS["pexels"].search("coffee", "video", P.Opts(count=2))
    a = check(v[0], "pexels", "video")
    assert (a["width"], a["height"], a["duration"], a["fps"]) == (3840, 2160, 14, 25)
    assert len(a["files"]) == 3 and "Video by Ana Lee on Pexels" in a["attribution"] and a["title"] == "pouring coffee"
    p = check(P.PROVIDERS["pexels"].search("coffee", "photo", P.Opts())[0], "pexels", "photo")
    assert "{w}" in p["extra"]["resize"] and p["files"][0]["width"] == 6000
    assert P.PROVIDERS["pexels"].auth_headers() == {"Authorization": "test-pexels_api_key"}


def test_pixabay(mocked, monkeypatch):
    all_keys(monkeypatch)
    px = P.PROVIDERS["pixabay"]
    im = check(px.search("tree", "photo", P.Opts())[0], "pixabay", "photo")
    assert im["files"][0]["width"] == 1280 and im["files"][0]["height"] == 720 and im["license"] == "Pixabay Content License"
    vec = px.search("tree", "vector", P.Opts())[0]
    assert vec["kind"] == "vector" and any("raster preview" in n for n in vec["notes"])
    v = check(px.search("flowers", "video", P.Opts())[0], "pixabay", "video")
    assert (v["width"], v["height"], v["thumb"]) == (1920, 1080, "https://cdn.pixabay.com/v/l.jpg")


def test_unsplash_and_download_ping(mocked, monkeypatch):
    all_keys(monkeypatch)
    u = P.PROVIDERS["unsplash"]
    it = check(u.search("coffee", "photo", P.Opts())[0], "unsplash", "photo")
    assert it["attribution_required"] and "Photo by Jeff Sheldon" in it["attribution"] and "utm_source" in it["attribution"]
    assert u.on_download(it) == "" and mocked["pings"] == ["https://api.unsplash.com/photos/eOLpJytrbsQ/download?ixid=x"]


def test_coverr(mocked, monkeypatch):
    all_keys(monkeypatch)
    it = check(P.PROVIDERS["coverr"].search("coffee", "video", P.Opts())[0], "coverr", "video")
    assert it["files"][0]["quality"] == "download" and (it["width"], it["height"]) == (2048, 1152)
    assert P.PROVIDERS["coverr"].auth_headers()["Authorization"].startswith("Bearer ")


def test_freesound_and_jamendo(mocked, monkeypatch):
    all_keys(monkeypatch)
    fs = P.PROVIDERS["freesound"].search("whoosh", "sfx", P.Opts())
    a = check(fs[0], "freesound", "sfx")
    assert a["license"] == "CC0" and a["files"][0]["quality"] == "hq-mp3" and "_wave_M" in a["thumb"]
    assert fs[1]["license"] == "CC BY 4.0" and fs[1]["attribution_required"]
    jm = P.PROVIDERS["jamendo"].search("coffee", "music", P.Opts())
    j = check(jm[0], "jamendo", "music")
    assert j["license"] == "CC BY-SA 3.0" and j["share_alike"] and "piano" in j["tags"]
    assert jm[1]["commercial_ok"] is False


def test_openverse(mocked):
    ov = P.PROVIDERS["openverse"]
    im = ov.search("cairo skyline", "photo", P.Opts())
    a = check(im[0], "openverse", "photo")
    assert a["source"] == "openverse/flickr" and a["thumb"].endswith("_n.jpg")   # Flickr thumb spares API quota
    au = check(ov.search("whoosh", "sfx", P.Opts())[0], "openverse", "sfx")
    assert au["duration"] == 3.16 or abs(au["duration"] - 3.155) < 0.01
    assert all("apiv2" not in f["url"] for f in au["files"])   # Freesound originals need OAuth: skipped


def test_wikimedia(mocked):
    vids = P.PROVIDERS["wikimedia"].search("coffee", "video", P.Opts())
    assert len(vids) == 1   # the JPEG page is not a video
    v = check(vids[0], "wikimedia", "video")
    assert v["license"] == "CC BY-SA 4.0" and v["creator"] == "Sarah Canbel" and v["share_alike"]
    assert any(f["quality"] == "480p.vp9.webm" for f in v["files"])
    ph = check(P.PROVIDERS["wikimedia"].search("cairo", "photo", P.Opts())[0], "wikimedia", "photo")
    assert ph["extra"]["resize"].endswith("/{w}px-Cairo.jpg")


def test_archive_resolve(mocked):
    ia = P.PROVIDERS["archive"]
    it = check(ia.search("coffee", "video", P.Opts())[0], "archive", "video")
    assert it["duration"] == 53 and it["files"] == []
    it = ia.resolve(it)
    assert len(it["files"]) == 2 and (it["width"], it["height"]) == (1920, 1080)


def test_nasa_resolve_and_third_party_flag(mocked):
    n = P.PROVIDERS["nasa"]
    items = n.search("rocket", "video", P.Opts())
    a = check(items[0], "nasa", "video")
    assert a["attribution_required"] and any("Rocket Lab" in x for x in a["notes"])   # non-NASA credit flagged
    assert items[1]["thumb"].startswith("https://")
    a = n.resolve(a)
    q = [f["quality"] for f in a["files"]]
    assert q == ["orig", "large", "small"] and (a["width"], a["height"]) == (3840, 2160) and a["duration"] == 30
    assert a["files"][1]["width"] == 1920 and a["files"][1]["estimated"]


def test_iconify_licences(mocked):
    ic = P.PROVIDERS["iconify"]
    icons = ic.search("coffee", "icon", P.Opts(count=10, commercial=True))
    assert [i["id"] for i in icons] == ["iconify:mdi:coffee", "iconify:tabler:coffee"]   # NC set dropped
    assert icons[0]["license"] == "Apache 2.0" and icons[0]["files"][0]["url"].endswith("/mdi/coffee.svg")
    nc = ic.icon_item("nc-set", "coffee", ICONIFY["collections"]["nc-set"])
    assert nc["commercial_ok"] is False and nc["attribution_required"]


def test_static_svg_settles_animation():
    s = '<svg><path stroke-dasharray="48" stroke-dashoffset="48" d="M1"><animate attributeName="stroke-dashoffset" values="48;0"/></path><g fill-opacity="0"/></svg>'
    out = P.static_svg(s)
    assert "animate" not in out and "dashoffset" not in out and 'fill-opacity="0"' not in out


# ───────────────────────────── ranking / ids ─────────────────────────────

def mk(pid, w, h, dur=10, lic=None, title="coffee pour", provider="pexels", kind="video"):
    return P.make_item(provider, pid, kind, lic or L.fixed("Pexels License", "u"), title=title, width=w, height=h, duration=dur,
                       creator="c" + str(pid), files=[{"url": f"https://x/{provider}/{pid}.mp4", "width": w, "height": h}])


def test_ranking_orientation_duration_resolution():
    o = P.Opts(orientation="portrait", max_duration=30)
    q = T._words("coffee pour")
    port, land = mk(1, 1080, 1920), mk(2, 3840, 2160)
    assert T._score(port, 1, "video", o, q) > T._score(land, 0, "video", o, q)
    assert T._score(mk(3, 1080, 1920, dur=45), 0, "video", o, q) is None
    hi, lo = T._score(mk(4, 1920, 1080), 0, "video", P.Opts(), q), T._score(mk(5, 426, 240), 0, "video", P.Opts(), q)
    assert hi > lo
    assert T._score(mk(6, 1000, 600), 0, "photo", P.Opts(min_width=1920), q) is None
    rel, irr = mk(7, 1920, 1080, title="Pouring coffee"), mk(8, 1920, 1080, title="Cuban prisoners")
    assert T._score(rel, 3, "video", P.Opts(), q) > T._score(irr, 3, "video", P.Opts(), q)


def test_search_end_to_end_mocked(mocked, monkeypatch):
    all_keys(monkeypatch)
    r = call("stock_search", {"query": "coffee pouring", "kind": "video", "count": 6, "project": "t"})
    ids = [x["id"] for x in r.data["results"]]
    assert ids[0].startswith(("pexels:", "coverr:", "pixabay:")) and len(ids) == 6 and len(set(ids)) == 6
    assert Path(r.previews[0]).exists() and Image.open(r.previews[0]).width >= 800
    assert set(r.data["providers"]) == {"pexels", "coverr", "pixabay", "wikimedia", "nasa", "archive"}
    assert T.lookup(ids[0])["id"] == ids[0] and T.lookup("1")["id"] == ids[0] and T.lookup("#2")["id"] == ids[1]


def test_search_keyless_only_and_commercial_filter(mocked):
    r = call("stock_search", {"query": "coffee", "kind": "music", "project": "t"})
    assert all(x["provider"] in ("openverse", "archive", "wikimedia") for x in r.data["results"])
    assert any("no key yet" in w for w in r.warnings)


def test_search_noncommercial_hidden(mocked, monkeypatch):
    all_keys(monkeypatch)
    r = call("stock_search", {"query": "coffee", "kind": "music", "providers": "jamendo", "project": "t"})
    assert [x["id"] for x in r.data["results"]] == ["jamendo:1204669"]
    r2 = call("stock_search", {"query": "coffee", "kind": "music", "providers": "jamendo", "commercial": False, "project": "t"})
    assert len(r2.data["results"]) == 2


def test_arabic_query_warns(mocked):
    r = call("stock_search", {"query": "قهوة", "kind": "photo", "providers": "openverse", "project": "t"})
    assert any("Arabic" in w for w in r.warnings)


def test_bad_args():
    with pytest.raises(ToolError):
        call("stock_search", {"query": "x", "kind": "hologram"})
    with pytest.raises(ToolError):
        call("stock_search", {"query": "x", "orientation": "diagonal"})
    with pytest.raises(ToolError):
        call("stock_download", {"ids": ["nope:1"]})


def test_choose_files_quality():
    it = P.PROVIDERS["pexels"]._video(PEXELS_VIDEOS["videos"][0])
    assert T.choose_files(it, "best", 0)[0]["width"] == 3840
    assert T.choose_files(it, "hd", 0)[0]["width"] == 1920
    assert T.choose_files(it, "sd", 0)[0]["width"] == 960
    assert T.choose_files(it, "best", 1280)[0]["width"] == 960
    ph = P.PROVIDERS["pexels"]._photo(PEXELS_PHOTOS["photos"][0])
    assert T.choose_files(ph, "hd", 0)[0]["url"].endswith("w=1920")
    wm = P.PROVIDERS["wikimedia"]._item(WIKIMEDIA_PHOTO["query"]["pages"][0], "photo")
    assert "/1280px-" in T.choose_files(wm, "best", 1500)[0]["url"]   # snapped to a standard thumb step


# ───────────────────────────── download, sidecars, credits ─────────────────────────────

@needs_ff
def test_download_sidecar_ledger_credits(mocked, monkeypatch):
    all_keys(monkeypatch)
    call("stock_search", {"query": "coffee", "kind": "photo", "providers": "unsplash,openverse", "project": "p1"})
    r = call("stock_search", {"query": "coffee", "kind": "video", "providers": "pexels,wikimedia", "project": "p1"})
    vid = [x["id"] for x in r.data["results"] if x["provider"] == "wikimedia"][0]
    d = call("stock_download", {"ids": [vid, "unsplash:eOLpJytrbsQ", "1"], "quality": "hd", "project": "p1"})
    assert len(d.data["downloaded"]) == 3, d.warnings
    for rec in d.data["downloaded"]:
        f, side = Path(rec["file"]), Path(rec["license_file"])
        assert f.exists() and side.name == f.name + ".license.json"
        meta = json.loads(side.read_text())
        assert meta["license"] and meta["attribution"] and meta["downloaded_at"] and meta["source_file_url"]
        assert f.parent.parent.name == "stock"
    assert mocked["pings"], "Unsplash download_location must be pinged"
    assert Path(d.previews[0]).exists()
    led = json.loads(T.ledger_path("p1").read_text())
    assert len(led["items"]) == 3
    c = call("stock_credits", {"project": "p1"})
    md, txt = Path(c.files[0]).read_text(), Path(c.files[1]).read_text()
    assert "Required attributions" in md and "Sarah Canbel" in txt and "Jeff Sheldon" in txt
    assert any("share-alike" in w for w in c.warnings)


@needs_ff
def test_noncommercial_refused(mocked, monkeypatch):
    all_keys(monkeypatch)
    T._save_items([P.PROVIDERS["jamendo"]._track(JAMENDO["results"][1])], {"commercial": True})
    with pytest.raises(ToolError, match="NOT allow commercial"):
        call("stock_download", {"ids": ["jamendo:77"], "project": "p2"})
    d = call("stock_download", {"ids": ["jamendo:77"], "allow_noncommercial": True, "project": "p2"})
    assert d.data["downloaded"] and any("NON-COMMERCIAL" in w for w in d.warnings)


def test_redaction():
    u = "https://pixabay.com/api/?key=SECRET&q=x&token=T2&client_id=C3"
    r = _http.redact(u)
    assert "SECRET" not in r and "T2" not in r and "C3" not in r and "q=x" in r


# ───────────────────────────── live (slow) ─────────────────────────────

live = pytest.mark.slow


def _live_one(query, kind, providers, quality="sd"):
    r = call("stock_search", {"query": query, "kind": kind, "providers": providers, "count": 4, "project": "live"})
    if not r.data.get("results"):
        if any("429" in w or "rate" in w.lower() for w in r.warnings):
            pytest.skip(f"{providers} rate-limited: {r.warnings}")
        pytest.fail(f"no results from {providers}: {r.warnings}")
    assert Path(r.previews[0]).exists()
    d = call("stock_download", {"ids": [r.data["results"][0]["id"]], "quality": quality, "max_width": 1280, "project": "live"})
    rec = d.data["downloaded"][0]
    assert Path(rec["file"]).stat().st_size > 1000 and Path(rec["license_file"]).exists()
    return rec


@live
@needs_ff
def test_live_openverse_sfx():
    rec = _live_one("whoosh", "sfx", "openverse")
    assert rec.get("duration")


@live
def test_live_nasa_photo():
    rec = _live_one("nebula", "photo", "nasa")
    assert rec["size"]


@live
@needs_ff
def test_live_archive_audio():
    _live_one("thunder", "sfx", "archive")


@live
def test_live_wikimedia_photo():
    _live_one("cairo skyline", "photo", "wikimedia")


@live
def test_live_iconify():
    r = call("stock_icon", {"query": "mdi:coffee", "color": "#C8102E", "size": 128, "project": "live"})
    svg = Path(r.data["icons"][0]["svg"])
    assert svg.read_text().lstrip().startswith("<svg") and "#C8102E" in svg.read_text()


@live
@pytest.mark.parametrize("prov,kind,key", [("pexels", "video", "PEXELS_API_KEY"), ("pixabay", "photo", "PIXABAY_API_KEY"),
                                           ("unsplash", "photo", "UNSPLASH_ACCESS_KEY"), ("coverr", "video", "COVERR_API_KEY"),
                                           ("freesound", "sfx", "FREESOUND_API_KEY"), ("jamendo", "music", "JAMENDO_CLIENT_ID")])
def test_live_keyed(prov, kind, key, monkeypatch):
    v = ORIG_ENV.get(key) or ""
    if not v:
        try:
            v = json.loads((Path(os.path.expanduser("~")) / "AI-Projects/creative-studio/keys.json").read_text()).get(key, "")
        except Exception:
            v = ""
    if not v:
        pytest.skip(f"{key} not set")
    monkeypatch.setenv(key, v)
    _live_one("coffee", kind, prov)
