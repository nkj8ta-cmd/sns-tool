import glob
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SNS 다운로더",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .stApp { background: linear-gradient(135deg, #eef4ff 0%, #ffffff 50%, #fff3ea 100%); }
    .block-container { padding-top: 3rem !important; padding-bottom: 3.5rem !important; max-width: 900px; }
    .snap-title {
        text-align: center; font-size: 32px; font-weight: 800;
        background: linear-gradient(90deg, #2563eb, #ec4899, #f97316);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px;
    }
    .snap-sub { text-align: center; font-size: 14.5px; color: #64748b; margin-bottom: 24px; }
    .support-sites { text-align: center; font-size: 12.5px; color: #94a3b8; margin: 8px 0 20px; }
    .meta-line { color: #64748b; font-size: 13.5px; margin: 4px 0 10px; }
    .meta-line b { color: #0a7cff; }
    .col-head { font-size: 18px; font-weight: 700; margin: 6px 0 10px; }
    .fmt-label { font-size: 14.5px; font-weight: 600; line-height: 1.35; }
    .fmt-size { font-size: 12.5px; color: #94a3b8; }
    button[kind="primary"] {
        background: #0a7cff !important; border: none !important; border-radius: 999px !important;
        font-weight: 700 !important; color: #fff !important;
    }
    button[kind="primary"]:hover { background: #0866d6 !important; }
</style>
""",
    unsafe_allow_html=True,
)

for _k, _v in {
    "main_url_field": "",
    "processed_result": None,
    "mp3_bytes": None,
    "yt": None,
    "dl_cache": {},
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ==========================================
# 공통 유틸
# ==========================================
def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url


def platform_name(url):
    u = url.lower()
    for key, name in [
        ("threads.", "Threads"), ("instagram.", "Instagram"), ("tiktok.", "TikTok"),
        ("douyin.", "Douyin"), ("xiaohongshu.", "RedNote"), ("rednote.", "RedNote"),
        ("xhslink.", "RedNote"), ("twitter.", "X (Twitter)"), ("x.com", "X (Twitter)"),
        ("facebook.", "Facebook"), ("fb.watch", "Facebook"),
    ]:
        if key in u:
            return name
    return "SNS"


def clean_social_url(raw_text):
    m = re.search(r"https?://[^\s<>\"']+", raw_text)
    if not m:
        return ""
    clean = m.group(0)

    if any(k in clean for k in ["xhslink.com", "v.douyin.com", "vt.tiktok.com", "/share/"]):
        try:
            r = requests.head(clean, allow_redirects=True, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            clean = r.url
        except Exception:
            pass

    if "youtube.com/shorts/" in clean:
        sid = clean.split("shorts/")[1].split("?")[0].split("&")[0]
        clean = f"https://www.youtube.com/watch?v={sid}"
    elif "youtu.be/" in clean:
        vid = clean.split("youtu.be/")[1].split("?")[0].split("&")[0]
        clean = f"https://www.youtube.com/watch?v={vid}"
    return clean


def safe_name(title, n=40):
    return re.sub(r'[\\/*?:"<>|\n\r]', "", title or "media").strip()[:n] or "media"


def fmt_size(b):
    return f"{b / (1024 * 1024):.1f}MB" if b else "용량 미확인"


def fmt_num(n):
    if n is None:
        return "-"
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    return f"{n:,}"


def fmt_date(ud):
    ud = ud or ""
    return f"{ud[:4]}. {int(ud[4:6])}. {int(ud[6:8])}." if len(ud) == 8 and ud.isdigit() else ""


def base_ydl_opts(outdir=None):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "merge_output_format": "mp4"}
    if outdir:
        opts["outtmpl"] = os.path.join(outdir, "%(id)s.%(ext)s")
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    return opts


def fetch_bytes(url, timeout=8):
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


# ==========================================
# 공통 헤더 (썸네일 + 제목 + 메타) — 모든 플랫폼 동일 UI
# ==========================================
def render_header(m, key):
    left, right = st.columns([1, 1.5])
    with left:
        if m.get("thumb"):
            st.image(m["thumb"], use_container_width=True)
            st.download_button("🖼 커버 이미지 다운로드", m["thumb"], "cover.jpg", "image/jpeg",
                               type="primary", use_container_width=True, key=f"cover_{key}")
    with right:
        st.markdown(f"### {m.get('title') or 'SNS Media'}")
        parts = []
        parts.append(f"<b>{m['channel']}</b>" if m.get("channel") else f"<b>{m.get('platform', 'SNS')}</b>")
        if m.get("date"):
            parts.append(m["date"])
        if m.get("views") is not None:
            parts.append(f"{fmt_num(m['views'])} 조회")
        if m.get("likes") is not None:
            parts.append(f"{fmt_num(m['likes'])} 좋아요")
        if m.get("comments") is not None:
            parts.append(f"{fmt_num(m['comments'])} 댓글")
        st.markdown(f'<div class="meta-line">{" · ".join(parts)}</div>', unsafe_allow_html=True)
        desc = m.get("desc") or ""
        if desc:
            st.caption(desc[:120] + ("..." if len(desc) > 120 else ""))
            if len(desc) > 120:
                with st.expander("더 보기"):
                    st.text(desc)


def format_row(label, size_text, btn_key):
    """[라벨 + 용량 | 다운로드 버튼] 한 줄. 버튼이 눌리면 True."""
    r1, r2 = st.columns([1.6, 1])
    with r1:
        st.markdown(f'<div class="fmt-label">{label}</div><div class="fmt-size">{size_text}</div>',
                    unsafe_allow_html=True)
    with r2:
        return st.button("다운로드", key=btn_key, type="primary", use_container_width=True)


# ==========================================
# YouTube: 정보 조회 (다운로드 없이 포맷 목록만)
# ==========================================
def fetch_youtube_info(url):
    opts = base_ydl_opts()
    opts.update({"skip_download": True, "ignore_no_formats_error": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []

    best_by_h = {}
    for f in formats:
        if f.get("ext") == "mhtml" or not f.get("height"):
            continue
        if f.get("vcodec") in (None, "none") or f.get("acodec") not in (None, "none"):
            continue
        h = f["height"]
        score = (f.get("ext") == "mp4", f.get("tbr") or 0)
        if h not in best_by_h or score > best_by_h[h][0]:
            best_by_h[h] = (score, f)
    video_opts = []
    for h in sorted(best_by_h, reverse=True):
        f = best_by_h[h][1]
        video_opts.append({
            "height": h, "fid": f["format_id"],
            "label": f"{h}p · {int(f.get('tbr') or 0)}kbps · {str(f.get('ext', '')).upper()}",
            "size": f.get("filesize") or f.get("filesize_approx") or 0,
        })

    seen, audio_opts = set(), []
    for f in sorted(formats, key=lambda x: x.get("abr") or 0, reverse=True):
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        key = (f.get("language"), f.get("ext"), round(f.get("abr") or 0))
        if key in seen:
            continue
        seen.add(key)
        audio_opts.append({
            "fid": f["format_id"],
            "label": f"{int(f.get('abr') or 0)}kbps · {f.get('language') or 'default'} · {str(f.get('ext', '')).upper()}",
            "size": f.get("filesize") or f.get("filesize_approx") or 0,
        })
        if len(audio_opts) >= 6:
            break

    subs = [{"lang": k, "auto": False, "label": k} for k in (info.get("subtitles") or {})]
    for k in info.get("automatic_captions") or {}:
        if k.endswith("-orig") or k in ("ko", "en", "ja", "zh-Hans"):
            subs.append({"lang": k, "auto": True, "label": f"{k} (자동)"})

    return {
        "url": url,
        "title": info.get("title", "YouTube"),
        "channel": info.get("uploader") or info.get("channel") or "",
        "platform": "YouTube",
        "date": fmt_date(info.get("upload_date")),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "comments": info.get("comment_count"),
        "desc": info.get("description") or "",
        "thumb": fetch_bytes(info["thumbnail"]) if info.get("thumbnail") else None,
        "video_opts": video_opts,
        "audio_opts": audio_opts,
        "subs": subs,
    }


# ==========================================
# FFmpeg / ffprobe
# ==========================================
def process_editing(video_bytes, hflip, speed):
    if not hflip and speed == 1.0:
        return video_bytes

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name
    out_path = in_path.replace(".mp4", "_edited.mp4")

    filters = []
    if hflip:
        filters.append("hflip")
    if speed != 1.0:
        filters.append(f"setpts={1.0 / speed}*PTS")
    vf_cmd = ["-vf", ",".join(filters)] if filters else []
    af_cmd = ["-filter:a", f"atempo={speed}"] if speed != 1.0 else []

    cmd = ["ffmpeg", "-y", "-i", in_path] + vf_cmd + af_cmd + [
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    res = video_bytes
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            res = f.read()
        os.remove(out_path)
    os.remove(in_path)
    return res


def extract_mp3_audio(video_bytes):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name
    out_path = in_path.replace(".mp4", ".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    mp3_res = None
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            mp3_res = f.read()
        os.remove(out_path)
    os.remove(in_path)
    return mp3_res


def probe_video(data):
    """코덱/해상도(회전 반영)/길이를 읽습니다."""
    info = {"codec": None, "pix_fmt": None, "w": None, "h": None, "duration": None}
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt,width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
             "-of", "json", path],
            capture_output=True, text=True,
        )
        j = json.loads(r.stdout or "{}")
        s = (j.get("streams") or [{}])[0]
        w, h = s.get("width"), s.get("height")
        rot = 0
        for sd in s.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        if s.get("tags", {}).get("rotate"):
            rot = int(float(s["tags"]["rotate"]))
        if w and h and abs(rot) % 180 == 90:
            w, h = h, w
        info.update({"codec": s.get("codec_name"), "pix_fmt": s.get("pix_fmt"), "w": w, "h": h})
        try:
            info["duration"] = float(j.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            pass
    except Exception:
        pass
    finally:
        os.remove(path)
    return info


def make_preview(video_bytes):
    """브라우저에서 재생되는 미리보기용 (bytes, w, h). 다운로드 파일은 항상 원본 그대로입니다."""
    info = probe_video(video_bytes)
    if info["codec"] == "h264" and info["pix_fmt"] == "yuv420p":
        return video_bytes, info["w"], info["h"]

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        in_path = f.name
    out_path = in_path.replace(".mp4", "_preview.mp4")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-t", "180",
             "-vf", "scale='min(1920,iw)':-2",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                pv = f.read()
            pinfo = probe_video(pv)
            return pv, pinfo["w"] or info["w"], pinfo["h"] or info["h"]
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.remove(p)
    return video_bytes, info["w"], info["h"]


MAX_PREVIEW_H = 720      # 미리보기 최대 높이(px). 더 크게 보고 싶으면 값을 올리세요.


def show_video(video_bytes, w=None, h=None, container_px=820):
    """원본 픽셀 크기 그대로 표시. 화면 폭/최대 높이를 넘으면 같은 비율로 축소, 가운데 정렬."""
    if w and h:
        disp_w = min(w, container_px, MAX_PREVIEW_H * w / h)
        frac = max(0.2, min(1.0, disp_w / container_px))
        if frac >= 0.98:
            st.video(video_bytes)
        else:
            side = (1 - frac) / 2
            _, mid, _ = st.columns([side, frac, side])
            with mid:
                st.video(video_bytes)
    else:
        st.video(video_bytes)


# ==========================================
# YouTube: 실제 파일 준비 (다이얼로그)
# ==========================================
MIME = {
    "mp4": "video/mp4", "mkv": "video/x-matroska", "webm": "video/webm",
    "m4a": "audio/mp4", "mp3": "audio/mpeg", "opus": "audio/ogg", "srt": "text/plain", "vtt": "text/vtt",
}


def _pick_output(tmp, exts):
    files = [f for f in glob.glob(os.path.join(tmp, "*")) if f.lower().endswith(exts)]
    if not files:
        raise Exception("다운로드된 파일을 찾지 못했습니다. ffmpeg 설치 여부를 확인해 주세요.")
    return max(files, key=os.path.getsize)


def prepare_file(job, pbar):
    tmp = tempfile.mkdtemp()

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = min(int(d.get("downloaded_bytes", 0) / total * 90), 90)
                pbar.progress(pct, text=f"⬇️ 다운로드 중... {pct}%")
        elif d["status"] == "finished":
            pbar.progress(92, text="🔧 병합/변환 중...")

    try:
        opts = base_ydl_opts(tmp)
        opts["progress_hooks"] = [hook]
        kind = job["kind"]
        name = safe_name(job["title"])

        if kind == "video":
            opts["format"] = job["fmt"]
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job["url"]])
            path = _pick_output(tmp, (".mp4", ".mkv", ".webm", ".mov"))
            with open(path, "rb") as fp:
                data = fp.read()
            if job["flip"] or job["speed"] != 1.0:
                pbar.progress(95, text=f"✂️ 편집 중 (배속 {job['speed']}x)...")
                data = process_editing(data, job["flip"], job["speed"])
                ext = "mp4"
            else:
                ext = os.path.splitext(path)[1].lstrip(".").lower()
            suffix = f"_{job['height']}p" + (f"_{job['speed']}x" if job["speed"] != 1.0 else "")
            return {"kind": "video", "bytes": data, "name": f"{name}{suffix}.{ext}", "mime": MIME.get(ext, "video/mp4")}

        if kind == "audio":
            opts["format"] = job["fmt"]
            if job["fmt"] == "mp3":
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ]
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job["url"]])
            path = _pick_output(tmp, (".mp3", ".m4a", ".opus", ".webm", ".ogg"))
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            with open(path, "rb") as fp:
                data = fp.read()
            return {"kind": "audio", "bytes": data, "name": f"{name}.{ext}", "mime": MIME.get(ext, "audio/mp4")}

        if kind == "sub":
            opts.update({
                "skip_download": True,
                "writesubtitles": not job["auto"],
                "writeautomaticsub": job["auto"],
                "subtitleslangs": [job["lang"]],
                "subtitlesformat": "srt/vtt/best",
                "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}],
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job["url"]])
            path = _pick_output(tmp, (".srt", ".vtt"))
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            with open(path, "rb") as fp:
                data = fp.read()
            return {"kind": "sub", "bytes": data, "name": f"{name}_{job['lang']}.{ext}", "mime": MIME.get(ext, "text/plain")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@st.dialog("파일 미리보기 및 다운로드", width="large")
def download_dialog(job):
    cache = st.session_state["dl_cache"]
    key = job["key"]
    if key not in cache:
        pbar = st.progress(0, text="파일을 준비하고 있습니다...")
        try:
            result = prepare_file(job, pbar)
        except Exception as e:
            pbar.empty()
            st.error(f"다운로드 실패: {e}")
            return
        cache.clear()
        cache[key] = result
        pbar.empty()

    res = cache[key]
    if res["kind"] == "video":
        if "preview" not in res:
            with st.spinner("미리보기를 준비하는 중..."):
                res["preview"] = make_preview(res["bytes"])
        show_video(*res["preview"])
    elif res["kind"] == "audio":
        st.audio(res["bytes"])
    else:
        st.text_area("자막 미리보기", res["bytes"].decode("utf-8", errors="ignore")[:3000], height=200)

    st.markdown(f"**{res['name']}**")
    st.download_button(
        f"⬇️ 다운로드 시작 ({fmt_size(len(res['bytes']))})",
        res["bytes"], res["name"], res["mime"], type="primary", use_container_width=True,
    )


# ==========================================
# 그 외 SNS 추출기
# ==========================================
def extract_rednote(target_url):
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,en-US;q=0.7",
    }
    res = session.get(target_url, headers=headers, timeout=12)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    title, desc, channel, video_bytes, images = "RedNote Content", "", "", None, []

    json_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html, re.DOTALL)
    if json_match:
        try:
            raw_json = re.sub(r":\s*undefined\b", ": null", json_match.group(1))
            state = json.loads(raw_json)
            note_dict = state.get("note", {}).get("noteDetailMap", {})
            note = next(iter(note_dict.values())).get("note", {})
            title = note.get("title") or title
            desc = note.get("desc") or desc
            channel = (note.get("user") or {}).get("nickname", "")

            v_stream = note.get("video", {}).get("media", {}).get("stream", {})
            v_url = (v_stream.get("h264", [{}])[0].get("masterUrl") or v_stream.get("h265", [{}])[0].get("masterUrl"))
            if v_url:
                vr = session.get(v_url, timeout=25)
                if vr.status_code == 200:
                    video_bytes = vr.content

            for img in note.get("imageList", []):
                iu = img.get("urlDefault") or img.get("infoList", [{}])[-1].get("url")
                if iu:
                    ir = session.get(iu, timeout=10)
                    if ir.status_code == 200:
                        images.append(ir.content)
        except Exception:
            pass

    if not video_bytes:
        links = re.findall(r'https?://[^\s"\'<>]+(?:xhscdn\.com|sns-video)[^\s"\'<>]*\.mp4[^\s"\'<>]*', page_html)
        for vl in list(dict.fromkeys(links)):
            try:
                vr = session.get(vl, headers=headers, timeout=15)
                if vr.status_code == 200 and len(vr.content) > 10000:
                    video_bytes = vr.content
                    break
            except Exception:
                pass

    if not desc:
        d_m = re.search(r'<meta\s+(?:name|property)=["\'](?:og:description|description)["\']\s+content=["\'](.*?)["\']', page_html)
        if d_m:
            desc = d_m.group(1)

    if not video_bytes and not images:
        raise Exception("미디어 스트림을 찾지 못했습니다. 링크를 확인해 주세요.")
    return {"title": title, "desc": desc, "channel": channel, "video": video_bytes,
            "images": images, "thumb": images[0] if images else None}


def _norm_media_url(u):
    """이스케이프 해제 + 구간 요청 파라미터(bytestart/byteend) 제거 → 파일 전체를 받도록."""
    u = html.unescape(u).replace("\\/", "/").replace("\\u0026", "&").replace("\\u0025", "%").rstrip("\\")
    p = urlparse(u)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in ("bytestart", "byteend")]
    return urlunparse(p._replace(query=urlencode(q)))


def _stream_types(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    t = set(r.stdout.split())
    return ("video" in t), ("audio" in t)


def extract_threads(target_url):
    """Threads는 영상/음성 트랙이 분리돼 있는 경우가 많아, 각 후보를 받아 종류를 판별한 뒤 병합합니다."""
    session = requests.Session()
    page_headers = {"User-Agent": "facebookexternalhit/1.1", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    dl_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    base_url = target_url.split("?")[0].rstrip("/")
    sources = [
        (target_url, page_headers),
        (target_url, dl_headers),
        (base_url + "/embed", dl_headers),
    ]
    texts = []
    for src_url, hdrs in sources:
        try:
            rr = session.get(src_url, headers=hdrs, timeout=10)
            if rr.status_code == 200 and rr.text:
                texts.append(html.unescape(rr.text).replace(r"\/", "/").replace("\\u0026", "&"))
        except Exception:
            continue
    if not texts:
        raise Exception("Threads 페이지를 불러오지 못했습니다.")
    page_html = "\n".join(texts)
    og_page = texts[0]

    def _og(prop):
        for pat in (
            rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\'](.*?)["\']',
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']{prop}["\']',
        ):
            mm = re.search(pat, og_page)
            if mm and mm.group(1).strip():
                return mm.group(1).strip()
        return ""

    title = _og("og:title") or "Threads Content"
    desc = _og("og:description")
    channel = ""
    h_m = re.search(r"\(@([\w.]+)\)", title)
    if h_m:
        channel = "@" + h_m.group(1)

    # 후보 URL: og:video(보통 음성 포함 완성본) 우선, 그 다음 페이지 내 mp4 전부
    cands = []
    for m in re.finditer(r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\'](.*?)["\']', page_html):
        cands.append(_norm_media_url(m.group(1)))
    for u in re.findall(
        r'https?://[^\s"\'<>\\]*(?:cdninstagram\.com|fbcdn\.net)[^\s"\'<>\\]*?\.mp4[^\s"\'<>\\]*', page_html
    ):
        cands.append(_norm_media_url(u))
    cands = list(dict.fromkeys(cands))[:12]

    tmp = tempfile.mkdtemp()
    video_bytes = None
    try:
        files = []  # (path, has_video, has_audio, size)
        for i, u in enumerate(cands):
            path = os.path.join(tmp, f"c{i}.mp4")
            try:
                r = session.get(u, headers=dl_headers, timeout=25, stream=True)
                if r.status_code != 200:
                    continue
                with open(path, "wb") as fp:
                    for chunk in r.iter_content(1 << 20):
                        fp.write(chunk)
                size = os.path.getsize(path)
                if size < 5000:
                    continue
                hv, ha = _stream_types(path)
                if hv or ha:
                    files.append((path, hv, ha, size))
            except Exception:
                continue

        full = [f for f in files if f[1] and f[2]]
        v_only = [f for f in files if f[1] and not f[2]]
        a_only = [f for f in files if f[2] and not f[1]]

        if full:
            with open(max(full, key=lambda x: x[3])[0], "rb") as fp:
                video_bytes = fp.read()
        elif v_only:
            best_v = max(v_only, key=lambda x: x[3])[0]
            if a_only:
                best_a = max(a_only, key=lambda x: x[3])[0]
                out = os.path.join(tmp, "merged.mp4")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", best_v, "-i", best_a, "-c", "copy", "-movflags", "+faststart", out],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                src = out if os.path.exists(out) and os.path.getsize(out) > 0 else best_v
            else:
                src = best_v
            with open(src, "rb") as fp:
                video_bytes = fp.read()
        # 음성만 있는 경우(a_only만)는 영상이 아니므로 사용하지 않음
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    images = []
    img_urls = re.findall(r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']', page_html)
    for iu in list(dict.fromkeys(img_urls)):
        if "static.cdninstagram.com" not in iu:
            b = fetch_bytes(iu, 10)
            if b:
                images.append(b)

    if not video_bytes and not images:
        raise Exception("Threads 게시물에서 영상/사진을 찾지 못했습니다. (비공개이거나 구조가 바뀌었을 수 있어요)")
    return {"title": title, "desc": desc, "channel": channel, "video": video_bytes,
            "images": [] if video_bytes else images, "thumb": images[0] if images else None}


def extract_generic(target_url):
    """Instagram / TikTok 등. 영상+음성 분리 포맷도 병합해서 받습니다."""
    tmp = tempfile.mkdtemp()
    try:
        opts = base_ydl_opts(tmp)
        opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
        path = _pick_output(tmp, (".mp4", ".mov", ".mkv", ".webm"))
        with open(path, "rb") as fp:
            video_bytes = fp.read()
        return {
            "title": info.get("title", "SNS Media"), "desc": info.get("description", "") or "",
            "channel": info.get("uploader") or info.get("channel") or "",
            "date": fmt_date(info.get("upload_date")),
            "views": info.get("view_count"), "likes": info.get("like_count"), "comments": info.get("comment_count"),
            "video": video_bytes, "images": [],
            "thumb": fetch_bytes(info["thumbnail"]) if info.get("thumbnail") else None,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def clear_text():
    st.session_state["main_url_field"] = ""
    st.session_state["processed_result"] = None
    st.session_state["mp3_bytes"] = None
    st.session_state["yt"] = None


# ==========================================
# 그 외 SNS: 다이얼로그 (편집본 / 원본 / MP3)
# ==========================================
@st.dialog("파일 미리보기 및 다운로드", width="large")
def local_dialog(kind):
    res = st.session_state.get("processed_result")
    if not res:
        return
    name = safe_name(res["title"])

    if kind in ("edited", "raw"):
        if kind == "edited":
            data, preview = res["video"], res["preview"]
            ed = res["edit"]
            fname = f"{name}_{ed['speed']}x{'_flip' if ed['flip'] else ''}.mp4"
        else:
            data = res["raw_video"]
            if not res["edited"]:
                preview = res["preview"]
            else:
                if not res.get("raw_preview"):
                    with st.spinner("미리보기를 준비하는 중..."):
                        res["raw_preview"] = make_preview(data)
                preview = res["raw_preview"]
            fname = f"{name}_raw.mp4"
        show_video(*preview)
        mime = "video/mp4"
    elif kind == "mp3":
        if not st.session_state.get("mp3_bytes"):
            with st.spinner("MP3 추출 중..."):
                st.session_state["mp3_bytes"] = extract_mp3_audio(res["video"])
        data = st.session_state["mp3_bytes"]
        if not data:
            st.error("MP3 추출에 실패했습니다. ffmpeg가 설치되어 있는지 확인해 주세요.")
            return
        st.audio(data)
        fname, mime = f"{name}.mp3", "audio/mpeg"
    else:
        return

    st.markdown(f"**{fname}**")
    st.download_button(
        f"⬇️ 다운로드 시작 ({fmt_size(len(data))})", data, fname, mime, type="primary", use_container_width=True,
    )


# ==========================================
# UI: 헤더 / 입력창
# ==========================================
st.markdown('<div class="snap-title">SNS 다운로더</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="snap-sub">YouTube (Shorts/Longform) · RedNote · TikTok · Reels · Threads 무워터마크 저장</div>',
    unsafe_allow_html=True,
)

c_in, c_clear, c_btn = st.columns([4.4, 0.5, 1.5])
with c_in:
    url_input_val = st.text_input(
        "URL", key="main_url_field",
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )
with c_clear:
    st.button("✖", on_click=clear_text, help="주소 지우기", use_container_width=True)
with c_btn:
    submit_btn = st.button("다운로드 링크 받기", use_container_width=True, type="primary")

st.markdown('<div class="support-sites">YouTube, RedNote(샤오홍슈), TikTok, Instagram, Threads 지원</div>',
            unsafe_allow_html=True)

with st.expander("⚙️ 영상 편집 옵션", expanded=False):
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        opt_flip = st.checkbox("🔄 좌우 대칭 변경 (반전)", value=False)
    with col_opt2:
        SPEED_MIN, SPEED_MAX, SPEED_STEP = 1.0, 2.0, 0.5   # ← 간격을 0.05로 바꾸면 1.0, 1.05, 1.1 ...
        speed_options = [round(SPEED_MIN + i * SPEED_STEP, 2)
                         for i in range(int(round((SPEED_MAX - SPEED_MIN) / SPEED_STEP)) + 1)]
        opt_speed = st.selectbox("⏩ 배속 선택", speed_options, index=0)


# ==========================================
# 실행
# ==========================================
if submit_btn:
    if not url_input_val.strip():
        st.warning("영상 링크를 입력해 주세요.")
    else:
        target_link = clean_social_url(url_input_val)
        if not target_link:
            st.error("입력한 텍스트에서 올바른 링크를 찾을 수 없습니다.")
        else:
            st.session_state["yt"] = None
            st.session_state["processed_result"] = None
            st.session_state["mp3_bytes"] = None
            st.session_state["dl_cache"] = {}
            try:
                if is_youtube(target_link):
                    with st.spinner("⚡ 다운로드 링크를 준비하는 중..."):
                        st.session_state["yt"] = fetch_youtube_info(target_link)
                else:
                    p_bar = st.progress(30, text="⚡ 미디어 무워터마크 스트림 추출 중... 30%")
                    if "rednote.com" in target_link or "xiaohongshu.com" in target_link:
                        raw = extract_rednote(target_link)
                    elif "threads.net" in target_link or "threads.com" in target_link:
                        try:
                            raw = extract_threads(target_link)
                        except Exception:
                            raw = {"title": "Threads Content", "desc": "", "channel": "", "video": None,
                                   "images": [], "thumb": None}
                        if not raw.get("video"):
                            # 페이지에서 영상을 못 찾으면 yt-dlp로 한 번 더 시도
                            try:
                                g = extract_generic(target_link)
                                if g.get("video"):
                                    raw["video"] = g["video"]
                                    raw["images"] = []  # og:image는 게시물 카드 캡처라 영상이 있으면 제외
                                    raw["thumb"] = raw.get("thumb") or g.get("thumb")
                                    for k in ("channel", "date", "views", "likes", "comments"):
                                        raw[k] = raw.get(k) or g.get(k)
                                    raw["desc"] = raw.get("desc") or g.get("desc", "")
                            except Exception:
                                pass
                        if not raw.get("video") and not raw.get("images"):
                            raise Exception("Threads 게시물에서 영상/사진을 찾지 못했습니다.")
                    else:
                        raw = extract_generic(target_link)

                    raw_video = raw.get("video")
                    final_video, preview, vinfo = None, None, {}
                    if raw_video:
                        p_bar.progress(65, text=f"✂️ 영상 편집 처리 중 (배속: {opt_speed}x)... 65%")
                        final_video = process_editing(raw_video, opt_flip, opt_speed)
                        p_bar.progress(90, text="🎞 미리보기 생성 중... 90%")
                        preview = make_preview(final_video)
                        vinfo = probe_video(raw_video)
                    p_bar.empty()

                    st.session_state["processed_result"] = {
                        "title": raw.get("title", "SNS Media"),
                        "meta": {
                            "title": raw.get("title", "SNS Media"), "channel": raw.get("channel", ""),
                            "platform": platform_name(target_link), "date": raw.get("date", ""),
                            "views": raw.get("views"), "likes": raw.get("likes"), "comments": raw.get("comments"),
                            "desc": raw.get("desc", ""), "thumb": raw.get("thumb"),
                        },
                        "video": final_video, "raw_video": raw_video, "preview": preview, "vinfo": vinfo,
                        "images": raw.get("images", []),
                        "edit": {"flip": opt_flip, "speed": opt_speed},
                        "edited": bool(opt_flip or opt_speed != 1.0),
                    }
            except Exception as err:
                st.error(f"다운로드 실패: {err}")


# ==========================================
# UI: YouTube 결과
# ==========================================
yt = st.session_state.get("yt")
if yt:
    st.success("✅ 다운로드 링크가 준비되었습니다!")
    render_header(yt, "yt")

    st.markdown("---")
    col_v, col_a, col_s = st.columns(3)

    with col_v:
        st.markdown('<div class="col-head">🎬 영상 (음성 포함)</div>', unsafe_allow_html=True)
        for o in yt["video_opts"]:
            if format_row(o["label"], fmt_size(o["size"]), f"v_{o['fid']}"):
                fmt = f"{o['fid']}+bestaudio[ext=m4a]/{o['fid']}+bestaudio/b[height<={o['height']}]"
                download_dialog({
                    "kind": "video", "url": yt["url"], "fmt": fmt, "height": o["height"],
                    "title": yt["title"], "flip": opt_flip, "speed": opt_speed,
                    "key": f"v|{o['fid']}|{opt_flip}|{opt_speed}",
                })
        if not yt["video_opts"]:
            st.caption("사용 가능한 영상 포맷이 없습니다. yt-dlp 업데이트와 JS 런타임(Deno)을 확인해 주세요.")

    with col_a:
        st.markdown('<div class="col-head">🎵 오디오</div>', unsafe_allow_html=True)
        for o in [{"fid": "mp3", "label": "MP3 (변환)", "size": 0}] + yt["audio_opts"]:
            if format_row(o["label"], fmt_size(o["size"]) if o["size"] else "&nbsp;", f"a_{o['fid']}"):
                download_dialog({
                    "kind": "audio", "url": yt["url"], "fmt": o["fid"],
                    "title": yt["title"], "key": f"a|{o['fid']}",
                })

    with col_s:
        st.markdown('<div class="col-head">💬 자막</div>', unsafe_allow_html=True)
        if yt["subs"]:
            labels = [s["label"] for s in yt["subs"]]
            sel = st.selectbox("자막 언어", labels, label_visibility="collapsed")
            s = yt["subs"][labels.index(sel)]
            if st.button("자막 다운로드", key="sub_dl", type="primary", use_container_width=True):
                download_dialog({
                    "kind": "sub", "url": yt["url"], "lang": s["lang"], "auto": s["auto"],
                    "title": yt["title"], "key": f"s|{s['lang']}|{s['auto']}",
                })
        else:
            st.caption("사용 가능한 자막이 없습니다.")


# ==========================================
# UI: 그 외 SNS 결과 (YouTube와 동일한 레이아웃)
# ==========================================
res = st.session_state.get("processed_result")
if res:
    st.success("✅ 다운로드 링크가 준비되었습니다!")
    render_header(res["meta"], "sns")

    vid, raw_vid, imgs = res["video"], res["raw_video"], res["images"]

    if vid:
        st.markdown("---")
        pv_bytes, pv_w, pv_h = res["preview"]
        vi0 = res.get("vinfo") or {}
        st.markdown('<div class="col-head">▶ 미리보기</div>', unsafe_allow_html=True)
        show_video(pv_bytes, pv_w, pv_h)
        size_note = f"원본 크기 {vi0['w']}×{vi0['h']}" if vi0.get("w") and vi0.get("h") else ""
        edit_note = " · 편집 적용본" if res["edited"] else ""
        st.caption(f"{size_note}{edit_note}".strip(" ·"))
        st.markdown("---")
        col_v, col_a = st.columns(2)
        vi = res.get("vinfo") or {}
        res_txt = f"{vi['w']}×{vi['h']} · " if vi.get("w") and vi.get("h") else ""

        with col_v:
            st.markdown('<div class="col-head">🎬 영상 (음성 포함)</div>', unsafe_allow_html=True)
            if res["edited"]:
                ed = res["edit"]
                tag = f"{ed['speed']}배속" + (" · 좌우반전" if ed["flip"] else "")
                if format_row(f"{res_txt}MP4 · 편집본 ({tag})", fmt_size(len(vid)), "g_edited"):
                    local_dialog("edited")
            if format_row(f"{res_txt}MP4 · 원본", fmt_size(len(raw_vid)), "g_raw"):
                local_dialog("raw")

        with col_a:
            st.markdown('<div class="col-head">🎵 오디오</div>', unsafe_allow_html=True)
            if format_row("MP3 (변환)", "&nbsp;", "g_mp3"):
                local_dialog("mp3")

    if imgs:
        st.markdown("---")
        st.markdown(f'<div class="col-head">🖼 사진 ({len(imgs)}장)</div>', unsafe_allow_html=True)
        img_cols = st.columns(3)
        for idx, img_b in enumerate(imgs):
            with img_cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(f"⬇️ 사진 #{idx + 1} 받기", img_b, f"photo_{idx + 1}.jpg", "image/jpeg",
                                   key=f"img_btn_{idx}", type="primary", use_container_width=True)

    if not vid and not imgs:
        st.warning("다운로드할 미디어를 찾지 못했습니다.")
