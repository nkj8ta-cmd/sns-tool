import io
import re
from urllib.parse import quote
import zipfile
from deep_translator import GoogleTranslator, MyMemoryTranslator
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SNS 콘텐츠 도구 모음", page_icon="⚡", layout="centered"
)

st.title("⚡ SNS 올인원 작업 도구")


# ==========================================================
# 캐싱 및 대체 엔진이 적용된 안전한 번역 함수
# ==========================================================
@st.cache_data(show_spinner=False)
def get_chinese_translation(text):
    clean_text = text.strip()
    try:
        # 1차 시도: 구글 번역
        return GoogleTranslator(source="ko", target="zh-CN").translate(
            clean_text
        )
    except Exception:
        try:
            # 2차 시도: 구글 차단 시 MyMemory 번역기로 우회
            return MyMemoryTranslator(source="ko-KR", target="zh-CN").translate(
                clean_text
            )
        except Exception as e:
            raise RuntimeError(f"모든 번역 서버 응답 실패: {e}")


# ==========================================================
# 1. 샤오홍슈 바이럴 중국어 키워드 생성기 (Form 적용)
# ==========================================================
st.subheader("🔍 샤오홍슈 바이럴 키워드 자동 생성")
st.caption("한글 제품명을 입력한 후 [변환하기] 버튼을 누르세요.")

# 폼으로 묶어 불필요한 중복 호출 방지
with st.form("trans_form"):
    kor_keyword = st.text_input(
        "한글 제품명 입력", placeholder="예: 전동 틈새 청소솔, 접이식 빨래바구니"
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
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.code(combo, language="text")
                with col2:
                    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(combo)}"
                    st.link_button(
                        "🔍 검색 열기", search_url, use_container_width=True
                    )

        except Exception as err:
            st.error(str(err))

st.divider()

# ==========================================================
# 2. SNS 미디어 다운로더 (기존 코드 유지)
# ==========================================================
# ==========================================================
# 2. SNS 미디어 다운로더
# ==========================================================
st.subheader("📥 SNS 콘텐츠 다운로더")
st.caption("샤오홍슈, 스레드, 인스타그램, 틱톡 영상과 본문을 추출합니다.")

url_input = st.text_input("🔗 다운로드할 링크", placeholder="https://...")
use_chrome_cookie = st.checkbox(
    "🔒 Chrome 쿠키 세션 자동 연동 (샤오홍슈·인스타 차단 방지)", value=False
)
analyze_btn = st.button(
    "🔍 영상 및 본문 분석하기", use_container_width=True, type="primary"
)


def extract_media(url, use_cookie):
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": False}
    if use_cookie:
        ydl_opts["cookiesfrombrowser"] = ("chrome",)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


if analyze_btn:
    if not url_input.strip():
        st.warning("링크를 입력해 주세요.")
    else:
        with st.spinner("게시물에서 소스를 추출하는 중..."):
            try:
                url_match = re.search(r"https?://\S+", url_input)
                target_url = url_match.group(0) if url_match else url_input
                info = extract_media(target_url, use_chrome_cookie)

                st.success("✅ 확인 완료!")

                # 본문
                post_text = (
                    info.get("description")
                    or info.get("title")
                    or "추출된 본문이 없습니다."
                )

                # 미디어 수집
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

                # 동영상 영역
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

                # 사진 영역
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

                # 본문 영역
                st.markdown("#### 📝 본문")
                st.text_area("내용", post_text, height=120)

                # ZIP 일괄 다운로드
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
                    "sns_package.zip",
                    "application/zip",
                    use_container_width=True,
                )

            except Exception as e:
                st.error(f"분석 실패: {e}")