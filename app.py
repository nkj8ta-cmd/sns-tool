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
    page_title="SNS 올인원 스튜디오",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🎬 SNS 멀티 추출 & 즉석 편집 스튜디오")
st.caption(
    "샤오홍슈·스레드·틱톡·인스타·유튜브 미디어 분리 추출 및 원클릭 리사이클링 편집"
)


# ==========================================
# 1. 중국어 바이럴 번역 캐싱
# ==========================================
@st.cache_data(show_spinner=False)
def get_chinese_translation(text):
    clean = text.strip()
    try:
        return GoogleTranslator(source="ko", target="zh-CN").translate(clean)
    except Exception:
        return MyMemoryTranslator(source="ko-KR", target="zh-CN").translate(
            clean
        )


# ==========================================
# 2. URL 전처리 (단축 링크 및 리다이렉트 자동 해제)
# ==========================================
def clean_social_url(url):
    clean = url.strip()
    # 샤오홍슈 단축 링크(xhslink.com) 및 스레드 share 링크 자동 확장
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


# ==========================================
# 3. 스레드 전용 추출기
# ==========================================
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

    return {
        "title": "Threads Post",
        "description": post_text,
        "videos": videos,
        "images": images,
    }


# ==========================================
# 4. 범용 다운로드 엔진 (yt-dlp)
# ==========================================
def download_media_package(target_url):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
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
    videos, images = [], []

    for f in downloaded_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in [".mp4", ".mkv", ".webm", ".mov"]:
            with open(f, "rb") as fp:
                videos.append(fp.read())
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            with open(f, "rb") as fp:
                images.append(fp.read())

    return {
        "title": title,
        "description": description,
        "videos": videos,
        "images": images,
    }


# ==========================================
# 5. FFmpeg 기반 미디어 가공 엔진 (편집기능)
# ==========================================
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

    # 비디오 필터 구성
    vf_list = []
    if hflip:
        vf_list.append("hflip")
    if speed != 1.0:
        pts_factor = 1.0 / speed
        vf_list.append(f"setpts={pts_factor}*PTS")

    vf_cmd = ["-vf", ",".join(vf_list)] if vf_list else []

    # 오디오 필터 구성
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


# ==========================================
# 6. UI 영역
# ==========================================

# 상단: 샤오홍슈 키워드 생성기 (접이식)
with st.expander("🔍 샤오홍슈 바이럴 키워드 생성기 열기", expanded=False):
    with st.form("trans_form"):
        kor_keyword = st.text_input(
            "제품명 입력", placeholder="예: 전동 틈새 청소솔"
        )
        trans_submit = st.form_submit_button("🇨🇳 중국어 치트키 생성")

    if trans_submit and kor_keyword.strip():
        try:
            translated = get_chinese_translation(kor_keyword)
            presets = [
                ("🎬 시각적 쾌감 / ASMR", f"{translated} 解压 沉浸式"),
                ("✨ 삶의 질 상승 / 치트키", f"{translated} 神器 提升幸福感"),
                ("🏠 자취방 / 1인 가구", f"{translated} 独居好物 出租屋"),
                ("🧹 청소·정리 강박", f"{translated} 懒人 强迫症"),
            ]
            st.markdown(f"**번역어:** `{translated}`")
            for title, combo in presets:
                c1, c2 = st.columns([3, 1])
                c1.code(combo, language="text")
                c2.link_button(
                    "🔍 검색 열기",
                    f"https://www.xiaohongshu.com/search_result?keyword={quote(combo)}",
                    use_container_width=True,
                )
        except Exception as err:
            st.error(f"번역 오류: {err}")

st.markdown("### 📥 콘텐츠 추출 및 스튜디오")
url_input = st.text_input(
    "링크 붙여넣기 (샤오홍슈, 스레드, 틱톡, 인스타, 유튜브 쇼츠)",
    placeholder="https://...",
)
analyze_btn = st.button(
    "⚡ 원클릭 추출 시작", use_container_width=True, type="primary"
)

