import glob
import html
import io
import json
import os
import re
import subprocess
import tempfile
from urllib.parse import quote
import zipfile
from deep_translator import GoogleTranslator, MyMemoryTranslator
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SnapWC - SNS 무워터마크 다운로더 & 스튜디오",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# SnapWC 스타일 CSS 주입
st.markdown(
    """
<style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 840px;
    }
    /* SnapWC 상단 네비게이션 바 */
    .snap-nav {
        display: flex;
        justify-content: center;
        gap: 15px;
        flex-wrap: wrap;
        margin-bottom: 25px;
        padding-bottom: 12px;
        border-bottom: 1px solid #f1f5f9;
    }
    .snap-nav-item {
        font-size: 14px;
        font-weight: 500;
        color: #475569;
        text-decoration: none;
        padding: 4px 8px;
        border-radius: 6px;
    }
    /* 메인 타이틀 그라데이션 */
    .snap-hero-title {
        text-align: center;
        font-size: 32px;
        font-weight: 800;
        background: linear-gradient(90deg, #3b82f6, #ec4899, #f97316);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 6px;
    }
    .snap-hero-sub {
        text-align: center;
        font-size: 15px;
        color: #64748b;
        margin-bottom: 25px;
    }
    /* 안내 배너 */
    .support-footer {
        text-align: center;
        font-size: 12.5px;
        color: #94a3b8;
        margin-top: 10px;
        margin-bottom: 25px;
    }
    .snap-header {
        font-size: 20px;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 3px;
    }
    .snap-sub {
        font-size: 13.5px;
        color: #64748b;
        margin-bottom: 15px;
    }
</style>

<div class="snap-nav">
    <span class="snap-nav-item" style="color: #ef4444; font-weight:700;">📕 샤오홍슈(RedNote)</span>
    <span class="snap-nav-item">⚫ TikTok</span>
    <span class="snap-nav-item">🔴 YouTube</span>
    <span class="snap-nav-item">📸 Instagram</span>
    <span class="snap-nav-item">🧵 Threads</span>
    <span class="snap-nav-item">🌐 X (트위터)</span>
</div>
""",
    unsafe_allow_html=True,
)


# 1. 중국어 번역 캐싱
@st.cache_data(show_spinner=False)
def get_chinese_translation(text):
    clean = text.strip()
    try:
        return GoogleTranslator(source="ko", target="zh-CN").translate(clean)
    except Exception:
        return MyMemoryTranslator(source="ko-KR", target="zh-CN").translate(
            clean
        )


