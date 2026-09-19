import html
import io
import re
from urllib.parse import quote, urlparse, urlunparse
import zipfile
from deep_translator import GoogleTranslator, MyMemoryTranslator
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SNS 올인원 작업 도구", page_icon="⚡", layout="centered"
)
st.title("⚡ SNS 올인원 작업 도구")


# 1. 번역 캐싱
@st.cache_data(show_spinner=False)
def get_chinese_translation(text):
    clean = text.strip()
    try:
        return GoogleTranslator(source="ko", target="zh-CN").translate(clean)
    except Exception:
        return MyMemoryTranslator(source="ko-KR", target="zh-CN").translate(
            clean
        )


# 2. 스레드 전용 추출기 (헤더 강화 및 URL 정제)
def extract_threads(raw_url):
    # 불필요한 공유 추적 쿼리스트링 제거
    parsed = urlparse(raw_url)
    clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)"
            " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148"
            " Safari/604.1"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }
    res = requests.get(
        clean_url, headers=headers, allow_redirects=True, timeout=10
    )
    page_html = res.text

    # 본문 추출
    desc_match = re.search(
        r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
        page_html,
        re.DOTALL,
    )
    description = (
        html.unescape(desc_match.group(1))
        if desc_match
        else "본문 텍스트가 없습니다."
    )

    # 비디오 추출
    video_matches = re.findall(
        r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    videos = [html.unescape(v) for v in set(video_matches) if v]

    # 이미지 추출
    image_matches = re.findall(
        r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    images = [
        html.unescape(i)
        for i in set(image_matches)
        if i and "static.cdninstagram.com" not in i
    ]

    return {"description": description, "videos": videos, "images": images}


# 3. 유튜브/틱톡/인스타 범용 추출기 (우회 옵션 탑재)
def extract_general(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        },
        # 유튜브 데이터센터 IP 차단 우회용 모바일 클라이언트 지정
        "extractor_args": {
            "youtube": {"player_client": ["android", "web", "ios"]}
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    post_text = (
        info.get("description")
        or info.get("title")
        or "추출된 내용이 없습니다."
    )
    videos, images = [], []

    if "entries" in info:
        for entry in info["entries"]:
            if entry.get("vcodec") != "none" and entry.get("url"):
                videos.append(entry.get("url"))
            elif entry.get("url"):
                images.append(entry.get("url"))
    else:
        if info.get("url") and info.get("vcodec") != "none":
            videos.append(info.get("url"))
        elif info.get("url") and not videos:
            videos.append(info.get("url"))

        if info.get("thumbnails") and not videos:
            images.append(info["thumbnails"][-1].get("url"))

    return {"description": post_text, "videos": videos, "images": images}


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
        with st.spinner("미디어 및 텍스트 추출 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                target_url = url_match.group(0) if url_match else url_input

                if "threads." in target_url:
                    data = extract_threads(target_url)
                else:
                    data = extract_general(target_url)

                st.success("✅ 확인 완료!")

                videos = data["videos"]
                images = data["images"]
                post_text = data["description"]

                # 틱톡/유튜브 CDN 차단 방지용 헤더
                dl_headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                        " AppleWebKit/537.36"
                    ),
                    "Referer": (
                        "https://www.tiktok.com/"
                        if "tiktok" in target_url
                        else "https://www.youtube.com/"
                    ),
                }

                # 1) 동영상 영역
                if videos:
                    st.markdown("#### 🎬 동영상")
                    for idx, v_url in enumerate(videos):
                        # 서버에서 바이너리를 직접 가져와 플레이어와 다운로드 버튼에 주입
                        v_res = requests.get(
                            v_url, headers=dl_headers, stream=True, timeout=15
                        )
                        v_bytes = v_res.content

                        st.video(v_bytes)  # URL 대신 데이터 바이트 직접 재생
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
                    for idx, img_url in enumerate(images):
                        with cols[idx % 3]:
                            st.image(img_url, use_container_width=True)
                            i_res = requests.get(img_url, timeout=10)
                            st.download_button(
                                "⬇️ 받기",
                                i_res.content,
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
                    for idx, v_url in enumerate(videos):
                        v_res = requests.get(
                            v_url, headers=dl_headers, timeout=15
                        )
                        zf.writestr(f"video_{idx + 1}.mp4", v_res.content)
                    for idx, img_url in enumerate(images):
                        i_res = requests.get(img_url, timeout=10)
                        zf.writestr(f"image_{idx + 1}.jpg", i_res.content)

                st.download_button(
                    "📦 전체 다운로드 (ZIP)",
                    zip_buffer.getvalue(),
                    "sns_media_pack.zip",
                    "application/zip",
                    use_container_width=True,
                )

            except Exception as e:
                st.error(f"분석 실패: {e}")