if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("미디어 분리 추출 및 전처리 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                raw_url = url_match.group(0) if url_match else url_input
                target_url = clean_social_url(raw_url)

                if "threads.net" in target_url or "threads.com" in target_url:
                    data = extract_threads_package(target_url)
                else:
                    data = download_media_package(target_url)

                st.session_state["media_data"] = data
                st.session_state["remix_video"] = None
                st.success("✅ 미디어 분리 완료! 아래 탭에서 확인하세요.")
            except Exception as e:
                st.error(f"분석 실패: {e}")

# 분석 결과가 세션에 있을 경우 UI 렌더링
if "media_data" in st.session_state and st.session_state["media_data"]:
    data = st.session_state["media_data"]
    videos = data["videos"]
    images = data["images"]
    title = data["title"]
    description = data["description"]

    st.markdown("---")

    # SnapWC 스타일 4개 분리 탭 구조
    tab1, tab2, tab3, tab4 = st.tabs([
        "🎬 비디오 & BGM 분리",
        "✂️ 즉석 리사이클링 편집기",
        "🖼 사진 & 썸네일",
        "📝 대본 & 본문 텍스트",
    ])

    # ---------------- 탭 1: 비디오 & BGM 추출 ----------------
    with tab1:
        if videos:
            st.markdown(f"#### 🎬 무워터마크 원본 영상 ({len(videos)}개)")
            for idx, v_bytes in enumerate(videos):
                c_vid, c_dl = st.columns([2, 1])
                with c_vid:
                    st.video(v_bytes)
                with c_dl:
                    st.write("**파일 다운로드 옵션**")
                    st.download_button(
                        f"⬇️ 고화질 MP4 받기",
                        v_bytes,
                        f"video_{idx + 1}.mp4",
                        "video/mp4",
                        key=f"tab1_v_{idx}",
                        use_container_width=True,
                    )

                    # MP3 오디오 추출 버튼
                    if st.button(
                        "🎵 배경음악(MP3)만 추출",
                        key=f"extract_mp3_{idx}",
                        use_container_width=True,
                    ):
                        with st.spinner("오디오 분리 인코딩 중..."):
                            mp3_data = extract_mp3_from_video(v_bytes)
                            if mp3_data:
                                st.audio(mp3_data, format="audio/mp3")
                                st.download_button(
                                    "⬇️ MP3 파일 다운로드",
                                    mp3_data,
                                    f"audio_{idx + 1}.mp3",
                                    "audio/mp3",
                                    key=f"dl_mp3_{idx}",
                                    use_container_width=True,
                                )
        else:
            st.info("이 게시물에는 비디오가 포함되어 있지 않습니다.")

    # ---------------- 탭 2: 즉석 리사이클링 편집실 (중복 방지 세탁) ----------------
    with tab2:
        st.markdown("#### ⚡ 알고리즘 중복 방지 원클릭 편집실")
        st.caption(
            "타 플랫폼에 재업로드할 때 중복 판정을 피할 수 있도록 화면 반전과 미세 배속을 적용합니다."
        )

        if videos:
            target_v = videos[0]

            col_edit_opt, col_edit_view = st.columns([1, 1])

            with col_edit_opt:
                st.markdown("##### 🛠 편집 옵션")
                opt_hflip = st.checkbox(
                    "🔄 좌우 반전 (Horizontal Flip)",
                    value=True,
                    help="좌우를 뒤집어 동일 영상 인식을 무력화합니다.",
                )
                opt_speed = st.select_slider(
                    "⏩ 배속 미세 조정",
                    options=[1.0, 1.05, 1.1, 1.15, 1.2],
                    value=1.1,
                    help="미세한 템포 변경으로 프레임 시퀀스를 재구성합니다.",
                )
                opt_mute = st.checkbox(
                    "🔇 원본 오디오 완전 음소거",
                    value=False,
                    help="국내 숏폼에 올릴 때 새로운 AI 보이스나 BGM을 입힐 경우 체크하세요.",
                )

                if st.button(
                    "🚀 세탁 및 리사이클링 렌더링",
                    type="primary",
                    use_container_width=True,
                ):
                    with st.spinner("FFmpeg 가속 렌더링 진행 중..."):
                        remix_bytes = process_video_remix(
                            target_v, opt_hflip, opt_speed, opt_mute
                        )
                        st.session_state["remix_video"] = remix_bytes
                        st.success("렌더링 완료!")

            with col_edit_view:
                st.markdown("##### 📺 가공된 영상 미리보기")
                if st.session_state.get("remix_video"):
                    st.video(st.session_state["remix_video"])
                    st.download_button(
                        "⬇️ 리사이클링 완료 영상 다운로드 (MP4)",
                        st.session_state["remix_video"],
                        "remix_complete.mp4",
                        "video/mp4",
                        use_container_width=True,
                    )
                else:
                    st.info("왼쪽 옵션을 선택하고 [렌더링] 버튼을 누르세요.")
        else:
            st.info("편집할 비디오 소스가 없습니다.")

    # ---------------- 탭 3: 사진 & 썸네일 추출 ----------------
    with tab3:
        if images:
            st.markdown(f"#### 🖼 사진 및 커버 썸네일 ({len(images)}장)")
            cols = st.columns(3)
            for idx, img_bytes in enumerate(images):
                with cols[idx % 3]:
                    st.image(img_bytes, use_container_width=True)
                    st.download_button(
                        f"⬇️ 사진 #{idx + 1} 받기",
                        img_bytes,
                        f"image_{idx + 1}.jpg",
                        "image/jpeg",
                        key=f"tab3_i_{idx}",
                        use_container_width=True,
                    )
        else:
            st.info("추출된 이미지가 없습니다.")

    # ---------------- 탭 4: 대본 & 텍스트 원클릭 복사 ----------------
    with tab4:
        st.markdown("#### 📝 게시물 제목 및 설명문")
        st.text_input("제목", value=title)
        st.text_area("본문 내용 및 해시태그", value=description, height=180)

        # 전체 리소스 일괄 압축 다운로드
        st.markdown("##### 📦 전체 패키지 다운로드")
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
            "📦 텍스트+사진+영상 전체 한 번에 받기 (ZIP)",
            zip_buffer.getvalue(),
            "snap_package.zip",
            "application/zip",
            use_container_width=True,
        )
