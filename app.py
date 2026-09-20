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
    page_title="SnapStudio - SNS 무워터마크 다운로더 & 스튜디오",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container {
        padding-top: 2.2rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 860px;
    }
    .seller-title {
        text-align: center;
        font-size: 28px;
        font-weight: 800;
        color: #1e293b;
        margin-bottom: 4px;
    }
    .seller-sub {
        text-align: center;
        font-size: 14.5px;
        color: #64748b;
        margin-bottom: 22px;
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
        alert("클립보드 권한을 허용해 주세요. (Ctrl+V로 직접 붙여넣으셔도 됩니다)");
    }
}
</script>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "main_url_field" not in st.session_state:
    st.session_state["main_url_field"] = ""
if "processed_data" not in st.session_state:
    st.session_state["processed_data"] = None


# ==========================================
# 1. 번역 및 URL 전처리 엔진
# ==========================================
def translate_zh_to_ko(text):
    if not text or not text.strip():
        return ""
    try:
        return GoogleTranslator(source="zh-CN", target="ko").translate(
            text[:500]
        )
    except Exception:
        try:
            return MyMemoryTranslator(source="zh-CN", target="ko-KR").translate(
                text[:300]
            )
        except Exception:
            return text


def get_search_query(text):
    clean = text.strip()
    if re.search(r"[\u4e00-\u9fff]", clean):
        return clean
    try:
        return GoogleTranslator(source="ko", target="zh-CN").translate(clean)
    except Exception:
        return clean


def clean_input_url(raw_text):
    m = re.search(r"https?://[^\s]+", raw_text)
    clean = m.group(0) if m else raw_text.strip()

    if any(
        k in clean
        for k in ["xhslink.com", "v.douyin.com", "/share/", "vt.tiktok.com"]
    ):
        try:
            r = requests.head(
                clean,
                allow_redirects=True,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            clean = r.url
        except Exception:
            pass

    # ※ rednote.com 도메인은 절대 xiaohongshu.com으로 치환하지 않음
    return clean


# ==========================================
# 2. RedNote / Xiaohongshu 직접 추출 엔진 (SnapWC 방식)
# ==========================================
def extract_rednote_package(target_url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,ko;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
    }

    res = session.get(target_url, headers=headers, timeout=12)
    page_html = res.text

    title = "RedNote Content"
    desc = ""
    video_bytes = None
    images_bytes = []

    json_match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html
    )
    if json_match:
        try:
            state_data = json.loads(json_match.group(1))
            note_dict = state_data.get("note", {}).get("noteDetailMap", {})
            first_note = next(iter(note_dict.values())).get("note", {})

            title = first_note.get("title") or title
            desc = first_note.get("desc") or desc

            v_stream = (
                first_note.get("video", {}).get("media", {}).get("stream", {})
            )
            v_url = (
                v_stream.get("h264", [{}])[0].get("masterUrl")
                or v_stream.get("h265", [{}])[0].get("masterUrl")
            )
            if v_url:
                vr = session.get(v_url, timeout=25)
                if vr.status_code == 200 and len(vr.content) > 5000:
                    video_bytes = vr.content

            for img_item in first_note.get("imageList", []):
                i_url = img_item.get("urlDefault") or img_item.get(
                    "infoList", [{}]
                )[-1].get("url")
                if i_url:
                    ir = session.get(i_url, timeout=10)
                    if ir.status_code == 200:
                        images_bytes.append(ir.content)
        except Exception:
            pass

    if not video_bytes:
        video_urls = re.findall(
            r'(https?://[^\s"\'<>]*(?:xhscdn\.com|sns-video)[^\s"\'<>]*?\.mp4[^\s"\'<>]*)',
            page_html.replace(r"\/", "/"),
        )
        for vu in list(dict.fromkeys(video_urls)):
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
            re.DOTALL,
        )
        if d_m:
            desc = html.unescape(d_m.group(1))

    if not video_bytes and not images_bytes:
        og_imgs = re.findall(
            r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        for iu in og_imgs:
            try:
                ir = session.get(html.unescape(iu), timeout=10)
                if ir.status_code == 200:
                    images_bytes.append(ir.content)
            except Exception:
                pass

    if not video_bytes and not images_bytes:
        raise Exception("미디어 데이터를 찾을 수 없습니다. 링크를 확인해 주세요.")

    return {
        "title": title,
        "desc": desc,
        "video": video_bytes,
        "images": images_bytes,
    }


# ==========================================
# 3. 도우인 / 틱톡 / 유튜브 등 범용 다운로더
# ==========================================
def extract_generic_package(target_url):
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

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "tv_embedded"]}
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target_url, download=True)
        title = info.get("title", "제품 영상")
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
# 4. FFmpeg 정밀 세탁 엔진
# ==========================================
def process_video_remix(video_bytes, hflip, speed, mute, blur_pos):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name

    out_path = in_path.replace(".mp4", "_remix.mp4")
    filters = []

    if hflip:
        filters.append("hflip")
    if speed != 1.0:
        filters.append(f"setpts={1.0 / speed}*PTS")

    if blur_pos != "블러 없음":
        pos_map = {
            "하단 자막 (바닥 20%)": (0.80, 0.20),
            "중하단 자막 (바닥 35% 위)": (0.65, 0.20),
            "중앙 자막 (영상 한가운데)": (0.40, 0.20),
            "상단 자막 (영상 상단 20%)": (0.05, 0.20),
        }
        y_ratio, h_ratio = pos_map.get(blur_pos, (0.80, 0.20))
        sub_filter = (
            f"split[main][sub];"
            f"[sub]crop=iw:ih*{h_ratio}:0:ih*{y_ratio},boxblur=20:5[blurred];"
            f"[main][blurred]overlay=0:H*{y_ratio}"
        )
        if filters:
            vf_cmd = ["-filter_complex", f"{','.join(filters)},{sub_filter}"]
        else:
            vf_cmd = ["-filter_complex", sub_filter]
    else:
        vf_cmd = ["-vf", ",".join(filters)] if filters else []

    af_cmd = (
        ["-an"]
        if mute
        else (["-filter:a", f"atempo={speed}"] if speed != 1.0 else [])
    )

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
            res = f.read()
        os.remove(in_path)
        os.remove(out_path)
        return res
    return video_bytes


