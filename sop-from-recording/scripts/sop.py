"""Everything the sop-from-recording skill runs, in one file. The first run installs what's missing (Pillow, numpy,
python-docx, and an ffmpeg if there isn't one), into this Python or, if it won't take packages, a private venv in
~/.sop-from-recording.

    python3 sop.py fetch LINK --out DIR                  download a Loom (public or unlisted link), YouTube or Vimeo video,
                                                         and a Loom's own transcript when it has one
    python3 sop.py transcribe VIDEO --out DIR            transcript.txt for a recording that has none (needs internet once)
    python3 sop.py sheet VIDEO --out DIR [--start S --end S] [--every S]
        contact sheets labelled with times: one tile each time the screen changes (pages, menus, dialogs), at least
        every 30s; --every S for a tile every S seconds instead
    python3 sop.py shot VIDEO --at T --out FILE [--box x0,y0,x1,y1] [--arrow] [--blur x0,y0,x1,y1 ...]
                        [--crop x0,y0,x1,y1] [--grid] [--zoom x0,y0,x1,y1]
        the step's screenshot: red box on what to click, private bits blurred, cropped. --grid lays a percentage
        grid over it (x then y, 0-100 from the top-left of the whole frame) to read where things are and check the
        box; --zoom shows just that area with a finer grid. Run it once more without --grid for the final file.
    python3 sop.py doc sop.json --out "SOP - Title.docx"
        the Word file (opens as a Google Doc in Google Drive)

shot also takes an image instead of VIDEO --at T.

sop.json:
{
  "title": "Add a new client to Stripe", "overview": "What this gets done, when to do it, how long it takes.",
  "owner": "Sam", "department": "Operations", "updated": "2026-11-02",
  "source": {"label": "Loom: adding a client", "url": "https://www.loom.com/share/..."},
  "before_you_start": ["Stripe login (in 1Password)"],
  "steps": [
    {"section": "Part 1: ...",                      # optional: a heading above this step
     "title": "Open Customers", "body": "Click **Customers** in the left menu.\\n- a bullet\\n- [a link](https://...)",
     "image": "shots/step01.png",                   # optional, relative to sop.json
     "note": "**To confirm:** who approves refunds?"}   # optional, in italics
  ],
  "tips": ["Never paste the Secret key into chat."]
}
Text takes **bold**, `code`, [links](url) and "- " bullet lines.
"""
import argparse
import contextlib
import importlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.abspath(__file__)
VENV = os.path.join(os.path.expanduser("~"), ".sop-from-recording", "venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")
RED = (229, 57, 53)


# ---------- setup ----------

@contextlib.contextmanager
def install_lock():
    """One install at a time, so two commands started together on a first run don't both build the venv."""
    os.makedirs(os.path.dirname(VENV), exist_ok=True)
    with open(os.path.join(os.path.dirname(VENV), ".lock"), "w") as f:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except ImportError:  # Windows
            pass
        yield


