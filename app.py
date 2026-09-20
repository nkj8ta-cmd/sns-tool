import glob
import html
import io
import json
import os
import re
import subprocess
import tempfile
import time
import requests
import streamlit as st
import yt_dlp

# 1. 사이트 이름: SNS 다운로더
st.set_page_config(
    page_title="SNS 다운로더",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container {
        padding-top: 3rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 820px;
    }
    .snap-title {
        text-align: center;
        font-size: 32px;
        font-weight: 800;
        background: linear-gradient(90deg, #2563eb, #ec4899, #f97316);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 6px;
    }
    .snap-sub {
        text-align: center;
        font-size: 14.5px;
        color: #64748b;
        margin-bottom: 24px;
    }
    .support-sites {
        text-align: center;
        font-size: 12.5px;
        color: #94a3b8;
        margin-top: 8px;
        margin-bottom: 20px;
    }
    .status-bar {
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        color: #1d4ed8;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 13.5px;
        font-weight: 600;
        margin: 10px 0;
        text-align: center;
    }
</style>

<script>
async function pasteFromClipboard() {
    try {
        const text = await navigator.clipboard.readText();
        if (text) {
            const inputs = window.parent.document.querySelectorAll('input[type="text"]');
            for (let input of inputs) {
                if (input.placeholder && input.placeholder.includes("붙여넣")) {
                    input.value = text;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    break;
                }
            }
        }
    } catch (e) {
        alert("클립보드 접근 권한을 허용해 주세요. (Ctrl+V로 직접 붙여넣으셔도 됩니다)");
    }
}
</script>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "main_url_field" not in st.session_state:
    st.session_state["main_url_field"] = ""
if "processed_result" not in st.session_state:
    st.session_state["processed_result"] = None
if "mp3_bytes" not in st.session_state:
    st.session_state["mp3_bytes"] = None


# ==========================================
# 1. URL 정제 (단축링크 & 쇼츠 정규화)
# ==========================================
def clean_social_url(raw_text):
    m = re.search(r"https?://[^\s<>\"']+", raw_text)
    if not m:
        return ""
    clean = m.group(0)

    if any(k in clean for k in ["xhslink.com", "v.douyin.com", "vt.tiktok.com", "youtu.be", "/share/"]):
        try:
            r = requests.head(clean, allow_redirects=True, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            clean = r.url
        except Exception:
            pass

    if "youtube.com/shorts/" in clean:
        shorts_id = clean.split("shorts/")[1].split("?")[0].split("&")[0]
        clean = f"https://www.youtube.com/watch?v={shorts_id}"
    elif "youtu.be/" in clean:
        vid_id = clean.split("youtu.be/")[1].split("?")[0]
        clean = f"https://www.youtube.com/watch?v={vid_id}"

    return clean


# ==========================================
# 2. RedNote (샤오홍슈) 무워터마크 직접 추출
# ==========================================
def extract_rednote(target_url):
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,en-US;q=0.7",
    }

    res = session.get(target_url, headers=headers, timeout=12)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    title = "RedNote Content"
    desc = ""
    video_bytes = None
    images = []

    json_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html, re.DOTALL)
    if json_match:
        try:
            raw_json = json_match.group(1)
            raw_json = re.sub(r":\s*undefined\b", ": null", raw_json)
            state = json.loads(raw_json)
            note_dict = state.get("note", {}).get("noteDetailMap", {})
            note = next(iter(note_dict.values())).get("note", {})
            title = note.get("title") or title
            desc = note.get("desc") or desc

            v_stream = note.get("video", {}).get("media", {}).get("stream", {})
            v_url = (
                v_stream.get("h264", [{}])[0].get("masterUrl")
                or v_stream.get("h265", [{}])[0].get("masterUrl")
            )
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
        video_links = re.findall(r'https?://[^\s"\'<>]+(?:xhscdn\.com|sns-video)[^\s"\'<>]*\.mp4[^\s"\'<>]*', page_html)
        for vl in list(dict.fromkeys(video_links)):
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

    thumb = images[0] if images else None
    return {"title": title, "desc": desc, "video": video_bytes, "images": images, "thumb": thumb}


# ==========================================
# 3. Threads (스레드) 추출 엔진
# ==========================================
def extract_threads(target_url):
    session = requests.Session()
    headers = {"User-Agent": "facebookexternalhit/1.1", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    res = session.get(target_url, headers=headers, timeout=10)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    title = "Threads Content"
    desc = ""
    video_bytes = None
    images = []

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

    thumb = images[0] if images else None
    return {"title": title, "desc": desc, "video": video_bytes, "images": images, "thumb": thumb}


# ==========================================
# 4. YouTube 봇 차단 우회 & Instagram 범용 추출
# ==========================================
def extract_generic_or_youtube(target_url):
    temp_dir = tempfile.mkdtemp()

    # 봇 차단(Sign in to confirm you're not a bot) 우회 클라이언트 다중 시도
    client_fallbacks = [
        ["android"],
        ["web_creator"],
        ["web_safari"],
        ["mweb", "web"],
    ]

    last_error = None

    for client_list in client_fallbacks:
        ydl_opts = {
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "format": "best[ext=mp4]/best",
        }

        if os.path.exists("cookies.txt"):
            ydl_opts["cookiefile"] = "cookies.txt"

        if "youtube.com" in target_url or "youtu.be" in target_url:
            ydl_opts["extractor_args"] = {
                "youtube": {
                    "player_client": client_list
                }
            }
            ydl_opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=True)
                title = info.get("title", "SNS Media")
                desc = info.get("description", "")
                thumb_url = info.get("thumbnail")

            video_bytes = None
            for f in glob.glob(os.path.join(temp_dir, "*")):
                if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
                    with open(f, "rb") as fp:
                        video_bytes = fp.read()
                    break

            if video_bytes:
                thumb_bytes = None
                if thumb_url:
                    try:
                        tr = requests.get(thumb_url, timeout=8)
                        if tr.status_code == 200:
                            thumb_bytes = tr.content
                    except Exception:
                        pass
                return {"title": title, "desc": desc, "video": video_bytes, "images": [], "thumb": thumb_bytes}

        except Exception as e:
            last_error = e
            continue

    raise last_error or Exception("YouTube 비디오를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.")


# ==========================================
# 5. FFmpeg 편집 엔진 (좌우 반전 & 1.1배속)
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

    cmd = (
        ["ffmpeg", "-y", "-i", in_path]
        + vf_cmd
        + af_cmd
        + ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-strict", "experimental", out_path]
    )
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            res = f.read()
        os.remove(in_path)
        os.remove(out_path)
        return res
    return video_bytes


# MP3 음원 추출
def extract_mp3_audio(video_bytes):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name
    out_path = in_path.replace(".mp4", ".mp3")
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mp3_res = None
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            mp3_res = f.read()
        os.remove(out_path)
    os.remove(in_path)
    return mp3_res


def clear_text():
    st.session_state["main_url_field"] = ""
    st.session_state["processed_result"] = None
    st.session_state["mp3_bytes"] = None


# ==========================================
# UI 1. 헤더 (SNS 다운로더)
# ==========================================
st.markdown('<div class="snap-title">SNS 다운로더</div>', unsafe_allow_html=True)
st.markdown('<div class="snap-sub">YouTube (Shorts/Longform) · RedNote · TikTok · Reels · Threads 무워터마크 저장</div>', unsafe_allow_html=True)


# ==========================================
# UI 2. 주소 입력창 (지우기 ✖ & 붙여넣기 📋)
# ==========================================
c_in, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_in:
    url_input_val = st.text_input(
        "URL",
        key="main_url_field",
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )

with c_clear:
    st.button("✖", on_click=clear_text, help="주소 지우기", use_container_width=True)

with c_paste:
    st.button("📋", help="클립보드에서 붙여넣기", use_container_width=True)
    st.markdown(
        """
        <script>
        const pasteBtns = window.parent.document.querySelectorAll('button');
        for (let b of pasteBtns) {
            if (b.innerText.includes('📋') && !b.dataset.pbound) {
                b.dataset.pbound = "true";
                b.addEventListener('click', pasteFromClipboard);
            }
        }
        </script>
        """,
        unsafe_allow_html=True,
    )

with c_btn:
    submit_btn = st.button("다운로드 링크 받기", use_container_width=True, type="primary")

st.markdown(
    '<div class="support-sites">YouTube, RedNote(샤오홍슈), TikTok, Instagram, Threads 지원</div>',
    unsafe_allow_html=True,
)


# ==========================================
# UI 3. 영상 편집 옵션 (반전 기본 미체크, 배속 기본 1.1)
# ==========================================
with st.expander("⚙️ 영상 편집 옵션", expanded=False):
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        opt_flip = st.checkbox("🔄 좌우 대칭 변경 (반전)", value=False)
    with col_opt2:
        # 요청사항 반영: 배속 기본값을 1.1로 설정 (index=2)
        opt_speed = st.selectbox("⏩ 배속 선택", [1.0, 1.05, 1.1, 1.15, 1.2], index=2)


# ==========================================
# 다운로드 실행 및 실시간 진행률 바
# ==========================================
if submit_btn:
    if not url_input_val.strip():
        st.warning("영상 링크를 입력해 주세요.")
    else:
        target_link = clean_social_url(url_input_val)
        if not target_link:
            st.error("입력한 텍스트에서 올바른 링크를 찾을 수 없습니다.")
        else:
            p_bar = st.progress(10, text="⌛ 링크 분석 및 모바일 리다이렉트 추적... 10%")
            try:
                time.sleep(0.2)
                p_bar.progress(40, text="⚡ 미디어 무워터마크 스트림 추출 중... 40%")

                if "rednote.com" in target_link or "xiaohongshu.com" in target_link:
                    raw = extract_rednote(target_link)
                elif "threads.net" in target_link or "threads.com" in target_link:
                    raw = extract_threads(target_link)
                else:
                    raw = extract_generic_or_youtube(target_link)

                p_bar.progress(75, text=f"✂️ 영상 편집 처리 중 (배속: {opt_speed}x)... 75%")

                final_video = None
                if raw.get("video"):
                    final_video = process_editing(raw["video"], opt_flip, opt_speed)

                p_bar.progress(100, text="✅ 완료! 다운로드 링크가 준비되었습니다.")
                time.sleep(0.3)
                p_bar.empty()

                st.session_state["processed_result"] = {
                    "video": final_video,
                    "raw_video": raw.get("video"),
                    "images": raw.get("images", []),
                    "title": raw.get("title", "SNS Media"),
                    "desc": raw.get("desc", ""),
                    "thumb": raw.get("thumb"),
                    "speed_used": opt_speed,
                }
                st.session_state["mp3_bytes"] = None
                st.success("✅ 다운로드 링크가 준비되었습니다!")
            except Exception as err:
                p_bar.empty()
                st.error(f"다운로드 실패: {err}")


# ==========================================
# UI 4. 파일 미리보기 및 다운로드 카드 (스크린샷 15 스타일)
# ==========================================
if st.session_state.get("processed_result"):
    data = st.session_state["processed_result"]
    vid = data.get("video")
    raw_vid = data.get("raw_video")
    imgs = data.get("images", [])
    title = data.get("title", "")
    desc = data.get("desc", "")
    thumb = data.get("thumb")
    speed_used = data.get("speed_used", 1.0)

    st.markdown("---")
    st.markdown("#### 🎬 파일 미리보기 및 다운로드")

    if vid:
        # 1) 영상 미리보기 플레이어
        st.video(vid)

        # 2) 파일명 및 상태 바
        clean_name = re.sub(r'[\\/*?:"<>|]', "", title)[:40]
        st.markdown(f"**{clean_name}.mp4**")
        st.markdown('<div class="status-bar">다운로드가 완료되었습니다.</div>', unsafe_allow_html=True)

        # 3) 다운로드 버튼 영역
        v_mb = round(len(vid) / (1024 * 1024), 1)
        c_d1, c_d2 = st.columns([1.5, 1])
        with c_d1:
            st.download_button(
                f"⬇️ {speed_used}배속 편집 영상 다운로드 ({v_mb} MB)",
                vid,
                f"{clean_name}_{speed_used}x.mp4",
                "video/mp4",
                type="primary",
                use_container_width=True,
            )
            if raw_vid and speed_used != 1.0:
                raw_mb = round(len(raw_vid) / (1024 * 1024), 1)
                st.download_button(
                    f"⬇️ 원본 영상 다운로드 ({raw_mb} MB)",
                    raw_vid,
                    f"{clean_name}_raw.mp4",
                    "video/mp4",
                    use_container_width=True,
                )
        with c_d2:
            if thumb:
                st.download_button(
                    "🖼 커버 이미지 다운로드",
                    thumb,
                    "cover.jpg",
                    "image/jpeg",
                    use_container_width=True,
                )

        st.markdown("<hr style='margin: 8px 0;'>", unsafe_allow_html=True)

        # MP3 음원 분리
        a1, a2 = st.columns([1.5, 1])
        with a1:
            if st.button("🎵 고음질 MP3 음원 분리", use_container_width=True):
                with st.spinner("MP3 추출 중..."):
                    st.session_state["mp3_bytes"] = extract_mp3_audio(vid)

        if st.session_state.get("mp3_bytes"):
            mp3_d = st.session_state["mp3_bytes"]
            with a2:
                st.download_button(
                    f"⬇️ MP3 다운로드 ({round(len(mp3_d)/(1024*1024), 1)} MB)",
                    mp3_d,
                    f"{clean_name}.mp3",
                    "audio/mp3",
                    use_container_width=True,
                )

        if desc:
            st.text_area("게시물 원본 텍스트", desc, height=95)

    elif imgs:
        st.markdown(f"**고화질 사진 ({len(imgs)}장)**")
        img_cols = st.columns(3)
        for idx, img_b in enumerate(imgs):
            with img_cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(
                    f"⬇️ 사진 #{idx+1} 받기",
                    img_b,
                    f"photo_{idx+1}.jpg",
                    "image/jpeg",
                    key=f"img_btn_{idx}",
                    use_container_width=True,
                )