# ==========================================
# 5. 실전 4대 플랫폼 판매 대본 생성기
# ==========================================
def generate_platform_scripts(kor_title, kor_desc):
    clean_kw = re.sub(r"[^\w\s]", "", kor_title).strip()
    words = clean_kw.split()
    prod = " ".join(words[:3]) if words else "이 꿀템"

    threads = (
        "자취 5년차인데 솔직히 이거 왜 이제 알았나 싶네요... \n\n"
        f"요즘 인스타/샤오홍슈에서 난리 난 {prod} 써봤는데 삶의 질이 달라집니다.\n"
        "기존 건 손목도 아프고 시간도 오래 걸렸는데, 이건 3초 만에 싹 해결되네요 ㅋㅋㅋ\n\n"
        f"{kor_desc[:120]}...\n\n"
        "궁금하신 분 계시면 좌표 댓글로 남겨둘게요!"
    )

    shorts = (
        "[0~3초 시선 후킹]\n"
        f'"아직도 고생하면서 쓰시나요? 쿠팡 직원도 몰래 산다는 {prod} 실물입니다."\n\n'
        "[3~12초 결핍 자극]\n"
        '"매번 귀찮고 찌든 때/정리 안 돼서 스트레스 받으셨죠? 기존 제품은 힘만 들었습니다."\n\n'
        "[12~24초 기능 시연]\n"
        '"이건 갖다 대기만 하면 틈새까지 싹 밀어냅니다. 방수까지 돼서 관리도 편해요."\n\n'
        "[24~30초 댓글 유도]\n"
        '"가격 대비 만족도 300%입니다. 제품 좌표는 고정 댓글 확인하세요!"'
    )

    reels = (
        f"살림/청소/정리 스트레스 받던 분들 집중! 🚨\n{prod} 찐 사용 후기 가져왔어요 🫧\n\n"
        "장점 3줄 요약:\n"
        "1. 손목에 힘 하나도 안 들어감\n"
        "2. 틈새 구석까지 완벽 커버\n"
        "3. 공간 차지 안 하는 슬림 보관\n\n"
        "📌 나중에 사려고 찾으면 품절되니 지금 미리 [저장]해두세요!\n"
        "🔗 제품 링크는 프로필에 남겨둘게요 🤍"
    )

    tiktok = (
        f'[0~2초] "틱톡 알고리즘이 절 여기로 이끌었습니다..."\n'
        f"[2~8초] {prod} 작동 쾌감 영상 노출 (Before ➔ After)\n"
        f'[8~12초] "솔직히 가격 보고 반신반의했는데 가성비 미쳤습니다."\n'
        f'[12~15초] "좌표는 프로필 링크 1번에 있어요! #살림꿀템 #자취템 #fyp"'
    )

    return {"threads": threads, "shorts": shorts, "reels": reels, "tiktok": tiktok}


