"""Pro-NLE project packages: the same edit the studio rendered, handed over as editable projects for every
professional app — Premiere Pro, After Effects, DaVinci Resolve (Edit/Color/Fusion/Fairlight), Final Cut Pro,
CapCut, Avid Media Composer / Pro Tools (AAF), Kdenlive/Shotcut — with the media, native editable graphics
and captions, LUTs, stems and a guide, in ONE portable folder.

    doc.build(...)      Timeline + render side-data → an app-neutral edit document (seconds, pixels)
    package.build(...)  copies media into the folder and runs every writer (w_*.py), then checks them
"""
APPS = ("premiere", "aftereffects", "resolve", "fcpx", "capcut", "avid", "kdenlive", "otio", "edl")
APP_NAMES = {"premiere": "Adobe Premiere Pro", "aftereffects": "Adobe After Effects", "resolve": "DaVinci Resolve",
             "fcpx": "Final Cut Pro", "capcut": "CapCut (desktop)", "avid": "Avid Media Composer / Pro Tools (AAF)",
             "kdenlive": "Kdenlive / Shotcut", "otio": "OpenTimelineIO", "edl": "CMX3600 EDL (any NLE)"}
