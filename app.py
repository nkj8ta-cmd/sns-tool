import glob
import html
import json
import os
import re
import shutil
import subprocess
import tempfile

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
    .stApp {
        background: linear-gradient(135deg, #eef4ff 0%, #ffffff 50%, #fff3ea 100%);
    }
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
    .status-bar {
        background: #eff6ff; border: 1px solid #bfdbfe; color: #1d4ed8; padding: 8px 12px;
        border-radius: 6px; font-size: 13.5px; font-weight: 600; margin: 10px 0; text-align: center;
    }
    /* 파란 라운드 버튼 (스크린샷 스타일) */
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
    return re.sub(r'[\\/*?:"<>|]', "", title or "media").strip()[:n] or "media"


def fmt_size(b):
    return f"{b / (1024 * 1024):.1f}MB" if b else "용량 미확인"


def fmt_num(n):
    if n is None:
        return "-"
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    return f"{n:,}"


def base_ydl_opts(outdir=None):
    """유튜브 클라이언트를 강제하지 않고 yt-dlp 기본값을 사용합니다."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
    }
    if outdir:
        opts["outtmpl"] = os.path.join(outdir, "%(id)s.%(ext)s")
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    return opts


# ==========================================
# YouTube: 정보 조회 (다운로드 없이 포맷 목록만)
# ==========================================
def fetch_youtube_info(url):
    opts = base_ydl_opts()
    opts.update({"skip_download": True, "ignore_no_formats_error": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []

    # 영상 (화질별 1개, mp4 우선)
    best_by_h = {}
    for f in formats:
        if f.get("ext") == "mhtml" or not f.get("height"):
            continue
        if f.get("vcodec") in (None, "none"):
            continue
        if f.get("acodec") not in (None, "none"):
            continue  # 영상 전용만
        h = f["height"]
        score = (f.get("ext") == "mp4", f.get("tbr") or 0)
        if h not in best_by_h or score > best_by_h[h][0]:
            best_by_h[h] = (score, f)
    video_opts = []
    for h in sorted(best_by_h, reverse=True):
        f = best_by_h[h][1]
        video_opts.append(
            {
                "height": h,
                "fid": f["format_id"],
                "label": f"{h}p · {int(f.get('tbr') or 0)}kbps · {str(f.get('ext', '')).upper()}",
                "size": f.get("filesize") or f.get("filesize_approx") or 0,
            }
        )

    # 오디오
    seen, audio_opts = set(), []
    for f in sorted(formats, key=lambda x: x.get("abr") or 0, reverse=True):
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        key = (f.get("language"), f.get("ext"), round(f.get("abr") or 0))
        if key in seen:
            continue
        seen.add(key)
        audio_opts.append(
            {
                "fid": f["format_id"],
                "label": f"{int(f.get('abr') or 0)}kbps · {f.get('language') or 'default'} · {str(f.get('ext', '')).upper()}",
                "size": f.get("filesize") or f.get("filesize_approx") or 0,
            }
        )
        if len(audio_opts) >= 6:
            break

    # 자막 (수동 자막 전체 + 자동 자막은 주요 언어만)
    subs = [{"lang": k, "auto": False, "label": k} for k in (info.get("subtitles") or {})]
    for k in info.get("automatic_captions") or {}:
        if k.endswith("-orig") or k in ("ko", "en", "ja", "zh-Hans"):
            subs.append({"lang": k, "auto": True, "label": f"{k} (자동)"})

    thumb_bytes = None
    if info.get("thumbnail"):
        try:
            tr = requests.get(info["thumbnail"], timeout=8)
            if tr.status_code == 200:
                thumb_bytes = tr.content
        except Exception:
            pass

    ud = info.get("upload_date") or ""
    date = f"{ud[:4]}. {int(ud[4:6])}. {int(ud[6:8])}." if len(ud) == 8 else ""

    return {
        "url": url,
        "title": info.get("title", "YouTube"),
        "channel": info.get("uploader") or info.get("channel") or "",
        "date": date,
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "comments": info.get("comment_count"),
        "desc": info.get("description") or "",
        "thumb": thumb_bytes,
        "video_opts": video_opts,
        "audio_opts": audio_opts,
        "subs": subs,
    }


# ==========================================
# FFmpeg 편집
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


# ==========================================
# YouTube: 실제 파일 준비 (다이얼로그 안에서 실행)
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
            opts.update(
                {
                    "skip_download": True,
                    "writesubtitles": not job["auto"],
                    "writeautomaticsub": job["auto"],
                    "subtitleslangs": [job["lang"]],
                    "subtitlesformat": "srt/vtt/best",
                    "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}],
                }
            )
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
        cache.clear()  # 메모리 절약: 마지막 파일만 보관
        cache[key] = result
        pbar.empty()

    res = cache[key]
    if res["kind"] == "video":
        st.video(res["bytes"])
    elif res["kind"] == "audio":
        st.audio(res["bytes"])
    else:
        st.text_area("자막 미리보기", res["bytes"].decode("utf-8", errors="ignore")[:3000], height=200)

    st.markdown(f"**{res['name']}**")
    st.download_button(
        f"⬇️ 다운로드 시작 ({fmt_size(len(res['bytes']))})",
        res["bytes"],
        res["name"],
        res["mime"],
        type="primary",
        use_container_width=True,
    )


# ==========================================
# 그 외 SNS (RedNote / Threads / Instagram / TikTok 등)
# ==========================================
def extract_rednote(target_url):
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,en-US;q=0.7",
    }
    res = session.get(target_url, headers=headers, timeout=12)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    title, desc, video_bytes, images = "RedNote Content", "", None, []

    json_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html, re.DOTALL)
    if json_match:
        try:
            raw_json = re.sub(r":\s*undefined\b", ": null", json_match.group(1))
            state = json.loads(raw_json)
            note_dict = state.get("note", {}).get("noteDetailMap", {})
            note = next(iter(note_dict.values())).get("note", {})
            title = note.get("title") or title
            desc = note.get("desc") or desc

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
    return {"title": title, "desc": desc, "video": video_bytes, "images": images, "thumb": images[0] if images else None}


def extract_threads(target_url):
    session = requests.Session()
    headers = {"User-Agent": "facebookexternalhit/1.1", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    res = session.get(target_url, headers=headers, timeout=10)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    desc, video_bytes, images = "", None, []
    d_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', page_html)
    if d_m:
        desc = d_m.group(1)

    video_urls = re.findall(r'(https?://[^\s"\'<>]*(?:cdninstagram\.com|fbcdn\.net)[^\s"\'<>]*?\.mp4[^\s"\'<>]*)', page_html)
    for vu in list(dict.fromkeys(video_urls)):
        try:
            vr = session.get(vu, timeout=15)
            if vr.status_code == 200 and len(vr.content) > 5000:
                video_bytes = vr.content
                break
        except Exception:
            pass

    img_urls = re.findall(r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']', page_html)
    for iu in list(dict.fromkeys(img_urls)):
        if "static.cdninstagram.com" not in iu:
            try:
                ir = session.get(iu, timeout=10)
                if ir.status_code == 200:
                    images.append(ir.content)
            except Exception:
                pass
    return {"title": "Threads Content", "desc": desc, "video": video_bytes, "images": images,
            "thumb": images[0] if images else None}


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
        thumb_bytes = None
        if info.get("thumbnail"):
            try:
                tr = requests.get(info["thumbnail"], timeout=8)
                if tr.status_code == 200:
                    thumb_bytes = tr.content
            except Exception:
                pass
        return {"title": info.get("title", "SNS Media"), "desc": info.get("description", ""),
                "video": video_bytes, "images": [], "thumb": thumb_bytes}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def clear_text():
    st.session_state["main_url_field"] = ""
    st.session_state["processed_result"] = None
    st.session_state["mp3_bytes"] = None
    st.session_state["yt"] = None


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
        opt_speed = st.selectbox("⏩ 배속 선택", [1.0, 1.05, 1.1, 1.15, 1.2], index=2)


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
                        raw = extract_threads(target_link)
                    else:
                        raw = extract_generic(target_link)
                    p_bar.progress(75, text=f"✂️ 영상 편집 처리 중 (배속: {opt_speed}x)... 75%")
                    final_video = process_editing(raw["video"], opt_flip, opt_speed) if raw.get("video") else None
                    p_bar.empty()
                    st.session_state["processed_result"] = {
                        "video": final_video, "raw_video": raw.get("video"),
                        "images": raw.get("images", []), "title": raw.get("title", "SNS Media"),
                        "desc": raw.get("desc", ""), "thumb": raw.get("thumb"), "speed_used": opt_speed,
                    }
            except Exception as err:
                st.error(f"다운로드 실패: {err}")


# ==========================================
# UI: YouTube 결과 (영상 / 오디오 / 자막 3열)
# ==========================================
yt = st.session_state.get("yt")
if yt:
    st.success("✅ 다운로드 링크가 준비되었습니다!")
    left, right = st.columns([1, 1.5])
    with left:
        if yt["thumb"]:
            st.image(yt["thumb"], use_container_width=True)
            st.download_button("🖼 커버 이미지 다운로드", yt["thumb"], "cover.jpg", "image/jpeg",
                               type="primary", use_container_width=True)
    with right:
        st.markdown(f"### {yt['title']}")
        meta = " · ".join(
            x for x in [
                f"<b>{yt['channel']}</b>" if yt["channel"] else "",
                yt["date"],
                f"{fmt_num(yt['views'])} 조회",
                f"{fmt_num(yt['likes'])} 좋아요",
                f"{fmt_num(yt['comments'])} 댓글",
            ] if x
        )
        st.markdown(f'<div class="meta-line">{meta}</div>', unsafe_allow_html=True)
        if yt["desc"]:
            st.caption(yt["desc"][:120] + ("..." if len(yt["desc"]) > 120 else ""))
            with st.expander("더 보기"):
                st.text(yt["desc"])

    st.markdown("---")
    col_v, col_a, col_s = st.columns(3)

    with col_v:
        st.markdown('<div class="col-head">🎬 영상 (음성 포함)</div>', unsafe_allow_html=True)
        for o in yt["video_opts"]:
            r1, r2 = st.columns([1.6, 1])
            with r1:
                st.markdown(f'<div class="fmt-label">{o["label"]}</div><div class="fmt-size">{fmt_size(o["size"])}</div>',
                            unsafe_allow_html=True)
            with r2:
                if st.button("다운로드", key=f"v_{o['fid']}", type="primary", use_container_width=True):
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
        audio_rows = [{"fid": "mp3", "label": "MP3 (변환)", "size": 0}] + yt["audio_opts"]
        for o in audio_rows:
            r1, r2 = st.columns([1.6, 1])
            with r1:
                size_txt = fmt_size(o["size"]) if o["size"] else ""
                st.markdown(f'<div class="fmt-label">{o["label"]}</div><div class="fmt-size">{size_txt}</div>',
                            unsafe_allow_html=True)
            with r2:
                if st.button("다운로드", key=f"a_{o['fid']}", type="primary", use_container_width=True):
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
# UI: 그 외 SNS 결과 카드
# ==========================================
if st.session_state.get("processed_result"):
    data = st.session_state["processed_result"]
    vid, raw_vid = data.get("video"), data.get("raw_video")
    imgs, title, desc = data.get("images", []), data.get("title", ""), data.get("desc", "")
    thumb, speed_used = data.get("thumb"), data.get("speed_used", 1.0)

    st.success("✅ 다운로드 링크가 준비되었습니다!")
    st.markdown("#### 🎬 파일 미리보기 및 다운로드")

    if vid:
        st.video(vid)
        clean_name = safe_name(title)
        st.markdown(f"**{clean_name}.mp4**")
        st.markdown('<div class="status-bar">다운로드가 완료되었습니다.</div>', unsafe_allow_html=True)

        c_d1, c_d2 = st.columns([1.5, 1])
        with c_d1:
            st.download_button(
                f"⬇️ {speed_used}배속 편집 영상 다운로드 ({fmt_size(len(vid))})",
                vid, f"{clean_name}_{speed_used}x.mp4", "video/mp4", type="primary", use_container_width=True,
            )
            if raw_vid and speed_used != 1.0:
                st.download_button(
                    f"⬇️ 원본 영상 다운로드 ({fmt_size(len(raw_vid))})",
                    raw_vid, f"{clean_name}_raw.mp4", "video/mp4", use_container_width=True,
                )
        with c_d2:
            if thumb:
                st.download_button("🖼 커버 이미지 다운로드", thumb, "cover.jpg", "image/jpeg", use_container_width=True)

        st.markdown("<hr style='margin: 8px 0;'>", unsafe_allow_html=True)
        a1, a2 = st.columns([1.5, 1])
        with a1:
            if st.button("🎵 고음질 MP3 음원 분리", use_container_width=True):
                with st.spinner("MP3 추출 중..."):
                    st.session_state["mp3_bytes"] = extract_mp3_audio(vid)
        if st.session_state.get("mp3_bytes"):
            mp3_d = st.session_state["mp3_bytes"]
            with a2:
                st.download_button(f"⬇️ MP3 다운로드 ({fmt_size(len(mp3_d))})", mp3_d,
                                   f"{clean_name}.mp3", "audio/mpeg", use_container_width=True)
        if desc:
            st.text_area("게시물 원본 텍스트", desc, height=95)

    elif imgs:
        st.markdown(f"**고화질 사진 ({len(imgs)}장)**")
        img_cols = st.columns(3)
        for idx, img_b in enumerate(imgs):
            with img_cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(f"⬇️ 사진 #{idx + 1} 받기", img_b, f"photo_{idx + 1}.jpg", "image/jpeg",
                                   key=f"img_btn_{idx}", use_container_width=True)
