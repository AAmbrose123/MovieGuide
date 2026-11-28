"""
Movie Guide - a visual guide to the movies and shows in your folders.

Shows posters, IMDb / Rotten Tomatoes / Metacritic scores, cast and plot,
using data from OMDb (free API key: https://www.omdbapi.com/apikey.aspx).

Run:   pythonw movie_guide.py      (or double-click "Movie Guide.bat")
Needs: pip install pywebview       (falls back to your web browser without it)
"""

import hashlib
import http.client
import html as html_mod
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "Movie Guide"
VERSION = "1.0.11"                       # set automatically by the GitHub build from the release tag
GITHUB_REPO = "AAmbrose123/MovieGuide"  # where updates are published
FROZEN = getattr(sys, "frozen", False)  # True when running as the built MovieGuide.exe
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
BUNDLE = Path(getattr(sys, "_MEIPASS", HERE))   # files packed inside the exe
UI_FILE = BUNDLE / "guide_ui.html"

_base = os.environ.get("APPDATA") or str(Path.home() / ".config")
DATA_DIR = Path(os.environ.get("MOVIEGUIDE_DATA", Path(_base) / "MovieGuide"))
POSTER_DIR = DATA_DIR / "posters"
CUSTOM_POSTER_DIR = HERE / "custom posters"
COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
CONFIG_FILE = DATA_DIR / "config.json"
CACHE_FILE = DATA_DIR / "cache.json"

OMDB_URL = os.environ.get("MOVIEGUIDE_OMDB_URL", "https://www.omdbapi.com/")
IMDB_GQL = os.environ.get("MOVIEGUIDE_IMDB_GQL", "https://caching.graphql.imdb.com/")
SUGGEST_URL = os.environ.get("MOVIEGUIDE_SUGGEST_URL", "https://v3.sg.media-imdb.com/suggestion/")
UA = {"User-Agent": "MovieGuide/1.0"}

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg",
             ".ts", ".m2ts", ".webm", ".flv", ".divx", ".iso", ".vob",
             ".asf", ".rm", ".rmvb", ".ogm", ".ogv", ".3gp", ".mts", ".m2v", ".f4v", ".mpe", ".qt",
             ".dv", ".xvid", ".wtv", ".dvr-ms", ".mk3d"}
ARCHIVE_EXT = {".rar", ".zip", ".7z", ".r00", ".r01", ".001", ".tar", ".gz"}
SKIP_WORDS = re.compile(r"(?i)(?<![a-z])(samples?|trailers?|featurettes?|extras\d+|extra\d+|deleted[ ._-]?scenes?|"
                        r"behind[ ._-]the[ ._-]scenes|bloopers?|gag[ ._-]?reel)(?![a-z])")
EXTRA_DIRS = {"extras", "extra", "featurettes", "bonus", "bonus features", "samples", "sample", "trailers",
              "behind the scenes", "deleted scenes", "interviews", "shorts", "other"}
EPISODE_RE = re.compile(r"(?i)(\bS\d{1,2}[ ._-]?E\d{1,3}\b|\b\d{1,2}x\d{2,3}\b)")
SHOW_MARK_RE = re.compile(
    r"(?i)(\bS\d{1,2}(?:[ ._-]?E\d{1,3})?(?:[ ._-]?-?[ ._-]?S?\d{0,2})?\b|\bSeasons?[ ._-]?\d+|"
    r"\bComplete[ ._-]Series\b|\b\d{1,2}x\d{2,3}\b)")
EP_LOOSE_RE = re.compile(
    r"(?i)(^\d{3,4}\s*[-+_.]\s|\s-\s\d{3}\b|-\s?\d{2}\s?-|_\d{2}\.\w{2,4}$|\b(?:ep|episode)[ ._-]?\d{1,3}\b|"
    r"\bE\d{2,3}\b|\b\d{1,2}x\d{2}\b|\bS\d{1,2}[ ._-]?E\d{1,3}\b)")
# things that are NOT movies/shows (for messy folders like Downloads)
GAME_FILE_EXT = {".exe", ".dll", ".pak", ".ucas", ".utoc", ".bik", ".bk2", ".usm", ".big", ".vpk", ".gcf",
                 ".esp", ".esm", ".ba2", ".bsa", ".rpf", ".forge", ".sys", ".msi", ".nsp", ".xci", ".iso_"}
GAME_DIR_NAMES = {"binaries", "_commonredist", "engine", "paks", "redist", "directx", "steamworks shared",
                  "bepinex", "ue4ss", "reshade-shaders", "mods", "plugins", "x64", "x86", "win64", "bin"}
GAME_NAME_RE = re.compile(
    r"(?i)(-(rune|codex|skidrow|empress|tenoke|flt|plaza|cpy|reloaded|prophet|darksiders|razor1911|hoodlum|"
    r"doge|tinyiso|elamigos|gog|p2p|kaos|i_know)\b|(?<![a-z])(fitgirl|dodi|elamigos|repack|trainer|crack(ed)?|"
    r"update|patch|hotfix|mod(s|pack)?|reshade|ue4ss|bepinex|setup|installer|portable|keygen|sdk|"
    r"x64|win64|macos|apk|emulator|texture(s)?|hd textures|gamedrive)(?![a-z])|(?<![a-z])v\d+(\.\d+){1,3}\b|"
    r"\b\d{4}-\d{2}-\d{2}t\d{2}-\d{2}z\b)")
RECORDING_RE = re.compile(
    r"(?i)(^(recording|screen ?recording|capture|clip|replay|obs|nvidia|shadowplay|vid|img|pxl|mvi)[ _-]|"
    r"\d{4}-\d{2}-\d{2}[ _]\d{2}[-.]\d{2}[-.]\d{2}|\bcompressed\b|\bgameplay\b|^\d{8}_\d{6})")
MIN_VIDEO = 40 * 2 ** 20        # in mixed folders, anything smaller is a clip/sample, not an episode or film


def looks_like_game(name):
    """Name says game/software and nothing says movie (no year, no SxxEyy)."""
    plain = re.sub(r"(?i)\d{4}-\d{2}-\d{2}t\d{2}-\d{2}z", "", name)
    has_year = re.search(r"(?<![A-Za-z0-9])(19\d{2}|20\d{2})(?![0-9])", plain)   # not "Razor1911"
    return bool(GAME_NAME_RE.search(name)) and not EPISODE_RE.search(name) and not has_year


SEASON_DIR_RE = re.compile(r"(?i)^(season|series|s)[ ._-]?\d{1,2}\b|^specials$")
JUNK_RE = re.compile(
    r"(?i)\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10?\+?|hdr|dv|dolby[ .]?vision|"
    r"blu-?ray|bdrip|brrip|bd|remux|web-?dl|webrip|web|hdtv|hdrip|dvdrip|dvdscr|dvd|"
    r"x26[45]|h[ .]?26[45]|hevc|avc|xvid|divx|10bit|8bit|aac\d?(?:[ .]\d)?|ac3|eac3|"
    r"ddp?\d?(?:[ .]\d)?|dts(?:-hd)?|truehd|atmos|flac|mp3|5[ .]1|7[ .]1|"
    r"proper|repack|extended|unrated|remastered|directors[ .]?cut|imax|"
    r"internal|limited|multi|dubbed|subbed|nf|amzn|dsnp|hmax|atvp|hulu|yts|yify|rarbg|"
    r"complete|criterion)\b")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
AMBIG_JUNK = {"limited", "complete", "proper", "internal", "web"}


# --------------------------------------------------------------------------- helpers

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def item_id(path):
    return hashlib.sha1(os.path.normcase(path).encode("utf-8", "ignore")).hexdigest()[:14]


def parse_name(name, is_file):
    """Turn 'The.Matrix.1999.1080p.BluRay.x264-GRP.mkv' into ('The Matrix', 1999, False)."""
    base = os.path.splitext(name)[0] if is_file else name
    s = re.sub(r"^\s*[\[\{][^\]\}]*[\]\}]\s*", "", base)        # leading [Group] tag
    show = bool(SHOW_MARK_RE.search(s))
    s = s.replace("_", " ")
    s = re.sub(r"(?<=\w)\.(?=\w)", " ", s)                       # dots used as spaces
    s = re.sub(r"(?<=\s)\.|\.(?=\s)", " ", s)

    cut = len(s)
    m = SHOW_MARK_RE.search(s)
    if m and m.start() > 0:
        cut = min(cut, m.start())
    # words like "Limited", "Complete", "Extended" are also real title words ("The Darjeeling Limited"):
    # they only count as release tags when they come after the year
    years = [y.start() for y in YEAR_RE.finditer(s) if y.start() > 0]
    for m in JUNK_RE.finditer(s):
        if m.start() == 0:
            continue
        if m.group(1).lower() in AMBIG_JUNK and any(y > m.start() for y in years):
            continue
        cut = min(cut, m.start())
        break

    max_year = time.localtime().tm_year + 1
    year = None
    paren = re.search(r"[\(\[]((?:19|20)\d{2})[\)\]]", s)
    if paren and int(paren.group(1)) <= max_year and paren.start() > 0:
        year = int(paren.group(1))
        cut = min(cut, paren.start())
    else:
        cands = [y for y in YEAR_RE.finditer(s[:cut + 6])
                 if y.start() > 0 and int(y.group(1)) <= max_year]
        if cands:
            y = cands[-1]
            year = int(y.group(1))
            cut = min(cut, y.start())
        else:   # year after the junk: "I Swear to God [.x264] 2009 Standup Comedy"
            late = [y for y in YEAR_RE.finditer(s) if y.start() > 0 and int(y.group(1)) <= max_year]
            if late:
                year = int(late[0].group(1))

    title = s[:cut]
    title = re.sub(r"[\(\[\{][^\)\]\}]*$", "", title)
    title = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -._,~")
    if not title:
        title = re.sub(r"[._]+", " ", base).strip()
    return title, year, show


def list_dir(path):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def is_video(entry_name):
    return os.path.splitext(entry_name)[1].lower() in VIDEO_EXT and not SKIP_WORDS.search(entry_name)


def inspect_folder(path, depth=0, acc=None):
    """Collect video files (path, size, name) up to 3 levels deep."""
    acc = acc if acc is not None else {"videos": [], "season_dirs": 0, "game": 0, "archives": 0, "files": 0}
    for e in list_dir(path):
        try:
            if e.is_dir(follow_symlinks=False):
                if e.name.lower() in GAME_DIR_NAMES:
                    acc["game"] += 1
                if SEASON_DIR_RE.search(e.name):
                    acc["season_dirs"] += 1
                if depth < 2 and not SKIP_WORDS.search(e.name) and e.name.lower() not in EXTRA_DIRS:
                    inspect_folder(e.path, depth + 1, acc)
            elif is_video(e.name):
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                acc["videos"].append((e.path, size, e.name))
            elif os.path.splitext(e.name)[1].lower() in GAME_FILE_EXT:
                acc["game"] += 1
            elif os.path.splitext(e.name)[1].lower() in ARCHIVE_EXT or re.search(r"(?i)\.part\d+\.rar$|\.r\d\d$", e.name):
                acc["archives"] += 1
            else:
                acc["files"] += 1
        except OSError:
            continue
    return acc


def scan_folder(folder, kind="auto", skipped=None):
    """kind="tv": every folder is one show and loose files are episodes. "auto": work it out.
    Anything left out is noted in `skipped` with the reason, for the library check."""
    items = []
    skip = (lambda name, why: skipped.append({"name": name, "reason": why})) if skipped is not None else (lambda *a: None)
    for e in list_dir(folder):
        try:
            if e.name.startswith((".", "$")):
                continue
            if e.name.lower() in SYSTEM_DIRS:
                continue
            if e.is_dir(follow_symlinks=False):
                if looks_like_game(e.name):        # "Hades.II-RUNE", "Tacoma-Codex", "...v1.5.0.9-Razor1911"
                    skip(e.name + "\\", "looks like a game or program")
                    continue
                info = inspect_folder(e.path)
                vids = info["videos"]
                if info["game"] >= 2 and not any(EPISODE_RE.search(v[2]) for v in vids) and \
                        max((v[1] for v in vids), default=0) < 600 * 2 ** 20:
                    skip(e.name + "\\", "game or program files (videos are only cut-scenes)")
                    continue                       # game / software folder with cut-scene videos
                if kind == "mixed":
                    small = len(vids)
                    vids = [v for v in vids if v[1] >= MIN_VIDEO and not RECORDING_RE.search(v[2])]
                    info["videos"] = vids
                    if small and not vids:
                        skip(e.name + "\\", "only small clips or recordings")
                        continue
                if not vids:
                    if info["archives"]:
                        skip(e.name + "\\", "still packed (.rar/.zip) - unpack it to add it")
                    elif info["files"] or info["season_dirs"]:
                        skip(e.name + "\\", "no video files inside")
                    else:
                        skip(e.name + "\\", "empty folder")
                    continue
                title, year, show = parse_name(e.name, False)
                eps = sum(1 for v in vids if EPISODE_RE.search(v[2]))
                loose = sum(1 for v in vids if EP_LOOSE_RE.search(v[2]))
                if eps >= 2 or info["season_dirs"] or (show and len(vids) > 1) or kind == "tv" or \
                        (len(vids) >= 3 and loose >= max(3, 0.6 * len(vids))):
                    show = True
                biggest = max(vids, key=lambda v: v[1])
                main = biggest[0]
                play, n = (None, max(eps, len(vids))) if show else (biggest[0], 0)
                size = sum(v[1] for v in vids) if show else biggest[1]
                if not show:
                    top = biggest[1]
                    big = [v for v in vids if v[1] >= max(0.35 * top, 150 * 2 ** 20)]
                    dvd = next((v for v in vids if v[2].upper().endswith((".VOB", ".M2TS")) or
                                "VIDEO_TS" in v[0].upper() or "BDMV" in v[0].upper()), None)
                    parts = any(re.search(r"(?i)\b(cd|dis[ck]|part|pt)[ ._-]?\d", v[2]) for v in big)
                    stems = {re.sub(r"\s*(\d{1,2}|[ivx]{1,4})$", "", tkey(parse_name(v[2], True)[0])) for v in big}
                    if dvd:
                        # a DVD/Blu-ray copy: one movie; "play" opens the disc folder
                        play = os.path.dirname(dvd[0])
                    elif len(big) >= 2 and len(stems) == 1 and not parts:
                        # "Read_Or_Die_1/2/3": numbered episodes of one title
                        show, play, n = True, None, len(big)
                        size = sum(v[1] for v in big)
                    elif len(big) >= 3 and not parts:
                        # a collection folder ("Eddie Izzard - 11 Shows", "Stand UP"): list each video
                        for v in big:
                            t2, y2, _ = parse_name(v[2], True)
                            if not y2:   # the year is often only on the file's own folder
                                y2 = parse_name(os.path.basename(os.path.dirname(v[0])), False)[1]
                            try:
                                added = int(os.stat(v[0]).st_mtime)
                            except OSError:
                                added = 0
                            items.append({"id": item_id(v[0]), "path": v[0], "name": v[2], "title": t2,
                                          "year": y2, "type": "movie", "play": v[0], "eps": 0,
                                          "added": added, "group": e.name, "size": v[1]})
                        continue
            elif is_video(e.name):
                if looks_like_game(e.name):
                    skip(e.name, "looks like a game or program")
                    continue
                if kind == "mixed":
                    try:
                        if e.stat().st_size < MIN_VIDEO:
                            skip(e.name, "small clip")
                            continue
                    except OSError:
                        continue
                    if RECORDING_RE.search(e.name) or looks_like_game(e.name):
                        skip(e.name, "screen/game recording")
                        continue
                title, year, show = parse_name(e.name, True)
                show = show or kind == "tv"
                play, n = e.path, (1 if show else 0)
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
            else:
                ext = os.path.splitext(e.name)[1].lower()
                if ext in ARCHIVE_EXT and kind != "mixed":
                    skip(e.name, "still packed (.rar/.zip) - unpack it to add it")
                elif SKIP_WORDS.search(e.name) and os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                    skip(e.name, "sample / trailer / extra")
                continue
            try:
                added = int(e.stat().st_mtime)
            except OSError:
                added = 0
            items.append({
                "id": item_id(e.path), "path": e.path, "name": e.name,
                "title": title, "year": year, "type": "series" if show else "movie",
                "play": play, "eps": n, "added": added, "size": size,
                "main": main if e.is_dir(follow_symlinks=False) else e.path,
            })
        except OSError:
            continue
    # merge loose episode files of the same show into one entry
    merged, shows = [], {}
    for it in items:
        if it["type"] == "series" and os.path.isfile(it["path"]):
            key = norm(it["title"])
            if key in shows:
                shows[key]["eps"] += 1
                shows[key]["size"] = shows[key].get("size", 0) + it.get("size", 0)
                shows[key]["added"] = max(shows[key]["added"], it["added"])
                continue
            it = dict(it, id=item_id(folder + "|show|" + key), path=it["path"], play=None)
            shows[key] = it
        merged.append(it)
    return merged