# 2. URL 전처리 (rednote.com / xhslink.com / threads / youtube 정제)
def clean_social_url(raw_input):
    # 텍스트와 섞여 있는 링크만 정규식으로 추출
    url_match = re.search(r"https?://[^\s]+", raw_input)
    clean = url_match.group(0) if url_match else raw_input.strip()

    # 샤오홍슈 단축 링크 또는 스레드 share 링크 리다이렉트 추적
    if "xhslink.com" in clean or "/share/" in clean:
        try:
            head_res = requests.head(
                clean,
                allow_redirects=True,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            clean = head_res.url
        except Exception:
            pass

    # rednote.com 링크를 표준 xiaohongshu.com 주소 체계로 변환 (yt-dlp 호환)
    if "rednote.com" in clean:
        clean = clean.replace("rednote.com/discovery/item/", "xiaohongshu.com/explore/")
        clean = clean.replace("rednote.com", "xiaohongshu.com")

    if "threads.com" in clean or "threads.net" in clean:
        clean = clean.split("?")[0].replace("threads.com", "threads.net")
    elif "youtube.com" in clean or "youtu.be" in clean:
        clean = clean.split("&")[0]
        if "shorts/" in clean:
            clean = clean.split("?")[0]

    return clean


# 3. 샤오홍슈 & 스레드 직접 파서 (yt-dlp 미지원/차단 시 Fallback)
def extract_direct_meta(url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,zh-CN;q=0.8,en-US;q=0.7",
    }
    if "threads" in url:
        headers["User-Agent"] = "facebookexternalhit/1.1"

    res = session.get(url, headers=headers, timeout=10)
    page_html = res.text

    title = "SNS Content"
    description = "추출된 본문이 없습니다."
    videos, images = [], []

    # 샤오홍슈 JSON 데이터 직접 파싱
    if "xiaohongshu.com" in url or "rednote" in url:
        try:
            json_match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html
            )
            if json_match:
                state_data = json.loads(json_match.group(1))
                note_dict = state_data.get("note", {}).get("noteDetailMap", {})
                first_note = next(iter(note_dict.values())).get("note", {})
                title = first_note.get("title") or title
                description = first_note.get("desc") or description

                # 무워터마크 동영상 추출
                if first_note.get("type") == "video":
                    v_stream = (
                        first_note.get("video", {})
                        .get("media", {})
                        .get("stream", {})
                    )
                    v_url = (
                        v_stream.get("h264", [{}])[0].get("masterUrl")
                        or v_stream.get("h265", [{}])[0].get("masterUrl")
                    )
                    if v_url:
                        videos.append(v_url)

                # 고화질 사진 리스트 추출
                for img_item in first_note.get("imageList", []):
                    img_u = img_item.get("urlDefault") or img_item.get(
                        "infoList", [{}]
                    )[-1].get("url")
                    if img_u:
                        images.append(img_u)
        except Exception:
            pass

    # Fallback: 일반 메타 태그 추출
    if not description or description == "추출된 본문이 없습니다.":
        desc_match = re.search(
            r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
            page_html,
            re.DOTALL,
        )
        if desc_match:
            description = html.unescape(desc_match.group(1))

    if not videos:
        og_v = re.findall(
            r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        videos.extend([html.unescape(v) for v in og_v])

    if not images:
        og_i = re.findall(
            r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        for img in og_i:
            clean_i = html.unescape(img)
            if "static.cdninstagram.com" not in clean_i:
                images.append(clean_i)

    # 미디어 바이너리 수집
    video_bytes_list, image_bytes_list = [], []
    for v_u in list(dict.fromkeys(videos)):
        try:
            r = session.get(v_u, timeout=15)
            if r.status_code == 200 and len(r.content) > 3000:
                video_bytes_list.append(r.content)
        except Exception:
            pass

    for i_u in list(dict.fromkeys(images)):
        try:
            r = session.get(i_u, timeout=10)
            if r.status_code == 200:
                image_bytes_list.append(r.content)
        except Exception:
            pass

    thumb = (
        image_bytes_list[0]
        if image_bytes_list
        else (video_bytes_list[0] if video_bytes_list else None)
    )
    return {
        "title": title,
        "description": description,
        "videos": video_bytes_list,
        "images": image_bytes_list,
        "thumbnail": thumb,
    }


# 4. 범용 다운로드 엔진 (yt-dlp)
def download_media_package(target_url):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
    }

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["format"] = "best[ext=mp4]/best"
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["tv", "android_creator", "mweb"]}
        }
        if os.path.exists("cookies.txt"):
            ydl_opts["cookiefile"] = "cookies.txt"
    else:
        ydl_opts["format"] = "bestvideo*+bestaudio/best"
        ydl_opts["format_sort"] = ["vcodec:h264", "acodec:m4a", "ext:mp4:m4a"]
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)

    title = info.get("title") or "SNS Content"
    description = info.get("description") or title

    downloaded_files = glob.glob(os.path.join(temp_dir, "*"))
    videos, images = [], []
    thumb_bytes = None

    for f in downloaded_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in [".mp4", ".mkv", ".webm", ".mov"]:
            with open(f, "rb") as fp:
                videos.append(fp.read())
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            with open(f, "rb") as fp:
                b_data = fp.read()
                images.append(b_data)
                if not thumb_bytes:
                    thumb_bytes = b_data

    if not thumb_bytes and info.get("thumbnail"):
        try:
            t_res = requests.get(info.get("thumbnail"), timeout=5)
            thumb_bytes = t_res.content
        except Exception:
            pass

    return {
        "title": title,
        "description": description,
        "videos": videos,
        "images": images,
        "thumbnail": thumb_bytes,
    }


