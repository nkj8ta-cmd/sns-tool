import glob
import html
import io
import os
import re
import tempfile
from urllib.parse import quote
import zipfile
from deep_translator import GoogleTranslator, MyMemoryTranslator
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SNS 올인원 작업 도구", page_icon="⚡", layout="centered"
)
st.title("⚡ SNS 올인원 작업 도구")


# ==========================================
# 1. 중국어 번역 캐싱
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
# 2. 스레드(Threads) 전용 추출 엔진 (yt-dlp 우회)
# ==========================================
def extract_threads_package(raw_url):
    session = requests.Session()

    # 단축/공유 링크(/share/) 리다이렉트 추적
    url = raw_url.strip()
    if "/share/" in url:
        try:
            head_res = session.head(
                url,
                allow_redirects=True,
                timeout=5,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            url = head_res.url
        except Exception:
            pass

    # 주소 정리 (파라미터 제거)
    url = url.split("?")[0].replace("threads.com", "threads.net")

    # 메타(Meta) SSR 크롤러 헤더 (로그인 없이 메타데이터를 강제로 받아오는 핵심)
    crawler_headers = {
        "User-Agent": (
            "facebookexternalhit/1.1"
            " (+http://www.facebook.com/externalhit_uatext.php)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }

    res = session.get(url, headers=crawler_headers, timeout=10)
    page_html = res.text

    # 크롤러 차단 대비 모바일 헤더 예비 시도
    if len(page_html) < 500 or (
        "og:description" not in page_html and "video_versions" not in page_html
    ):
        mobile_headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)"
                " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4"
                " Mobile/15E148 Safari/604.1"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        }
        res = session.get(url, headers=mobile_headers, timeout=10)
        page_html = res.text

    # 1) 본문 추출
    post_text = "추출된 본문이 없습니다."
    desc_match = re.search(
        r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
        page_html,
        re.DOTALL,
    )
    if desc_match:
        post_text = html.unescape(desc_match.group(1))
        # 만약 "작성자 on Threads: '실제본문'" 형태라면 본문만 추출
        sub_match = re.search(r':\s*["“](.*)["”]$', post_text, re.DOTALL)
        if sub_match:
            post_text = sub_match.group(1)
    else:
        title_match = re.search(
            r'<meta\s+(?:property|name)=["\']og:title["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        if title_match:
            post_text = html.unescape(title_match.group(1))

    # 2) 동영상 URL 추출
    video_urls = []
    og_videos = re.findall(
        r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    video_urls.extend([html.unescape(v) for v in og_videos])

    # JSON 내부 비디오 링크 탐색
    if not video_urls:
        json_videos = re.findall(
            r'"video_versions":\s*\[\s*\{[^}]*"url":\s*"([^"]+)"', page_html
        )
        video_urls.extend([
            v.replace(r"\/", "/").replace(r"\u0026", "&") for v in json_videos
        ])

    if not video_urls:
        direct_mp4 = re.findall(
            r'(https?:\\?/\\?/[^"\']+\.mp4[^"\']*)', page_html
        )
        for m in direct_mp4:
            clean_m = m.replace(r"\/", "/").replace(r"\u0026", "&")
            if any(k in clean_m for k in ["cdninstagram.com", "fbcdn.net"]):
                video_urls.append(clean_m)

    # 3) 이미지 URL 추출
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

    # 중복 제거
    video_urls = list(dict.fromkeys(video_urls))
    image_urls = list(dict.fromkeys(image_urls))

    # 다운로드 진행 (바이너리 바이트 수집)
    dl_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Referer": "https://www.threads.net/",
    }

    videos = []
    for v_url in video_urls:
        try:
            v_res = session.get(v_url, headers=dl_headers, timeout=15)
            if v_res.status_code == 200 and len(v_res.content) > 2000:
                videos.append(v_res.content)
        except Exception:
            pass

    images = []
    for i_url in image_urls:
        try:
            i_res = session.get(i_url, headers=dl_headers, timeout=10)
            if i_res.status_code == 200:
                images.append(i_res.content)
        except Exception:
            pass

    return post_text, videos, images


# ==========================================
# 3. 유튜브/틱톡/인스타 범용 엔진 (yt-dlp)
# ==========================================
def download_media_package(target_url):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "format": (
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        ),
        "format_sort": ["vcodec:h264", "acodec:m4a", "ext:mp4:m4a"],
    }

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["extractor_args"] = {
            "youtube": {
                "player_client": ["ios", "mweb"],
            }
        }
    else:
        ydl_opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)

    post_text = (
        info.get("description")
        or info.get("title")
        or "추출된 본문이 없습니다."
    )

    downloaded_files = glob.glob(os.path.join(temp_dir, "*"))
    videos = []
    images = []

    for f in downloaded_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in [".mp4", ".mkv", ".webm", ".mov"]:
            with open(f, "rb") as fp:
                videos.append(fp.read())
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            with open(f, "rb") as fp:
                images.append(fp.read())

    return post_text, videos, images