# --------------------------------------------------------------------------- episodes

SXE_RE = re.compile(r"(?i)\bS(\d{1,2})[ ._-]?E(\d{1,3})(?:-?E(\d{1,3})|-(\d{2,3})(?![\dp]))?")
XE_RE = re.compile(r"(?i)(?<![\dx])(\d{1,2})x(\d{2,3})\b")
EPW_RE = re.compile(r"(?i)\b(?:ep|episode|e)[ ._-]?(\d{1,3})\b")
NUM3_RE = re.compile(r"(?:^|\s-\s|\s)(\d)(\d{2})(?=[\s.+_\-\[(]|$)")
DASHNUM_RE = re.compile(r"(?:^|[-_\s])-?\s?(\d{1,2})\s?(?=[-_.\s]|$)")
SEASON_PATH_RE = re.compile(r"(?i)\b(?:season|series|staffel|saison|s)[ ._-]?(\d{1,2})\b")


def _clean_ep_title(t, show_key):
    t = re.sub(r"[\[\{(][^\]\})]*[\]\})]", " ", t)
    t = t.replace("_", " ")
    t = re.sub(r"(?<=\w)\.(?=\w)", " ", t)
    m = JUNK_RE.search(t)
    if m:
        t = t[:m.start()]
    t = re.sub(r"\s+", " ", t).strip(" -+._,~")
    if show_key and tkey(t).startswith(show_key):
        words, sk = t.split(), show_key.split()
        t = " ".join(words[len(sk):]).strip(" -+._,~")
    return t or None


def episode_info(path, show_root, show_title):
    stem = os.path.splitext(os.path.basename(path))[0]
    rel = os.path.relpath(os.path.dirname(path), show_root) if show_root else ""
    dirs = [] if rel in (".", "") else re.split(r"[\\/]", rel)
    show_key = tkey(show_title)
    s_ = e_ = e2 = None
    rest = stem
    m = SXE_RE.search(stem) or XE_RE.search(stem)
    if m:
        s_, e_ = int(m.group(1)), int(m.group(2))
        e2 = int(m.group(3) or m.group(4)) if m.re is SXE_RE and (m.group(3) or m.group(4)) else None
        rest = stem[m.end():]
    else:
        for d in reversed(dirs):
            dm = SEASON_PATH_RE.search(d)
            if dm:
                s_ = int(dm.group(1))
                break
        clean = stem.replace("_", " ")
        sm = SEASON_PATH_RE.search(clean)
        if s_ is None and sm and not re.match(r"(?i)s\d", sm.group(0)):
            s_ = int(sm.group(1))
        m = EPW_RE.search(clean)
        if m:
            e_, rest = int(m.group(1)), clean[m.end():]
        else:
            m = NUM3_RE.search(clean)
            if m and not YEAR_RE.fullmatch(m.group(1) + m.group(2)):
                if s_ is None:
                    s_ = int(m.group(1))
                e_, rest = int(m.group(2)), clean[m.end():]
            else:
                tail = clean[sm.end():] if sm else clean
                m = DASHNUM_RE.search(tail)
                if m:
                    e_, rest = int(m.group(1)), tail[m.end():]
    title = _clean_ep_title(rest, show_key)
    if not title and not m:
        title = _clean_ep_title(stem, show_key)
    return {"season": s_, "episode": e_, "episode2": e2, "title": title}


# bonus material that ships with a show but isn't an episode
EXTRA_WORDS_RE = re.compile(r"(?i)(?<![a-z])(extras?|featurettes?|bonus(?: features?| material)?|special features|"
                            r"behind[ ._-]the[ ._-]scenes|making[ ._-]of|deleted[ ._-]scenes?|interviews?|"
                            r"bloopers?|gag[ ._-]?reels?|outtakes?|trailers?|teasers?|promos?|commentar(?:y|ies)|"
                            r"documentar(?:y|ies)|retrospective|webisodes?|minisodes?|"
                            r"(?:music|promotional) videos?|recaps?|ncop|nced|creditless)(?![a-z])")
SAMPLE_RE = re.compile(r"(?i)(?<![a-z])samples?(?![a-z])")


def is_extra_name(text, show_title=""):
    t = re.sub(r"[._]+", " ", text)
    if show_title:                    # "Trailer Park Boys" is a show, not a trailer
        words = [re.escape(w) for w in re.split(r"[^A-Za-z0-9]+", show_title) if w]
        if words:
            t = re.sub(r"(?i)" + r"[^A-Za-z0-9]*".join(words), " ", t)
    return bool(EXTRA_WORDS_RE.search(t))


def list_episodes(show_path, show_title):
    files = []
    extra_paths = set()
    if os.path.isfile(show_path):          # loose episode files sitting in a shared folder
        root = os.path.dirname(show_path)
        want = tkey(parse_name(os.path.basename(show_path), True)[0])
        for e in list_dir(root):
            if e.is_file() and is_video(e.name) and tkey(parse_name(e.name, True)[0]) == want:
                files.append(e)
    else:
        root = show_path
        stack = [(show_path, 0, False)]
        while stack:
            d, depth, in_extras = stack.pop()
            for e in list_dir(d):
                try:
                    if e.is_dir(follow_symlinks=False):
                        if depth < 3 and not SAMPLE_RE.search(e.name):
                            extra_dir = in_extras or e.name.lower() in EXTRA_DIRS or \
                                (is_extra_name(e.name, show_title) and not SEASON_PATH_RE.search(e.name))
                            stack.append((e.path, depth + 1, extra_dir))
                    elif os.path.splitext(e.name)[1].lower() in VIDEO_EXT and not SAMPLE_RE.search(e.name):
                        files.append(e)
                        if in_extras:
                            extra_paths.add(e.path)
                except OSError:
                    continue
    eps = []
    for e in files:
        info = episode_info(e.path, root, show_title)
        try:
            size = e.stat().st_size
        except OSError:
            size = 0
        if e.path in extra_paths or (info["episode"] is None or info["season"] is None) and \
                is_extra_name(os.path.splitext(e.name)[0], show_title) and not SXE_RE.search(e.name):
            # bonus material goes under "Extras", in name order
            info = dict(info, season=0, episode=None, title=info.get("title") or os.path.splitext(e.name)[0])
        eps.append(dict(info, path=e.path, name=e.name, size=size))
    known = [x for x in eps if x["season"] is not None]
    default_season = 1 if (not known or len(known) < len(eps) / 2) else None
    for x in eps:
        if x["season"] is None:
            x["season"] = default_season if default_season is not None else 0      # 0 = "Other"
    eps.sort(key=lambda x: (x["season"], x["episode"] if x["episode"] is not None else 9999,
                            collator_key(x["name"])))
    seasons = {}
    for x in eps:
        seasons.setdefault(x["season"], []).append(x)
    for lst in seasons.values():           # files without a number: number them in order
        if all(x["episode"] is None for x in lst):
            for i, x in enumerate(lst, 1):
                x["episode"] = i
    return [{"season": k, "episodes": v} for k, v in sorted(seasons.items(), key=lambda kv: (kv[0] == 0, kv[0]))]


def collator_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


# --------------------------------------------------------------------------- web lookups
# Free, keyless sources used to fill the gaps OMDb leaves:
#   * IMDb's search-suggestion feed  -> finds titles OMDb's strict search misses, plus posters/stars
#   * Wikidata                        -> the exact Rotten Tomatoes / Metacritic page for an IMDb id
#   * rottentomatoes.com / metacritic.com pages -> the scores themselves

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
WIKI_UA = {"User-Agent": "MovieGuide/1.2 (personal media library viewer; https://www.wikidata.org/wiki/Wikidata:Data_access) python-urllib",
           "Accept": "application/sparql-results+json"}


def http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or BROWSER)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def tkey(s):
    """Title key that ignores apostrophes/punctuation: "Ender's Game" == "Enders Game"."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()   # Amélie -> Amelie
    s = s.lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\b([a-z]) (?=[a-z]\b)", r"\1", s)               # "l a confidential" -> "la confidential"
    s = re.sub(r"\b([a-z]+) s\b", r"\1s", s)                  # "bueller s" (lost apostrophe) -> "buellers"
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)    # "alien3" -> "alien 3"
    romans = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}
    return " ".join(romans.get(w, w) if i else w for i, w in enumerate(s.split()))


def imdb_suggest(query, strict=False):
    """IMDb's search-as-you-type data. strict=True raises on network trouble instead of
    returning [] - so a dropped connection is never mistaken for "no such title"."""
    q = tkey(query) if not re.match(r"^tt\d+$", query) else query
    if not q:
        return []
    first = q[0] if q[0].isalnum() else "x"
    url = f"{SUGGEST_URL}{first}/{urllib.parse.quote(q)}.json"
    try:
        for attempt in range(3):
            try:
                d = json.loads(http_get(url, timeout=10))
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 503) or attempt == 2:
                    raise
                time.sleep(2.0 * (attempt + 1))           # IMDb says "slow down"
    except urllib.error.HTTPError as e:
        if strict and e.code not in (404,):
            raise ConnectionError(f"IMDb search: HTTP {e.code}")
        return []
    except Exception as e:
        if strict:
            raise ConnectionError(f"IMDb search: {e}")
        return []
    out = []
    for x in d.get("d", []) or []:
        if not str(x.get("id", "")).startswith("tt"):
            continue
        img = (x.get("i") or {}).get("imageUrl")
        if img:
            img = re.sub(r"\._V1_.*?\.jpg$", "._V1_SX300.jpg", img)
            if "._V1_" not in img:
                img = img.replace(".jpg", "._V1_SX300.jpg")
        out.append({"id": x["id"], "title": x.get("l") or "", "year": x.get("y"), "rank": x.get("rank"),
                    "kind": x.get("qid") or x.get("q") or "", "stars": x.get("s") or "", "image": img})
    return out


def title_variants(title):
    """Extra ways a messy file name might be written on IMDb."""
    v = [title]
    words = title.split()
    # "Alfred Hitchcocks Vertigo", "Guillermo del Toros Pinocchio" -> "Vertigo", "Pinocchio"
    for i in range(1, min(4, len(words) - 1)):
        if re.search(r"(?i)s$|'s$", words[i]) and words[0][:1].isupper():
            v.append(" ".join(words[i + 1:]))
    # "Alien 2 Aliens", "Final Destination 4 The Final Destination" -> part after the number
    m = re.match(r"^(.+?)\s+(\d{1,2}|I{1,3}|IV|V|VI{0,3})\s+(.{3,})$", title)
    if m:
        v.append(m.group(3))
        v.append(m.group(1) + " " + m.group(2))
    v.append(re.sub(r"(?i)\s+(US|UK|AU|NZ|CA)$", "", title))
    v.append(re.sub(r"(?i)\bpart (i|1)$", "", title).strip())
    v.append(re.sub(r"(?i)\s+(I|1)$", "", title).strip())          # "Halloween I" -> "Halloween"
    seen, out = set(), []
    for x in v:
        k = tkey(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


BAD_KINDS = {"videogame", "podcastseries", "podcastepisode", "tvepisode", "musicvideo"}
WEAK_KINDS = {"video", "short", "tvspecial", "tvshort"}     # "making of", reviews, featurettes...


def title_sim(q, r):
    """How alike two titles are (0..1). Extra words ("150 ...", "... Movie Review") count against."""
    import difflib
    a = re.sub(r"^(the|a|an) ", "", tkey(q))
    b = re.sub(r"^(the|a|an) ", "", tkey(r))
    if not a or not b:
        return 0.0
    if a == b or a.replace(" ", "") == b.replace(" ", ""):     # "Stake Land" / "Stakeland"
        return 1.0
    at, bt = a.split(), b.split()
    extra = len(set(at) ^ set(bt))
    sim = difflib.SequenceMatcher(None, a, b).ratio()
    if b.startswith(a + " ") or a.startswith(b + " "):         # "Dune Part Two" / "Dune"
        short = min(len(at), len(bt))
        sim = max(sim, max(0.9 - 0.05 * abs(len(at) - len(bt)), 0.72 if short >= 2 else 0.5))
    elif (b.endswith(" " + a) and re.search(r"s$", b[:-len(a)].strip())) or \
            (a.endswith(" " + b) and re.search(r"s$", a[:-len(b)].strip())):
        sim = max(sim, 0.85)                                   # "National Lampoon's Christmas Vacation"
    elif len(at) >= 2 and len(bt) >= 2 and at[:2] == bt[:2]:  # "Die Hard 3" / "Die Hard with a Vengeance"
        sim = max(sim, 0.66)
    elif extra >= 2:
        sim = min(sim, 0.6)
    return sim


def year_bonus(year, other):
    m = re.match(r"\d{4}", str(other or ""))
    if not year or not m:
        return 0.0
    dy = abs(int(m.group(0)) - int(year))
    return 0.3 if dy == 0 else 0.12 if dy == 1 else -0.6


def plausible(title, year, found_title, found_year, found_type=None):
    if (found_type or "").lower() in ("episode", "game"):
        return False
    yb = year_bonus(year, found_year)
    for t in title_variants(title):
        sim = title_sim(t, found_title)
        if (sim >= 0.8 and yb > -0.1) or (sim >= 0.66 and yb >= 0.3):
            return True
    return False


def obscure(d):
    """An OMDb hit that's probably the wrong film: no poster, no rating, hardly any votes."""
    try:
        votes = int(str(d.get("imdbVotes") or "0").replace(",", "") or 0)
    except ValueError:
        votes = 0
    poster = d.get("Poster") or d.get("posterUrl")
    rating = d.get("imdbRating") or d.get("imdb")
    return votes < 1500 or not poster or poster == "N/A" or rating in (None, "N/A")


