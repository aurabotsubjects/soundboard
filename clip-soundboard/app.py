"""
Clip Soundboard - cut 1-10 second audio clips from YouTube videos and play them
from a library list or a 10-button soundboard.

Run:  python app.py
Then open http://127.0.0.1:5000 (it opens automatically).
"""
import json
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func

BASE_DIR = Path(__file__).resolve().parent
CLIPS_DIR = BASE_DIR / "clips"          # audio files live here
DATA_FILE = CLIPS_DIR / "library.json"  # clip names + board layout
CLIPS_DIR.mkdir(exist_ok=True)

PREFERRED_PORT = 5000                   # first port to try; the app moves up if something else is using it
MIN_SECONDS, MAX_SECONDS = 1.0, 10.0
MIN_TRIM_KEEP = 0.05                    # a trimmed clip must keep at least 50 ms
BOARD_SIZE = 10
GAIN_MIN_DB, GAIN_MAX_DB = -20.0, 24.0  # per-clip volume adjustment range
TARGET_LUFS = -16.0                     # "Auto-match" aims every clip at this loudness
DEFAULT_COLORS = [
    "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
    "#3498db", "#9b59b6", "#e84393", "#95a5a6", "#34495e",
]

app = Flask(__name__, static_folder=None)
lock = threading.Lock()


# ---------- storage ----------
def default_data():
    return {
        "clips": [],
        "board": [{"clip_id": None, "color": DEFAULT_COLORS[i]} for i in range(BOARD_SIZE)],
    }


def clean_board(board, ids):
    """A board with exactly BOARD_SIZE slots, forgetting clips that no longer exist."""
    board = [dict(s) for s in (board or []) if isinstance(s, dict)][:BOARD_SIZE]
    while len(board) < BOARD_SIZE:
        board.append({"clip_id": None, "color": DEFAULT_COLORS[len(board)]})
    for i, slot in enumerate(board):
        if slot.get("clip_id") not in ids:
            slot["clip_id"] = None
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(slot.get("color", ""))):
            slot["color"] = DEFAULT_COLORS[i]
        board[i] = {"clip_id": slot["clip_id"], "color": slot["color"]}
    return board


def load():
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = default_data()
    else:
        data = default_data()
    # make sure the board always has exactly 10 slots
    board = data.setdefault("board", [])
    while len(board) < BOARD_SIZE:
        board.append({"clip_id": None, "color": DEFAULT_COLORS[len(board)]})
    data["board"] = board[:BOARD_SIZE]
    data.setdefault("clips", [])
    # drop clips whose audio file has gone missing
    data["clips"] = [c for c in data["clips"] if (CLIPS_DIR / c["file"]).exists()]
    ids = {c["id"] for c in data["clips"]}
    for slot in data["board"]:
        if slot.get("clip_id") not in ids:
            slot["clip_id"] = None
    # saved layouts ("presets"): a sound deleted from the library just leaves its button empty
    presets = [p for p in data.get("presets", []) if isinstance(p, dict) and p.get("id") and p.get("name")]
    data["presets"] = [{"id": p["id"], "name": p["name"], "board": clean_board(p.get("board"), ids)} for p in presets]
    return data


def save(data):
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------- helpers ----------
def parse_time(value):
    """Accepts 83, 83.5, 1:23, 1:23.5, 1:02:03 -> seconds (float)."""
    s = str(value).strip()
    if not s:
        return 0.0
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if len(parts) in (2, 3) and all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    raise ValueError("Start time must look like 83, 1:23 or 1:02:03")


def clean_name(name, fallback="Untitled clip"):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return name[:40] or fallback


def original_path(clip_id):
    """Untouched copy of a clip, saved the first time it is trimmed."""
    return CLIPS_DIR / f"{clip_id}.orig.mp3"


def public_clip(clip):
    """Clip as sent to the browser, plus whether an original backup exists."""
    return {**clip, "edited": original_path(clip["id"]).exists()}


def probe_duration(path):
    """Length of the audio you actually hear, in seconds (None if it cannot be measured).
    Decodes the file instead of reading the container, because MP3 headers include a
    few milliseconds of encoder padding that is never played."""
    if not shutil.which("ffmpeg"):
        return None
    try:
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=60,
        ).stdout
        return round(len(pcm) / 2 / 8000, 3) if pcm else None
    except (subprocess.SubprocessError, OSError):
        return None


def measure_lufs(path):
    """Integrated loudness of a clip in LUFS (None if it cannot be measured)."""
    if not shutil.which("ffmpeg"):
        return None
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        ).stderr
    except (subprocess.SubprocessError, OSError):
        return None
    # the summary at the end of the output has a line like "    I:         -20.8 LUFS"
    found = re.findall(r"^\s+I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", out, re.M)
    return float(found[-1]) if found else None


