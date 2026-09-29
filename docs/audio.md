# Audio department (Audition / Audacity replacement)

Code: `src/studio/depts/audio/` · Tests: `tests/test_audio.py` (`-m slow` = network + real models).
Every tool writes to `STUDIO_HOME/projects/<project>/audio/…` (never overwrites), measures its output
(integrated LUFS, true peak, silence %, clipping, stereo phase) and returns a waveform + log-spectrogram
PNG in `previews` — look at it before presenting.

| Tool | What it does |
|---|---|
| `audio_transcribe` | Speech (audio or video) → SRT/VTT/TXT/JSON with word timestamps + per-word confidence. **Stable contract for the video dept:** `data.srt`, `data.json`, `data.vtt`, `data.txt`, `data.words=[{word,start,end,probability}]`, `data.language`, `data.confidence`, `data.flagged_segments`. SRT and JSON are always written. |
| `audio_transcribe_models` | Which Whisper models are cached, sizes, speed/accuracy trade-off, mlx availability. |
| `audio_voiceover` | Text/script → voice (edge-tts online; Piper offline fallback), chunked by sentence/paragraph, stitched, normalised (−16 LUFS default), **SRT aligned to the voice** (edge word boundaries, punctuation restored from the script). `data.audio/srt/json/words`. |
| `audio_voices` | Voice catalogue (Edge 320+ voices, Piper) + optional rendered samples and one audition file. |
| `audio_clean` | “Studio voice”: HP, de-click, noise reduction (noisereduce), EQ, split-band de-esser, expander, compressor, loudness. Presets `podcast`, `reel`, `phone`, `light`, `music`. Before/after preview. |
| `audio_master` | Loudness to target (`streaming` −14, `podcast` −16, `broadcast` −23, `atsc` −24 or a number) with a look-ahead true-peak limiter; reports the measured result. |
| `audio_mix` | Voice + music with auto-ducking (VAD envelope, attack/release), intro/outro, fades, crossfaded looping, mastering. `data.duck_regions`, `data.voice_offset_seconds`. |
| `audio_trim_silence` | Shorten long pauses (jump-cut), trim edges; returns the kept-segment edit list for matching video cuts. |
| `audio_convert` / `audio_join` / `audio_analyze` | Format/sample-rate/channel conversion (also extracts audio from video) · join with crossfade/gap + offsets · measure + see any audio, optional pass/fail vs a target. |
| `audio_separate_stems` | Demucs htdemucs (vocals/no_vocals or 4 stems) or a crude stereo-centre fallback. |
| `audio_music` | Offline procedural music: `lofi`, `cinematic`, `corporate`, `oriental` (maqam hijaz/hijazkar/kurd/nahawand/bayati/rast/saba/nikriz; darbuka rhythms maqsum/baladi/saidi/malfuf). WAV + multi-track MIDI (editable master). |
| `audio_sfx` | Procedural effects: whoosh, riser, downlifter, impact, pop, click, ding, success, error, glitch, typing, tick, shutter. |
| `audio_library_search` | Free-licensed recordings: Openverse (no key; Freesound/Jamendo/ccMixter/Wikimedia) and Freesound (free key `FREESOUND_API_KEY` or `STUDIO_HOME/keys.json`). Downloads write a `.license.txt` with the attribution line. Pixabay has **no** audio API. |
| `audio_audacity` | Drives a running Audacity through mod-script-pipe (status/open/run/process→export); explains how to enable it when missing. |

## Transcription notes (honesty rules)
- Output is Whisper's text **verbatim** — nothing is “repaired”. Uncertain words (p < 0.45) and suspicious
  segments (low logprob, likely non-speech, repetition, known hallucination phrases like
  “اشتركوا في القناة”, implausible speed) are *flagged* for human review.
- Default model = best cached (`large-v3`, 3 GB). `speed='fast'` → `base.en` for English.
  Other models need `allow_download=true` (ask the user first; sizes in `audio_transcribe_models`).
- `dialect='egyptian'` adds an Egyptian-colloquial prompt: it gives punctuated captions and colloquial
  spelling; it does not change which words are recognised.
- Apple Silicon: `pip install mlx-whisper` and `engine='auto'` uses it (several× faster than CPU).
  *Not verifiable in this Linux container.*

Measured round trip here (edge-tts → faster-whisper large-v3, int8, 2 CPU cores):

| Test | WER | Notes |
|---|---|---|
| English, en-US-AndrewMultilingual, 55 words | 1.8 % | only error “ten” → “10” (normalisation), base.en same WER at 0.8× realtime |
| Egyptian Arabic, ar-EG-Shakir, 49 words | 12.2 % | errors on colloquial words (النهارده→“انه تدهم”, ماتنسوش→“متسوش”, الكومنتات→“اكتومنتات”) — **with high confidence (0.95), so flags don’t catch these**; review Egyptian captions by ear |

Large-v3 on this 2-core box: ~1.2–1.7× realtime after the model is in RAM (first load of the 3 GB file
can take minutes from a cold disk; the MCP server keeps one model loaded). RAM ≈ 3.5 GB.

## Voices
Aliases: `egyptian-female` (ar-EG-SalmaNeural), `egyptian-male` (ar-EG-ShakirNeural), `saudi-*`,
`emirati-*`, `kuwaiti-*`, `qatari-*`, `english-*` (US multilingual), `british-*`, `narrator-*`.
Edge's free endpoint no longer accepts custom SSML, so speaking *styles* (cheerful, sad…) are not
available — rate/pitch/volume are. Write numbers the way they should be spoken. Piper offline has no
Egyptian voice (only `ar_JO-kareem`, Jordanian); Piper gives sentence-exact but word-estimated caption
timing. Kokoro was not added: no Arabic voices. Edge voices are Microsoft's online service — check
their terms for commercial use. Behind TLS-inspecting proxies the module adds `SSL_CERT_FILE` to
edge-tts's certifi context.

## Music — honest assessment
Structurally musical (sections, progressions, swing, phrase-based maqam melodies with cadences, exact
quarter-tones in audio and ±2-semitone pitch-bend in MIDI) with synthetic timbres: FM e-piano,
Karplus–Strong oud/qanun/plucks, additive strings, synthesized darbuka/riq. Good as a bed under a voice
for reels/explainers; not a composer or a real oud player. Use the MIDI to re-orchestrate in
GarageBand/Logic/MuseScore/LMMS. AI music (MusicGen, Stable Audio Open) belongs to the **ai** dept
(ComfyUI) — too heavy for this module.

## Stems
Demucs 4.1 downloads `htdemucs` (~81 MB) into the Hugging Face cache on first use; with PyTorch CPU the
install is ~1 GB. ~1× realtime on 2 cores (30 s mix in 30 s). The `center` fallback only works on
stereo with centre-panned vocals and leaks bass/kick.

## Audacity bridge
One-time: Audacity → Preferences/Settings → Modules → `mod-script-pipe` = Enabled → restart.
Pipes: `/tmp/audacity_script_pipe.{to,from}.<uid>` (macOS/Linux), `\\.\pipe\ToSrvPipe` (Windows).
Tested here only against a fake pipe server (no Audacity GUI in the container).

## Dependencies
pip: `edge-tts faster-whisper noisereduce soundfile pedalboard piper-tts mido scipy`; optional
`demucs` (+ `torch`/`torchaudio` CPU), `mlx-whisper` (Apple Silicon). Programs: `ffmpeg`; optional
`sox`, Audacity (`brew install --cask audacity`).