def best_suggestion(title, year, kind):
    best, best_score = None, 0.0
    want_tv = kind == "series"
    for q in title_variants(title):
        for r in imdb_suggest(q, strict=True):
            rk = r["kind"].lower()
            if rk in BAD_KINDS:
                continue
            sim = title_sim(q, r["title"])
            score = sim + year_bonus(year, r["year"])
            if rk in WEAK_KINDS and sim < 0.95:      # exact-title specials (stand-up etc.) are fine
                score -= 0.35
            is_tv = rk.startswith("tv") and rk not in ("tvmovie", "tvspecial", "tvshort")
            score += 0.08 if is_tv == want_tv else -0.15
            rank = r.get("rank") or 10 ** 7
            score += 0.08 if rank < 5000 else 0.04 if rank < 50000 else 0
            if score > best_score:
                best, best_score = r, score
        if best_score >= 1.3:
            break
    return best if best_score >= 0.9 else None


# --------------------------------------------------------------------------- IMDb (main source)
# The same GraphQL service imdb.com's own pages use: no key, no daily limit.  Every optional field
# sits in its own block; if IMDb ever renames one, that block is dropped and the rest still works.
GQL_BLOCKS = {
    "core": "id titleText { text } titleType { id text canHaveEpisodes } releaseYear { year endYear }",
    "rating": "ratingsSummary { aggregateRating voteCount }",
    "meta": "metacritic { metascore { score } }",
    "image": "primaryImage { url }",
    "plot": "plot { plotText { plainText } }",
    "genres": "genres { genres { text } }",
    "credits": "principalCredits { category { id text } credits { name { nameText { text } } } }",
    "runtime": "runtime { seconds }",
    "cert": "certificate { rating }",
    "released": "releaseDate { day month year }",
    "country": "countriesOfOrigin { countries { text } }",
    "lang": "spokenLanguages { spokenLanguages { text } }",
    "seasons": "episodes { seasons { number } }",
    "gross": "lifetimeGross(boxOfficeArea: DOMESTIC) { total { amount currency } }",
}
GQL_DEAD = set()
GQL_HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
               "x-imdb-client-name": "imdb-web-next", "Origin": "https://www.imdb.com",
               "Referer": "https://www.imdb.com/", "User-Agent": BROWSER["User-Agent"],
               "Accept-Language": "en-US,en;q=0.9"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def gql(query, variables, tries=3):
    body = json.dumps({"query": query, "variables": variables}).encode()
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(IMDB_GQL, data=body, headers=GQL_HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):                            # IMDb says "slow down": wait, then retry
                last = e
                time.sleep(2.0 * (attempt + 1))
                continue
            try:
                return json.loads(e.read().decode("utf-8"))     # GraphQL errors come back as 400s
            except Exception:
                last = e
        except Exception as e:
            last = e
        time.sleep(0.7 * (attempt + 1))
    raise ConnectionError(f"IMDb: {last}")


def gql_blocks(head, variables, key):
    """Run `head { <all live blocks> }`; if IMDb rejects a field, drop that block and retry."""
    for _ in range(len(GQL_BLOCKS)):
        live = [k for k in GQL_BLOCKS if k not in GQL_DEAD]
        q = head + " { " + " ".join(GQL_BLOCKS[k] for k in live) + " } }"
        d = gql(q, variables)
        if not isinstance(d, dict):
            raise ConnectionError("IMDb: unexpected reply")
        data = d.get("data") or {}
        if data.get(key) is not None:
            return data[key]
        errs = d.get("errors") or []
        if not errs:
            return None                                     # no such title
        msg = " ".join(str(e.get("message", "")) for e in errs)
        named = set(re.findall(r"['\"]([A-Za-z_]+)['\"]", msg))
        bad = [k for k in live if k != "core" and named & set(re.findall(r"[A-Za-z_]+", GQL_BLOCKS[k]))]
        if bad:
            GQL_DEAD.update(bad)            # IMDb renamed something: drop just that part and retry
            continue
        if key in data:                     # query was fine, the id just isn't there
            return None
        raise ConnectionError("IMDb: " + msg[:200])
    return None


def gql_title_raw(imdb_id):
    return gql_blocks("query T($id: ID!) { title(id: $id)", {"id": imdb_id}, "title")


def imdb_titles(ids):
    """Many titles in ONE request (used to refresh the whole library quickly)."""
    raw = gql_blocks("query B($ids: [ID!]!) { titles(ids: $ids)", {"ids": list(ids)}, "titles")
    if raw is None:
        raise ConnectionError("IMDb batch lookup returned nothing")
    return {t["id"]: parse_title(t) for t in raw if t and t.get("id")}


def imdb_title(imdb_id):
    """Full details for one title from IMDb, in the same shape the rest of the app uses."""
    t = gql_title_raw(imdb_id)
    return parse_title(t) if t else None


def parse_title(t):
    imdb_id = t.get("id")
    g = lambda *path: _dig(t, *path)
    is_tv = bool(g("titleType", "canHaveEpisodes"))
    y, ye = g("releaseYear", "year"), g("releaseYear", "endYear")
    year = None if not y else (f"{y}–{ye}" if ye and ye != y else (f"{y}–" if is_tv and not ye else str(y)))
    credits = {}
    for c in g("principalCredits") or []:
        cid = ((c.get("category") or {}).get("id") or "").lower()
        names = [_dig(x, "name", "nameText", "text") for x in c.get("credits") or []]
        credits.setdefault(cid, []).extend(n for n in names if n)
    join = lambda xs, n=None: ", ".join(xs[:n] if n else xs) or None
    rel = g("releaseDate")
    released = None
    if rel and rel.get("year"):
        released = " ".join(str(x) for x in [rel.get("day") and f"{rel['day']:02d}",
                                             rel.get("month") and MONTHS[rel["month"] - 1], rel["year"]] if x)
    secs = g("runtime", "seconds")
    votes = g("ratingsSummary", "voteCount")
    rating = g("ratingsSummary", "aggregateRating")
    img = g("primaryImage", "url")
    if img:
        img = re.sub(r"\._V1_.*?\.jpg$", "._V1_.jpg", img)
        img = re.sub(r"\._V1_\.jpg$", "._V1_SX300.jpg", img) if "._V1_" in img else img.replace(".jpg", "._V1_SX300.jpg")
    seasons = [x.get("number") for x in (g("episodes", "seasons") or []) if x.get("number") not in (None, 0)]
    gross = g("lifetimeGross", "total")
    return {
        "imdbID": imdb_id, "Title": g("titleText", "text"), "Year": year,
        "Rated": g("certificate", "rating"), "Released": released,
        "Runtime": f"{secs // 60} min" if secs else None,
        "Genre": join([x.get("text") for x in (g("genres", "genres") or []) if x.get("text")]),
        "Director": join(credits.get("director", []) or credits.get("creator", [])),
        "Writer": join(credits.get("writer", []) or credits.get("creator", [])),
        "Actors": join(credits.get("cast", []) or credits.get("actor", []) or credits.get("star", []), 6),
        "Plot": g("plot", "plotText", "plainText"),
        "Language": join([x.get("text") for x in (g("spokenLanguages", "spokenLanguages") or []) if x.get("text")]),
        "Country": join([x.get("text") for x in (g("countriesOfOrigin", "countries") or []) if x.get("text")]),
        "Type": "series" if is_tv else "movie",
        "totalSeasons": str(len(seasons)) if is_tv and seasons else None,
        "imdbVotes": f"{votes:,}" if votes else None,
        "BoxOffice": f"${gross['amount']:,}" if gross and gross.get("amount") and gross.get("currency") == "USD" else None,
        "imdb": round(float(rating), 1) if rating else None,
        "mc": g("metacritic", "metascore", "score"),
        "rt": None, "posterUrl": img, "fetched": int(time.time()), "src": "imdb",
    }


def imdb_season(imdb_id, season):
    q = ("query S($id: ID!, $s: String!) { title(id: $id) { episodes { episodes(first: 250, filter: "
         "{ includeSeasons: [$s] }) { edges { node { id titleText { text } releaseDate { day month year } "
         "ratingsSummary { aggregateRating } series { displayableEpisodeNumber { episodeNumber { text } } } } } } } } }")
    d = gql(q, {"id": imdb_id, "s": str(season)})
    edges = _dig(d, "data", "title", "episodes", "episodes", "edges")
    if edges is None:
        raise ConnectionError("IMDb season lookup failed")
    eps = []
    for e in edges:
        n = e.get("node") or {}
        try:
            num = int(_dig(n, "series", "displayableEpisodeNumber", "episodeNumber", "text"))
        except (TypeError, ValueError):
            continue
        rel = n.get("releaseDate") or {}
        released = None
        if rel.get("year"):
            released = " ".join(str(x) for x in [rel.get("day") and f"{rel['day']:02d}",
                                                 rel.get("month") and MONTHS[rel["month"] - 1], rel["year"]] if x)
        r = _dig(n, "ratingsSummary", "aggregateRating")
        eps.append({"episode": num, "title": _dig(n, "titleText", "text"), "released": released,
                    "imdb": round(float(r), 1) if r else None, "imdbID": n.get("id")})
    return eps


def _dig(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def wikidata_links(imdb_ids):
    """imdbID -> {'rt': 'm/the_matrix', 'mc': 'movie/the-matrix', 'rtScore': 83, 'mcScore': 73}."""
    if not imdb_ids:
        return {}
    vals = " ".join(f'"{i}"' for i in imdb_ids)
    q = ("SELECT ?imdb ?rt ?mc ?score ?byl WHERE { VALUES ?imdb { %s } ?item wdt:P345 ?imdb . "
         "OPTIONAL { ?item wdt:P1258 ?rt } OPTIONAL { ?item wdt:P1712 ?mc } "
         "OPTIONAL { ?item p:P444 ?st . ?st ps:P444 ?score ; pq:P447 ?by . "
         "?by rdfs:label ?byl . FILTER(LANG(?byl) = 'en') } }" % vals)
    out = {}
    d = None
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://query.wikidata.org/sparql",
                                         data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
                                         headers=dict(WIKI_UA, **{"Content-Type": "application/x-www-form-urlencoded"}))
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode("utf-8"))
            break
        except Exception as e:
            web_log(f"Wikidata ({len(imdb_ids)} titles) try {attempt + 1}: {type(e).__name__}: {e}")
            time.sleep(3 * (attempt + 1))
    if d is None:
        if len(imdb_ids) > 10:                  # too big or too busy: ask in smaller pieces
            half = len(imdb_ids) // 2
            out = wikidata_links(imdb_ids[:half])
            out.update(wikidata_links(imdb_ids[half:]))
        return out
    for b in d.get("results", {}).get("bindings", []):
        i = b["imdb"]["value"]
        cur = out.setdefault(i, {})
        if "rt" in b and "rt" not in cur:
            cur["rt"] = b["rt"]["value"]
        if "mc" in b and "mc" not in cur:
            cur["mc"] = b["mc"]["value"]
        if "score" in b and "byl" in b:
            val, by = b["score"]["value"].strip(), b["byl"]["value"].lower()
            if "rotten" in by and val.endswith("%"):
                n = _pct(val)
                if n is not None:
                    cur["rtScore"] = n
            elif "metacritic" in by and val.endswith("/100"):
                n = _pct(val.split("/")[0])
                if n is not None:
                    cur["mcScore"] = n
    return out


def _ld_ratings(html):
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                ar = x.get("aggregateRating")
                if isinstance(ar, dict) and ar.get("ratingValue") not in (None, ""):
                    yield ar
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))


def _pct(v):
    try:
        n = int(round(float(str(v).strip().rstrip("%"))))
        return n if 0 <= n <= 100 else None
    except (TypeError, ValueError):
        return None


def _parse_rt_main(html):
    for ar in _ld_ratings(html):
        if "audience" not in str(ar.get("name", "")).lower():
            n = _pct(ar.get("ratingValue"))
            if n is not None:
                return n
    for pat in (r'slot="critics-?[Ss]core"[^>]*>\s*(\d{1,3})\s*%',
                r'"criticsScore"\s*:\s*\{[^{}]*?"score(?:Percent)?"\s*:\s*"?(\d{1,3})',
                r'"tomatometerScore"\s*:\s*\{[^{}]*?"(?:value|score)"\s*:\s*"?(\d{1,3})',
                r'tomatometer-?score="(\d{1,3})"',
                r'data-qa="tomatometer"[^>]*>\s*(\d{1,3})%'):
        m = re.search(pat, html)
        if m:
            return _pct(m.group(1))
    # visible text, e.g. "92% Tomatometer 295 Reviews" (first one on the page is the main scorecard)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"(\d{1,3})\s*%\s*(?:Avg\.?\s*)?Tomatometer", text)
    if m:
        return _pct(m.group(1))
    return None


def rt_season_avg(html):
    """RT scores TV shows per season; the show page lists them. Average the seasons that have one."""
    vals = []
    for block in re.findall(r"<tile-season\b.*?</tile-season>", html, re.S):
        m = re.search(r'slot="critics-score"[^>]*>\s*(\d{1,3})\s*%', block)
        if m:
            vals.append(int(m.group(1)))
    return (round(sum(vals) / len(vals)), len(vals)) if vals else (None, 0)


