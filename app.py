import glob
import html
import io
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
    page_title="SNS 미디어 스튜디오",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# SnapWC 스타일 커스텀 CSS 주입
st.markdown(
    """
<style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 820px;
    }
    .snap-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    .snap-header {
        font-size: 22px;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 4px;
    }
    .snap-sub {
        font-size: 14px;
        color: #64748b;
        margin-bottom: 18px;
    }
    .media-row {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
</style>
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


# 2. URL 전처리 (단축 링크 및 파라미터 자동 정제)
def clean_social_url(url):
    clean = url.strip()
    if "xhslink.com" in clean or "/share/" in clean:
        try:
            head_res = requests.head(
                clean,
                allow_redirects=True,
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            clean = head_res.url
        except Exception:
            pass

    if "threads.com" in clean or "threads.net" in clean:
        clean = clean.split("?")[0].replace("threads.com", "threads.net")
    elif "youtube.com" in clean or "youtu.be" in clean:
        clean = clean.split("&")[0]
        if "shorts/" in clean:
            clean = clean.split("?")[0]
    return clean


# 3. 스레드 전용 추출기
def extract_threads_package(url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "facebookexternalhit/1.1"
            " (+http://www.facebook.com/externalhit_uatext.php)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }
    res = session.get(url, headers=headers, timeout=10)
    page_html = res.text

    # 본문 추출
    post_text = "추출된 본문이 없습니다."
    desc_match = re.search(
        r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
        page_html,
        re.DOTALL,
    )
    if desc_match:
        post_text = html.unescape(desc_match.group(1))
        sub_match = re.search(r':\s*["“](.*)["”]$', post_text, re.DOTALL)
        if sub_match:
            post_text = sub_match.group(1)

    # 비디오 추출
    video_urls = []
    og_videos = re.findall(
        r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    video_urls.extend([html.unescape(v) for v in og_videos])

    if not video_urls:
        json_videos = re.findall(
            r'"video_versions":\s*\[\s*\{[^}]*"url":\s*"([^"]+)"', page_html
        )
        video_urls.extend([
            v.replace(r"\/", "/").replace(r"\u0026", "&") for v in json_videos
        ])

    # 이미지 추출
    image_urls = []
    og_images = re.findall(
        r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    for img in og_images:
        img_clean = html.unescape(img)
        if (
            "static.cdninstagram.com" not in img_clean
            and "barcode" not in img_clean
        ):
            image_urls.append(img_clean)

    video_urls = list(dict.fromkeys(video_urls))
    image_urls = list(dict.fromkeys(image_urls))

    dl_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Referer": "https://www.threads.net/",
    }

    videos, images = [], []
    for v_url in video_urls:
        try:
            v_res = session.get(v_url, headers=dl_headers, timeout=15)
            if v_res.status_code == 200 and len(v_res.content) > 2000:
                videos.append(v_res.content)
        except Exception:
            pass

    for i_url in image_urls:
        try:
            i_res = session.get(i_url, headers=dl_headers, timeout=10)
            if i_res.status_code == 200:
                images.append(i_res.content)
        except Exception:
            pass

    thumb_bytes = images[0] if images else (videos[0] if videos else None)
    return {
        "title": "Threads Content",
        "description": post_text,
        "videos": videos,
        "images": images,
        "thumbnail": thumb_bytes,
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
            "youtube": {
                "player_client": ["tv", "android_creator", "mweb"],
            }
        }
        if os.path.exists("cookies.txt"):
            ydl_opts["cookiefile"] = "cookies.txt"
    else:
        ydl_opts["format"] = "bestvideo*+bestaudio/best"
        ydl_opts["format_sort"] = ["vcodec:h264", "acodec:m4a", "ext:mp4:m4a"]
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)

    title = info.get("title") or "SNS Content"
    description = info.get("description") or title

    downloaded_files = glob.glob(os.path.join(temp_dir, "*"))
    videos = []
    images = []
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

    # 썸네일 URL을 직접 끌어오는 fallback
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


# 5. FFmpeg 엔진 (MP3 추출 & 리사이클링 편집)
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
        pts_factor = 1.0 / speed
        vf_list.append(f"setpts={pts_factor}*PTS")
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
st.title("⚡ SnapStudio")
st.caption("샤오홍슈·틱톡·인스타·유튜브·스레드 무워터마크 추출 & 즉석 편집기")

# 상단 접이식 키워드 번역기
with st.expander("🔍 샤오홍슈 바이럴 키워드 치트키 생성기", expanded=False):
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

# 링크 입력 바
c_input, c_btn = st.columns([4, 1])
with c_input:
    url_input = st.text_input(
        "URL",
        placeholder="다운로드할 영상/게시물 링크를 입력하세요",
        label_visibility="collapsed",
    )
with c_btn:
    analyze_btn = st.button(
        "다운로드", use_container_width=True, type="primary"
    )

if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("미디어 분석 및 분리 추출 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                raw_url = url_match.group(0) if url_match else url_input
                target_url = clean_social_url(raw_url)

                if "threads.net" in target_url or "threads.com" in target_url:
                    data = extract_threads_package(target_url)
                else:
                    data = download_media_package(target_url)

                st.session_state["data"] = data
                st.session_state["remix_video"] = None
            except Exception as e:
                st.error(f"분석 실패: {e}")

# SnapWC 스타일 결과 화면
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

    # 1. 상단 프리뷰 카드 (썸네일 + 본문)
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
            st.text_area("게시물 내용 및 해시태그 (복사 가능)", description, height=160)

    st.markdown("---")

    # 2. 미디어 규격별 다운로드 섹션 (SnapWC 스타일 테이블)
    st.markdown("#### 🎬 영상 및 미디어")

    if videos:
        v_main = videos[0]
        v_size_mb = round(len(v_main) / (1024 * 1024), 1)

        # UHD MP4 옵션
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

        # HD MP4 옵션
        r2_col1, r2_col2 = st.columns([3, 1])
        with r2_col1:
            st.markdown(
                f"**HD MP4 (일반 화질)**  \n`{v_size_mb} MB` · 표준 호환 포맷"
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

        # 오디오 MP3 추출 옵션
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

    # 사진 포스트인 경우
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

    # 3. 중복 방지 즉석 리사이클링 편집실 (차별화 핵심 기능)
    st.markdown("#### ✂️ 즉석 영상 세탁 & 리사이클링 편집기")
    st.caption(
        "타 플랫폼(스레드, 릴스, 쇼츠) 재사용 콘텐츠 감지를 회피하기 위해 화면을 반전하고 프레임 속도를 미세 조정합니다."
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
                help="1.1배속은 시청 지속 시간을 늘려주고 영상 핑거프린트를 파괴합니다.",
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
                with st.spinner("FFmpeg 하드웨어 가속 렌더링 중..."):
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
                st.caption("원본 미리보기 (왼쪽 옵션 적용 후 렌더링을 누르세요)")
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
