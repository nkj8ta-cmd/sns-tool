import glob
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


# 2. URL 전처리 (스레드 share 단축 및 threads.com -> threads.net 변환)
def clean_social_url(url):
    clean = url.strip()
    if "threads.com" in clean or "threads.net" in clean:
        if "/share/" in clean:
            try:
                res = requests.head(
                    clean,
                    allow_redirects=True,
                    timeout=5,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                clean = res.url
            except Exception:
                pass
        clean = clean.replace("threads.com", "threads.net")
        clean = clean.split("?")[0]
    return clean


# 3. yt-dlp 로컬 임시 다운로드 엔진 (CDN 차단 완벽 우회)
def download_media_package(target_url):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        # 영상과 소리가 합쳐진 최적의 MP4 우선 다운로드
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        },
        "extractor_args": {
            "youtube": {"player_client": ["android", "ios", "web"]}
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)

    post_text = (
        info.get("description")
        or info.get("title")
        or "추출된 본문이 없습니다."
    )

    # 폴더에 다운로드된 실제 미디어 파일 읽기
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


# ================= UI 영역 =================
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
url_input = st.text_input("🔗 링크 붙여넣기", placeholder="https://...")
analyze_btn = st.button(
    "🔍 영상 및 본문 분석하기", use_container_width=True, type="primary"
)

if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("서버에서 미디어를 안전하게 내려받는 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                raw_url = url_match.group(0) if url_match else url_input
                target_url = clean_social_url(raw_url)

                post_text, videos, images = download_media_package(target_url)

                st.success("✅ 확인 완료!")

                # 1) 동영상 영역
                if videos:
                    st.markdown("#### 🎬 동영상")
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
                st.text_area("내용", post_text, height=120)

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