def parse_rt(html):
    # a TV show's own scorecard is usually empty ("Avg. Tomatometer", 0 reviews): use its seasons instead
    m = re.search(r'"criticsScore"\s*:\s*(\{[^{}]*\})', html)
    show_empty = "<tile-season" in html and (not m or not re.search(r'"score(?:Percent)?"\s*:\s*"?\d', m.group(1)))
    if show_empty:
        avg = rt_season_avg(html)[0]
        if avg is not None:
            return avg
    return _parse_rt_main(html)


def mc_slug(title):
    t = re.sub(r"[’'.]", "", (title or "").lower())
    t = t.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def parse_mc(html):
    """Only the page's own Metascore. Metacritic pages also list *other* titles' scores
    ("more like this"), so when the title has no Metascore yet ("tbd") the answer is None."""
    for ar in _ld_ratings(html):
        if "metascore" in str(ar.get("name", "")).lower() or not ar.get("name"):
            n = _pct(ar.get("ratingValue"))
            if n is not None:
                return n
    if re.search(r'application/ld\+json', html, re.I):
        return None                      # page described itself and gave no score: there isn't one
    m = re.search(r'"metascore"\s*:\s*\{[^{}]*?"score"\s*:\s*(\d{1,3})', html)
    return _pct(m.group(1)) if m else None


class Blocked(Exception):
    pass


class Pacer:
    """A few requests at a time per site, started a short gap apart. If the site pushes back
    (403/429/503), everyone waits and retries with growing pauses instead of hammering it."""

    def __init__(self, gap, at_once=4):
        self.gap, self.lock, self.last, self.until, self.backoff = gap, threading.Lock(), 0.0, 0.0, 30
        self.slots = threading.Semaphore(at_once)

    def _wait_turn(self):
        with self.lock:
            start = max(self.until, self.last + self.gap, time.time())
            self.last = start
        if start > time.time():
            time.sleep(start - time.time())

    def get(self, url):
        with self.slots:
            for attempt in range(6):
                self._wait_turn()
                try:
                    html = http_get(url)
                    self.backoff = 30
                    return html
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return None
                    if e.code in (403, 429, 503, 520, 521, 522):
                        with self.lock:
                            self.until = time.time() + self.backoff
                            self.backoff = min(self.backoff * 2, 900)
                        continue
                    raise
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                        http.client.HTTPException) as e:          # incl. half-finished downloads
                    web_log(f"retry {url}: {type(e).__name__}: {e}")
                    with self.lock:
                        self.until = max(self.until, time.time() + 2)
            raise Blocked(url)


RT = Pacer(0.12, 10)
MC = Pacer(0.12, 10)


_web_log_lock = threading.Lock()


def web_log(line):
    """Small rolling log of score-site trouble (AppData\\MovieGuide\\web_errors.log) for diagnosing."""
    try:
        with _web_log_lock:
            f = DATA_DIR / "web_errors.log"
            if f.exists() and f.stat().st_size > 300_000:
                f.replace(DATA_DIR / "web_errors.old.log")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def rt_really_none(html):
    """True when the page is a real RT title page that simply has no Tomatometer yet."""
    if "<tile-season" in html and rt_season_avg(html)[0] is not None:
        return False              # the seasons have scores even if the show page doesn't
    if re.search(r'"criticsScore"\s*:\s*\{[^{}]*"reviewCount"\s*:\s*0\b', html):
        return True
    m = re.search(r'"criticsScore"\s*:\s*(\{[^{}]*\})', html)
    if m and '"title"' in m.group(1) and not re.search(r'"score(?:Percent)?"\s*:', m.group(1)):
        return True               # too few reviews for a Tomatometer yet
    if re.search(r"(?i)tomatometer[^<]{0,40}(not yet available|no score yet)", html):
        return True
    return False


def rt_search(title, year, kind):
    """Fallback when Wikidata has no RT link: read RT's own search results."""
    try:
        html = RT.get("https://www.rottentomatoes.com/search?search=" + urllib.parse.quote(title)) or ""
    except Blocked:
        raise
    except Exception:
        return None, None
    want = tkey(title)
    exact, loose = None, None
    for block in re.findall(r"<search-page-media-row(.*?)</search-page-media-row>", html, re.S):
        href = re.search(r'href="(https://www\.rottentomatoes\.com/(?:m|tv)/[^"]+)"', block)
        name = re.search(r'data-qa="info-name"[^>]*>\s*([^<]+?)\s*<', block)
        score = re.search(r'tomatometer-?score="(\d{1,3})"', block)
        yr = re.search(r'(?:release-?year|start-?year)="(\d{4})"', block)
        if not (href and name):
            continue
        if ("/tv/" in href.group(1)) != (kind == "series"):
            continue
        # shows get revived and re-dated ("Liquid Television" 1991 vs 2013), so only movies check the year
        if kind != "series" and year and yr and abs(int(yr.group(1)) - int(year)) > 1:
            continue
        got = tkey(html_mod.unescape(name.group(1)))       # "It&#39;s" -> "It's"
        hit = (href.group(1), _pct(score.group(1)) if score else None)
        if got == want:
            exact = exact or hit
        elif len(want) >= 8 and (got.endswith(" " + want) or want.endswith(" " + got) and len(got) >= 8):
            loose = loose or hit                              # "Funny or Die's Billy on the Street"
    best = exact or loose
    return best if best else (None, None)


SYSTEM_DIRS = {"windows", "program files", "program files (x86)", "programdata", "users",
               "system volume information", "$recycle.bin", "recovery", "perflogs", "appdata",
               "msocache", "intel", "drivers", "boot", "config.msi", "@eadir", "#recycle", ".trash"}


# --------------------------------------------------------------------------- OMDb

class ApiError(Exception):
    pass


class LimitError(ApiError):
    pass


def slim(d):
    ratings = {r.get("Source"): r.get("Value") for r in d.get("Ratings", []) or []}

    def num(v, kind):
        if not v or v == "N/A":
            return None
        try:
            if kind == "imdb":
                return float(v.split("/")[0])
            return int(v.rstrip("%").split("/")[0])
        except ValueError:
            return None

    imdb = num(d.get("imdbRating"), "imdb") or num(ratings.get("Internet Movie Database"), "imdb")
    rt = num(ratings.get("Rotten Tomatoes"), "pct")
    mc = num(d.get("Metascore"), "pct") or num(ratings.get("Metacritic"), "pct")

    def clean(k):
        v = d.get(k)
        return None if v in (None, "N/A", "") else v

    poster = clean("Poster")
    return {
        "imdbID": d.get("imdbID"), "Title": clean("Title"), "Year": clean("Year"),
        "Rated": clean("Rated"), "Released": clean("Released"), "Runtime": clean("Runtime"),
        "Genre": clean("Genre"), "Director": clean("Director"), "Writer": clean("Writer"),
        "Actors": clean("Actors"), "Plot": clean("Plot"), "Language": clean("Language"),
        "Country": clean("Country"), "Awards": clean("Awards"), "Type": clean("Type"),
        "totalSeasons": clean("totalSeasons"), "imdbVotes": clean("imdbVotes"),
        "BoxOffice": clean("BoxOffice"),
        "imdb": imdb, "rt": rt, "mc": mc,
        "posterUrl": poster, "fetched": int(time.time()),
    }


def default_kind(name):
    if re.search(r"(?i)\b(downloads?|incoming|torrents?|complete[d]?|unsorted|misc)\b", name or ""):
        return "mixed"
    return "tv" if re.search(r"(?i)\b(tv|shows?|series|television)\b", name or "") else "auto"