# ==========================================
# 콜백 함수: 주소 지우기 X 버튼
# ==========================================
def clear_url_callback():
    st.session_state["main_url_field"] = ""
    st.session_state["processed_data"] = None


# ==========================================
# UI 1. 사이드바 (소싱 검색창)
# ==========================================
with st.sidebar:
    st.markdown("### 🇨🇳 현지 소싱 검색 열기")
    st.caption("한글 또는 중국어를 입력하면 현지 검색창으로 직결됩니다.")

    kw_input = st.text_input("소싱할 제품명", placeholder="예: 틈새 청소솔 또는 电动缝隙刷")
    if kw_input.strip():
        q = get_search_query(kw_input)
        st.success(f"검색어: **{q}**")
        c1, c2 = st.columns(2)
        with c1:
            st.link_button(
                "📕 RedNote",
                f"https://www.rednote.com/search_result?keyword={quote(q + ' 沉浸式')}",
                use_container_width=True,
            )
        with c2:
            st.link_button(
                "🎵 Douyin",
                f"https://www.douyin.com/search/{quote(q)}",
                use_container_width=True,
            )


# ==========================================
# UI 2. 메인 헤더
# ==========================================
st.markdown('<div class="seller-title">🛒 SNS 미디어 다운로더 & 스튜디오</div>', unsafe_allow_html=True)
st.markdown('<div class="seller-sub">RedNote · Douyin · TikTok · Shorts 무워터마크 추출 & 즉석 세탁</div>', unsafe_allow_html=True)


# ==========================================
# UI 3. 메인: 한글 ➔ 중국어 바이럴 키워드 검색기 (복원 완료!)
# ==========================================
with st.expander("🔍 한글 ➔ 샤오홍슈(RedNote) 바이럴 키워드 검색기 (치트키 조합)", expanded=False):
    st.caption("한글 제품명을 입력하면 중국 현지 바이럴 검색어로 즉시 조합되어 검색창이 열립니다.")
    with st.form("trans_main_form"):
        k_col1, k_col2 = st.columns([3.5, 1.2])
        with k_col1:
            main_kor_kw = st.text_input(
                "제품명 입력",
                placeholder="예: 전동 틈새 청소솔, 자취방 조명, 빨래 바구니",
                label_visibility="collapsed",
            )
        with k_col2:
            trans_submit = st.form_submit_button("🇨🇳 치트키 생성", use_container_width=True)

    if trans_submit and main_kor_kw.strip():
        try:
            zh_trans = get_search_query(main_kor_kw)
            presets = [
                ("🎬 시각적 ASMR / 쾌감", f"{zh_trans} 解压 沉浸式"),
                ("✨ 삶의 질 상승템 / 치트키", f"{zh_trans} 神器 提升幸福感"),
                ("🏠 1인 가구 / 자취방 꿀템", f"{zh_trans} 独居好物 出租屋"),
                ("🧹 청소·정리 강박 / 귀차니즘", f"{zh_trans} 懒人 强迫症"),
            ]
            st.success(f"기본 번역: **{zh_trans}**")
            for label, combo in presets:
                c1, c2 = st.columns([3, 1])
                c1.code(combo, language="text")
                c2.link_button(
                    "🔍 검색 열기",
                    f"https://www.rednote.com/search_result?keyword={quote(combo)}",
                    use_container_width=True,
                )
        except Exception as e:
            st.error(f"번역 오류: {e}")

st.write("")


# ==========================================
# UI 4. 링크 입력창 (지우기 ✖ 및 클립보드 📋 버튼 복원 완료!)
# ==========================================
c_in, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_in:
    url_input = st.text_input(
        "URL",
        key="main_url_field",
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
    )

with c_clear:
    st.button(
        "✖",
        on_click=clear_url_callback,
        help="주소 지우기",
        use_container_width=True,
    )

with c_paste:
    st.button(
        "📋",
        help="클립보드에서 붙여넣기",
        use_container_width=True,
    )
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


