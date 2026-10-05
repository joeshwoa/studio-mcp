# Stock department

The stock tools find and download free stock media (video, photos, illustrations, vectors, music, sound effects, icons) from every free stock site that has an official API. Licence and attribution travel with every file automatically, so the video and motion teams can build new videos from stock and still credit it properly.

| Tool | What it does |
|---|---|
| `stock_sources()` | Shows which providers are active, what each one gives, the licence and attribution rules, rate limits, and the steps to get each free key. It also creates `STUDIO_HOME/keys.json` with empty entries so you only have to paste keys in. It never shows key values. |
| `stock_search(query, kind, orientation, count, min_duration, max_duration, min_width, commercial, providers, page, project)` | Searches all providers for that kind in parallel, normalises the results, removes duplicates and ranks them. Returns `data.results` plus a **numbered contact sheet**: thumbnails, provider, size, fps, duration and a licence badge (green = free with no credit, amber = credit required, red = not for commercial use). Audio tiles show the real waveform of the preview. |
| `stock_download(ids, quality, max_width, allow_noncommercial, project)` | Takes ids from a search (`"pexels:123"`, or `"3"` for result 3 of the last search). Saves to `projects/<p>/stock/<kind>/<provider>-<title>.<ext>` with a `<file>.license.json` sidecar and adds an entry to `stock_credits.json`. Every file is checked with ffprobe or Pillow and shown on a contact sheet. `quality`: best \| 4k \| hd \| sd. |
| `stock_credits(project)` | Builds `CREDITS.md` and `credits.txt` from the ledger. `credits.txt` is ready to paste into a video description. Both are regenerated (overwritten) on every call. |
| `stock_icon(query \| "prefix:name", color, size, count, project)` | Downloads Iconify icons as SVG plus a transparent PNG, with the icon set's licence. A word search also returns a sheet of 24 alternatives. |

`kind`: `video` · `photo` · `illustration` · `vector` · `music` · `sfx` · `icon` · `any`.
`commercial=true` (the default) keeps only licences that allow **commercial use and edits**. Videos are edits, so this also drops ND licences.
**Write queries in English.** Arabic queries are still sent, with a warning: most APIs index only English, though Openverse and Wikimedia Commons match some Arabic.

## Providers (official APIs only)

| Provider | Gives | Key name (`keys.json` / env) | Licence | Attribution | Rate limit |
|---|---|---|---|---|---|
| Pexels | video, photo | `PEXELS_API_KEY` | Pexels License: commercial OK | Not required. The API terms ask for "Video by NAME on Pexels". | 200/h, 20k/month |
| Pixabay | photo, illustration, vector*, video | `PIXABAY_API_KEY` | Pixabay Content License: commercial OK | Not required | 100/60 s. Responses **must be cached 24 h** (this is done). No hotlinking, so files are downloaded. |
| Unsplash | photo | `UNSPLASH_ACCESS_KEY` | Unsplash License: commercial OK | **Required by the API guidelines**: "Photo by NAME on Unsplash" with links. The download ping is sent automatically. | 50/h (demo) |
| Coverr | video | `COVERR_API_KEY` | Coverr License: commercial OK | Not required | ~1000 calls/month in dev. Downloads go through `mp4_download`, which registers the download as Coverr requires. |
| Freesound | sfx, music loops | `FREESOUND_API_KEY` | Per sound: CC0 / CC BY / **CC BY-NC (not commercial)** / Sampling+ | Required for CC BY | 60/min, 2000/day |
| Jamendo | music | `JAMENDO_CLIENT_ID` | Per track CC. Most are BY-NC, which `commercial=true` hides. | Required | 35k/month. The free API is for non-commercial apps. |
| Openverse | photo, illustration, vector (SVG), music, sfx | none | Per item CC0 / PDM / CC BY(-SA) | Ready-made text per item | Anonymous: ~20/min, a few hundred/day (cached 24 h) |
| Wikimedia Commons | video (WebM/OGV), photo, SVG, audio | none | Per file: PD / CC0 / CC BY / **CC BY-SA** / GFDL | Author + licence + link | Shared and strict: requests are paced at 1/s with a descriptive User-Agent and cached. Bursts can still get HTTP 429, which shows up as a warning. |
| Internet Archive | video, music, sfx | none | Only items whose `licenseurl` is PD/CC0/CC BY/BY-SA (commercial) | Creator + licence + link | Gentle pacing |
| NASA Image & Video Library | photo, video, audio | none | Generally public domain. **No NASA insignia or logo use, no implied endorsement, and identifiable people need consent for commercial use.** Items credited to a non-NASA photographer or partner are flagged, because they may be copyrighted. | "Courtesy NASA/CENTER" requested | generous |
| Iconify | icons (200k+, 150+ sets) | none | Per icon set: MIT / Apache-2.0 / ISC / CC BY / OFL / GPL | Only CC BY sets need visible credit | public |

