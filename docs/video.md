# Video department (Premiere replacement)

Code: `src/studio/depts/video/` (`_common` helpers/presets · `timeline` JSON edit → ffmpeg graph · `render`
orchestration · `kdenlive` MLT/Kdenlive writer + melt check · `captions` libass · `reframe` face tracking ·
tools in `edit`, `social`, `finishing`). Tests: `tests/test_video.py` (media synthesised with ffmpeg;
`-m slow` = cross-department renders: motion titles, Whisper, audio_mix, vid.stab, reframe).

Engines: **ffmpeg** renders everything; every multi-clip result is **also** written as an editable
**`.kdenlive`** project (MLT XML, Kdenlive document 1.1) that is **render-checked headless with `melt`**
(duration + per-frame similarity vs the ffmpeg render, preview `*-kdenlive-check.png`). Outputs go to
`STUDIO_HOME/projects/<project>/video/`, never overwrite, and are measured (ffprobe, EBU R128 loudness,
black-frame %) with a timestamped contact sheet in `previews` — look at it before presenting.

| Tool | What it does |
|---|---|
| `video_edit` | **The editor.** JSON timeline → MP4 + `.kdenlive` (+ `.srt`/`.ass` when captioned). Clips (video in/out/speed, stills with Ken Burns, colour cards, full-frame motion title cards), transitions (dissolve, dip, dip_white, wipe_*, slide_*, push, zoom, iris, radial, blur, pixelize… = ffmpeg xfade), **J/L cuts**, per-clip grade/fit/zoom, overlays (motion lower thirds/titles/CTAs/counters rendered by the motion dept, shape wipes, alpha videos, logos, B-roll, PiP), audio tracks with **auto-ducking** (audio_mix), auto-captions, programme grade, fades, loudness. `video_timeline_example` prints ready-to-edit examples. |
| `video_probe` | Duration/size/fps/codecs/rotation/alpha/audio + measured loudness + contact sheet. |
| `video_trim` / `video_split` / `video_concat` | Frame-accurate cut (or keyframe stream-copy) · split at times / every N s / **detected scene cuts** · join any mix of clips (conformed) with a transition + audio crossfade. |
| `video_captions` | Auto-captions via `audio_transcribe` (word timestamps) or a given SRT/words → burned styled captions: `reels` (big words, accent **box behind the spoken word**, pop-in), `bold`, `karaoke` (\kf fill), `clean`, `boxed` + a sentence-level `.srt` to upload + the `.ass`. Arabic: separate Arabic face, shaped by libass, correct right-to-left word order (see notes). |
| `video_remove_silence` | Dead-air/jump-cut edit from `audio_trim_silence` speech segments, click-free, optional alternating punch-in (`jump_zoom`). Every cut stays editable in the `.kdenlive`. |
| `video_reframe` | 16:9 → 9:16 / 1:1 / 4:5 following faces (YuNet from the photo dept; saliency when no face) with a virtual camera operator (dead zone, eased moves, re-frame at scene cuts). Tracking plot preview; `.kdenlive` gets the same moves as Transform keyframes. Modes `track`, `center`, `blur`. |
| `video_grade` | Looks identical to `photo_look` (via its `.cube` export, cached) or any `.cube` LUT, strength blend, exposure/contrast/saturation/temperature/vibrance/vignette. Before/after sheet. |
| `video_stabilize` | Two-pass vid.stab (fallback `deshake`), tripod mode; reports measured frame-to-frame motion before/after. |
| `video_speed` | Constant speed or eased **speed ramps** (pitch-preserved audio 0.5–2×, muted beyond). |
| `video_music` | Music bed with ducking under speech + mastering; picture untouched. |
| `video_loudness` | Platform loudness (−14 YouTube/Reels/TikTok, −16 podcast, −23 broadcast…), true-peak limited, video stream-copied. |
| `video_export` | Delivery presets: `reels`, `tiktok`, `shorts`, `youtube`, `youtube_4k`, `linkedin`, `linkedin_square`, `instagram_feed`, `x`, `whatsapp`, `prores`, `prores4444`, `gif`, `webm`, `master` — size (smart reframe when the aspect differs), fps, H.264 high + bitrate cap, AAC 48 k, faststart, loudness, platform length warnings. |
| `video_broll` / `video_pip` / `video_overlay` | Cutaways over continuous A-roll audio · picture-in-picture with rounded corners, border, shadow · any alpha graphic/PNG/logo at a time and position. |
| `video_thumbnail` | Scores ~60 frames (sharpness, exposure, colourfulness, face size), keeps the best spread-out ones; with `title=` designs a finished YouTube thumbnail via `design_create` (brand-aware). |
| `video_render_project` | Render any `.kdenlive`/`.mlt` with melt (after the user edits by hand). |