# ==========================================
# UI 5. 원클릭 세탁 옵션 바
# ==========================================
with st.expander("⚙️ 원클릭 영상 세탁 옵션 (필요시 조절)", expanded=True):
    op1, op2, op3, op4 = st.columns(4)
    with op1:
        opt_flip = st.checkbox("🔄 좌우 반전", value=True)
    with op2:
        opt_spd = st.selectbox("⏩ 배속", [1.0, 1.05, 1.1, 1.15, 1.2], index=2)
    with op3:
        opt_blur = st.selectbox(
            "🔲 자막 블러 위치",
            ["하단 자막 (바닥 20%)", "중하단 자막 (바닥 35% 위)", "중앙 자막 (영상 한가운데)", "상단 자막 (영상 상단 20%)", "블러 없음"],
            index=0,
        )
    with op4:
        opt_mute = st.checkbox("🔇 원본 음소거", value=False)


# 다운로드 및 세탁 실행
if submit_btn and url_input.strip():
    with st.spinner("미디어 무워터마크 추출 및 처리 중..."):
        try:
            target_url = clean_input_url(url_input)

            # RedNote 및 샤오홍슈는 직접 파서 가동 (도메인 변조 없이 추출)
            if "rednote.com" in target_url or "xiaohongshu.com" in target_url:
                raw_data = extract_rednote_package(target_url)
            else:
                raw_data = extract_generic_package(target_url)

            # 비디오가 있으면 세탁 처리
            remix_v = None
            if raw_data["video"]:
                remix_v = process_video_remix(
                    raw_data["video"], opt_flip, opt_spd, opt_mute, opt_blur
                )

            # 중국어 본문 한글 번역
            kor_title = translate_zh_to_ko(raw_data["title"])
            kor_desc = translate_zh_to_ko(raw_data["desc"])

            st.session_state["processed_data"] = {
                "raw_video": raw_data["video"],
                "remix_video": remix_v,
                "images": raw_data["images"],
                "title": kor_title,
                "desc": kor_desc,
            }
            st.success("✅ 다운로드 링크가 준비되었습니다!")
        except Exception as e:
            st.error(f"다운로드 실패: {e}")


# ==========================================
# UI 6. 결과 화면 & 판매 대본 출력
# ==========================================
if st.session_state.get("processed_data"):
    data = st.session_state["processed_data"]
    raw_v = data["raw_video"]
    remix_v = data["remix_video"]
    imgs = data["images"]
    title = data["title"]
    desc = data["desc"]

    st.markdown("---")

    # 1) 영상 결과물
    if remix_v or raw_v:
        c_v1, c_v2 = st.columns([1.5, 1.1])
        with c_v1:
            st.video(remix_v if remix_v else raw_v)
            st.caption("✨ [세탁 완료 영상] 좌우반전 + 배속 + 자막블러 적용")
        with c_v2:
            st.markdown(f"**제품명:** {title}")
            st.text_area("번역된 내용", desc, height=110)

            if remix_v:
                st.download_button(
                    "⬇️ 세탁 완료 영상 받기 (MP4)",
                    remix_v,
                    "remix_video.mp4",
                    "video/mp4",
                    type="primary",
                    use_container_width=True,
                )
            if raw_v:
                st.download_button(
                    "⬇️ 원본 영상 다운로드 (MP4)",
                    raw_v,
                    "raw_video.mp4",
                    "video/mp4",
                    use_container_width=True,
                )

    # 2) 사진 결과물 (카드뉴스/노트인 경우)
    if imgs and not raw_v:
        st.markdown(f"#### 🖼️ 고화질 사진 노트를 추출했습니다 ({len(imgs)}장)")
        cols = st.columns(3)
        for idx, img_b in enumerate(imgs):
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

    # 3) 다운로드 바로 밑: 4대 플랫폼 판매 대본
    st.markdown("---")
    st.markdown("#### ✍️ 4대 플랫폼 맞춤 판매 대본 (원클릭 복사)")
    st.caption("다운받은 영상의 실제 내용을 바탕으로 구성된 실전 판매 대본입니다.")

    scripts = generate_platform_scripts(title, desc)
    tab_th, tab_ys, tab_ir, tab_tt = st.tabs([
        "🧵 스레드 (댓글/링크 유도)",
        "🔴 유튜브 쇼츠 (30초 풀버전)",
        "📸 인스타 릴스 (저장 유도형)",
        "⚫ 틱톡 (15초 초고속형)",
    ])

    with tab_th:
        st.text_area("스레드 본문", scripts["threads"], height=190)
    with tab_ys:
        st.text_area("쇼츠 대본", scripts["shorts"], height=210)
    with tab_ir:
        st.text_area("릴스 캡션", scripts["reels"], height=210)
    with tab_tt:
        st.text_area("틱톡 대본", scripts["tiktok"], height=170)
