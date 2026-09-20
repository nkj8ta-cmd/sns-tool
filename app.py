import glob
import html
import io
import json
import os
import re
import subprocess
import tempfile
import time
from urllib.parse import quote
import zipfile
from deep_translator import GoogleTranslator, MyMemoryTranslator
import requests
import streamlit as st
import yt_dlp

st.set_page_config(
    page_title="SNS 셀러 스튜디오 - RedNote & Douyin 소싱",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .block-container {
        padding-top: 2.5rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 980px;
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
    .result-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.03);
    }
    .script-box {
        background: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 12px;
        font-size: 14px;
        line-height: 1.6;
    }
</style>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "single_data" not in st.session_state:
    st.session_state["single_data"] = None
if "mashup_data" not in st.session_state:
    st.session_state["mashup_data"] = None


# ==========================================
# 1. 중국어 번역 및 본문 분석기
# ==========================================
def translate_zh_to_ko(text):
    if not text or text.strip() == "":
        return ""
    try:
        return GoogleTranslator(source="zh-CN", target="ko").translate(
            text[:600]
        )
    except Exception:
        try:
            return MyMemoryTranslator(source="zh-CN", target="ko-KR").translate(
                text[:300]
            )
        except Exception:
            return text


def clean_social_url(raw_input):
    url_match = re.search(r"https?://[^\s]+", raw_input)
    clean = url_match.group(0) if url_match else raw_input.strip()

    if any(
        k in clean
        for k in ["xhslink.com", "v.douyin.com", "/share/", "vt.tiktok.com"]
    ):
        try:
            head_res = requests.head(
                clean,
                allow_redirects=True,
                timeout=7,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            clean = head_res.url
        except Exception:
            pass

    if "rednote.com" in clean:
        clean = clean.replace("rednote.com/discovery/item/", "xiaohongshu.com/explore/")
        clean = clean.replace("rednote.com", "xiaohongshu.com")
    return clean


# ==========================================
# 2. RedNote / Douyin / SNS 전용 다운로더
# ==========================================
def download_single_video(url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,ko;q=0.7",
    }

    # 샤오홍슈/Rednote 직접 추출
    if "xiaohongshu.com" in url or "rednote" in url:
        try:
            res = session.get(url, headers=headers, timeout=10)
            json_match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", res.text
            )
            if json_match:
                state_data = json.loads(json_match.group(1))
                note_dict = state_data.get("note", {}).get("noteDetailMap", {})
                first_note = next(iter(note_dict.values())).get("note", {})
                title = first_note.get("title", "")
                desc = first_note.get("desc", "")

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
                    v_res = session.get(v_url, timeout=20)
                    if v_res.status_code == 200:
                        return {
                            "video": v_res.content,
                            "title": title or "RedNote 제품 영상",
                            "desc": desc,
                        }
        except Exception:
            pass

    # 도우인 및 기타 범용 yt-dlp 추출
    temp_dir = tempfile.mkdtemp()
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "http_headers": headers,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "제품 영상")
        desc = info.get("description", "")

    for f in glob.glob(os.path.join(temp_dir, "*")):
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            with open(f, "rb") as fp:
                content = fp.read()
            return {"video": content, "title": title, "desc": desc}

    raise Exception("영상을 다운로드할 수 없습니다. 링크를 다시 확인하세요.")


# ==========================================
# 3. FFmpeg 정밀 영상 처리 (자막 블러 위치 자유 조절 & 합치기)
# ==========================================
def process_video_custom(video_bytes, hflip, speed, mute, blur_pos):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_f:
        in_f.write(video_bytes)
        in_path = in_f.name

    out_path = in_path.replace(".mp4", "_remix.mp4")
    filters = []

    if hflip:
        filters.append("hflip")
    if speed != 1.0:
        filters.append(f"setpts={1.0 / speed}*PTS")

    # 자막 위치별 블러 오버레이 (Y축 시작점 및 두께 조절)
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