def need(*pkgs):
    """[(module, pip spec)]: install what's missing. If this Python won't take packages (Homebrew's, Debian's), rerun
    the same command in our own venv, which keeps them for next time."""
    def missing():
        importlib.invalidate_caches()
        return [spec for mod, spec in pkgs if importlib.util.find_spec(mod) is None]
    if not missing():
        return
    in_venv = os.path.abspath(sys.prefix) == os.path.abspath(VENV)
    with install_lock():
        todo = missing()  # another run may have just installed them
        if not todo:
            return
        if not in_venv and os.path.exists(VENV_PY):
            os.execv(VENV_PY, [VENV_PY, HERE] + sys.argv[1:])
        pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        if in_venv:
            print("installing " + ", ".join(todo) + " (first run only)", file=sys.stderr)
        if subprocess.run(pip + todo, capture_output=True, text=True).returncode == 0:
            return
        if in_venv:
            raise SystemExit("Couldn't install " + " ".join(todo) + ". No internet here?")
        if subprocess.run([sys.executable, "-m", "venv", VENV], capture_output=True).returncode == 0:
            os.execv(VENV_PY, [VENV_PY, HERE] + sys.argv[1:])
        # no venv module (some Linux sandboxes): last resort, this Python with pip's override
        r = subprocess.run(pip + ["--break-system-packages"] + todo, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("Couldn't install " + " ".join(todo) + ":\n" + r.stderr[-1500:])


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    need(("imageio_ffmpeg", "imageio-ffmpeg"))
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration(video):
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", video], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise SystemExit(f"Can't read {video} as a video:\n{err[-600:]}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def fmt(t):
    t = int(round(t))
    return f"{t // 60}:{t % 60:02d}" if t < 3600 else f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def font(size):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "C:\\Windows\\Fonts\\arialbd.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def frame(video, t, width=None):
    from PIL import Image
    vf = ["-vf", f"scale={width}:-2"] if width else []
    png = subprocess.run([ffmpeg(), "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1", *vf,
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    if not png:
        raise SystemExit(f"No frame at {t}s in {video} (it's {duration(video):.0f}s long)")
    return Image.open(io.BytesIO(png)).convert("RGB")


def source(a):
    from PIL import Image
    return frame(a.src, a.at) if a.at is not None else Image.open(a.src).convert("RGB")


def pct(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 4 or v[0] >= v[2] or v[1] >= v[3]:
        raise SystemExit(f"bad area {s}: want x0,y0,x1,y1 in percent, with x0<x1 and y0<y1")
    return v


def save(im, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im.save(out, optimize=True)
    print(out, "x".join(map(str, im.size)))


# ---------- fetch / transcribe ----------

def loom_transcript(link, out):
    """Loom's own captions for a shared Loom, as transcript.txt. Returns the path, or None if it has none."""
    import urllib.request
    m = re.search(r"loom\.com/(?:share|embed)/([0-9a-f]{32})", link)
    if not m:
        return None
    q = {"operationName": "FetchVideoTranscript", "variables": {"videoId": m[1], "password": None},
         "query": "query FetchVideoTranscript($videoId: ID!, $password: String) { fetchVideoTranscript(videoId: "
                  "$videoId, password: $password) { ... on VideoTranscriptDetails { captions_source_url } } }"}
    try:
        req = urllib.request.Request("https://www.loom.com/graphql", json.dumps(q).encode(),
                                     {"Content-Type": "application/json", "apollographql-client-name": "web"})
        url = json.load(urllib.request.urlopen(req, timeout=30))["data"]["fetchVideoTranscript"]["captions_source_url"]
        vtt = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return None
    lines = []
    for block in re.split(r"\n\s*\n", vtt.replace("\r", "")):
        t = re.search(r"(\d+):(\d+):([\d.]+)\s*-->", block)
        text = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in block.split("\n")[block.split("\n").index(
            next(l for l in block.split("\n") if "-->" in l)) + 1:]) if t else ""
        if t and text.strip():
            lines.append(f"[{fmt(int(t[1]) * 3600 + int(t[2]) * 60 + float(t[3]))}] {text.strip()}")
    if not lines:
        return None
    path = os.path.join(out, "transcript.txt")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def cmd_fetch(a):
    need(("yt_dlp", "yt-dlp"))
    import yt_dlp
    os.makedirs(a.out, exist_ok=True)
    opts = {"outtmpl": os.path.join(a.out, "recording.%(ext)s"), "quiet": True, "no_warnings": True, "noprogress": True,
            "ffmpeg_location": ffmpeg(), "format": "bv*[height<=1440]+ba/b", "merge_output_format": "mp4"}
    for attempt in (1, 2):  # the first try sometimes fails for no lasting reason
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(a.link)
                path = y.prepare_filename(info)
            break
        except Exception as e:
            if attempt == 2:
                raise SystemExit(f"Couldn't download it ({str(e)[:300]}).\nIf it's a private Loom, ask them to set "
                                 "the link to 'Anyone with the link' or send the file. If there's no internet here, "
                                 "ask for the file.")
    path = os.path.splitext(path)[0] + ".mp4" if not os.path.exists(path) else path
    print(f"{path}  ({fmt(info.get('duration') or duration(path))}, \"{info.get('title', '')}\")")
    if info.get("description"):
        print("description: " + info["description"].strip()[:1500])
    tr = loom_transcript(a.link, a.out)
    print(f"{tr}  (Loom's own transcript)" if tr else "no transcript came with it: run transcribe, or ask for one")


def cmd_transcribe(a):
    need(("faster_whisper", "faster-whisper"), ("av", "av<17"))
    from importlib.metadata import version
    if int(version("av").split(".")[0]) >= 17:  # av 17 dropped an argument faster-whisper 1.2 still passes
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "av<17"], check=True)
    os.makedirs(a.out, exist_ok=True)
    wav = os.path.join(a.out, "audio16k.wav")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", a.video, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(a.model, device="cpu", compute_type="int8")
    except Exception as e:
        raise SystemExit(f"Couldn't get the speech model ({str(e)[:200]}). No internet here? Ask for the transcript.")
    segs, _ = model.transcribe(wav, vad_filter=True)
    out = os.path.join(a.out, "transcript.txt")
    n = 0
    with open(out, "w") as f:
        for s in segs:
            f.write(f"[{fmt(s.start)}] {s.text.strip()}\n")
            n += 1
    os.remove(wav)
    print(f"{out}  ({n} lines)")


# ---------- sheet ----------

def screen_changes(video, start, end, sens, max_gap):
    """Times worth a tile: the screen settled after a change, or max_gap passed. Pixels that change most of the
    time (a webcam bubble, a playing video, a clock) are ignored, or they'd fire constantly."""
    import numpy as np
    w, h = 192, 108
    fps = 2 if end - start <= 1200 else 1 if end - start <= 3600 else 0.5
    raw = subprocess.run([ffmpeg(), "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", video,
                          "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8)[: len(raw) // (w * h) * w * h].reshape(-1, h, w).astype(np.int16)
    if len(f) < 2:
        return [start], 0.0
    moving = np.abs(np.diff(f, axis=0)) > 18
    still = moving.mean(axis=0) < 0.5
    t = start + np.arange(len(f)) / fps
    kept, ref, i = [0], f[0], 1
    while i < len(f):
        if ((np.abs(f[i] - ref) > 18) & still).mean() > sens or t[i] - t[kept[-1]] >= max_gap:
            j = i  # let a loading page or opening menu settle (up to 3s)
            while j + 1 < len(f) and j - i < 3 * fps and (moving[j] & still).mean() > sens / 4:
                j += 1
            kept.append(j)
            ref, i = f[j], j + 1
        else:
            i += 1
    return [round(float(t[k]), 2) for k in kept], float(1 - still.mean())


def cmd_sheet(a):
    from PIL import Image, ImageDraw
    end = min(a.end, duration(a.video)) if a.end is not None else duration(a.video)
    if a.every:
        times, note = [round(a.start + k * a.every, 2) for k in range(int((end - a.start) / a.every) + 1)], ""
    else:
        times, busy = screen_changes(a.video, a.start, end, a.sens, a.max_gap)
        note = f" ({busy:.0%} of the screen ignored as always moving, e.g. a webcam bubble)" if busy > 0.005 else ""
    if len(times) > 200:
        print(f"{len(times)} tiles is a lot: sheet only the part that matters (--start/--end) or raise --sens.")
    dense = len(times) > 96  # long stretch: smaller tiles, 20 a sheet, so it's fewer images to open
    a.width = a.width or (480 if dense else 640)
    a.cols, a.rows = a.cols or (4 if dense else 3), a.rows or (5 if dense else 4)
    with ThreadPoolExecutor(4) as ex:
        tiles = list(ex.map(lambda t: frame(a.video, t, a.width), times))
    os.makedirs(a.out, exist_ok=True)
    per, fnt, made = a.cols * a.rows, font(max(14, a.width // 28)), []
    for n in range(0, len(tiles), per):
        group, tt = tiles[n:n + per], times[n:n + per]
        tw, th = group[0].size
        cols = min(a.cols, len(group))
        sheet = Image.new("RGB", (cols * (tw + 8) + 8, ((len(group) - 1) // a.cols + 1) * (th + 8) + 8), "white")
        d = ImageDraw.Draw(sheet)
        for k, (im, t) in enumerate(zip(group, tt)):
            x, y = 8 + (k % a.cols) * (tw + 8), 8 + (k // a.cols) * (th + 8)
            sheet.paste(im.resize((tw, th)), (x, y))
            label = fmt(t) + (f" ({t:.0f}s)" if t >= 60 else "")
            b = d.textbbox((0, 0), label, font=fnt)
            d.rectangle((x, y, x + b[2] + 12, y + b[3] + 8), fill=(0, 0, 0))
            d.text((x + 6, y + 3), label, fill=(255, 255, 0), font=fnt)
        path = os.path.join(a.out, f"sheet_{n // per + 1:02d}_{int(tt[0])}s-{int(tt[-1])}s.png")
        sheet.save(path)
        made.append(path)
    with open(os.path.join(a.out, "moments.txt"), "w") as f:
        f.write("\n".join(f"{fmt(t)}\t{t}" for t in times) + "\n")
    how = f"every {a.every}s" if a.every else "one per screen change"
    print(f"{len(times)} tiles, {how}, {fmt(a.start)}-{fmt(end)}{note}, on {len(made)} sheet(s):\n" + "\n".join(made))


# ---------- shot ----------

def with_grid(im, area):
    """im cropped to area (x0,y0,x1,y1 percent of the whole frame), scaled to 1600 wide, with a grid labelled in
    whole-frame percent: every 5% (labels every 10%), or every 1% along a side that's 30% or less."""
    from PIL import Image, ImageDraw
    W, H = im.size
    x0, y0, x1, y1 = area
    im = im.crop((int(W * x0 / 100), int(H * y0 / 100), int(W * x1 / 100), int(H * y1 / 100)))
    sx = 1600 / im.width if (x1 - x0) < 100 else min(1.0, 1600 / im.width)
    sy = max(sx, 700 / im.height) if (y1 - y0) < 100 else sx  # a wide, short strip gets stretched tall to be readable
    im = im.resize((int(im.width * sx), int(im.height * sy)))
    w, h = im.size
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = font(max(12, w // 70))
    for axis, lo, hi in (("x", x0, x1), ("y", y0, y1)):
        step = 1 if hi - lo <= 30 else 5
        for p in range(int(math.ceil(lo)), int(hi) + 1):
            if p % step or not lo < p < hi:
                continue
            major = p % 10 == 0
            colour = (255, 0, 255, 150 if major else 70)
            label = major or step == 1
            if axis == "x":
                x = (p - lo) / (hi - lo) * w
                d.line([(x, 0), (x, h)], fill=colour, width=2 if major else 1)
                for y in (2, h - f.size - 6) if label else ():
                    d.rectangle((x + 2, y, x + 2 + f.size * 1.6, y + f.size + 4), fill=(0, 0, 0, 170))
                    d.text((x + 4, y + 1), str(p), fill=(255, 255, 255, 255), font=f)
            else:
                y = (p - lo) / (hi - lo) * h
                d.line([(0, y), (w, y)], fill=colour, width=2 if major else 1)
                for x in (2, w - f.size * 1.6 - 6) if label else ():
                    d.rectangle((x, y + 2, x + f.size * 1.6, y + f.size + 6), fill=(0, 0, 0, 170))
                    d.text((x + 2, y + 3), str(p), fill=(255, 255, 255, 255), font=f)
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def cmd_shot(a):
    from PIL import Image, ImageDraw, ImageFilter
    im = source(a)
    W, H = im.size
    px = lambda b: (int(W * b[0] / 100), int(H * b[1] / 100), int(W * b[2] / 100), int(H * b[3] / 100))
    for b in map(pct, a.blur):
        x0, y0, x1, y1 = px(b)
        region = im.crop((x0, y0, x1, y1))
        small = region.resize((max(1, (x1 - x0) // 14), max(1, (y1 - y0) // 14)))
        im.paste(small.resize(region.size, Image.NEAREST).filter(ImageFilter.GaussianBlur(3)), (x0, y0))
    d = ImageDraw.Draw(im)
    lw = max(3, W // 300)
    boxes = [px(b) for b in map(pct, a.box)]
    for x0, y0, x1, y1 in boxes:
        d.rounded_rectangle((x0 - lw * 2, y0 - lw * 2, x1 + lw * 2, y1 + lw * 2), radius=lw * 3, outline=RED, width=lw)
    arrow_side = None
    if boxes and a.arrow:  # comes in from the side with the most room
        x0, y0, x1, y1 = boxes[0]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        room = {"left": x0, "right": W - x1, "above": y0, "below": H - y1}
        arrow_side = max(room, key=room.get)
        tip = {"left": (x0 - lw * 4, cy), "right": (x1 + lw * 4, cy), "above": (cx, y0 - lw * 4),
               "below": (cx, y1 + lw * 4)}[arrow_side]
        vec = {"left": (-1, -0.35), "right": (1, -0.35), "above": (-0.35, -1), "below": (-0.35, 1)}[arrow_side]
        L = max(W, H) * 0.09
        tail = (tip[0] + vec[0] * L, tip[1] + vec[1] * L)
        d.line([tail, tip], fill=RED, width=lw * 2)
        ang, head = math.atan2(tip[1] - tail[1], tip[0] - tail[0]), lw * 7
        d.polygon([tip, (tip[0] - head * math.cos(ang - 0.45), tip[1] - head * math.sin(ang - 0.45)),
                   (tip[0] - head * math.cos(ang + 0.45), tip[1] - head * math.sin(ang + 0.45))], fill=RED)
    if boxes and a.number:  # optional badge, just outside the box on a side the arrow doesn't use
        x0, y0, x1, y1 = boxes[0]
        r = max(14, W // 70)
        gap, top = lw * 2 + 4 + r, y0 - lw * 2 + r
        spots = {"left": (x0 - gap, top), "above": (x0 - lw * 2 + r, y0 - gap), "right": (x1 + gap, top),
                 "below": (x0 - lw * 2 + r, y1 + gap)}
        fits = lambda c: r <= c[0] <= W - r and r <= c[1] <= H - r
        order = [s for s in spots if s != arrow_side]
        cx, cy = next((spots[s] for s in order if fits(spots[s])), (max(r + 2, x0), max(r + 2, y0)))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline="white", width=max(2, lw // 2))
        f = font(int(r * 1.2))
        tb = d.textbbox((0, 0), a.number, font=f)
        d.text((cx - (tb[2] - tb[0]) / 2 - tb[0], cy - (tb[3] - tb[1]) / 2 - tb[1]), a.number, fill="white", font=f)
    if a.grid or a.zoom:
        save(with_grid(im, pct(a.zoom or a.crop or "0,0,100,100")), a.out)
        return
    if a.crop:
        im = im.crop(px(pct(a.crop)))
    if im.width > 1600:
        im = im.resize((1600, int(im.height * 1600 / im.width)), Image.LANCZOS)
    save(im, a.out)


# ---------- doc ----------

def cmd_doc(a):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    from PIL import Image

    ink, muted = RGBColor(0x1F, 0x1F, 0x1F), RGBColor(0x6B, 0x6B, 0x6B)
    sop = json.load(open(a.spec))
    base = os.path.dirname(os.path.abspath(a.spec))
    doc = Document()
    for sec in doc.sections:  # Google Docs' 1in margins
        sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Inches(1)
    text_w = (doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin) / 914400
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    for name, size in (("Title", 24), ("Heading 1", 16), ("Heading 2", 13)):
        s = doc.styles[name]
        s.font.name, s.font.size, s.font.color.rgb, s.font.bold = "Arial", Pt(size), ink, True

    def link(par, text, url):
        rid = par.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                                 is_external=True)
        hl, run, rpr = OxmlElement("w:hyperlink"), OxmlElement("w:r"), OxmlElement("w:rPr")
        hl.set(qn("r:id"), rid)
        for tag, val in (("w:color", "1155CC"), ("w:u", "single")):
            el = OxmlElement(tag)
            el.set(qn("w:val"), val)
            rpr.append(el)
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        run.append(rpr)
        run.append(t)
        hl.append(run)
        par._p.append(hl)

    def rich(par, text, italic=False, color=None):
        for tok in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))", text):
            if not tok:
                continue
            if tok.startswith("["):
                m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
                link(par, m[1], m[2])
                continue
            r = par.add_run(tok[2:-2] if tok.startswith("**") else tok[1:-1] if tok.startswith("`") else tok)
            r.bold = tok.startswith("**") or None
            if tok.startswith("`"):
                r.font.name = "Courier New"
            r.italic = italic or None
            if color is not None:
                r.font.color.rgb = color

    def body(text):
        for line in (text or "").split("\n"):
            line = line.strip()
            if line.startswith(("- ", "* ")):
                rich(doc.add_paragraph(style="List Bullet"), line[2:])
            elif line:
                rich(doc.add_paragraph(), line)

    doc.add_paragraph(sop["title"], style="Title")
    meta = [f"{k}: {sop[k.lower()]}" for k in ("Owner", "Department", "Updated") if sop.get(k.lower())]
    if meta:
        r = doc.add_paragraph().add_run("   ·   ".join(meta))
        r.font.color.rgb, r.font.size = muted, Pt(10)
    src = sop.get("source") or {}
    if src.get("url") or src.get("label"):
        p = doc.add_paragraph()
        r = p.add_run("Recording: ")
        r.font.color.rgb, r.font.size = muted, Pt(10)
        if (src.get("url") or "").startswith("http"):
            link(p, src.get("label") or src["url"], src["url"])
        else:
            p.add_run(src.get("label") or src.get("url"))
    body(sop.get("overview"))
    steps = sop.get("steps", [])
    if len(steps) > 3:
        doc.add_paragraph("Contents", style="Heading 1")
        for i, s in enumerate(steps, 1):
            if s.get("section"):
                doc.add_paragraph(s["section"]).runs[0].bold = True
            doc.add_paragraph(f"Step {i}: {s['title']}", style="List Bullet")
    if sop.get("before_you_start"):
        doc.add_paragraph("Before you start", style="Heading 1")
        for item in sop["before_you_start"]:
            rich(doc.add_paragraph(style="List Bullet"), item)
    if not (steps and steps[0].get("section")):  # sections are their own top-level headings
        doc.add_paragraph("Steps", style="Heading 1")
    shots = 0
    for i, s in enumerate(steps, 1):
        if s.get("section"):
            doc.add_paragraph(s["section"], style="Heading 1")
        doc.add_paragraph(f"Step {i}: {s['title']}", style="Heading 2")
        body(s.get("body"))
        if s.get("image"):
            path = s["image"] if os.path.isabs(s["image"]) else os.path.join(base, s["image"])
            if os.path.exists(path):
                w, h = Image.open(path).size
                doc.add_picture(path, width=Inches(text_w if w >= h else min(text_w, 7.0 * w / h)))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                shots += 1
            else:
                doc.add_paragraph(f"[missing screenshot: {path}]")
        if s.get("note"):
            rich(doc.add_paragraph(), s["note"], italic=True, color=muted)
    if sop.get("tips"):
        doc.add_paragraph("Tips and common mistakes", style="Heading 1")
        for t in sop["tips"]:
            rich(doc.add_paragraph(style="List Bullet"), t)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    doc.save(a.out)
    print(f"{a.out}  ({len(steps)} steps, {shots} screenshots, {os.path.getsize(a.out) / 1e6:.1f} MB)")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="SOP from a recording. Run a command with -h for its options.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("link")
    p.add_argument("--out", required=True)
    p = sub.add_parser("transcribe")
    p.add_argument("video")
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="base.en", help="faster-whisper model; small.en is better and slower")
    p = sub.add_parser("sheet")
    p.add_argument("video")
    p.add_argument("--out", required=True)
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--end", type=float)
    p.add_argument("--every", type=float, help="a tile every N seconds instead of one per screen change")
    p.add_argument("--sens", type=float, default=0.004, help="how much of the screen must change for a new tile")
    p.add_argument("--max-gap", type=float, default=30, help="a tile at least this often, changes or not")
    p.add_argument("--width", type=int, help="tile width (640, or 480 when there are many tiles)")
    p.add_argument("--cols", type=int)
    p.add_argument("--rows", type=int)
    p = sub.add_parser("shot")
    p.add_argument("src", help="the recording (with --at) or an image")
    p.add_argument("--at", type=float, help="seconds into the recording")
    p.add_argument("--out", required=True)
    p.add_argument("--box", action="append", default=[], help="x0,y0,x1,y1 to outline; repeatable")
    p.add_argument("--arrow", action="store_true", help="an arrow at the first box")
    p.add_argument("--blur", action="append", default=[], help="x0,y0,x1,y1 to hide; repeatable")
    p.add_argument("--crop", help="x0,y0,x1,y1 to keep")
    p.add_argument("--grid", action="store_true", help="lay a percentage grid over it, to read positions and check")
    p.add_argument("--zoom", help="x0,y0,x1,y1: show just this area, enlarged, with a finer grid")
    p.add_argument("--number", help="a step-number badge by the first box (the doc numbers steps anyway)")
    p = sub.add_parser("doc")
    p.add_argument("spec")
    p.add_argument("--out", required=True)
    a = ap.parse_args()

    need(("PIL", "pillow"), ("numpy", "numpy"), ("docx", "python-docx"))
    {"fetch": cmd_fetch, "transcribe": cmd_transcribe, "sheet": cmd_sheet, "shot": cmd_shot, "doc": cmd_doc}[a.cmd](a)


if __name__ == "__main__":
    main()