class Store:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config = load_json(CONFIG_FILE, {})
        self.config.setdefault("apiKey", "")
        self.config.setdefault("folders", [])
        self.config.setdefault("current", None)
        self.config.setdefault("overrides", {})
        if "libraries" not in self.config:
            libs = []
            for f in self.config.get("folders", []):
                lid = "L" + hashlib.sha1(f.encode("utf-8", "ignore")).hexdigest()[:8]
                libs.append({"id": lid, "name": os.path.basename(f.rstrip("\\/")) or f, "paths": [f]})
                if self.config.get("current") == f:
                    self.config["current"] = lid
            self.config["libraries"] = libs
            if self.config.get("current") not in [l["id"] for l in libs]:
                self.config["current"] = libs[0]["id"] if libs else None
        for l in self.config.get("libraries", []):
            l.setdefault("kind", default_kind(l.get("name", "")))
        if not self.config.get("kindFix1"):
            for l in self.config.get("libraries", []):
                if l.get("kind") == "auto" and default_kind(l.get("name", "")) == "mixed":
                    l["kind"] = "mixed"
            self.config["kindFix1"] = True
        self.cache = load_json(CACHE_FILE, {})
        self.cache.setdefault("titles", {})   # imdbID -> slim data
        self.cache.setdefault("lookup", {})   # "type|title|year" -> imdbID or ""
        self.cache.setdefault("scans", {})    # folder -> items (last scan)
        self.dirty = False
        # undo the RiffTrax / MST3K experiment: drop its folder entry, lookups and downloaded data
        riff_libs = [l for l in self.config.get("libraries", []) if l.get("kind") not in ("auto", "tv", "mixed")]
        riff_paths = {p for l in riff_libs for p in l.get("paths", [])}
        if riff_libs:
            self.config["libraries"] = [l for l in self.config["libraries"] if l not in riff_libs]
            if self.config.get("current") not in [l["id"] for l in self.config["libraries"]]:
                self.config["current"] = self.config["libraries"][0]["id"] if self.config["libraries"] else None
            save_json(CONFIG_FILE, self.config)
        lk = self.cache["lookup"]
        gone = [k for k, v in lk.items() if k.startswith(("riff", "film")) or str(v).startswith("rf")]
        old_titles = [k for k in self.cache["titles"] if k.startswith("rf")]
        if gone or old_titles or riff_paths & set(self.cache["scans"]):
            for k in gone:
                lk.pop(k, None)
            for k in old_titles:
                self.cache["titles"].pop(k, None)
            for p in riff_paths:
                self.cache["scans"].pop(p, None)
                (self.cache.get("skipped") or {}).pop(p, None)
            self.dirty = True
        for f in [DATA_DIR / "rifftrax_index.json"] + list(POSTER_DIR.glob("rf*.jpg")):
            try:
                f.unlink()
            except OSError:
                pass
        self._matches_mtime = 0
        self.limited_at = self.cache.get("limitedAt", 0)
        self.load_matches()
        if self.cache.get("v", 1) < 2:
            # v2 matches far more titles: give earlier misses another try
            self.cache["lookup"] = {k: v for k, v in self.cache["lookup"].items() if v}
            self.cache["v"] = 2
            self.dirty = True
        if self.cache.get("v", 1) < 3:
            for key, iid in list(self.cache["lookup"].items()):
                t = self.cache["titles"].get(iid)
                kind, ntitle, yr = (key.split("|") + ["", "", ""])[:3]
                if not t or not plausible(ntitle, int(yr) if yr.isdigit() else None,
                                          t.get("Title"), t.get("Year"), t.get("Type")):
                    del self.cache["lookup"][key]
            for t in self.cache["titles"].values():
                if t.get("rt") is None:
                    t["webChecked"] = 0
            self.cache["v"] = 3
            self.dirty = True
        self.cache.setdefault("recheck", [])
        if self.cache.get("v", 1) < 4:
            # matches that look doubtful get re-checked; they're only replaced if something clearly better turns up
            for key, iid in list(self.cache["lookup"].items()):
                t = self.cache["titles"].get(iid)
                kind, ntitle, yr = (key.split("|") + ["", "", ""])[:3]
                if not t or obscure(t) or not plausible(ntitle, int(yr) if yr.isdigit() else None,
                                                        t.get("Title"), t.get("Year"), t.get("Type")):
                    self.cache["recheck"].append(key)
            self.cache["v"] = 4
            self.dirty = True
        self.recheck = set(self.cache["recheck"])
        if self.cache.get("v", 1) < 5:
            # Rotten Tomatoes rate-limited earlier runs: everything without a score gets another go
            for t in self.cache["titles"].values():
                if t.get("rt") is None or t.get("mc") is None:
                    t["webChecked"] = 0
            self.cache["v"] = 5
            self.dirty = True
        if self.cache.get("v", 1) < 6:
            # a run that read RT pages without finding the score shouldn't wait 3 weeks to try again
            for t in self.cache["titles"].values():
                if t.get("rt") is None:
                    t["webChecked"] = 0
            self.cache["v"] = 6
            self.dirty = True
        if self.cache.get("v", 1) < 7:
            # downloads that broke half way were wrongly treated as "no score": check those again
            for t in self.cache["titles"].values():
                if t.get("rt") is None:
                    t["webChecked"] = 0
            self.cache["v"] = 7
            self.dirty = True
        if self.cache.get("v", 1) < 8:
            # RT changed its search page, so those lookups found the page but not the score: redo them
            for t in self.cache["titles"].values():
                if t.get("rt") is None:
                    t["webChecked"] = 0
                    t["webRetryAt"] = 0
                    t["webFails"] = 0
            self.cache["v"] = 8
            self.dirty = True
        if self.cache.get("v", 1) < 9:
            # new: TV shows get Rotten Tomatoes (average of seasons) and Metacritic scores
            for t in self.cache["titles"].values():
                if (t.get("Type") == "series" and (t.get("rt") is None or t.get("mc") is None)) or \
                        (t.get("rt") is None and not t.get("rtUrl")):     # RT search misread titles with apostrophes
                    t["webChecked"] = 0
                    t["webRetryAt"] = 0
            self.cache["v"] = 9
            self.dirty = True
        if self.cache.get("v", 1) < 10:
            # Metacritic pages show other titles' scores too; earlier reads sometimes took one of those
            for t in self.cache["titles"].values():
                if t.get("Type") == "series":
                    t["mc"] = None
                    t.pop("mcUrl", None)
                    t["webChecked"] = 0
                    t["webRetryAt"] = 0
                elif t.get("mcUrl"):
                    t["fetched"] = 0          # let IMDb's Metascore settle it
                if t.get("Type") == "series" and t.get("rt") is None:
                    t["webChecked"] = 0
            self.cache["v"] = 10
            self.dirty = True
        threading.Thread(target=self._saver, daemon=True).start()

    def load_matches(self):
        """matches.json next to the app: {"<file or folder path>": "tt1234567"} - hand-picked matches."""
        f = HERE / "matches.json"
        try:
            mt = f.stat().st_mtime
        except OSError:
            return
        if mt == self._matches_mtime:
            return
        self._matches_mtime = mt
        data = load_json(f, {})
        with self.lock:
            for path, iid in data.items():
                if isinstance(iid, str) and re.match(r"^tt\d+$", iid.strip()):
                    if self.config["overrides"].get(path) != iid.strip():
                        self.config["overrides"][path] = iid.strip()
                        self.dirty = True

    def _saver(self):
        while True:
            time.sleep(1.5)
            self.flush()

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            self.dirty = False
            cache = json.loads(json.dumps(self.cache))
            config = dict(self.config)
        try:
            save_json(CACHE_FILE, cache)
            save_json(CONFIG_FILE, config)
        except OSError:
            pass

    def touch(self):
        self.dirty = True

    # -- omdb
    def omdb(self, **params):
        key = self.config.get("apiKey")
        if not key:
            raise ApiError("Add your free OMDb API key in Settings to load posters and scores.")
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["apikey"] = key
        url = OMDB_URL + "?" + urllib.parse.urlencode(params)
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                try:
                    data = json.loads(e.read().decode("utf-8"))
                    break
                except Exception:
                    last = e
            except Exception as e:
                last = e
            time.sleep(0.6 * (attempt + 1))
        else:
            raise ConnectionError(str(last))
        if data.get("Response") == "False":
            err = data.get("Error", "")
            if re.search(r"(?i)limit", err):
                self.limited_at = time.time()
                self.cache["limitedAt"] = self.limited_at
                self.touch()
                raise LimitError("OMDb's free daily limit (1,000 lookups) is used up. Posters and titles are coming "
                                 "from IMDb meanwhile; full details, plots and scores fill in after it resets.")
            if re.search(r"(?i)api key", err):
                raise ApiError("OMDb says: " + err)
            return None
        return data

    def omdb_ok(self):
        return bool(self.config.get("apiKey")) and not self.limited()

    @staticmethod
    def stale(hit):
        """Worth asking IMDb again: a stop-gap entry, one that came from OMDb with holes in it,
        or ratings older than a month."""
        if not hit:
            return True
        if hit.get("partial"):
            return True
        if hit.get("src") != "imdb":
            return hit.get("imdb") is None or hit.get("mc") is None or not hit.get("posterUrl") \
                or not hit.get("fetchedImdbTry") or time.time() - hit["fetchedImdbTry"] > 7 * 86400
        return time.time() - (hit.get("fetched") or 0) > 30 * 86400

    def by_id(self, imdb_id, force=False):
        with self.lock:
            hit = self.cache["titles"].get(imdb_id)
        if hit and not force and not self.stale(hit):
            return hit
        s, err = None, None
        try:
            s = imdb_title(imdb_id)                     # main source: IMDb itself
        except Exception as e:
            err = e
        if s is None and self.omdb_ok():               # backup: OMDb, if you've added a key
            try:
                d = self.omdb(i=imdb_id, plot="full")
                s = slim(d) if d else None
            except (ApiError, ConnectionError):
                pass
        if s is None:
            if hit:
                with self.lock:
                    hit["fetchedImdbTry"] = int(time.time())   # don't hammer; try again in a week
                    self.touch()
                return hit
            if err:
                d = self.web_only_id(imdb_id)
                if d:
                    return d
                raise err
            return None
        return self.save_title(imdb_id, s)

    def save_title(self, imdb_id, s):
        with self.lock:
            old = self.cache["titles"].get(imdb_id) or {}
            for k, v in old.items():                   # keep what IMDb doesn't have (RT score, awards...)
                if s.get(k) is None and v is not None:
                    if k == "mc" and s.get("src") == "imdb" and s.get("Type") == "movie":
                        continue                       # IMDb lists every movie Metascore; none means none
                    s[k] = v
            s.pop("partial", None)
            s["fetchedImdbTry"] = int(time.time())
            if s.get("src") == "imdb":
                s["fetched"] = int(time.time())
            self.cache["titles"][imdb_id] = s
            self.touch()
        return s

    def refresh_many(self, ids):
        try:
            got = imdb_titles(ids)
        except Exception:
            if len(ids) <= 25:
                raise
            half = len(ids) // 2                 # IMDb wanted a smaller batch: split it and carry on
            out = self.refresh_many(ids[:half])
            out.update(self.refresh_many(ids[half:]))
            return out
        out = {}
        for i in ids:
            if got.get(i):
                out[i] = self.save_title(i, got[i])
            else:
                with self.lock:
                    hit = self.cache["titles"].get(i)
                    if hit:
                        hit["fetchedImdbTry"] = int(time.time())
                        self.touch()
                out[i] = hit
        return out

    def limited(self):
        return time.time() - self.limited_at < 2 * 3600       # retry OMDb every couple of hours

    def web_only_id(self, imdb_id):
        for sug in imdb_suggest(imdb_id):
            if sug["id"] == imdb_id:
                return self.from_suggestion(sug)
        return None

    def web_only_match(self, title, year, kind):
        """Match without OMDb (daily limit reached): IMDb's search only. Not cached as a miss."""
        key = f"{kind}|{norm(title)}|{year or ''}"
        iid = self.cache["lookup"].get(key)
        if iid:
            return self.cache["titles"].get(iid) or self.web_only_id(iid)
        sug = best_suggestion(title, year, kind)
        if not sug:
            return None
        d = self.from_suggestion(sug)
        with self.lock:
            self.cache["lookup"][key] = sug["id"]
            self.touch()
        return d

    def from_suggestion(self, sug):
        """Use IMDb's own search data when OMDb doesn't know a title."""
        s = {"imdbID": sug["id"], "Title": sug["title"], "Year": str(sug["year"] or "") or None,
             "Actors": sug["stars"] or None, "Type": "series" if sug["kind"].lower().startswith("tv")
             and sug["kind"].lower() != "tvmovie" else "movie",
             "imdb": None, "rt": None, "mc": None, "posterUrl": sug["image"], "fetched": int(time.time()),
             "partial": True}
        with self.lock:
            old = self.cache["titles"].get(sug["id"])
            if old and not old.get("partial"):
                return old
            self.cache["titles"][sug["id"]] = s
            self.touch()
        return s

    def match(self, title, year, kind):
        key = f"{kind}|{norm(title)}|{year or ''}"
        old = None
        with self.lock:
            if key in self.recheck:
                old = self.cache["titles"].get(self.cache["lookup"].get(key) or "")
            elif key in self.cache["lookup"]:
                iid = self.cache["lookup"][key]
                if not iid:
                    return None
                hit = self.cache["titles"].get(iid)
                if hit and not self.stale(hit):
                    return hit
                return self.by_id(iid)
        found = None

        def ok(d, name):
            return d and d.get("imdbID") and plausible(name, year, d.get("Title"), d.get("Year"), d.get("Type"))

        # 1) IMDb's own search (free, no quota, copes with missing apostrophes, prefixes, etc.)
        sug = best_suggestion(title, year, kind)
        alt = re.sub(r"(?i)\s+\(?(US|UK|AU|NZ|CA)\)?$", "", title)
        if not sug and alt != title:
            sug = best_suggestion(alt, year, kind)
        if sug:
            found = self.by_id(sug["id"]) or self.from_suggestion(sug)
        # 2) backup: OMDb's searches, only if you've added a key
        if not found and self.omdb_ok():
            try:
                other = "movie" if kind == "series" else "series"
                for how, y, t in [("t", year, kind), ("t", None, kind), ("s", year, kind), ("t", year, other)]:
                    if how == "t":
                        d = self.omdb(t=title, y=y, type=t, plot="full")
                        if ok(d, title):
                            found = self.by_id(d["imdbID"]) or slim(d)
                            break
                    else:
                        res = (self.omdb(s=title, y=y, type=t) or {}).get("Search") or []
                        res = [r for r in res if tkey(r.get("Title")) == tkey(title)]
                        if res:
                            found = self.by_id(res[0]["imdbID"])
                            if found:
                                break
            except (ApiError, ConnectionError):
                pass
        if old and (not found or (found["imdbID"] != old["imdbID"] and obscure(found) and not obscure(old))):
            found = old                              # nothing clearly better: keep the earlier match
        with self.lock:
            if key in self.recheck:
                self.recheck.discard(key)
                self.cache["recheck"] = list(self.recheck)
            self.cache["lookup"][key] = found["imdbID"] if found else ""
            if found:
                self.cache["titles"][found["imdbID"]] = found
            self.touch()
        return found

    def poster_path(self, imdb_id, big=False):
        size = "H" if big is True else (big or "")
        return POSTER_DIR / f"{imdb_id}{'_' + size if size else ''}.jpg"

    def custom_poster(self, imdb_id):
        """A picture dropped into C:\\MovieGuide\\custom posters named <imdbID>.jpg/.png always wins."""
        for ext in COVER_EXTS:
            p = CUSTOM_POSTER_DIR / f"{imdb_id}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        return None


# --------------------------------------------------------------------------- library worker