## Timeline JSON (video_edit)
See the docstring of `studio.depts.video.timeline` or run `studio video_timeline_example '{"kind":"full"}'`.
Transition durations overlap the previous clip (Premiere behaviour); `j_cut`/`l_cut` extend the clip's
**audio** before/after its picture and are crossfaded; transitions crossfade audio too. Overlay `type`s
`title`, `lower_third`, `cta`, `kinetic`, `counter`, `infographic`, `logo`, `countdown`, `shape_wipe` and
`motion` (any `motion_*` tool + `args`) are rendered by the motion department at the timeline size as
ProRes 4444 alpha and kept in `<name>-assets/` next to the MP4 (the `.kdenlive` references them).

## Kdenlive projects — what carries over
Written the way Kdenlive ≥ 20.12 stores them: bin producers with `kdenlive:id`, `main_bin`, one tractor
per track with two playlists, transitions as same-track **mixes** (luma), internal qtblend/mix track
compositing, effects inside timeline entries (LUT = `avfilter.lut3d`, Ken Burns / punch-in / reframe =
`qtblend` rect keyframes, fades = `brightness`/`volume`), speed via `timewarp` producers, captions as an
`avfilter.subtitles` filter on the main tractor, music ducking as `volume` level keyframes. Honest limits
(reported in warnings per edit): non-dissolve transitions become dissolve mixes; overlay fades, PiP rounded
corners/border and grade strength/manual adjustments exist only in the MP4; audio levels are approximate
(the MP4 has the mastered mix). **Verified here:** every project renders with melt 7.22 and matches the
ffmpeg render (typical mean frame difference 15–20/255 at 384 px — encode + scaling differences; titles,
lower thirds, captions, grade and Ken Burns all present). **Not verified here:** opening in the Kdenlive
GUI (not installed in this container) — try `open_in_app` on the Mac.

## Captions notes
- Arabic: libass lays out *style-separated* runs left→right even in RTL lines, which scrambles word order
  when one word is highlighted. Fix used: for Arabic chunks every word is its own run (invisible 1/255
  alpha difference) written in visual order. Checked visually for reels/bold/karaoke (karaoke-Arabic uses
  per-word colouring instead of `\kf`, whose sweep would run left→right).
- Social styles drop trailing `. , ، ; :` (standard for reels and avoids bidi-misplaced punctuation).
- Transcripts are Whisper's verbatim output (the audio dept never "repairs" words) — with `large-v3` on 2
  CPU cores an 8 s Egyptian clip took ~6 min including model load; on the Mac use mlx-whisper.

## Measured here (Linux, 2 CPUs)
- 15 s 720p edit with title card, 4 transitions, Ken Burns, lower third, logo, ducked music, auto-captions
  (base.en) and melt check: 95 s. Loudness landed at −14.0 LUFS / −1.2 dBTP.
- Reframe 11.7 s 1080p → 1080×1920 with face tracking: 86 s; face kept in frame in every sampled frame.
- vid.stab on synthetic 1.4 Hz shake: mean global frame motion 12.9 → 6.2 px (the remainder is the
  clip's own subject/camera motion).
- Needs: ffmpeg (with libass, xfade, lut3d; vid.stab optional), melt (MLT) for the project check —
  on Linux without a display melt's Qt module needs `xvfb-run` (used automatically); on macOS
  `brew install mlt` works directly. OpenCV (`opencv-python-headless`) for reframe/thumbnails.
