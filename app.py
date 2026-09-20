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
    page_title="SnapWC - SNS 다운로더",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container {
        padding-top: 2.8rem !important;
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
        margin-bottom: 26px;
    }
    .preview-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 18px;
        margin-top: 15px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
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
        alert("클립보드 접근 권한을 허용해 주세요.");
    }
}
</script>
""",
    unsafe_allow_html=True,
)

if "url_input" not in st.session_state:
    st.session_state["url_input"] = ""
if "media_result" not in st.session_state:
    st.session_state["media_result"] = None


# ==========================================
# 1. URL 정제 (유튜브 Shorts 및 RedNote 완벽 정규화)
# ==========================================
def clean_url(raw_text):
    # 한글/중국어 혼입 텍스트에서 순수 URL만 추출
    m = re.search(r"https?://[^\s<>\"']+", raw_text)
    clean = m.group(0) if m else raw_text.strip()

    # 모바일 단축 링크 리다이렉트 추적
    if any(k in clean for k in ["xhslink.com", "v.douyin.com", "vt.tiktok.com", "youtu.be", "/share/"]):
        try:
            r = requests.head(clean, allow_redirects=True, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            clean = r.url
        except Exception:
            pass

    # 유튜브 쇼츠 403 방지를 위해 표준 watch 주소로 변환
    if "youtube.com/shorts/" in clean:
        shorts_id = clean.split("shorts/")[1].split("?")[0].split("&")[0]
        clean = f"https://www.youtube.com/watch?v={shorts_id}"
    elif "youtu.be/" in clean:
        vid_id = clean.split("youtu.be/")[1].split("?")[0]
        clean = f"https://www.youtube.com/watch?v={vid_id}"

    return clean


# ==========================================
# 2. RedNote (샤오홍슈) 직접 추출 엔진
# ==========================================
def fetch_rednote(target_url):
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,ko;q=0.7",
    }

    res = session.get(target_url, headers=headers, timeout=12)
    page_html = html.unescape(res.text).replace(r"\/", "/")

    title = "RedNote Media"
    desc = ""
    video_bytes = None
    images = []

    # 1) JSON 내부 탐색
    json_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html)
    if json_match:
        try:
            state = json.loads(json_match.group(1))
            note_dict = state.get("note", {}).get("noteDetailMap", {})
            note = next(iter(note_dict.values())).get("note", {})
            title = note.get("title") or title
            desc = note.get("desc") or desc

            # 비디오 확인
            v_stream = note.get("video", {}).get("media", {}).get("stream", {})
            v_url = (v_stream.get("h264", [{}])[0].get("masterUrl") or 
                     v_stream.get("h265", [{}])[0].get("masterUrl"))
            if v_url:
                vr = session.get(v_url, timeout=25)
                if vr.status_code == 200:
                    video_bytes = vr.content

            # 이미지 확인
            for img in note.get("imageList", []):
                iu = img.get("urlDefault") or img.get("infoList", [{}])[-1].get("url")
                if iu:
                    ir = session.get(iu, timeout=10)
                    if ir.status_code == 200:
                        images.append(ir.content)
        except Exception:
            pass

    # 2) Fallback: 비디오 스트림 주소 직접 정규식 추출
    if not video_bytes:
        video_links = re.findall(r'https?://[^\s"\'<>]+(?:xhscdn\.com|sns-video)[^\s"\'<>]*', page_html)
        for vl in list(dict.fromkeys(video_links)):
            if not vl.endswith((".jpg", ".png", ".webp")):
                try:
                    vr = session.get(vl, timeout=15)
                    if vr.status_code == 200 and len(vr.content) > 10000:
                        video_bytes = vr.content
                        break
                except Exception:
                    pass

    # 3) Fallback: 이미지 직접 정규식 추출
    if not video_bytes and not images:
        img_links = re.findall(r'https?://[^\s"\'<>]+ci\.xiaohongshu\.com/[^\s"\'<>]+', page_html)
        for il in list(dict.fromkeys(img_links)):
            try:
                ir = session.get(il, timeout=10)
                if ir.status_code == 200 and len(ir.content) > 5000:
                    images.append(ir.content)
            except Exception:
                pass

    if not video_bytes and not images:
        raise Exception("미디어 스트림을 찾지 못했습니다. 링크가 유효한지 확인해 주세요.")

    return {
        "title": title,
        "desc": desc,
        "video": video_bytes,
        "images": images,
    }


# ==========================================
# 3. 유튜브 & 기타 SNS 다운로더 (403 방어 적용)
# ==========================================
def fetch_generic(target_url):
    temp_dir = tempfile.mkdtemp()
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "format": "best[ext=mp4]/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"
        },
    }

    # 유튜브 403 에러 우회 설정
    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["ios", "mweb"]}
        }

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
    except Exception as e:
        # 다운로드가 403에 걸릴 경우 직통 스트림 URL 추출로 우회
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            stream_url = info.get("url")
            if stream_url:
                r = requests.get(stream_url, timeout=25, headers=ydl_opts["http_headers"])
                if r.status_code == 200:
                    return {
                        "title": info.get("title", "SNS Media"),
                        "desc": info.get("description", ""),
                        "video": r.content,
                        "images": [],
                    }
        raise e

    title = info.get("title", "SNS Media")
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


def clear_input():
    st.session_state["url_input"] = ""
    st.session_state["media_result"] = None


# ==========================================
# UI 영역
# ==========================================
st.markdown('<div class="snap-title">SnapWC 무워터마크 다운로더</div>', unsafe_allow_html=True)
st.markdown('<div class="snap-sub">RedNote · YouTube Shorts · TikTok · Reels · Threads 무워터마크 저장</div>', unsafe_allow_html=True)

# 검색 입력창 (✖ 지우기, 📋 붙여넣기 탑재)
c_in, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_in:
    url_val = st.text_input(
        "URL",
        key="url_input",
        placeholder="영상 링크 또는 공유 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )

with c_clear:
    st.button("✖", on_click=clear_input, help="주소 지우기", use_container_width=True)

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

st.caption("YouTube Shorts, RedNote(샤오홍슈), TikTok, Instagram, Threads 완벽 지원")


# 다운로드 실행
if submit_btn and url_val.strip():
    with st.spinner("미디어 분석 및 무워터마크 추출 중..."):
        try:
            target_link = clean_url(url_val)

            if "rednote.com" in target_link or "xiaohongshu.com" in target_link:
                res = fetch_rednote(target_link)
            else:
                res = fetch_generic(target_link)

            st.session_state["media_result"] = res
            st.success("✅ 미디어 준비 완료!")
        except Exception as e:
            st.error(f"다운로드 실패: {e}")


# ==========================================
# 결과 화면 (스크린샷 17 스타일 1:1 구현)
# ==========================================
if st.session_state.get("media_result"):
    data = st.session_state["media_result"]
    vid = data.get("video")
    imgs = data.get("images", [])
    title = data.get("title", "downloaded_video")
    desc = data.get("desc", "")

    st.markdown("---")
    st.markdown("#### 🎬 파일 미리보기 및 다운로드")

    if vid:
        # 1) SnapWC 스타일 대형 비디오 미리보기
        st.video(vid)

        # 2) 파일명 및 완료 상태 바
        clean_name = re.sub(r'[\\/*?:"<>|]', "", title)[:40]
        st.markdown(f"**{clean_name}.mp4**")
        st.markdown('<div class="status-bar">다운로드가 완료되었습니다.</div>', unsafe_allow_html=True)

        # 3) 다운로드 액션 버튼
        v_mb = round(len(vid) / (1024 * 1024), 1)
        c_d1, c_d2 = st.columns([1.5, 1])
        with c_d1:
            st.download_button(
                f"⬇️ 다운로드 ({v_mb} MB)",
                vid,
                f"{clean_name}.mp4",
                "video/mp4",
                type="primary",
                use_container_width=True,
            )
        with c_d2:
            st.button("📋 다운로드 준비됨", use_container_width=True)

        if desc:
            st.text_area("게시물 원본 텍스트", desc, height=90)

    elif imgs:
        st.markdown(f"**고화질 사진 노트 ({len(imgs)}장)**")
        cols = st.columns(3)
        for idx, img_bytes in enumerate(imgs):
            with cols[idx % 3]:
                st.image(img_bytes, use_container_width=True)
                st.download_button(
                    f"⬇️ 사진 #{idx+1}",
                    img_bytes,
                    f"photo_{idx+1}.jpg",
                    "image/jpeg",
                    key=f"p_{idx}",
                    use_container_width=True,
                )