class Library:
    def __init__(self, store):
        self.s = store
        self.pool = ThreadPoolExecutor(max_workers=100)      # 100 lookups at once
        self.poster_pool = ThreadPoolExecutor(max_workers=32)
        self.m_queued = set()
        self.poster_fail = {}       # (imdbID, big) -> time of last failure
        self.gen = 0
        self.events = []        # (seq, gen, item)
        self.seq = 0
        self.pending = 0
        self.total = 0
        self.error = None
        self.lock = threading.Lock()
        self.poster_locks = {}
        # web score lookups (Rotten Tomatoes / Metacritic / missing posters)
        self.web_pool = ThreadPoolExecutor(max_workers=16)
        self.web_q, self.web_seen, self.web_pending = [], set(), 0
        self.web_cv = threading.Condition()
        self.cur_gen, self.cur_index = 0, {}      # imdbID -> [items] in the open folder
        threading.Thread(target=self._web_loop, daemon=True).start()

    # ---- web enrichment
    @staticmethod
    def needs_web(d):
        if not d or not str(d.get("imdbID", "")).startswith("tt"):
            return False
        now = time.time()
        if now - (d.get("webChecked") or 0) < 30 * 86400:      # answered recently: don't ask again
            return False
        if now < (d.get("webRetryAt") or 0):                   # failed recently: wait its turn
            return False
        # IMDb already says whether there's a Metascore, so only titles from elsewhere need Metacritic checked
        return d.get("rt") is None or (d.get("mc") is None and (d.get("src") != "imdb" or d.get("Type") == "series")) \
            or not d.get("posterUrl")

    def track(self, gen, it):
        d = it.get("data")
        if not d:
            return
        if gen == self.cur_gen:
            lst = self.cur_index.setdefault(d["imdbID"], [])
            if all(x["id"] != it["id"] for x in lst):
                lst.append({k: v for k, v in it.items() if k != "data"})
        if self.needs_web(d):
            with self.web_cv:
                if d["imdbID"] not in self.web_seen:
                    self.web_seen.add(d["imdbID"])
                    self.web_q.append(d["imdbID"])
                    self.web_pending += 1
                    self.web_cv.notify()

    def _web_loop(self):
        while True:
            with self.web_cv:
                while not self.web_q:
                    self.web_cv.wait()
            time.sleep(0.2)                       # let a batch build up
            with self.web_cv:
                batch, self.web_q = self.web_q[:100], self.web_q[100:]
            links = wikidata_links(batch)
            for i in batch:                       # don't wait for this batch: start the next one right away
                self.web_pool.submit(self._enrich, i, links.get(i, {}))

    def _enrich(self, imdb_id, links):
        try:
            d = dict(self.s.cache["titles"].get(imdb_id) or {})
            if not d:
                return
            kind = "series" if d.get("Type") == "series" else "movie"
            year = (re.match(r"\d{4}", d.get("Year") or "") or [None])[0]
            upd, blocked, unsure = {}, False, False
            # fastest: scores Wikidata already holds (one request covers 100 titles)
            if d.get("rt") is None and links.get("rtScore") is not None:
                d["rt"] = upd["rt"] = links["rtScore"]
                if links.get("rt"):
                    upd["rtUrl"] = "https://www.rottentomatoes.com/" + links["rt"].lstrip("/")
            if d.get("mc") is None and links.get("mcScore") is not None:
                d["mc"] = upd["mc"] = links["mcScore"]
                if links.get("mc"):
                    upd["mcUrl"] = "https://www.metacritic.com/" + links["mc"].strip("/") + "/"
            if d.get("rt") is None:
                url = score = None
                try:
                    if links.get("rt"):
                        url = "https://www.rottentomatoes.com/" + links["rt"].lstrip("/")
                        html = RT.get(url)
                        if html is None:
                            url = None                      # page moved: fall back to RT's search
                        else:
                            score = parse_rt(html)
                            if score is None and not rt_really_none(html):
                                unsure = True            # odd page (busy/blocked?): try again next time
                                web_log(f"RT {url}: page had no readable score ({len(html)} bytes)")
                                dbg = DATA_DIR / "debug_rt_page.html"
                                try:
                                    dbg.write_text(url + "\n" + html, encoding="utf-8")
                                except OSError:
                                    pass
                    if score is None and not url and d.get("Title"):
                        url, score = rt_search(d["Title"], year, kind)
                        if url and score is None:
                            html = RT.get(url)
                            if html:
                                score = parse_rt(html)
                                if score is None and not rt_really_none(html):
                                    unsure = True
                                    web_log(f"RT {url}: page had no readable score ({len(html)} bytes)")
                except Blocked:
                    blocked = True
                except Exception as e:
                    unsure = True                        # a dropped download is not "no score"
                    web_log(f"RT {url}: {type(e).__name__}: {e}")
                if score is None:
                    score = links.get("rtScore")
                if url:
                    upd["rtUrl"] = url
                if score is not None:
                    upd["rt"] = score
            is_tv = d.get("Type") == "series"
            mc_link = links.get("mc") or (f"tv/{mc_slug(d.get('Title'))}" if is_tv and d.get("Title") else None)
            # IMDb already carries Metacritic for movies; TV shows (and anything Wikidata links) get checked directly
            if d.get("mc") is None and (d.get("src") != "imdb" or is_tv or links.get("mc")) and \
                    (mc_link or links.get("mcScore") is not None):
                score = None
                if mc_link:
                    url = "https://www.metacritic.com/" + mc_link.strip("/") + "/"
                    upd["mcUrl"] = url
                    try:
                        html = MC.get(url)
                        score = parse_mc(html) if html else None
                        if html is None:
                            upd["mcUrl"] = None             # guessed address doesn't exist
                    except Blocked:
                        blocked = True
                    except Exception as e:
                        unsure = True
                        web_log(f"MC {url}: {type(e).__name__}: {e}")
                if score is None:
                    score = links.get("mcScore")
                if score is not None:
                    upd["mc"] = score
            if not blocked and not unsure:
                upd["webChecked"] = int(time.time())     # settled: found, or the site truly has none
                upd["webFails"] = 0
                upd["webRetryAt"] = 0
            else:                                        # a hiccup: retry later, waiting longer each time
                n = int(d.get("webFails") or 0)
                upd["webFails"] = n + 1
                upd["webRetryAt"] = int(time.time() + [1800, 6 * 3600, 86400, 3 * 86400, 7 * 86400][min(n, 4)])
            if not d.get("posterUrl") or not d.get("Actors"):
                for sug in imdb_suggest(imdb_id):
                    if sug["id"] == imdb_id:
                        if not d.get("posterUrl") and sug["image"]:
                            upd["posterUrl"] = sug["image"]
                        if not d.get("Actors") and sug["stars"]:
                            upd["Actors"] = sug["stars"]
                        break
            with self.s.lock:
                cur = self.s.cache["titles"].get(imdb_id)
                if cur is None:
                    return
                cur.update(upd)
                new = dict(cur)
                self.s.touch()
            if upd.get("posterUrl"):
                self.poster_pool.submit(self.ensure_poster, imdb_id, "M")
            gen = self.cur_gen
            for base in list(self.cur_index.get(imdb_id, [])):
                self.push(gen, dict(base, status="ok", data=new))
        finally:
            with self.web_cv:
                self.web_pending = max(0, self.web_pending - 1)
                self.web_seen.discard(imdb_id)

    def resolve(self, it):
        """Attach cached metadata to a scanned item (no network)."""
        ov = self.s.config["overrides"].get(it["path"])
        data = None
        if ov:
            data = self.s.cache["titles"].get(ov)
        else:
            key = f"{it['type']}|{norm(it['title'])}|{it['year'] or ''}"
            if key in self.s.cache["lookup"]:
                iid = self.s.cache["lookup"][key]
                data = self.s.cache["titles"].get(iid) if iid else None
                if key in self.s.recheck:
                    return dict(it, status="pending", data=data, recheck=True)
                if not iid:
                    return dict(it, status="missing", data=None)
        if data and self.s.stale(data):
            return dict(it, status="ok", data=data, refresh=True)     # show it now, refresh quietly
        return dict(it, status="ok" if data else "pending", data=data)

    def with_custom(self, item):
        d = item.get("data") if isinstance(item, dict) else None
        # IMDb knows best: a folder that looked like a show but is really a movie goes to Movies
        # (a folder of several shorts, like a director's collection, stays a show)
        if d and d.get("Type") == "movie" and item.get("type") == "series" and \
                (item.get("lib") in ("auto", "mixed") or (item.get("eps") or 0) <= 3):
            item = dict(item, type="movie", play=item.get("main") or item.get("play"))
        if d and d.get("imdbID") and not d.get("posterUrl") and self.s.custom_poster(d["imdbID"]):
            item = dict(item, data=dict(d, posterUrl="custom"))
        return item

    def push(self, gen, item):
        if item.get("__replace__"):
            item = dict(item, items=[self.with_custom(x) for x in item["items"]])
        else:
            item = self.with_custom(item)
        with self.lock:
            self.seq += 1
            self.events.append((self.seq, gen, item))
            if len(self.events) > 5000:
                self.events = self.events[-2500:]

    def open_library(self, paths, kind="auto"):
        """Return cached items instantly, then rescan every path + fetch in the background."""
        with self.lock:
            self.gen += 1
            gen = self.gen
            self.error = None
            seq = self.seq          # capture before the worker can push anything
        pairs = [(p, kind) if isinstance(p, str) else (p[0], p[1]) for p in paths]
        paths = [p for p, _ in pairs]
        scans = [self.s.cache["scans"].get(p) for p in paths]
        items = None
        if any(sc is not None for sc in scans):
            items = [self.with_custom(self.resolve(i)) for sc in scans if sc for i in sc]
        threading.Thread(target=self._refresh, args=(pairs, gen, items is None), daemon=True).start()
        return {"gen": gen, "items": items, "seq": seq}

    def _scan_one(self, path, kind="auto"):
        if not os.path.isdir(path):
            return path, None                       # offline: keep last known list
        skipped = []
        scanned = scan_folder(path, kind, skipped)
        for it in scanned:
            it["lib"] = kind
        with self.s.lock:
            self.s.cache.setdefault("skipped", {})[path] = skipped
            changed = self.s.cache["scans"].get(path) != scanned
            self.s.cache["scans"][path] = scanned
            if changed:
                self.s.touch()
        return path, changed

    def _refresh(self, pairs, gen, first):
        paths = [p for p, _ in pairs]
        results = list(ThreadPoolExecutor(max_workers=max(1, len(pairs))).map(
            lambda pk: self._scan_one(pk[0], pk[1]), pairs))
        if gen != self.gen:
            return
        offline = [p for p, ch in results if ch is None]
        changed = any(ch for _, ch in results)
        resolved = [self.resolve(i) for p in paths for i in (self.s.cache["scans"].get(p) or [])]
        self.cur_gen, self.cur_index = gen, {}
        for i in resolved:
            self.track(gen, i)
        self.prefetch_posters(resolved)
        if changed or first or offline:
            self.push(gen, {"__replace__": True, "items": resolved, "offline": offline})
        todo = [i for i in resolved if i["status"] == "pending"]
        stale = {}
        for i in resolved:
            if i.get("refresh") and i.get("data"):
                stale.setdefault(i["data"]["imdbID"], []).append(i)
        with self.lock:
            self.pending = self.total = len(todo) + sum(len(v) for v in stale.values())
        ids = list(stale)
        for n in range(0, len(ids), 100):             # 100 titles per IMDb request
            chunk = ids[n:n + 100]
            self.pool.submit(self._refresh_batch, gen, {k: stale[k] for k in chunk})
        for it in todo:
            self.pool.submit(self._fetch, gen, it)

    def _refresh_batch(self, gen, group):
        if gen != self.gen:
            return
        new = None
        if not Library.batch_broken:
            try:
                new = self.s.refresh_many(list(group))
            except Exception:
                Library.batch_broken = True           # fall back to one title per request
        if new is None:
            for its in group.values():
                for it in its:
                    self.pool.submit(self._fetch, gen, dict(it, status="pending"))
            return
        for iid, its in group.items():
            d = new.get(iid)
            for it in its:
                try:
                    out = {k: v for k, v in it.items() if k != "refresh"}
                    out.update(status="ok" if d else "missing", data=d)
                    self.track(gen, out)
                    if d and d.get("posterUrl"):
                        self.poster_pool.submit(self.ensure_poster, iid, "M")
                    self.push(gen, out)
                finally:
                    with self.lock:
                        self.pending = max(0, self.pending - 1)

    batch_broken = False

    def _fetch(self, gen, it, force=False):
        try:
            if gen != self.gen:
                return
            ov = self.s.config["overrides"].get(it["path"])
            try:
                if ov:
                    data = self.s.by_id(ov, force=force)
                else:
                    data = self.s.match(it["title"], it["year"], it["type"])
            except LimitError as e:
                self.error = str(e)
                data = self.s.web_only_id(ov) if ov else self.s.web_only_match(it["title"], it["year"], it["type"])
            out = dict(it, status="ok" if data else "missing", data=data)
            self.track(gen, out)
            if data and data.get("posterUrl"):
                self.poster_pool.submit(self.ensure_poster, data["imdbID"], "M")
            self.push(gen, out)
        except ApiError as e:
            self.error = str(e)
        except Exception:
            self.push(gen, dict(it, status="error", data=None))
        finally:
            with self.lock:
                self.pending = max(0, self.pending - 1)

    def refetch(self, it):
        with self.lock:
            self.pending += 1
            self.total = max(self.total, self.pending)
        key = f"{it['type']}|{norm(it['title'])}|{it['year'] or ''}"
        with self.s.lock:
            iid = self.s.config["overrides"].get(it["path"]) or self.s.cache["lookup"].get(key)
            if iid and iid in self.s.cache["titles"]:
                self.s.cache["titles"][iid]["webChecked"] = 0     # re-check RT / Metacritic too
                for k in ("rt", "mc"):
                    self.s.cache["titles"][iid].pop(k, None)
            self.s.cache["lookup"].pop(key, None)
        self.pool.submit(self._fetch, self.gen, it, True)

    def poll(self, gen, since):
        if self.pending == 0 and getattr(self, "reported", None) != gen and gen == self.gen and getattr(self, "app", None):
            self.reported = gen                       # everything looked up: refresh library_check.txt
            threading.Thread(target=self.app.library_check, daemon=True).start()
        with self.lock:
            ev = [e[2] for e in self.events if e[0] > since and e[1] == gen]
            return {"events": ev, "seq": self.seq, "pending": self.pending, "webPending": self.web_pending,
                    "total": self.total, "error": self.error, "gen": self.gen}

    def prefetch_posters(self, items):
        """Download every missing poster in the background so scrolling never waits."""
        for it in items:
            d = it.get("data")
            if d and d.get("imdbID") and not self.s.poster_path(d["imdbID"], "M").exists():
                self.poster_pool.submit(self.ensure_poster, d["imdbID"], "M")

    def _download(self, url, path):
        with urllib.request.urlopen(urllib.request.Request(url, headers=BROWSER), timeout=15) as r:
            blob = r.read()
        if len(blob) < 500 or blob[:3] != b"\xff\xd8\xff" and blob[:4] != b"\x89PNG":
            raise ValueError("not an image")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        return path

    SIZES = {"M": "SX600", "H": "SX2000"}      # card size / full-screen (near-4K tall) size

    @staticmethod
    def sized(url, size):
        if not size or not url:
            return url
        return re.sub(r"\._V1_.*?\.jpg$", "._V1_" + Library.SIZES[size] + ".jpg", url)

    def ensure_poster(self, imdb_id, big=False, quick=False):
        big = "H" if big is True else (big or "")
        custom = self.s.custom_poster(imdb_id)
        if custom:
            return custom
        path = self.s.poster_path(imdb_id, big)
        if path.exists() and path.stat().st_size > 0:
            return path
        if quick and big == "M":
            # never make a card wait: show the poster we already have, fetch the sharper one meanwhile
            small = self.s.poster_path(imdb_id, "")
            if small.exists() and small.stat().st_size > 0:
                if imdb_id not in self.m_queued:
                    self.m_queued.add(imdb_id)
                    self.poster_pool.submit(self.ensure_poster, imdb_id, "M")
                return small
        if time.time() - self.poster_fail.get((imdb_id, big), 0) < 90:
            return self.ensure_poster(imdb_id, False) if big else None
        lock = self.poster_locks.setdefault((imdb_id, big), threading.Lock())
        with lock:
            if path.exists() and path.stat().st_size > 0:
                return path
            data = self.s.cache["titles"].get(imdb_id) or {}
            urls = []
            if data.get("posterUrl"):
                u = data["posterUrl"]
                urls.append(self.sized(u, big))
            try:
                if big and urls:
                    return self._download(urls[0], path)
                for u in urls:
                    try:
                        return self._download(u, path)
                    except Exception:
                        pass
                # OMDb's link is dead or missing: ask IMDb for the poster instead
                for sug in imdb_suggest(imdb_id):
                    if sug["id"] == imdb_id and sug["image"] and self.sized(sug["image"], big) not in urls:
                        self._download(self.sized(sug["image"], big), path)
                        with self.s.lock:
                            if imdb_id in self.s.cache["titles"]:
                                self.s.cache["titles"][imdb_id]["posterUrl"] = sug["image"]
                                self.s.touch()
                        return path
                raise ValueError("no poster")
            except Exception:
                self.poster_fail[(imdb_id, big)] = time.time()
                if big:
                    return self.ensure_poster(imdb_id, False)
                return None


# --------------------------------------------------------------------------- app api