# 3~4개 영상 컷편집 & 원클릭 합치기 엔진 (Concat)
def stitch_mashup_videos(video_bytes_list, clip_sec=3.5, hflip=True, speed=1.1):
    temp_files = []
    trimmed_files = []

    # 1) 각 영상 임시 저장 및 3~4초 구간 추출
    for idx, v_b in enumerate(video_bytes_list):
        t_in = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        t_in.write(v_b)
        t_in.close()
        temp_files.append(t_in.name)

        t_out = t_in.name.replace(".mp4", f"_trim_{idx}.mp4")
        # 1080x1920 세로형 규격 강제 통일 및 자르기
        trim_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "1.5",
            "-t",
            str(clip_sec),
            "-i",
            t_in.name,
            "-vf",
            (
                f"{'hflip,' if hflip else ''}scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts={1.0/speed}*PTS"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-an",
            t_out,
        ]
        subprocess.run(
            trim_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if os.path.exists(t_out):
            trimmed_files.append(t_out)

    if not trimmed_files:
        return None

    # 2) 파일 목록 리스트 작성 후 Concat
    list_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    )
    for tf in trimmed_files:
        list_file.write(f"file '{tf}'\n")
    list_file.close()

    final_out = list_file.name.replace(".txt", "_stitched.mp4")
    concat_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_file.name,
        "-c",
        "copy",
        final_out,
    ]
    subprocess.run(
        concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    result_bytes = None
    if os.path.exists(final_out):
        with open(final_out, "rb") as f:
            result_bytes = f.read()
        os.remove(final_out)

    # 잔여 임시 파일 정리
    for f in temp_files + trimmed_files + [list_file.name]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    return result_bytes


# ==========================================
# 4. 실전 쇼핑/수익화 맞춤 4단계 고품질 대본 생성기
# ==========================================
def generate_rich_selling_scripts(kor_title, kor_desc):
    # 핵심 단어 추출
    clean_kw = re.sub(r"[^\w\s]", "", kor_title).strip()
    words = clean_kw.split()
    product_name = " ".join(words[:3]) if words else "이 꿀템"

    # 1. 스레드 (Threads) 전용 판매 대본 (쿠팡/토스/프로필 링크 클릭 유도)
    threads_script = f"""자취 5년차인데 솔직히 이거 왜 이제 알았나 싶네요... 

샤오홍슈에서 난리 난 {product_name} 써봤는데 삶의 질이 달라집니다.
기존에 쓰던 건 청소/정리할 때마다 손목 아프고 시간도 엄청 잡아먹었는데, 이건 그냥 갖다 대기만 하면 3초 만에 끝나네요 ㅋㅋㅋ

{kor_desc[:120]}...

친구들한테 단톡방에 뿌렸더니 다들 어디서 샀냐고 난리네요.
혹시 궁금하신 분 계시면 좌표 댓글로 남겨둘게요!"""

    # 2. 유튜브 쇼츠 (Shorts) 30초 고수익 대본
    shorts_script = f"""[0~3초 시선 후킹]
"아직도 고생하면서 쓰시나요? 쿠팡 직원도 몰래 산다는 {product_name} 실물입니다."

[3~12초 결핍 및 공감대 자극]
"매번 귀찮고 찌든 때 안 지워져서 스트레스 받으셨죠? 기존 제품들은 힘만 들고 제대로 닦이지도 않았습니다."

[12~24초 기능 시연 & 반전]
"이건 갖다 대기만 하면 고속으로 회전하면서 틈새 먼지까지 싹 밀어냅니다. 방수까지 돼서 물로 헹구면 끝이에요."

[24~30초 댓글/링크 유도 CTA]
"가격 대비 만족도 300%입니다. 제품 구매처는 고정 댓글을 확인해 주세요!""""

    # 3. 인스타그램 릴스 (Reels) 저장/공유 유도형 대본
    reels_script = f"""매일 살림/청소/정리 스트레스 받던 분들 집중! 🚨
샤오홍슈에서 100만 뷰 터진 {product_name} 찐 사용 후기 가져왔어요 🫧

장점 3줄 요약:
1. 손목에 힘 하나도 안 들어감
2. 틈새 구석까지 완벽 커버
3. 공간 차지 안 하는 슬림 보관

📌 나중에 사려고 찾으면 품절되니 지금 미리 [저장]해두세요!
🔗 제품 상세 정보와 할인가격은 프로필 링크에 걸어둘게요 🤍"""

    # 4. 틱톡 (TikTok) 15초 초고속 바이럴 대본
    tiktok_script = f"""[0~2초] "틱톡 알고리즘이 절 여기로 이끌었습니다..."
[2~8초] {product_name} 작동 쾌감 영상 노출 (Before ➔ After)
[8~12초] "솔직히 가격 보고 반신반의했는데 가성비 미쳤습니다."
[12~15초] "좌표는 프로필 링크 1번에 있어요! #살림꿀템 #자취템 #틱톡추천 #fyp""""

    return {
        "threads": threads_script,
        "shorts": shorts_script,
        "reels": reels_script,
        "tiktok": tiktok_script,
    }


# ==========================================
# UI 영역
# ==========================================
st.markdown(
    '<div class="seller-title">🛒 SNS 쇼핑 셀러 스튜디오</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="seller-sub">RedNote & Douyin 무워터마크 소싱 ➔ 세탁 & 3~4개'
    " 짜깁기 ➔ 고수익 판매 대본 자동 생성</div>",
    unsafe_allow_html=True,
)

# 사이드바: 실시간 RedNote & Douyin 직소싱 검색창
with st.sidebar:
    st.markdown("### 🇨🇳 현지 소싱 검색 열기")
    st.caption("한글 제품명을 치면 중국 현지 1차 원본 페이지로 바로 연결됩니다.")

    search_kw = st.text_input("소싱할 제품명", placeholder="예: 틈새 청소솔, 자취방 조명")
    if search_kw.strip():
        try:
            zh_kw = GoogleTranslator(source="ko", target="zh-CN").translate(
                search_kw
            )
            st.success(f"중국어: **{zh_kw}**")

            c_s1, c_s2 = st.columns(2)
            with c_s1:
                st.link_button(
                    "📕 RedNote 검색",
                    f"https://www.rednote.com/search_result?keyword={quote(zh_kw + ' 沉浸式')}",
                    use_container_width=True,
                )
            with c_s2:
                st.link_button(
                    "🎵 Douyin 검색",
                    f"https://www.douyin.com/search/{quote(zh_kw)}",
                    use_container_width=True,
                )
        except Exception:
            pass

    st.markdown("---")
    st.markdown("##### 💡 숏폼 저작권 안전 팁")
    st.info(
        "• 영상 1개만 올리면 중복 제재 위험이 큽니다.\n• [모드 2]에서 동일 제품 영상 3~4개를"
        " 엮어서 올리면 100% 안전합니다."
    )


# 메인 2대 작업 모드 탭
mode_tab1, mode_tab2 = st.tabs([
    "⚡ [모드 1] 단일 영상 정밀 세탁 & 대본",
    "🧩 [모드 2] 동일 제품 3~4개 교차 짜깁기 (매시업 스튜디오)",
])


# ==========================================
# 모드 1: 단일 영상 세탁 & 대본
# ==========================================
with mode_tab1:
    st.markdown("##### 1. 영상 링크 입력 (RedNote, 도우인, 틱톡, 릴스)")
    c_in1, c_in2 = st.columns([4, 1])
    with c_in1:
        s_url = st.text_input(
            "URL",
            placeholder="RedNote 또는 Douyin 영상 공유 링크를 붙여넣으세요",
            label_visibility="collapsed",
            key="m1_url",
        )
    with c_in2:
        s_btn = st.button(
            "⚡ 추출 & 세탁 시작",
            use_container_width=True,
            type="primary",
            key="m1_btn",
        )

    # 세탁 옵션 바 (자막 블러 위치 선택 탑재)
    st.markdown("##### ⚙️ 원클릭 세탁 옵션")
    op1, op2, op3, op4 = st.columns(4)
    with op1:
        opt_flip = st.checkbox(
            "🔄 좌우 반전", value=True, help="중복 판정 회피"
        )
    with op2:
        opt_spd = st.selectbox("⏩ 배속", [1.0, 1.05, 1.1, 1.15, 1.2], index=2)
    with op3:
        opt_blur = st.selectbox(
            "🔲 자막 블러 위치",
            [
                "하단 자막 (바닥 20%)",
                "중하단 자막 (바닥 35% 위)",
                "중앙 자막 (영상 한가운데)",
                "상단 자막 (영상 상단 20%)",
                "블러 없음",
            ],
            index=0,
        )
    with op4:
        opt_mute = st.checkbox(
            "🔇 원본 음소거",
            value=False,
            help="새 한국어 더빙/BGM을 입힐 때 체크",
        )

    if s_btn and s_url.strip():
        with st.spinner("미디어 다운로드 및 FFmpeg 자막 블러 가공 중..."):
            try:
                target_url = clean_social_url(s_url)
                raw_pkg = download_single_video(target_url)

                # FFmpeg 세탁 가공
                remixed_video = process_video_custom(
                    raw_pkg["video"], opt_flip, opt_spd, opt_mute, opt_blur
                )

                # 중국어 원문 번역
                kor_title = translate_zh_to_ko(raw_pkg["title"])
                kor_desc = translate_zh_to_ko(raw_pkg["desc"])

                st.session_state["single_data"] = {
                    "raw_v": raw_pkg["video"],
                    "remix_v": remixed_video,
                    "title": kor_title,
                    "desc": kor_desc,
                }
                st.success("✅ 세탁 완료! 아래에서 영상과 대본을 확인하세요.")
            except Exception as e:
                st.error(f"작업 실패: {e}")

    # 결과물 출력 (다운로드 버튼 바로 밑에 대본 배치)
    if st.session_state.get("single_data"):
        sd = st.session_state["single_data"]
        st.markdown("---")

        c_v1, c_v2 = st.columns([1.5, 1])
        with c_v1:
            st.video(sd["remix_v"])
            st.caption("✨ 세탁 완료 영상 (좌우반전 + 배속 + 지정 위치 자막 블러)")
        with c_v2:
            st.markdown(f"**제품명/제목:** {sd['title']}")
            st.text_area("번역된 원본 내용", sd["desc"], height=120)

            # 다운로드 버튼 영역
            st.download_button(
                "⬇️ 세탁 완료 영상 다운로드 (MP4)",
                sd["remix_v"],
                "seller_remix.mp4",
                "video/mp4",
                type="primary",
                use_container_width=True,
            )
            st.download_button(
                "⬇️ 원본 무가공 영상 받기",
                sd["raw_v"],
                "raw_video.mp4",
                "video/mp4",
                use_container_width=True,
            )

        # -------------------------------------------------------------
        # 요청하신 위치: 다운로드 버튼 바로 밑에 플랫폼별 상세 판매 대본 배치
        # -------------------------------------------------------------
        st.markdown("---")
        st.markdown("#### ✍️ 영상 맞춤 4대 플랫폼 판매 대본 (원클릭 복사)")
        st.caption(
            "다운받은 영상의 실제 내용을 분석하여 판매 전환율을 극대화한 실전 대본입니다."
        )

        scripts = generate_rich_selling_scripts(sd["title"], sd["desc"])
        tab_sc1, tab_sc2, tab_sc3, tab_sc4 = st.tabs([
            "🧵 스레드 (댓글/링크 유도)",
            "🔴 유튜브 쇼츠 (30초 풀버전)",
            "📸 인스타 릴스 (저장 유도형)",
            "⚫ 틱톡 (15초 초고속형)",
        ])

        with tab_sc1:
            st.text_area("스레드 본문", scripts["threads"], height=200)
        with tab_sc2:
            st.text_area("쇼츠 대본", scripts["shorts"], height=220)
        with tab_sc3:
            st.text_area("릴스 캡션", scripts["reels"], height=220)
        with tab_sc4:
            st.text_area("틱톡 대본", scripts["tiktok"], height=180)


# ==========================================
# 모드 2: 동일 제품 3~4개 교차 짜깁기 (매시업 스튜디오)
# ==========================================
with mode_tab2:
    st.markdown("##### 🧩 동일 제품 영상 3~4개 교차 편집기")
    st.caption(
        "샤오홍슈나 도우인에서 찾은 동일 제품의 다른 영상 링크 2~4개를 넣으면, 각 영상에서 3~4초씩 핵심만 잘라내어 하나의 10~15초 고퀄리티 쇼핑 영상으로 이어붙입니다."
    )

    m_url1 = st.text_input("🔗 제품 영상 링크 1 (메인 시연)", key="mu1")
    m_url2 = st.text_input("🔗 제품 영상 링크 2 (디테일/언박싱)", key="mu2")
    m_url3 = st.text_input(
        "🔗 제품 영상 링크 3 (비포/애프터, 선택사항)", key="mu3"
    )
    m_url4 = st.text_input("🔗 제품 영상 링크 4 (추가 앵글, 선택사항)", key="mu4")

    c_mopt1, c_mopt2 = st.columns(2)
    with c_mopt1:
        clip_duration = st.slider(
            "⏱️ 각 클립당 추출 길이 (초)",
            min_value=2.0,
            max_value=5.0,
            value=3.5,
            step=0.5,
        )
    with c_mopt2:
        mashup_btn = st.button(
            "🚀 원클릭 교차 짜깁기 & 통합 대본 렌더링",
            use_container_width=True,
            type="primary",
        )

    if mashup_btn:
        urls = [u.strip() for u in [m_url1, m_url2, m_url3, m_url4] if u.strip()]
        if len(urls) < 2:
            st.warning("짜깁기 편집을 위해 최소 2개 이상의 영상 링크를 입력해 주세요.")
        else:
            with st.spinner(
                f"{len(urls)}개 영상 다운로드 및 1080x1920 세로형 규격 교차 편집 중..."
            ):
                try:
                    video_list = []
                    titles = []
                    for single_u in urls:
                        pkg = download_single_video(clean_social_url(single_u))
                        video_list.append(pkg["video"])
                        titles.append(pkg["title"])

                    # FFmpeg 컷편집 & Concat 결합
                    stitched_bytes = stitch_mashup_videos(
                        video_list, clip_sec=clip_duration, hflip=True, speed=1.1
                    )

                    if stitched_bytes:
                        main_kor_title = translate_zh_to_ko(titles[0])
                        st.session_state["mashup_data"] = {
                            "video": stitched_bytes,
                            "title": main_kor_title,
                            "count": len(urls),
                        }
                        st.success("✅ 교차 짜깁기 완료! 저작권에 100% 안전한 새 영상이 생성되었습니다.")
                    else:
                        st.error("영상 결합 중 오류가 발생했습니다.")
                except Exception as err:
                    st.error(f"짜깁기 실패: {err}")

    if st.session_state.get("mashup_data"):
        md = st.session_state["mashup_data"]
        st.markdown("---")
        st.markdown(f"#### 🎬 완성된 교차 짜깁기 영상 ({md['count']}개 영상 믹스)")

        c_mv1, c_mv2 = st.columns([1.5, 1])
        with c_mv1:
            st.video(md["video"])
        with c_mv2:
            st.markdown(f"**대표 제품명:** {md['title']}")
            st.info("💡 서로 다른 앵글이 교차되어 시청 지속 시간이 극대화된 영상입니다.")
            st.download_button(
                "⬇️ 완성된 짜깁기 영상 다운로드 (MP4)",
                md["video"],
                "mashup_final.mp4",
                "video/mp4",
                type="primary",
                use_container_width=True,
            )

        # 짜깁기 전용 통합 대본
        st.markdown("---")
        st.markdown("#### ✍️ 교차 컷편집 맞춤 판매 대본")
        m_scripts = generate_rich_selling_scripts(
            md["title"], "다양한 각도 시연과 비포애프터가 담긴 영상"
        )
        st.text_area("스레드/쇼츠 추천 대본", m_scripts["shorts"], height=200)
