import html
import io
import re
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


# --- 1. 번역 캐싱 함수 ---
@st.cache_data(show_spinner=False)
def get_chinese_translation(text):
    clean = text.strip()
    try:
        return GoogleTranslator(source="ko", target="zh-CN").translate(clean)
    except Exception:
        try:
            return MyMemoryTranslator(
                source="ko-KR", target="zh-CN"
            ).translate(clean)
        except Exception as e:
            raise RuntimeError(f"번역 실패: {e}")


# --- 2. 스레드(Threads) 전용 추출 함수 ---
def extract_threads(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    # 리다이렉트 추적하여 실제 페이지 가져오기
    res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
    page_html = res.text

    # 1) 본문 추출 (og:description)
    desc_match = re.search(
        r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\'](.*?)["\']',
        page_html,
        re.DOTALL,
    )
    if not desc_match:
        desc_match = re.search(
            r'<meta\s+(?:property|name)=["\']twitter:description["\']\s+content=["\'](.*?)["\']',
            page_html,
            re.DOTALL,
        )
    description = (
        html.unescape(desc_match.group(1))
        if desc_match
        else "추출된 본문이 없습니다."
    )

    # 2) 동영상 추출 (og:video)
    video_matches = re.findall(
        r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    videos = [html.unescape(v) for v in set(video_matches) if v]

    # 3) 이미지 추출 (og:image)
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


# --- 3. 일반 SNS 미디어 추출 함수 (yt-dlp) ---
def extract_general(url, use_cookie):
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": False}
    if use_cookie:
        ydl_opts["cookiesfrombrowser"] = ("chrome",)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    post_text = (
        info.get("description")
        or info.get("title")
        or "추출된 본문이 없습니다."
    )
    videos, images = [], []

    if "entries" in info:
        for entry in info["entries"]:
            if entry.get("vcodec") != "none" and entry.get("url"):
                videos.append(entry.get("url"))
            elif entry.get("url"):
                images.append(entry.get("url"))
    else:
        if info.get("vcodec") != "none" and info.get("url"):
            videos.append(info.get("url"))
        if info.get("thumbnails") and not videos:
            images.append(info["thumbnails"][-1].get("url"))

    return {"description": post_text, "videos": videos, "images": images}


# ==========================================================
# UI: 1. 샤오홍슈 키워드 생성기
# ==========================================================
st.subheader("🔍 샤오홍슈 바이럴 키워드 자동 생성")
with st.form("trans_form"):
    kor_keyword = st.text_input(
        "한글 제품명 입력", placeholder="예: 전동 틈새 청소솔, 자취생 빨래바구니"
    )
    trans_submit = st.form_submit_button("🇨🇳 중국어 치트키 생성")

if trans_submit and kor_keyword.strip():
    with st.spinner("중국어 번역 및 치트키 조합 중..."):
        try:
            translated = get_chinese_translation(kor_keyword)
            presets = [
                ("🎬 시각적 쾌감 / ASMR", f"{translated} 解压 沉浸式"),
                ("✨ 삶의 질 상승 / 치트키", f"{translated} 神器 提升幸福感"),
                ("🏠 자취방 / 1인 가구", f"{translated} 独居好物 出租屋"),
                ("🧹 청소·정리 강박 / 귀차니즘", f"{translated} 懒人 强迫症"),
            ]
            st.markdown(f"**기본 번역어:** `{translated}`")
            for title, combo in presets:
                c1, c2 = st.columns([3, 1])
                c1.code(combo, language="text")
                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(combo)}"
                c2.link_button(
                    "🔍 검색 열기", search_url, use_container_width=True
                )
        except Exception as err:
            st.error(str(err))

st.divider()

# ==========================================================
# UI: 2. SNS 미디어 다운로더
# ==========================================================
st.subheader("📥 SNS 콘텐츠 다운로더")
st.caption(
    "스레드(Threads), 틱톡, 인스타그램, 샤오홍슈 영상과 본문을 추출합니다."
)

url_input = st.text_input(
    "🔗 다운로드할 링크",
    placeholder="https://www.threads.net/... 또는 https://...",
)
use_chrome_cookie = st.checkbox(
    "🔒 Chrome 쿠키 세션 자동 연동 (PC 로컬 전용)", value=False
)
analyze_btn = st.button(
    "🔍 영상 및 본문 분석하기", use_container_width=True, type="primary"
)

if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("게시물 정보를 추출하는 중..."):
            try:
                # 링크 정제
                url_match = re.search(r"https?://\S+", url_input)
                target_url = url_match.group(0) if url_match else url_input

                # 스레드 링크인지 판별 후 자동 분기
                if "threads.com" in target_url or "threads.net" in target_url:
                    data = extract_threads(target_url)
                else:
                    data = extract_general(target_url, use_chrome_cookie)

                st.success("✅ 확인 완료!")

                videos = data["videos"]
                images = data["images"]
                post_text = data["description"]

                # 1) 동영상 영역
                if videos:
                    st.markdown("#### 🎬 동영상")
                    for idx, v_url in enumerate(videos):
                        st.video(v_url)
                        v_res = requests.get(v_url, stream=True)
                        st.download_button(
                            f"⬇️ 영상 #{idx + 1} 다운로드",
                            v_res.content,
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
                            i_res = requests.get(img_url)
                            st.download_button(
                                "⬇️ 받기",
                                i_res.content,
                                f"img_{idx + 1}.jpg",
                                "image/jpeg",
                                key=f"i_{idx}",
                            )

                # 3) 본문 영역
                st.markdown("#### 📝 본문")
                st.text_area("내용", post_text, height=130)

                # 4) ZIP 일괄 압축
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(
                    zip_buffer, "w", zipfile.ZIP_DEFLATED
                ) as zf:
                    zf.writestr("content.txt", post_text.encode("utf-8"))
                    for idx, v in enumerate(videos):
                        zf.writestr(
                            f"video_{idx + 1}.mp4", requests.get(v).content
                        )
                    for idx, i in enumerate(images):
                        zf.writestr(
                            f"image_{idx + 1}.jpg", requests.get(i).content
                        )

                st.download_button(
                    "📦 전체 다운로드 (ZIP)",
                    zip_buffer.getvalue(),
                    "threads_package.zip",
                    "application/zip",
                    use_container_width=True,
                )

            except Exception as e:
                st.error(f"분석 실패: {e}")