class App:
    window = None

    def __init__(self):
        self.store = Store()
        self.lib = Library(self.store)
        self.lib.app = self
        self._ep_cache = {}

    # libraries = tabs in the sidebar; each can hold several folders
    def libs(self):
        return self.store.config["libraries"]

    def find_lib(self, lid):
        return next((l for l in self.libs() if l["id"] == lid), None)

    def state(self, _=None):
        c = self.store.config
        scans = self.store.cache["scans"]
        libs = [dict(l, count=sum(len(scans.get(p) or []) for p in l["paths"])) for l in self.libs()]
        return {"libraries": libs, "current": c["current"], "hasKey": bool(c["apiKey"]),
                "keyHint": (c["apiKey"][:2] + "•••••") if c["apiKey"] else "",
                "native": App.window is not None, "titles": c.get("titles", {}),
                "lastWatched": c.get("lastWatched", {}), **self._covers()}

    def _resolve_path(self, a):
        path = a.get("path")
        if a.get("browse") and App.window is not None:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
            res = App.window.create_file_dialog(kind)
            path = res[0] if res else None
        if not path:
            return None, None
        raw = path.strip().strip('"').strip()
        if raw.startswith("//"):
            raw = raw.replace("/", "\\")
        # Explorer labels look like "Movies A-M (\\glados)" or "... (Glados)" - try without the label too
        cands = [raw, re.sub(r"\s*\([^()]*\)\s*$", "", raw)]
        m = re.match(r"^(.*?)\s*\((\\\\[^()]+)\)(?:\s*\([A-Za-z]:\))?$", raw)
        if m:
            cands.append(m.group(2).rstrip("\\") + "\\" + m.group(1).strip())
        m = re.search(r"\(([A-Za-z]:)\)\s*$", raw)
        if m:
            cands.append(m.group(1) + "\\")
        found = next((os.path.normpath(c) for c in cands if c and os.path.isdir(os.path.normpath(c))), None)
        if not found:
            srv = re.match(r"^(\\\\[^\\]+)", raw)
            hint = (f" Type {srv.group(1)} into File Explorer's address bar to see the exact share names."
                    if srv else "")
            return None, f"Couldn't open {raw}. Check the drive/NAS is on and the name is right.{hint}"
        return found, None

    def check_folder(self, a):
        path, err = self._resolve_path(a or {})
        return {"error": err} if err else {"path": path} if path else {"cancelled": True}

    def add_folder(self, a):
        """Add a folder as a new tab, or into an existing tab (a['library'])."""
        a = a or {}
        path, err = self._resolve_path(a)
        if err:
            return dict(self.state(), error=err)
        if not path:
            return dict(self.state(), cancelled=True)
        with self.store.lock:
            lib = self.find_lib(a.get("library")) if a.get("library") else None
            if lib:
                if path not in lib["paths"]:
                    lib["paths"].append(path)
            else:
                name = (a.get("name") or "").strip() or os.path.basename(path.rstrip("\\/")) or path
                kind = a.get("kind") if a.get("kind") in ("auto", "tv", "mixed") else default_kind(name)
                if kind == "auto" and a.get("browse"):      # picked with Browse: guess from the folder's name
                    kind = default_kind(name)
                lib = {"id": "L" + hashlib.sha1((path + str(time.time())).encode()).hexdigest()[:8],
                       "name": name, "paths": [path], "kind": kind}
                self.libs().append(lib)
            self.store.config["current"] = lib["id"]
            self.store.touch()
        return dict(self.state(), added=lib["id"], path=path)

    def update_library(self, a):
        with self.store.lock:
            lib = self.find_lib(a["id"])
            if lib:
                if a.get("name") is not None and a["name"].strip():
                    lib["name"] = a["name"].strip()
                if a.get("paths") is not None:
                    lib["paths"] = [p for p in a["paths"] if p]
                if a.get("kind") in ("auto", "tv", "mixed"):
                    lib["kind"] = a["kind"]
                self.store.touch()
        return self.state()

    def remove_library(self, a):
        with self.store.lock:
            libs = self.libs()
            libs[:] = [l for l in libs if l["id"] != a["id"]]
            if self.store.config["current"] == a["id"]:
                self.store.config["current"] = libs[0]["id"] if libs else None
            self.store.touch()
        return self.state()

    def merge_library(self, a):
        """Drop one tab onto another: its folders join that tab."""
        with self.store.lock:
            src, dst = self.find_lib(a["from"]), self.find_lib(a["into"])
            if src and dst and src is not dst:
                dst["paths"] += [p for p in src["paths"] if p not in dst["paths"]]
                self.libs().remove(src)
                self.store.config["current"] = dst["id"]
                self.store.touch()
        return self.state()

    def reorder(self, a):
        with self.store.lock:
            order = {lid: i for i, lid in enumerate(a["ids"])}
            self.libs().sort(key=lambda l: order.get(l["id"], 999))
            self.store.touch()
        return self.state()

    def select(self, a):
        lib = self.find_lib(a["id"])
        if not lib:
            return {"error": "Library not found"}
        self.store.load_matches()
        with self.store.lock:
            self.store.config["current"] = lib["id"]
            self.store.touch()
        return self.lib.open_library(lib["paths"], lib.get("kind", "auto"))

    def open_all(self, a=None):
        """Every folder from every library at once - the UI splits them into Movies and Shows."""
        self.store.load_matches()
        pairs, seen = [], set()
        for l in self.libs():
            for p in l["paths"]:
                if p not in seen:
                    seen.add(p)
                    pairs.append((p, l.get("kind", "auto")))
        return dict(self.lib.open_library(pairs), folders=self.folders())

    def folders(self, a=None):
        return [{"path": p, "kind": l.get("kind", "auto"), "lib": l["id"]} for l in self.libs() for p in l["paths"]]

    def remove_folder(self, a):
        with self.store.lock:
            for l in list(self.libs()):
                if a["path"] in l["paths"]:
                    l["paths"].remove(a["path"])
                    if not l["paths"]:
                        self.libs().remove(l)
            self.store.touch()
        return {"folders": self.folders()}

    # ---------------------------------------------------------------- playlists & pinned cards
    def playlists(self, a=None):
        c = self.store.config
        return {"playlists": c.get("playlists") or [], "pinCards": c.get("pinCards") or []}

    def playlist_edit(self, a):
        """One endpoint for every playlist change: create, rename, delete, add, remove, move."""
        op = a.get("op")
        with self.store.lock:
            pls = self.store.config.setdefault("playlists", [])
            pl = next((p for p in pls if p["id"] == a.get("id")), None)
            if op == "create":
                pl = {"id": "P" + hashlib.sha1(f"{time.time()}{a.get('name')}".encode()).hexdigest()[:8],
                      "name": (a.get("name") or "New playlist").strip()[:80], "items": [], "created": int(time.time())}
                pls.append(pl)
                if a.get("item"):
                    pl["items"].append(dict(a["item"], added=int(time.time())))
            elif pl and op == "color":
                pl["hue"] = int(a.get("hue") or 0) % 360
            elif pl and op == "rename":
                pl["name"] = (a.get("name") or pl["name"]).strip()[:80]
            elif pl and op == "delete":
                pls.remove(pl)
            elif pl and op == "add" and a.get("item"):
                if not any(i["key"] == a["item"]["key"] for i in pl["items"]):
                    pl["items"].append(dict(a["item"], added=int(time.time())))
            elif pl and op == "remove":
                pl["items"] = [i for i in pl["items"] if i["key"] != a.get("key")]
            elif pl and op == "move":
                idx = next((n for n, i in enumerate(pl["items"]) if i["key"] == a.get("key")), None)
                if idx is not None:
                    to = max(0, min(len(pl["items"]) - 1, idx + int(a.get("by") or 0)))
                    pl["items"].insert(to, pl["items"].pop(idx))
            self.store.touch()
        return dict(self.playlists(), id=pl["id"] if pl and op != "delete" else None)

    def pin_card(self, a):
        with self.store.lock:
            pins = self.store.config.setdefault("pinCards", [])
            if a["key"] in pins:
                pins.remove(a["key"])
            else:
                pins.insert(0, a["key"])
            self.store.touch()
        return self.playlists()

    RATERS = ["Danny", "Adam", "Chris"]

    def ratings(self, a=None):
        c = self.store.config
        return {"ratings": c.get("myRatings") or {}, "raters": c.get("raters") or self.RATERS}

    def rate(self, a):
        """One person's rating for a title: 0.5-10 in half points (10 stars); 0/None clears it.
        Stored as myRatings[key] = {"by": {"Danny": 8, "Adam": 7}, imdbID, title, t}."""
        key, who = a.get("key"), (a.get("who") or "").strip()
        if not key or not who:
            return self.ratings()
        try:
            r = max(0.0, min(10.0, round(float(a.get("rating") or 0) * 2) / 2))   # half points: 7.5
            r = int(r) if r == int(r) else r
        except (TypeError, ValueError):
            r = 0
        with self.store.lock:
            rs = self.store.config.setdefault("myRatings", {})
            e = rs.get(key) or {}
            by = dict(e.get("by") or {})
            if r:
                by[who] = r
            else:
                by.pop(who, None)
            if by:
                e.update(by=by, imdbID=a.get("imdbID") or e.get("imdbID"), title=a.get("title") or e.get("title"), t=int(time.time()))
                e.pop("r", None)          # the old single "your rating" from before named raters
                rs[key] = e
            else:
                rs.pop(key, None)
            self.store.touch()
        return self.ratings()

    def pins(self, a=None):
        return {"pins": self.store.config.get("pins") or {}}

    def pin_toggle(self, a):
        """Pin / unpin an episode file to the top of its show."""
        with self.store.lock:
            pins = self.store.config.setdefault("pins", {})
            if a["path"] in pins:
                del pins[a["path"]]
            else:
                pins[a["path"]] = {"show": a.get("show"), "season": a.get("season"), "episode": a.get("episode"),
                                   "title": a.get("title"), "t": int(time.time())}
            self.store.touch()
        return {"pins": pins}

    def set_folder_kind(self, a):
        kind = a.get("kind")
        if kind not in ("auto", "tv", "mixed"):
            return {"folders": self.folders()}
        with self.store.lock:
            for l in list(self.libs()):
                if a["path"] in l["paths"]:
                    if len(l["paths"]) == 1:
                        l["kind"] = kind
                    else:                       # split it out so the other folders keep their type
                        l["paths"].remove(a["path"])
                        self.libs().append({"id": "L" + hashlib.sha1((a["path"] + str(time.time())).encode()).hexdigest()[:8],
                                            "name": os.path.basename(a["path"].rstrip("\\/")) or a["path"],
                                            "paths": [a["path"]], "kind": kind})
            self.store.touch()
        return {"folders": self.folders()}

    def poll(self, a):
        return self.lib.poll(a["gen"], a["since"])

    def library_check(self, a=None):
        """What's in each folder, what got left out and why, and what couldn't be identified."""
        out, lines = [], []
        c = self.store.cache
        for f in self.folders():
            items = c["scans"].get(f["path"]) or []
            skipped = (c.get("skipped") or {}).get(f["path"], [])
            unmatched = []
            for it in items:
                r = self.lib.resolve(it)
                if not r.get("data") and r.get("status") == "missing":
                    unmatched.append({"name": it["name"], "path": it["path"], "title": it["title"], "year": it["year"]})
            movies = sum(1 for it in items if it["type"] == "movie")
            out.append({"path": f["path"], "kind": f["kind"], "items": len(items), "movies": movies,
                        "shows": len(items) - movies, "skipped": skipped, "unmatched": unmatched,
                        "scanned": f["path"] in c["scans"]})
            lines.append(f"{f['path']}  ({f['kind']}): {len(items)} found ({movies} movies, {len(items) - movies} shows)")
            for sk in skipped:
                lines.append(f"   left out: {sk['name']}  -  {sk['reason']}")
            for u in unmatched:
                lines.append(f"   not identified: {u['name']}")
        try:
            (DATA_DIR / "library_check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
        return {"folders": out}

    def set_key(self, a):
        key = (a.get("key") or "").strip()
        old = self.store.config["apiKey"]
        self.store.config["apiKey"] = key
        try:
            ok = self.store.omdb(i="tt0133093") is not None
        except ApiError as e:
            self.store.config["apiKey"] = old
            return {"ok": False, "error": str(e)}
        except Exception as e:
            self.store.config["apiKey"] = old
            return {"ok": False, "error": "Couldn't reach OMDb: " + str(e)}
        with self.store.lock:
            self.store.cache["lookup"] = {k: v for k, v in self.store.cache["lookup"].items() if v}
            self.store.touch()
        self.lib.error = None
        return {"ok": ok, "state": self.state()}

    def search(self, a):
        try:
            sug = imdb_suggest(a["q"], strict=True)
        except Exception as e:
            sug, err = [], e
        res = []
        for r in sug:
            k = (r["kind"] or "").lower()
            typ = "series" if k.startswith("tv") and k not in ("tvmovie", "tvspecial", "tvshort") else "movie"
            if a.get("type") and a["type"] != typ:
                continue
            res.append({"imdbID": r["id"], "Title": r["title"], "Year": str(r["year"] or ""),
                        "Type": typ, "poster": r["image"]})
        if res or not self.store.omdb_ok():
            return {"results": res} if res or sug else {"results": [], "error": None}
        try:
            d = self.store.omdb(s=a["q"], type=a.get("type") or None, y=a.get("year") or None)
        except LimitError:
            res = [{"imdbID": r["id"], "Title": r["title"], "Year": str(r["year"] or ""),
                    "Type": "series" if r["kind"].lower().startswith("tv") and r["kind"].lower() != "tvmovie"
                    else r["kind"] or "movie", "poster": r["image"]} for r in imdb_suggest(a["q"])]
            return {"results": res}
        except ApiError as e:
            return {"error": str(e), "results": []}
        except Exception as e:
            return {"error": str(e), "results": []}
        res = []
        for r in (d or {}).get("Search", [])[:20]:
            p = r.get("Poster")
            res.append({"imdbID": r.get("imdbID"), "Title": r.get("Title"), "Year": r.get("Year"),
                        "Type": r.get("Type"), "poster": p if p and p != "N/A" else None})
        return {"results": res}

    def set_match(self, a):
        it = a["item"]
        with self.store.lock:
            self.store.config["overrides"][it["path"]] = a["imdbID"]
            self.store.touch()
        try:
            data = self.store.by_id(a["imdbID"])
        except Exception as e:
            return {"error": str(e)}
        out = dict(it, status="ok" if data else "missing", data=data)
        if data:
            self.lib.poster_pool.submit(self.lib.ensure_poster, data["imdbID"], "M")
            self.lib.track(self.lib.cur_gen, out)
        return {"item": out}

    def refetch(self, a):
        self.lib.refetch(a["item"])
        return {"ok": True}

    def details(self, a):
        try:
            data = self.store.by_id(a["imdbID"])
        except Exception as e:
            return {"error": str(e)}
        return self.lib.with_custom({"data": data})

    def episodes(self, a):
        """Seasons and episode files for a show (scanned on demand, remembered for the session)."""
        path, title = a["path"], a.get("title") or ""
        key = path
        hit = self._ep_cache.get(key)
        if hit and time.time() - hit[0] < 300 and not a.get("refresh"):
            return {"seasons": hit[1]}
        if not os.path.exists(path):
            return {"seasons": [], "error": "Can't reach this show's folder right now."}
        seasons = list_episodes(path, title)
        self._ep_cache[key] = (time.time(), seasons)
        return {"seasons": seasons}

    def season(self, a):
        """Episode names and ratings for one season, from OMDb (cached)."""
        iid, sn = a["imdbID"], int(a["season"])
        ck = f"{iid}|{sn}"
        seasons = self.store.cache.setdefault("seasons", {})
        hit = seasons.get(ck)
        # only IMDb's own episode lists count as "known" (older OMDb ones were missing ratings)
        if hit and hit.get("src") == "imdb" and time.time() - hit.get("t", 0) < (14 * 86400 if hit.get("eps") else 6 * 3600):
            return {"episodes": hit["eps"]}
        eps = None
        try:
            eps = imdb_season(iid, sn)
        except Exception:
            eps = None
        if eps is not None:
            with self.store.lock:
                seasons[ck] = {"t": time.time(), "eps": eps, "src": "imdb"}
                self.store.touch()
            return {"episodes": eps}
        if not self.store.omdb_ok():
            return {"episodes": hit["eps"] if hit else []}
        try:
            d = self.store.omdb(i=iid, Season=sn)
        except Exception:
            return {"episodes": hit["eps"] if hit else []}
        eps = []
        for x in (d or {}).get("Episodes", []) or []:
            try:
                n = int(x.get("Episode"))
            except (TypeError, ValueError):
                continue
            r = x.get("imdbRating")
            eps.append({"episode": n, "title": x.get("Title"), "released": x.get("Released"),
                        "imdb": float(r) if r and r != "N/A" else None, "imdbID": x.get("imdbID")})
        with self.store.lock:
            seasons[ck] = {"t": time.time(), "eps": eps}
            self.store.touch()
        return {"episodes": eps}

    def play(self, a):
        target = a.get("play") or a.get("path")
        # English-language titles: open in VLC with English audio and subtitles off
        if target and self.store.config.get("englishVLC", True) and Path(target).suffix.lower() in VIDEO_EXT:
            try:
                if self._lang_of(target).lower() == "english":
                    vlc = _find_vlc()
                    if vlc:
                        subprocess.Popen([vlc, "--audio-language=en,eng", "--no-spu", target], close_fds=True,
                                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                        return {"ok": True, "english": True}
            except Exception:
                pass                                   # anything odd: just open it the normal way
        return self._open(target)

    def _lang_of(self, path):
        """Main spoken language of the movie / show a file belongs to, from its IMDb data ("" if unknown)."""
        f = os.path.normcase(os.path.abspath(path))
        best = None
        for items in self.store.cache["scans"].values():
            for it in items or []:
                ip = os.path.normcase(os.path.abspath(it["path"]))
                if (f == ip or f.startswith(ip.rstrip("\\/") + os.sep)) and (not best or len(ip) > len(best[0])):
                    best = (ip, it)
        if not best:
            return ""
        d = (self.lib.resolve(best[1]) or {}).get("data") or {}
        return (d.get("Language") or "").split(",")[0].strip()

    def reveal(self, a):
        p = a["path"]
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
            return {"ok": True}
        return self._open(p if os.path.isdir(p) else os.path.dirname(p))

    def open_url(self, a):
        webbrowser.open(a["url"])
        return {"ok": True}

    def _open(self, p):
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- auto-update (GitHub Releases)
def _ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


class Updater:
    """Checks GitHub for a newer release and swaps the running exe for it."""
    API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    ASSET = "MovieGuide.exe"
    EVERY = 6 * 3600

    def __init__(self):
        self.info = {"current": VERSION, "available": False, "frozen": FROZEN}
        self.lock = threading.Lock()
        self.busy = False
        self._cleanup()

    def _cleanup(self):
        if FROZEN:
            for f in (Path(sys.executable).with_suffix(".old"), Path(sys.executable).with_suffix(".new")):
                try:
                    f.unlink()
                except OSError:
                    pass

    def start(self):
        def loop():
            time.sleep(3)
            while True:
                self.check()
                time.sleep(self.EVERY)
        threading.Thread(target=loop, daemon=True).start()

    def check(self):
        try:
            req = urllib.request.Request(self.API, headers={"User-Agent": f"MovieGuide/{VERSION}",
                                                            "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rel = json.loads(r.read().decode("utf-8"))
            tag = rel.get("tag_name", "")
            asset = next((a for a in rel.get("assets", []) if a.get("name") == self.ASSET), None)
            info = {"current": VERSION, "latest": tag.lstrip("vV"), "notes": (rel.get("body") or "")[:2000],
                    "page": rel.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
                    "url": asset and asset.get("browser_download_url"),
                    "size": asset and asset.get("size"), "frozen": FROZEN,
                    "available": _ver(tag) > _ver(VERSION) and bool(asset)}
        except Exception as e:
            info = dict(self.info, error=f"{type(e).__name__}: {e}")
        with self.lock:
            self.info = info
        return info

    def install(self):
        """Download the new exe, rename the running one aside, put the new one in place, relaunch."""
        info = self.info
        if not info.get("available"):
            return {"error": "No update available."}
        if not FROZEN:
            webbrowser.open(info["page"])
            return {"ok": True, "opened": True}
        if self.busy:
            return {"error": "Already updating."}
        self.busy = True
        exe = Path(sys.executable)
        new, old = exe.with_suffix(".new"), exe.with_suffix(".old")
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": f"MovieGuide/{VERSION}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(new, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if info.get("size") and new.stat().st_size != info["size"]:
                raise IOError("download was incomplete")
            if old.exists():
                old.unlink()
            exe.rename(old)            # Windows lets a running exe be renamed, just not overwritten
            new.rename(exe)
        except Exception as e:
            self.busy = False
            try:
                if not exe.exists() and old.exists():
                    old.rename(exe)
                new.unlink()
            except OSError:
                pass
            return {"error": f"Update failed: {e}"}

        def relaunch():
            time.sleep(0.5)
            env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env, close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            try:
                App.store_ref.dirty = True
                App.store_ref.flush()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=relaunch, daemon=True).start()
        return {"ok": True, "restarting": True}


UPDATER = Updater()


def _update_check(self, a=None):
    return UPDATER.check() if (a or {}).get("now") else UPDATER.info


def _update_install(self, a=None):
    return UPDATER.install()


App.update_check = _update_check
App.update_install = _update_install
App.store_ref = None


# ---------------------------------------------------------------- display size (same proportions on any screen)
def _get_zoom(self, a=None):
    return {"zoom": getattr(App, "_zoom", 1.0), "scale": self.store.config.get("uiScale", "auto"),
            "native": App.window is not None}


def _set_zoom(self, a):
    """Real browser zoom on the WebView2 control, so every part of the page scales together."""
    a = a or {}
    if "scale" in a:                                   # the user's choice: "auto" or a fixed factor
        sc = a["scale"]
        self.store.config["uiScale"] = sc if sc == "auto" else max(0.5, min(3.0, float(sc)))
        self.store.touch()
    if "zoom" not in a:
        return _get_zoom(self)
    z = max(0.5, min(3.0, float(a["zoom"])))
    form = getattr(App.window, "native", None) if App.window is not None else None
    view = getattr(form, "webview", None)
    if view is None:
        return {"ok": False, "zoom": getattr(App, "_zoom", 1.0)}
    try:
        from System import Action

        def apply():
            view.ZoomFactor = z
        if form.InvokeRequired:
            form.Invoke(Action(apply))
        else:
            apply()
        App._zoom = z
        return {"ok": True, "zoom": z}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "zoom": getattr(App, "_zoom", 1.0)}


App.get_zoom = _get_zoom
App.set_zoom = _set_zoom
App._zoom = 1.0


# ---------------------------------------------------------------- your own cover art
def _covers(self):
    """Which titles have a custom cover, plus a revision number per title so the app reloads the picture."""
    rev = self.store.config.setdefault("coverRev", {})
    have = {}
    try:
        for f in CUSTOM_POSTER_DIR.iterdir():
            if f.suffix.lower() in COVER_EXTS and re.match(r"^tt\d+$", f.stem):
                have[f.stem] = rev.get(f.stem) or int(f.stat().st_mtime)
    except OSError:
        pass
    return {"covers": have, "coverRev": rev}


def _image_kind(b):
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    return None


def _set_cover(self, a):
    iid = (a.get("imdbID") or "").strip()
    if not re.match(r"^tt\d{5,}$", iid):
        return {"error": "This title isn't matched to IMDb yet. Use Fix match first."}
    data = None
    try:
        if a.get("browse"):
            if App.window is None:
                return {"error": "File picker only works in the app window."}
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
            res = App.window.create_file_dialog(kind, file_types=("Image files (*.jpg;*.jpeg;*.png;*.webp)", "All files (*.*)"))
            if not res:
                return {"cancelled": True}
            data = Path(res[0] if isinstance(res, (list, tuple)) else res).read_bytes()
        elif a.get("data"):
            import base64
            data = base64.b64decode(a["data"].split(",", 1)[-1])
        elif a.get("url"):
            url = a["url"].strip()
            if not re.match(r"^https?://", url, re.I):
                return {"error": "That doesn't look like a web link (it should start with http)."}
            req = urllib.request.Request(url, headers=BROWSER)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(30 * 2 ** 20 + 1)
        else:
            return {"error": "No picture given."}
    except Exception as e:
        return {"error": f"Couldn't get that picture: {e}"}
    if not data or len(data) > 30 * 2 ** 20:
        return {"error": "That picture is empty or too big (30 MB max)."}
    ext = _image_kind(data)
    if not ext:
        return {"error": "That isn't a JPG, PNG or WebP picture. (For a web link, use the image's own address: right-click the picture, Copy image address.)"}
    try:
        CUSTOM_POSTER_DIR.mkdir(parents=True, exist_ok=True)
        for e in COVER_EXTS:
            old = CUSTOM_POSTER_DIR / f"{iid}{e}"
            if old.exists():
                old.unlink()
        (CUSTOM_POSTER_DIR / f"{iid}{ext}").write_bytes(data)
    except OSError as e:
        return {"error": f"Couldn't save it in {CUSTOM_POSTER_DIR}: {e}"}
    self.store.config.setdefault("coverRev", {})[iid] = int(time.time() * 1000)
    self.store.touch()
    return dict(ok=True, imdbID=iid, **_covers(self))


def _clear_cover(self, a):
    iid = (a.get("imdbID") or "").strip()
    if not re.match(r"^tt\d{5,}$", iid):
        return {"error": "Unknown title."}
    try:
        for e in COVER_EXTS:
            f = CUSTOM_POSTER_DIR / f"{iid}{e}"
            if f.exists():
                f.unlink()
    except OSError as e:
        return {"error": f"Couldn't remove it: {e}"}
    self.store.config.setdefault("coverRev", {})[iid] = int(time.time() * 1000)
    self.store.touch()
    return dict(ok=True, imdbID=iid, **_covers(self))


App._covers = _covers
App.set_cover = _set_cover
App.clear_cover = _clear_cover


# ---------------------------------------------------------------- your own titles
def _set_title(self, a):
    """Rename a title in the app only (files on disk are never touched). Empty name = back to the original."""
    key = (a.get("key") or "").strip()
    if not key:
        return {"error": "Unknown title."}
    name = re.sub(r"\s+", " ", (a.get("title") or "")).strip()[:200]
    titles = self.store.config.setdefault("titles", {})
    if name and name != (a.get("original") or "").strip():
        titles[key] = name
    else:
        titles.pop(key, None)
    self.store.touch()
    return {"ok": True, "titles": titles}


App.set_title = _set_title


# ---------------------------------------------------------------- full screen
def _is_fs():
    form = getattr(App.window, "native", None) if App.window is not None else None
    return bool(getattr(form, "is_fullscreen", False))


def _fullscreen(self, a=None):
    """{"toggle": true} switches full screen on/off now; {"startup": bool} sets whether the app opens in full screen."""
    a = a or {}
    if "startup" in a:
        self.store.config["fullscreen"] = bool(a["startup"])
        self.store.touch()
    if a.get("toggle") and App.window is not None:
        try:
            App.window.toggle_fullscreen()
            time.sleep(0.15)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    return {"on": _is_fs(), "startup": self.store.config.get("fullscreen", True), "native": App.window is not None}


App.fullscreen = _fullscreen


# ---------------------------------------------------------------- last watched episode per show
def _set_last(self, a):
    """Remember the episode you last played for a show, so it can be highlighted. {"show": folder, "path": file}"""
    show, path = a.get("show"), a.get("path")
    if not (isinstance(show, str) and show and isinstance(path, str) and path):
        return {"error": "No episode given."}
    lw = self.store.config.setdefault("lastWatched", {})
    lw[show] = {"path": path, "t": int(time.time())}
    self.store.touch()
    return {"ok": True, "lastWatched": lw}


App.set_last = _set_last


# ---------------------------------------------------------------- VLC for English-language titles
def _find_vlc():
    """Path to vlc.exe, or None."""
    import shutil
    if os.name != "nt":
        return shutil.which("vlc")
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in (None, "InstallDir"):
                            try:
                                v = winreg.QueryValueEx(k, name)[0]
                            except OSError:
                                continue
                            p = Path(v) if str(v).lower().endswith(".exe") else Path(v) / "vlc.exe"
                            if p.exists():
                                return str(p)
                except OSError:
                    pass
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "VideoLAN" / "VLC" / "vlc.exe"
            if p.exists():
                return str(p)
    return shutil.which("vlc")


def _play_prefs(self, a=None):
    """{"englishVLC": bool} turns the English-in-VLC rule on/off."""
    a = a or {}
    if "englishVLC" in a:
        self.store.config["englishVLC"] = bool(a["englishVLC"])
        self.store.touch()
    return {"englishVLC": self.store.config.get("englishVLC", True), "vlc": _find_vlc()}


App.play_prefs = _play_prefs


# ---------------------------------------------------------------- close the app (the X shown in full screen)
def _quit(self, a=None):
    try:
        self.store.dirty = True
        self.store.flush()
    except Exception:
        pass

    def bye():
        time.sleep(0.15)
        try:
            if App.window is not None:
                App.window.destroy()
        except Exception:
            pass
        time.sleep(2)
        os._exit(0)                      # in case anything is still holding the process open
    threading.Thread(target=bye, daemon=True).start()
    return {"ok": True}


App.quit = _quit


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype, cache=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8", "no-store")
            m = re.match(r"^/poster/(tt\d+|[\w-]+?)(?:_(L|M|H))?\.jpg$", u.path)
            if m:
                size = {"L": "H"}.get(m.group(2), m.group(2) or "")
                p = app.lib.ensure_poster(m.group(1), size, quick=True)
                if p:
                    mime = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
                    exact = p.stem == m.group(1) + ("_" + size if size else "")
                    keep = "public, max-age=31536000, immutable" if exact and p.parent != CUSTOM_POSTER_DIR \
                        else "no-cache"            # a stand-in: ask again next time
                    return self._send(200, p.read_bytes(), mime, keep)
                return self._send(404, b"", "text/plain")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            m = re.match(r"^/api/(\w+)$", self.path)
            fn = m and getattr(app, m.group(1), None)
            if not fn or m.group(1).startswith("_"):
                return self._send(404, b"{}", "application/json")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                arg = json.loads(self.rfile.read(n) or b"{}")
                out = fn(arg)
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {e}"}
            self._send(200, json.dumps(out).encode("utf-8"), "application/json")

    return H


def _win_icon_setup():
    """Windows: give the process its own taskbar identity so it shows our icon, not Python's."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AAmbrose123.MovieGuide")
    except Exception:
        pass


def _win_icon_apply(title):
    """Windows: put MovieGuide.ico on the app window's title bar and taskbar button."""
    if os.name != "nt":
        return
    ico = next((p for p in (BUNDLE / "MovieGuide.ico", HERE / "MovieGuide.ico") if p.exists()), None)
    if not ico:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        hwnd = None
        for _ in range(100):                       # wait up to ~10 s for the window to exist
            hwnd = u.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        big = u.LoadImageW(None, str(ico), IMAGE_ICON, u.GetSystemMetrics(11), u.GetSystemMetrics(12), LR_LOADFROMFILE)
        small = u.LoadImageW(None, str(ico), IMAGE_ICON, u.GetSystemMetrics(49), u.GetSystemMetrics(50), LR_LOADFROMFILE)
        if big:
            u.SendMessageW(hwnd, WM_SETICON, 1, big)
        if small:
            u.SendMessageW(hwnd, WM_SETICON, 0, small)
    except Exception:
        pass


def main():
    app = App()
    App.store_ref = app.store
    UPDATER.start()
    port = int(os.environ.get("MOVIEGUIDE_PORT", "0"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    if os.environ.get("MOVIEGUIDE_HEADLESS"):
        print(url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    try:
        import webview
    except ImportError:
        print("pywebview not installed - opening in your browser instead.\n"
              "For the app window run:  pip install pywebview")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    _win_icon_setup()
    title = f"{APP_NAME} {VERSION}"
    fs = bool(app.store.config.get("fullscreen", True))
    App.window = webview.create_window(title, url, width=1440, height=920, maximized=not fs, fullscreen=fs,
                                       min_size=(960, 620), background_color="#0a0b10")
    webview.start(_win_icon_apply, (title,))
    app.store.dirty = True
    app.store.flush()


if __name__ == "__main__":
    main()