# ---------- routes ----------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/clips/<path:filename>")
def clip_file(filename):
    return send_from_directory(CLIPS_DIR, filename)


@app.get("/api/state")
def get_state():
    with lock:
        data = load()
        save(data)
        return jsonify({**data, "clips": [public_clip(c) for c in data["clips"]]})


@app.post("/api/clips")
def create_clip():
    body = request.get_json(force=True, silent=True) or {}
    url = str(body.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        return jsonify(error="Please paste a full video link (starting with http)."), 400
    try:
        start = parse_time(body.get("start", 0))
        duration = float(body.get("duration", 3))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not (MIN_SECONDS <= duration <= MAX_SECONDS):
        return jsonify(error=f"Length must be between {MIN_SECONDS:g} and {MAX_SECONDS:g} seconds."), 400

    clip_id = uuid.uuid4().hex[:10]
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(CLIPS_DIR / f"{clip_id}.%(ext)s"),
        "download_ranges": download_range_func(None, [(start, start + duration)]),
        "force_keyframes_at_cuts": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:  # yt-dlp raises many exception types
        for f in CLIPS_DIR.glob(f"{clip_id}.*"):
            f.unlink(missing_ok=True)
        msg = str(e).replace("ERROR: ", "")
        if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower():
            msg = "ffmpeg was not found. Install ffmpeg and make sure it is on your PATH."
        return jsonify(error=f"Could not download that clip: {msg[:300]}"), 400

    filename = f"{clip_id}.mp3"
    if not (CLIPS_DIR / filename).exists():
        return jsonify(error="The download finished but no audio file was produced."), 500

    name = clean_name(body.get("name") or (info or {}).get("title"))
    clip = {"id": clip_id, "name": name, "file": filename, "source": url, "start": start, "duration": duration}
    with lock:
        data = load()
        data["clips"].append(clip)
        save(data)
    return jsonify(clip), 201


@app.patch("/api/clips/<clip_id>")
def rename_clip(clip_id):
    body = request.get_json(force=True, silent=True) or {}
    with lock:
        data = load()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            return jsonify(error="Clip not found."), 404
        if "name" in body:
            clip["name"] = clean_name(body.get("name"), clip["name"])
        if "gain_db" in body:
            try:
                gain = float(body["gain_db"])
            except (TypeError, ValueError):
                return jsonify(error="Volume must be a number."), 400
            if not math.isfinite(gain):
                return jsonify(error="Volume must be a number."), 400
            gain = round(min(max(gain, GAIN_MIN_DB), GAIN_MAX_DB), 1)
            if gain == 0:
                clip.pop("gain_db", None)       # 0 dB is the default, keep the file tidy
            else:
                clip["gain_db"] = gain
        save(data)
        return jsonify(public_clip(clip))


@app.delete("/api/clips/<clip_id>")
def delete_clip(clip_id):
    with lock:
        data = load()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            return jsonify(error="Clip not found."), 404
        (CLIPS_DIR / clip["file"]).unlink(missing_ok=True)
        original_path(clip_id).unlink(missing_ok=True)
        data["clips"] = [c for c in data["clips"] if c["id"] != clip_id]
        for slot in data["board"]:
            if slot["clip_id"] == clip_id:
                slot["clip_id"] = None
        save(data)
        return jsonify(ok=True)


@app.post("/api/clips/<clip_id>/trim")
def trim_clip(clip_id):
    """Keep only the part of a clip between `start` and `end` (seconds).
    Either value may be omitted/null to leave that side untouched."""
    body = request.get_json(force=True, silent=True) or {}
    try:
        start = float(body.get("start") or 0)
        end = body.get("end")
        end = None if end is None else float(end)
    except (TypeError, ValueError):
        return jsonify(error="Trim times must be numbers."), 400
    if not math.isfinite(start) or start < 0 or (end is not None and not math.isfinite(end)):
        return jsonify(error="Trim times must be zero or greater."), 400
    if end is not None and end - start < MIN_TRIM_KEEP:
        return jsonify(error=f"Keep at least {int(MIN_TRIM_KEEP * 1000)} ms of the sound."), 400
    if start == 0 and end is None:
        return jsonify(error="Nothing to trim."), 400
    if not shutil.which("ffmpeg"):
        return jsonify(error="ffmpeg was not found. Install ffmpeg and make sure it is on your PATH."), 500

    with lock:
        clip = next((c for c in load()["clips"] if c["id"] == clip_id), None)
    if not clip:
        return jsonify(error="Clip not found."), 404
    src = CLIPS_DIR / clip["file"]
    tmp = CLIPS_DIR / f"{clip_id}.trim-tmp.mp3"

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", str(tmp)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return jsonify(error="Trimming took too long and was stopped."), 500
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return jsonify(error="Could not trim that clip: " + (result.stderr.strip()[-250:] or "ffmpeg produced no audio.")), 400

    with lock:
        data = load()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            tmp.unlink(missing_ok=True)
            return jsonify(error="Clip not found."), 404
        if not original_path(clip_id).exists():          # first edit: keep the untouched sound
            shutil.copy2(src, original_path(clip_id))
        tmp.replace(src)
        clip["duration"] = probe_duration(src) or round((end - start) if end is not None else max(clip["duration"] - start, 0), 3)
        clip["rev"] = int(clip.get("rev", 0)) + 1          # lets the browser drop its cached copy
        save(data)
        return jsonify(public_clip(clip))


@app.get("/api/clips/<clip_id>/loudness")
def clip_loudness(clip_id):
    """Measure how loud a clip is and suggest the volume change that brings it to TARGET_LUFS."""
    with lock:
        clip = next((c for c in load()["clips"] if c["id"] == clip_id), None)
    if not clip:
        return jsonify(error="Clip not found."), 404
    lufs = measure_lufs(CLIPS_DIR / clip["file"])
    if lufs is None:
        return jsonify(error="Could not measure this sound (is ffmpeg installed?)."), 500
    suggested = round(min(max(TARGET_LUFS - lufs, GAIN_MIN_DB), GAIN_MAX_DB), 1)
    return jsonify(lufs=lufs, target=TARGET_LUFS, suggested_gain_db=suggested)


@app.post("/api/clips/<clip_id>/restore")
def restore_clip(clip_id):
    """Undo all trims by putting the saved original back."""
    with lock:
        data = load()
        clip = next((c for c in data["clips"] if c["id"] == clip_id), None)
        if not clip:
            return jsonify(error="Clip not found."), 404
        backup = original_path(clip_id)
        if not backup.exists():
            return jsonify(error="This clip has not been trimmed."), 400
        backup.replace(CLIPS_DIR / clip["file"])
        clip["duration"] = probe_duration(CLIPS_DIR / clip["file"]) or clip["duration"]
        clip["rev"] = int(clip.get("rev", 0)) + 1
        save(data)
        return jsonify(public_clip(clip))


@app.put("/api/board")
def update_board():
    body = request.get_json(force=True, silent=True) or {}
    incoming = body.get("board")
    if not isinstance(incoming, list) or len(incoming) != BOARD_SIZE:
        return jsonify(error=f"Board must have exactly {BOARD_SIZE} buttons."), 400
    with lock:
        data = load()
        ids = {c["id"] for c in data["clips"]}
        board = []
        for i, slot in enumerate(incoming):
            color = str(slot.get("color", ""))
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                color = DEFAULT_COLORS[i]
            clip_id = slot.get("clip_id")
            board.append({"clip_id": clip_id if clip_id in ids else None, "color": color})
        data["board"] = board
        save(data)
        return jsonify(board)


def find_port(start):
    """First port from `start` upward that nothing is answering on. (macOS uses 5000 for AirPlay
    Receiver, which replies "403 Forbidden"; connecting is a more reliable test than binding.)"""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


# ---------- saved layouts (presets) ----------
@app.post("/api/presets")
def save_preset():
    """Save the board as it is right now under a name. Re-using a name replaces that layout."""
    body = request.get_json(force=True, silent=True) or {}
    name = clean_name(body.get("name"), "")
    if not name:
        return jsonify(error="Type a name for this layout first."), 400
    with lock:
        data = load()
        board = clean_board(data["board"], {c["id"] for c in data["clips"]})
        existing = next((p for p in data["presets"] if p["name"].casefold() == name.casefold()), None)
        if existing:
            existing["name"], existing["board"] = name, board
            preset = existing
        else:
            preset = {"id": uuid.uuid4().hex[:8], "name": name, "board": board}
            data["presets"].append(preset)
        save(data)
        return jsonify(preset), 201


@app.post("/api/presets/<preset_id>/apply")
def apply_preset(preset_id):
    """Put a saved layout onto the soundboard."""
    with lock:
        data = load()
        preset = next((p for p in data["presets"] if p["id"] == preset_id), None)
        if not preset:
            return jsonify(error="That layout no longer exists."), 404
        data["board"] = clean_board(preset["board"], {c["id"] for c in data["clips"]})
        save(data)
        return jsonify(data["board"])


@app.delete("/api/presets/<preset_id>")
def delete_preset(preset_id):
    with lock:
        data = load()
        if not any(p["id"] == preset_id for p in data["presets"]):
            return jsonify(error="That layout no longer exists."), 404
        data["presets"] = [p for p in data["presets"] if p["id"] != preset_id]
        save(data)
        return jsonify(ok=True)


if __name__ == "__main__":
    PORT = find_port(int(os.environ.get("PORT", PREFERRED_PORT)))
    url = f"http://127.0.0.1:{PORT}"
    print(f"Clip Soundboard running at {url}  (Ctrl+C to stop)")
    print(f"Clips are saved in: {CLIPS_DIR}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