\* Pixabay "vector" results download as raster previews (≤1280 px). The .svg/.ai source needs Pixabay full API access. For real SVGs, use Openverse or Wikimedia (`kind="vector"`).

**No official API, so browse these by hand** and pass the file to the video tools: Mixkit, Videvo, Pixabay Music/SFX pages, Uppbeat, YouTube Audio Library.

## Getting the free keys (about 2 minutes each)

Run `stock_sources()` first. It creates `STUDIO_HOME/keys.json` (chmod 600) with empty values. Paste each key between its quotes; environment variables with the same names take priority over the file.

- **Pexels**: create an account at pexels.com/join, open https://www.pexels.com/api/new/, fill in the short form, then copy "Your API Key" into `PEXELS_API_KEY`.
- **Pixabay**: register at pixabay.com, then open https://pixabay.com/api/docs/ while logged in. Your key appears in the `key (required)` row; put it in `PIXABAY_API_KEY`.
- **Unsplash**: create an account, go to https://unsplash.com/oauth/applications and choose New Application, then copy the **Access Key** (not the Secret) into `UNSPLASH_ACCESS_KEY`.
- **Coverr**: go to https://coverr.co/developers and choose Get API Key, then create an app and copy the key into `COVERR_API_KEY`. You can also ask team@coverr.co for a key.
- **Freesound**: create an account, open https://freesound.org/apiv2/apply and create a credential, then copy the long API key into `FREESOUND_API_KEY`.
- **Jamendo**: sign up at https://devportal.jamendo.com, create an app, then copy the **Client ID** into `JAMENDO_CLIENT_ID`.

## What gets written

```
projects/<p>/stock/
  video/pexels-pouring-coffee.mp4
  video/pexels-pouring-coffee.mp4.license.json   # licence, attribution, source URLs, measured size, date
  photo/… sfx/… music/… icon/…
  stock_credits.json                              # the ledger (one entry per item)
  CREDITS.md  credits.txt                          # from stock_credits
  _previews/search-*.png  downloaded*.png          # contact sheets to LOOK at
```

Search results are remembered in `STUDIO_HOME/cache/stock/items.json`, so ids resolve without searching again. API responses are cached for 24 h in `cache/stock/api/`.

## Honest limits

- **Without keys**, video comes from Wikimedia, NASA and Internet Archive only. That works but gives less polished b-roll than Pexels, Coverr or Pixabay. Adding the free Pexels and Pixabay keys is the single biggest upgrade.
- **The Coverr adapter has not been tested against the live API**: no key was available while it was built. It follows https://api.coverr.co/docs (Bearer auth, `hits[]`, `urls.mp4_download`). The other keyed adapters were also never called live here, because no keys were available while building. Their code follows each provider's API docs, and their tests run only against mocked responses copied from those docs.
- **Freesound** downloads are the HQ MP3 previews (~128 kbps). Originals need an OAuth login, which these tools don't do.
- **Internet Archive** licences are set by the uploader, so check the item page before commercial use. When an item contains several audio tracks, only the first one is downloaded.
- **NASA** video sizes are only known at download time. Renditions other than the original are estimated, then measured with ffprobe.
- **Wikimedia** videos are WebM/OGV (VP9/Theora). The download warns when the codec is one a desktop editor might reject; the ffmpeg-based studio tools handle them fine.
- **Pixabay and Coverr** file URLs can expire. If a download fails, run `stock_search` again.
- **Ranking** is a heuristic: provider order, title/tag keyword match, resolution, orientation, duration fit, and whether credit is needed. Always look at the contact sheet.
- **Licences**: the tools carry exactly what each provider reports. Anything unrecognised is treated as unsafe (non-commercial, credit required). **CC BY-SA** items are flagged, because a video that adapts them must use the same licence.