# 5. FFmpeg 엔진 (MP3 추출 & 세탁 편집)
def extract_mp3_from_video(video_bytes):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_file:
        in_file.write(video_bytes)
        in_path = in_file.name
    out_path = in_path.replace(".mp4", ".mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        in_path,
        "-vn",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        out_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            mp3_bytes = f.read()
        os.remove(in_path)
        os.remove(out_path)
        return mp3_bytes
    return None


def process_video_remix(video_bytes, hflip, speed, mute):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_file:
        in_file.write(video_bytes)
        in_path = in_file.name

    out_path = in_path.replace(".mp4", "_remix.mp4")
    vf_list = []
    if hflip:
        vf_list.append("hflip")
    if speed != 1.0:
        pts = 1.0 / speed
        vf_list.append(f"setpts={pts}*PTS")
    vf_cmd = ["-vf", ",".join(vf_list)] if vf_list else []

    af_cmd = []
    if mute:
        af_cmd = ["-an"]
    elif speed != 1.0:
        af_cmd = ["-filter:a", f"atempo={speed}"]

    cmd = (
        ["ffmpeg", "-y", "-i", in_path]
        + vf_cmd
        + af_cmd
        + [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-strict",
            "experimental",
            out_path,
        ]
    )
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            result_bytes = f.read()
        os.remove(in_path)
        os.remove(out_path)
        return result_bytes
    return video_bytes


# ================= UI 영역 =================

# SnapWC 메인 히어로 타이틀
st.markdown(
    '<div class="snap-hero-title">샤오홍슈 워터마크 없는 다운로드</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="snap-hero-sub">샤오홍슈 영상, 사진, 노트를 HD로 무료 저장</div>',
    unsafe_allow_html=True,
)

# SnapWC 스타일 검색 & 다운로드 바
c_input, c_btn = st.columns([3.8, 1.2])
with c_input:
    url_input = st.text_input(
        "입력창",
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )
with c_btn:
    analyze_btn = st.button(
        "다운로드 링크 받기", use_container_width=True, type="primary"
    )

st.markdown(
    '<div class="support-footer">YouTube, TikTok, X (Twitter), Instagram,'
    " Facebook, Threads, 샤오홍슈(RedNote) 등 지원</div>",
    unsafe_allow_html=True,
)

# 상단 접이식 중국어 키워드 생성기
with st.expander("🔍 샤오홍슈 바이럴 키워드 치트키 생성기 열기", expanded=False):
    with st.form("trans_form"):
        kor_keyword = st.text_input(
            "제품명 입력", placeholder="예: 전동 틈새 청소솔"
        )
        trans_submit = st.form_submit_button("🇨🇳 중국어 키워드 생성")

    if trans_submit and kor_keyword.strip():
        try:
            translated = get_chinese_translation(kor_keyword)
            presets = [
                ("🎬 시각적 ASMR", f"{translated} 解压 沉浸式"),
                ("✨ 삶의 질 상승템", f"{translated} 神器 提升幸福感"),
                ("🏠 1인 가구/자취방", f"{translated} 独居好物 出租屋"),
                ("🧹 청소/정리 강박", f"{translated} 懒人 强迫症"),
            ]
            st.markdown(f"**기본 번역:** `{translated}`")
            for title, combo in presets:
                c1, c2 = st.columns([3, 1])
                c1.code(combo, language="text")
                c2.link_button(
                    "🔍 검색",
                    f"https://www.xiaohongshu.com/search_result?keyword={quote(combo)}",
                    use_container_width=True,
                )
        except Exception as err:
            st.error(f"번역 오류: {err}")

# 다운로드 실행
if analyze_btn:
    if not url_input.strip():
        st.warning("링크 또는 공유 텍스트를 입력해 주세요.")
    else:
        with st.spinner("미디어 분석 및 무워터마크 추출 중..."):
            try:
                target_url = clean_social_url(url_input)

                # 샤오홍슈/스레드는 전용 메타 파서 우선 시도, 실패 시 yt-dlp 분기
                if any(
                    k in target_url
                    for k in ["rednote", "xiaohongshu", "threads"]
                ):
                    try:
                        data = extract_direct_meta(target_url)
                        if not data["videos"] and not data["images"]:
                            data = download_media_package(target_url)
                    except Exception:
                        data = download_media_package(target_url)
                else:
                    data = download_media_package(target_url)

                st.session_state["data"] = data
                st.session_state["remix_video"] = None
                st.session_state["mp3_bytes"] = None
            except Exception as e:
                st.error(f"분석 실패: {e}")

# 다운로드 결과 카드 렌더링
if "data" in st.session_state and st.session_state["data"]:
    data = st.session_state["data"]
    videos = data["videos"]
    images = data["images"]
    title = data["title"]
    description = data["description"]
    thumb = data.get("thumbnail")

    st.markdown("---")
    st.markdown(
        '<div class="snap-header">다운로드 링크가 준비되었습니다</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="snap-sub">원하는 형식과 품질을 선택하세요</div>',
        unsafe_allow_html=True,
    )

    # 1. 상단 프리뷰 카드 (커버 썸네일 + 본문)
    with st.container():
        c_thumb, c_text = st.columns([1.3, 2.7])
        with c_thumb:
            if thumb:
                st.image(thumb, use_container_width=True)
                st.download_button(
                    "🖼 커버 이미지 다운로드",
                    thumb,
                    "cover_thumbnail.jpg",
                    "image/jpeg",
                    use_container_width=True,
                )
            else:
                st.info("커버 이미지 없음")
        with c_text:
            st.text_area("게시물 내용 및 해시태그 (복사 가능)", description, height=165)

    st.markdown("---")

    # 2. 미디어 규격별 다운로드 섹션
    st.markdown("#### 🎬 영상 및 음원")

    if videos:
        v_main = videos[0]
        v_size_mb = round(len(v_main) / (1024 * 1024), 1)

        r1_col1, r1_col2 = st.columns([3, 1])
        with r1_col1:
            st.markdown(
                f"**UHD MP4 (원본 화질)**  \n`{v_size_mb} MB` · 무워터마크 최고화질"
            )
        with r1_col2:
            st.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_uhd.mp4",
                "video/mp4",
                key="dl_uhd",
                use_container_width=True,
                type="primary",
            )

        st.markdown(
            "<hr style='margin: 8px 0; border: none; border-top: 1px solid"
            " #f1f5f9;'>",
            unsafe_allow_html=True,
        )

        r2_col1, r2_col2 = st.columns([3, 1])
        with r2_col1:
            st.markdown(
                f"**HD MP4 (표준 화질)**  \n`{v_size_mb} MB` · 표준 호환 포맷"
            )
        with r2_col2:
            st.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_hd.mp4",
                "video/mp4",
                key="dl_hd",
                use_container_width=True,
            )

        st.markdown(
            "<hr style='margin: 8px 0; border: none; border-top: 1px solid"
            " #f1f5f9;'>",
            unsafe_allow_html=True,
        )

        r3_col1, r3_col2 = st.columns([3, 1])
        with r3_col1:
            st.markdown(
                "**고음질 MP3 (배경음악/오디오)**  \n영상 내 음원만 분리 추출"
            )
        with r3_col2:
            if st.button(
                "🎵 MP3 추출", key="btn_mp3_gen", use_container_width=True
            ):
                with st.spinner("음원 분리 중..."):
                    mp3_data = extract_mp3_from_video(v_main)
                    if mp3_data:
                        st.session_state["mp3_bytes"] = mp3_data

        if st.session_state.get("mp3_bytes"):
            mp3_b = st.session_state["mp3_bytes"]
            st.download_button(
                f"⬇️ MP3 다운로드 ({round(len(mp3_b)/(1024*1024), 1)} MB)",
                mp3_b,
                "audio_track.mp3",
                "audio/mp3",
                key="dl_mp3_btn",
                use_container_width=True,
            )

    # 샤오홍슈 사진/노트 포스트
    if images and not videos:
        st.markdown(f"**고화질 사진 ({len(images)}장)**")
        cols = st.columns(3)
        for idx, img_b in enumerate(images):
            with cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(
                    f"⬇️ 사진 #{idx + 1} 받기",
                    img_b,
                    f"img_{idx + 1}.jpg",
                    "image/jpeg",
                    key=f"img_dl_{idx}",
                    use_container_width=True,
                )

    st.markdown("---")

    # 3. 즉석 영상 세탁 & 리사이클링 편집기
    st.markdown("#### ✂️ 즉석 영상 세탁 & 리사이클링 편집기")
    st.caption(
        "타 플랫폼(스레드, 릴스, 쇼츠) 재업로드 시 중복 감지를 방지하기 위해 화면을 반전하고 미세 배속을 적용합니다."
    )

    if videos:
        v_target = videos[0]
        c_opt, c_view = st.columns([1.2, 1.8])

        with c_opt:
            opt_hflip = st.checkbox(
                "🔄 좌우 반전 (Horizontal Flip)",
                value=True,
                help="화면 축을 반전시켜 중복 판정을 무력화합니다.",
            )
            opt_speed = st.select_slider(
                "⏩ 미세 배속 조정",
                options=[1.0, 1.05, 1.1, 1.15, 1.2],
                value=1.1,
                help="1.1배속은 시청 지속 시간을 늘려주고 영상 핑거프린트를 변경합니다.",
            )
            opt_mute = st.checkbox(
                "🔇 원본 오디오 음소거",
                value=False,
                help="새로운 AI 나레이션이나 국내 BGM을 입힐 때 체크하세요.",
            )

            if st.button(
                "🚀 리사이클링 렌더링 시작",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("FFmpeg 가속 렌더링 중..."):
                    remix_res = process_video_remix(
                        v_target, opt_hflip, opt_speed, opt_mute
                    )
                    st.session_state["remix_video"] = remix_res
                    st.success("렌더링 완료!")

        with c_view:
            if st.session_state.get("remix_video"):
                st.video(st.session_state["remix_video"])
                st.download_button(
                    "⬇️ 세탁 완료 영상 다운로드 (MP4)",
                    st.session_state["remix_video"],
                    "remix_final.mp4",
                    "video/mp4",
                    use_container_width=True,
                )
            else:
                st.video(v_target)
                st.caption("원본 미리보기")
    else:
        st.info("편집할 비디오 소스가 없습니다.")

    # 4. 전체 일괄 압축 ZIP
    st.markdown("---")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "content.txt", f"제목: {title}\n\n본문:\n{description}".encode(
                "utf-8"
            )
        )
        for idx, v in enumerate(videos):
            zf.writestr(f"video_{idx + 1}.mp4", v)
        for idx, i in enumerate(images):
            zf.writestr(f"image_{idx + 1}.jpg", i)

    st.download_button(
        "📦 모든 미디어+대본 한 번에 받기 (ZIP)",
        zip_buffer.getvalue(),
        "sns_package.zip",
        "application/zip",
        use_container_width=True,
    )
