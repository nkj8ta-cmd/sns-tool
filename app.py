import glob
import html
import io
import json
import os
import re
import subprocess
import tempfile
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SnapWC - SNS 무워터마크 다운로더",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# SnapWC 스타일 깔끔한 미니멀 CSS
st.markdown(
    """
<style>
    .block-container {
        padding-top: 3.5rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 800px;
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
        margin-bottom: 28px;
    }
    .support-sites {
        text-align: center;
        font-size: 12.5px;
        color: #94a3b8;
        margin-top: 10px;
        margin-bottom: 25px;
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
if "url_input" not in st.session_state:
    st.session_state["url_input"] = ""
if "result_data" not in st.session_state:
    st.session_state["result_data"] = None
if "mp3_bytes" not in st.session_state:
    st.session_state["mp3_bytes"] = None


# ==========================================
# 1. URL 정제 (공유 텍스트 및 단축 링크 자동 추적)
# ==========================================
def clean_social_url(raw_text):
    # 텍스트가 섞여 있어도 URL만 정규식으로 추출
    m = re.search(r"https?://[^\s]+", raw_text)
    clean = m.group(0) if m else raw_text.strip()

    # 모바일 단축 링크 리다이렉트 추적
    if any(
        k in clean
        for k in ["xhslink.com", "v.douyin.com", "/share/", "vt.tiktok.com", "youtu.be"]
    ):
        try:
            r = requests.head(
                clean,
                allow_redirects=True,
                timeout=7,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            clean = r.url
        except Exception:
            pass

    # ※ rednote.com 도메인은 절대 변경하지 않고 그대로 유지
    return clean


# ==========================================
# 2. RedNote / Xiaohongshu 직접 추출 (SnapWC 엔진)
# ==========================================
def extract_rednote(url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,en-US;q=0.7",
    }

    res = session.get(url, headers=headers, timeout=12)
    page_html = res.text

    title = "RedNote Media"
    desc = ""
    video_bytes = None
    images = []

    # 1) JSON 직접 파싱
    json_match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html
    )
    if json_match:
        try:
            state_data = json.loads(json_match.group(1))
            note_dict = state_data.get("note", {}).get("noteDetailMap", {})
            note = next(iter(note_dict.values())).get("note", {})

            title = note.get("title") or title
            desc = note.get("desc") or desc

            # 비디오 확인
            v_stream = note.get("video", {}).get("media", {}).get("stream", {})
            v_url = (
                v_stream.get("h264", [{}])[0].get("masterUrl")
                or v_stream.get("h265", [{}])[0].get("masterUrl")
            )
            if v_url:
                vr = session.get(v_url, timeout=25)
                if vr.status_code == 200:
                    video_bytes = vr.content

            # 이미지 확인
            for img in note.get("imageList", []):
                i_url = img.get("urlDefault") or img.get("infoList", [{}])[-1].get("url")
                if i_url:
                    ir = session.get(i_url, timeout=10)
                    if ir.status_code == 200:
                        images.append(ir.content)
        except Exception:
            pass

    # 2) Fallback: 비디오 직접 정규식 매칭
    if not video_bytes:
        video_matches = re.findall(
            r'(https?://[^\s"\'<>]*(?:xhscdn\.com|sns-video)[^\s"\'<>]*?\.mp4[^\s"\'<>]*)',
            page_html.replace(r"\/", "/"),
        )
        for vu in list(dict.fromkeys(video_matches)):
            try:
                vr = session.get(vu, headers=headers, timeout=15)
                if vr.status_code == 200 and len(vr.content) > 5000:
                    video_bytes = vr.content
                    break
            except Exception:
                pass

    if not desc:
        d_m = re.search(
            r'<meta\s+(?:name|property)=["\'](?:og:description|description)["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        if d_m:
            desc = html.unescape(d_m.group(1))

    if not video_bytes and not images:
        raise Exception("미디어 스트림을 찾을 수 없습니다. 링크를 확인해 주세요.")

    return {
        "title": title,
        "desc": desc,
        "video": video_bytes,
        "images": images,
    }


# ==========================================
# 3. Threads 전용 파서
# ==========================================
def extract_threads(url):
    session = requests.Session()
    headers = {
        "User-Agent": "facebookexternalhit/1.1",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }
    res = session.get(url, headers=headers, timeout=10)
    page_html = html.unescape(res.text).replace(r"\/", "/").replace(r"\u0026", "&")

    title = "Threads Content"
    desc = ""
    video_bytes = None
    images = []

    # 본문
    d_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', page_html)
    if d_m:
        desc = d_m.group(1)

    # 비디오 URL 정규식 탐색
    video_urls = re.findall(
        r'(https?://[^\s"\'<>]*(?:cdninstagram\.com|fbcdn\.net)[^\s"\'<>]*?\.mp4[^\s"\'<>]*)',
        page_html,
    )
    if not video_urls:
        video_urls = re.findall(r'"playback_url":\s*"([^"]+)"', page_html)

    for vu in list(dict.fromkeys(video_urls)):
        try:
            vr = session.get(vu, timeout=15)
            if vr.status_code == 200 and len(vr.content) > 5000:
                video_bytes = vr.content
                break
        except Exception:
            pass

    # 이미지 탐색
    img_urls = re.findall(r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']', page_html)
    for iu in list(dict.fromkeys(img_urls)):
        if "static.cdninstagram.com" not in iu:
            try:
                ir = session.get(iu, timeout=10)
                if ir.status_code == 200:
                    images.append(ir.content)
            except Exception:
                pass

    return {
        "title": title,
        "desc": desc,
        "video": video_bytes,
        "images": images,
    }


# ==========================================
# 4. Douyin / TikTok / YouTube / Instagram 범용 추출
# ==========================================
def extract_generic(url):
    temp_dir = tempfile.mkdtemp()
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        },
    }

    if "youtube.com" in url or "youtu.be" in url:
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "tv_embedded"]}
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "SNS 미디어")
        desc = info.get("description", "")

    video_bytes = None
    for f in glob.glob(os.path.join(temp_dir, "*")):
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            with open(f, "rb") as fp:
                video_bytes = fp.read()
            break

    return {
        "title": title,
        "desc": desc,
        "video": video_bytes,
        "images": [],
    }


# ==========================================
# 5. MP3 음원 분리 추출기 (FFmpeg)
# ==========================================
def extract_mp3(video_bytes):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name
    out_path = in_path.replace(".mp4", ".mp3")

    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    mp3_data = None
    if os.path.exists(out_path):
        with open(out_path, "rb") as fp:
            mp3_data = fp.read()
        os.remove(out_path)
    os.remove(in_path)
    return mp3_data


# ==========================================
# UI 이벤트 콜백: 주소 지우기 X
# ==========================================
def clear_field():
    st.session_state["url_input"] = ""
    st.session_state["result_data"] = None
    st.session_state["mp3_bytes"] = None


# ==========================================
# UI 1. 메인 헤더
# ==========================================
st.markdown('<div class="snap-title">SnapWC 무워터마크 다운로더</div>', unsafe_allow_html=True)
st.markdown('<div class="snap-sub">샤오홍슈(RedNote), 도우인, 틱톡, 릴스, 유튜브, 스레드 고화질 무료 저장</div>', unsafe_allow_html=True)


# ==========================================
# UI 2. SnapWC 검색창 (✖, 📋 버튼 탑재)
# ==========================================
c_input, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_input:
    raw_val = st.text_input(
        "링크 입력",
        key="url_input",
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )

with c_clear:
    st.button("✖", on_click=clear_field, help="주소 지우기", use_container_width=True)

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
    analyze_btn = st.button("다운로드 링크 받기", type="primary", use_container_width=True)

st.markdown(
    '<div class="support-sites">RedNote(샤오홍슈), TikTok, Douyin, YouTube, Instagram, Threads 등 지원</div>',
    unsafe_allow_html=True,
)


# ==========================================
# 다운로드 실행 엔진
# ==========================================
if analyze_btn and raw_val.strip():
    with st.spinner("미디어 분석 및 무워터마크 데이터 추출 중..."):
        try:
            target_url = clean_social_url(raw_val)

            # 플랫폼별 최적 엔진 분기
            if "rednote.com" in target_url or "xiaohongshu.com" in target_url:
                data = extract_rednote(target_url)
            elif "threads.net" in target_url or "threads.com" in target_url:
                data = extract_threads(target_url)
            else:
                data = extract_generic(target_url)

            st.session_state["result_data"] = data
            st.session_state["mp3_bytes"] = None
            st.success("✅ 다운로드 링크가 준비되었습니다!")
        except Exception as e:
            st.error(f"다운로드 실패: {e}")


# ==========================================
# UI 3. 결과 화면 (SnapWC 1:1 레이아웃)
# ==========================================
if st.session_state.get("result_data"):
    res = st.session_state["result_data"]
    video = res.get("video")
    images = res.get("images", [])
    title = res.get("title", "")
    desc = res.get("desc", "")

    st.markdown("---")

    # 1) 영상 게시물인 경우
    if video:
        c_v, c_info = st.columns([1.5, 1.1])
        with c_v:
            st.video(video)
            st.caption("✨ 무워터마크 원본 미리보기")

        with c_info:
            st.markdown(f"**제목:** {title}")
            st.text_area("게시물 내용 및 해시태그 (복사 가능)", desc, height=130)

            # 비디오 MP4 다운로드
            v_size_mb = round(len(video) / (1024 * 1024), 1)
            st.download_button(
                f"⬇️ 무워터마크 MP4 받기 ({v_size_mb} MB)",
                video,
                "downloaded_video.mp4",
                "video/mp4",
                type="primary",
                use_container_width=True,
            )

            # MP3 음원 분리 버튼
            if st.button("🎵 고음질 MP3 음원 추출", use_container_width=True):
                with st.spinner("음원 분리 중..."):
                    st.session_state["mp3_bytes"] = extract_mp3(video)

            if st.session_state.get("mp3_bytes"):
                mp3_b = st.session_state["mp3_bytes"]
                st.download_button(
                    f"⬇️ MP3 다운로드 ({round(len(mp3_b)/(1024*1024), 1)} MB)",
                    mp3_b,
                    "audio_track.mp3",
                    "audio/mp3",
                    use_container_width=True,
                )

    # 2) 사진(카드뉴스) 슬라이드 게시물인 경우
    elif images:
        st.markdown(f"#### 🖼️ 고화질 사진 노트를 추출했습니다 ({len(images)}장)")
        cols = st.columns(3)
        for idx, img_b in enumerate(images):
            with cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(
                    f"⬇️ 사진 #{idx+1} 받기",
                    img_b,
                    f"image_{idx+1}.jpg",
                    "image/jpeg",
                    key=f"img_dl_{idx}",
                    use_container_width=True,
                )

        if desc:
            st.text_area("게시물 내용 (복사 가능)", desc, height=120)