# ==========================================
# 4. Streamlit UI 영역
# ==========================================
st.subheader("🔍 샤오홍슈 키워드 생성")
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

st.divider()

st.subheader("📥 SNS 콘텐츠 다운로더")
url_input = st.text_input(
    "🔗 링크 붙여넣기 (스레드, 틱톡, 유튜브, 인스타 등)",
    placeholder="https://...",
)
analyze_btn = st.button(
    "🔍 영상 및 본문 분석하기", use_container_width=True, type="primary"
)

if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("미디어 파일 다운로드 및 변환 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                raw_url = url_match.group(0) if url_match else url_input

                # 스레드는 전용 파서로 우회, 나머지는 yt-dlp 엔진 사용
                if "threads.com" in raw_url or "threads.net" in raw_url:
                    post_text, videos, images = extract_threads_package(raw_url)
                else:
                    post_text, videos, images = download_media_package(raw_url)

                st.success("✅ 확인 완료!")

                # 1) 동영상 영역
                if videos:
                    st.markdown(f"#### 🎬 동영상 ({len(videos)}개)")
                    for idx, v_bytes in enumerate(videos):
                        st.video(v_bytes)
                        st.download_button(
                            f"⬇️ 영상 #{idx + 1} 다운로드",
                            v_bytes,
                            f"video_{idx + 1}.mp4",
                            "video/mp4",
                            key=f"v_{idx}",
                        )

                # 2) 사진 영역
                if images:
                    st.markdown(f"#### 🖼 사진 ({len(images)}개)")
                    cols = st.columns(min(len(images), 3))
                    for idx, img_bytes in enumerate(images):
                        with cols[idx % 3]:
                            st.image(img_bytes, use_container_width=True)
                            st.download_button(
                                "⬇️ 받기",
                                img_bytes,
                                f"img_{idx + 1}.jpg",
                                "image/jpeg",
                                key=f"i_{idx}",
                            )

                # 3) 본문 영역
                st.markdown("#### 📝 본문")
                st.text_area("내용", post_text, height=130)

                # 4) ZIP 일괄 다운로드
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(
                    zip_buffer, "w", zipfile.ZIP_DEFLATED
                ) as zf:
                    zf.writestr("content.txt", post_text.encode("utf-8"))
                    for idx, v in enumerate(videos):
                        zf.writestr(f"video_{idx + 1}.mp4", v)
                    for idx, i in enumerate(images):
                        zf.writestr(f"image_{idx + 1}.jpg", i)

                st.download_button(
                    "📦 전체 다운로드 (ZIP)",
                    zip_buffer.getvalue(),
                    "sns_media_pack.zip",
                    "application/zip",
                    use_container_width=True,
                )

            except Exception as e:
                st.error(f"분석 실패: {e}")
