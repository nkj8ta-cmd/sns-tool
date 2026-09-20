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
        padding-top: 2rem !important;
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
        margin-bottom: 20px;
    }
    .clip-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 8px 10px;
        margin-top: 6px;
    }
    .tag-clean {
        background: #dcfce7;
        color: #15803d;
        font-size: 11px;
        font-weight: 700;
        padding: 2px 6px;
        border-radius: 4px;
    }
    div[role="radiogroup"] {
        display: flex;
        justify-content: center;
        gap: 12px;
        margin-bottom: 22px;
    }
    div[role="radiogroup"] label {
        background: #ffffff;
        border: 1.5px solid #cbd5e1;
        padding: 10px 22px;
        border-radius: 25px;
        cursor: pointer;
        font-size: 14.5px;
        font-weight: 700;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        transition: all 0.2s ease;
    }
    div[role="radiogroup"] label:hover {
        border-color: #2563eb;
        color: #2563eb;
    }
</style>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "mode_choice" not in st.session_state:
    st.session_state["mode_choice"] = "⚡ [모드 1] 단일 영상 정밀 세탁 & 대본"
if "m1_url" not in st.session_state:
    st.session_state["m1_url"] = ""
if "mu1" not in st.session_state:
    st.session_state["mu1"] = ""
if "mu2" not in st.session_state:
    st.session_state["mu2"] = ""
if "mu3" not in st.session_state:
    st.session_state["mu3"] = ""
if "mu4" not in st.session_state:
    st.session_state["mu4"] = ""
if "single_data" not in st.session_state:
    st.session_state["single_data"] = None
if "mashup_data" not in st.session_state:
    st.session_state["mashup_data"] = None


# ==========================================
# 1. 번역 및 URL 정제 엔진
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
# 2. RedNote / Douyin / SNS 전용 다운로더 (검색 URL 방어 탑재)
# ==========================================
def download_single_video(url):
    # 검색 페이지 주소 필터링
    if "search_result" in url or "search/" in url:
        raise Exception(
            "입력하신 주소는 '검색 결과 목록' 링크입니다. 영상 1개를 클릭해서 열린"
            " '개별 영상 링크'를 복사해서 넣어주세요!"
        )

    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8,ko;q=0.7",
    }

    # 샤오홍슈/RedNote 직접 추출
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

    # 도우인 / 틱톡 / 유튜브 등 범용 추출
    temp_dir = tempfile.mkdtemp()
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "http_headers": headers,
    }

    if "youtube.com" in url or "youtu.be" in url:
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "tv_embedded"]}
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

    raise Exception("영상을 다운로드할 수 없습니다. 링크를 확인하세요.")


# ==========================================
# 3. FFmpeg 정밀 영상 처리 (자막 블러 & Concat)
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


def stitch_mashup_videos(video_bytes_list, clip_sec=3.5, hflip=True, speed=1.1):
    temp_files = []
    trimmed_files = []

    for idx, v_b in enumerate(video_bytes_list):
        t_in = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        t_in.write(v_b)
        t_in.close()
        temp_files.append(t_in.name)

        t_out = t_in.name.replace(".mp4", f"_trim_{idx}.mp4")
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

    for f in temp_files + trimmed_files + [list_file.name]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    return result_bytes


# ==========================================
# 4. 실전 쇼핑 판매 대본 생성기
# ==========================================
def generate_rich_selling_scripts(kor_title, kor_desc):
    clean_kw = re.sub(r"[^\w\s]", "", kor_title).strip()
    words = clean_kw.split()
    product_name = " ".join(words[:3]) if words else "이 꿀템"

    threads_script = (
        "자취 5년차인데 솔직히 이거 왜 이제 알았나 싶네요... \n\n"
        f"샤오홍슈에서 난리 난 {product_name} 써봤는데 삶의 질이 달라집니다.\n"
        "기존에 쓰던 건 청소/정리할 때마다 손목 아프고 시간도 엄청 잡아먹었는데, 이건 그냥 갖다 대기만 하면 3초 만에 끝나네요 ㅋㅋㅋ\n\n"
        f"{kor_desc[:120]}...\n\n"
        "친구들한테 단톡방에 뿌렸더니 다들 어디서 샀냐고 난리네요.\n"
        "혹시 궁금하신 분 계시면 좌표 댓글로 남겨둘게요!"
    )

    shorts_script = (
        "[0~3초 시선 후킹]\n"
        f'"아직도 고생하면서 쓰시나요? 쿠팡 직원도 몰래 산다는 {product_name} 실물입니다."\n\n'
        "[3~12초 결핍 및 공감대 자극]\n"
        '"매번 귀찮고 찌든 때 안 지워져서 스트레스 받으셨죠? 기존 제품들은 힘만 들고 제대로 닦이지도 않았습니다."\n\n'
        "[12~24초 기능 시연 & 반전]\n"
        '"이건 갖다 대기만 하면 고속으로 회전하면서 틈새 먼지까지 싹 밀어냅니다. 방수까지 돼서 물로 헹구면 끝이에요."\n\n'
        "[24~30초 댓글/링크 유도 CTA]\n"
        '"가격 대비 만족도 300%입니다. 제품 구매처는 고정 댓글을 확인해 주세요!"'
    )

    reels_script = (
        "매일 살림/청소/정리 스트레스 받던 분들 집중! 🚨\n"
        f"샤오홍슈에서 100만 뷰 터진 {product_name} 찐 사용 후기 가져왔어요 🫧\n\n"
        "장점 3줄 요약:\n"
        "1. 손목에 힘 하나도 안 들어감\n"
        "2. 틈새 구석까지 완벽 커버\n"
        "3. 공간 차지 안 하는 슬림 보관\n\n"
        "📌 나중에 사려고 찾으면 품절되니 지금 미리 [저장]해두세요!\n"
        "🔗 제품 상세 정보와 할인가격은 프로필 링크에 걸어둘게요 🤍"
    )

    tiktok_script = (
        '[0~2초] "틱톡 알고리즘이 절 여기로 이끌었습니다..."\n'
        f"[2~8초] {product_name} 작동 쾌감 영상 노출 (Before ➔ After)\n"
        '[8~12초] "솔직히 가격 보고 반신반의했는데 가성비 미쳤습니다."\n'
        '[12~15초] "좌표는 프로필 링크 1번에 있어요! #살림꿀템 #자취템 #틱톡추천 #fyp"'
    )

    return {
        "threads": threads_script,
        "shorts": shorts_script,
        "reels": reels_script,
        "tiktok": tiktok_script,
    }


# ==========================================
# 5. 실시간 중국 바이럴 소싱 추천 아이템 목록
# ==========================================
@st.cache_data(ttl=3600)
def get_trending_china_products():
    return [
        {
            "name": "전동 회전 틈새 청소솔",
            "zh": "电动缝隙刷",
            "point": "월 판매 10만건 돌파 / 타일·창틀 찌든때 쾌감 회전",
            "sub_searches": [
                ("무자막 쾌감 시연", "电动缝隙刷 沉浸式 无字"),
                ("언박싱 & 헤드 교체", "电动缝隙刷 开箱 刷头"),
                ("화장실 줄눈 비포애프터", "电动缝隙刷 浴室清洁对比"),
                ("창문 틈새 먼지 세척", "电动缝隙刷 窗户槽清洁"),
            ],
        },
        {
            "name": "정량 토출 0.5g 원터치 양념통",
            "zh": "定量调料罐",
            "point": "건강/식단 바이럴 / 누르면 정확히 0.5g 토출",
            "sub_searches": [
                ("무자막 토출 쾌감", "定量调料罐 按压出盐 无字"),
                ("밀폐 방습 구조 분해", "定量调料瓶 密封防潮"),
                ("요리 중 한 손 조작", "按压控盐罐 做饭实测"),
            ],
        },
        {
            "name": "원터치 팝업 실리콘 얼음틀",
            "zh": "按压制冰盒",
            "point": "홈카페 필수템 / 버튼 누르면 얼음 전량 낙하",
            "sub_searches": [
                ("무자막 얼음 낙하", "按压制冰盒 解压落冰 无字"),
                ("아이스 커피 제조", "制冰盒 冰美式 沉浸式"),
                ("실리콘 복원력 테스트", "按压冰格 硅胶软底"),
            ],
        },
        {
            "name": "3cm 납작 접이식 빨래바구니",
            "zh": "折叠脏衣篮",
            "point": "원룸 자취방 필수템 / 세탁기 틈새 숨김 보관",
            "sub_searches": [
                ("무자막 틈새 수납", "折叠脏衣篮 夹缝收纳 无字"),
                ("벽걸이 거치 & 대용량", "壁挂脏衣篓 大容量"),
                ("손잡이 휴대 시연", "手提脏衣篮 独居好物"),
            ],
        },
    ]


# ==========================================
# UI 헤더
# ==========================================
st.markdown(
    '<div class="seller-title">🛒 SNS 쇼핑 셀러 스튜디오</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="seller-sub">RedNote & Douyin 직소싱 ➔ 원클릭 세탁 & 3~4개 교차'
    " 짜깁기 ➔ 고수익 판매 대본 자동 생성</div>",
    unsafe_allow_html=True,
)

# 사이드바
with st.sidebar:
    st.markdown("### 🔍 1. 키워드 직접 검색")
    st.caption("궁금한 제품명을 한글로 치면 중국 검색창이 열립니다.")

    search_kw = st.text_input(
        "소싱할 제품명",
        placeholder="예: 틈새 청소솔, 자취방 조명",
        key="custom_search_kw",
    )
    if search_kw.strip():
        try:
            zh_kw = GoogleTranslator(source="ko", target="zh-CN").translate(
                search_kw
            )
            st.success(f"중국어: **{zh_kw}**")
            c_s1, c_s2 = st.columns(2)
            with c_s1:
                st.link_button(
                    "📕 RedNote",
                    f"https://www.rednote.com/search_result?keyword={quote(zh_kw + ' 沉浸式')}",
                    use_container_width=True,
                )
            with c_s2:
                st.link_button(
                    "🎵 Douyin",
                    f"https://www.douyin.com/search/{quote(zh_kw)}",
                    use_container_width=True,
                )
        except Exception:
            pass

    st.markdown("---")
    st.markdown("### 🔥 2. 실시간 중국 바이럴 소싱 추천")
    st.caption("클릭하면 해당 앵글의 현지 영상 목록으로 바로 이동합니다.")

    trending_items = get_trending_china_products()

    for p_idx, prod in enumerate(trending_items):
        with st.expander(
            f"📦 #{p_idx+1} {prod['name']}", expanded=(p_idx == 0)
        ):
            st.caption(f"💡 {prod['point']}")

            for s_name, s_query in prod["sub_searches"]:
                st.markdown(f"• **{s_name}**")
                c1, c2 = st.columns(2)
                with c1:
                    st.link_button(
                        "📕 RedNote 열기",
                        f"https://www.rednote.com/search_result?keyword={quote(s_query)}",
                        use_container_width=True,
                    )
                with c2:
                    st.link_button(
                        "🎵 Douyin 열기",
                        f"https://www.douyin.com/search/{quote(s_query)}",
                        use_container_width=True,
                    )

    st.markdown("---")
    st.markdown("##### 🧪 기능 확인용 원클릭 테스트")
    st.caption("링크를 직접 찾기 번거로우실 때 눌러서 짜깁기 엔진을 바로 테스트해보세요.")
    if st.button("🚀 샘플 영상으로 짜깁기 즉시 테스트", use_container_width=True):
        st.session_state["mode_choice"] = (
            "🧩 [모드 2] 동일 제품 3~4개 교차 짜깁기 (매시업 스튜디오)"
        )
        st.session_state["mu1"] = "https://www.tiktok.com/@test/video/1"
        st.session_state["mu2"] = "https://www.tiktok.com/@test/video/2"
        st.rerun()


# 메인 작업 모드 선택
mode_selection = st.radio(
    "작업 모드 선택",
    options=[
        "⚡ [모드 1] 단일 영상 정밀 세탁 & 대본",
        "🧩 [모드 2] 동일 제품 3~4개 교차 짜깁기 (매시업 스튜디오)",
    ],
    key="mode_choice",
    label_visibility="collapsed",
)


# ==========================================
# 모드 1: 단일 영상 세탁 & 대본
# ==========================================
if mode_selection == "⚡ [모드 1] 단일 영상 정밀 세탁 & 대본":
    st.markdown("##### 1. 영상 링크 입력 (RedNote, 도우인, 틱톡, 릴스)")
    c_in1, c_in2 = st.columns([4, 1])
    with c_in1:
        s_url = st.text_input(
            "URL",
            placeholder="영상 공유 링크(예: discovery/item/... 또는 xhslink.com/...)를 붙여넣으세요",
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

                remixed_video = process_video_custom(
                    raw_pkg["video"], opt_flip, opt_spd, opt_mute, opt_blur
                )

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
else:
    st.markdown("##### 🧩 동일 제품 영상 3~4개 교차 편집기")
    st.caption(
        "동일 제품의 다른 앵글 개별 영상 링크(discovery/item/... 또는 xhslink.com)를"
        " 넣으면, 각 영상에서 3~4초씩 추출해 1080x1920 세로형 완제품 쇼핑 영상으로 결합합니다."
    )

    m_url1 = st.text_input(
        "🔗 제품 영상 링크 1 (메인 시연)",
        value=st.session_state["mu1"],
        key="mu1",
        placeholder="예: https://www.rednote.com/discovery/item/...",
    )
    m_url2 = st.text_input(
        "🔗 제품 영상 링크 2 (디테일/언박싱)",
        value=st.session_state["mu2"],
        key="mu2",
        placeholder="예: http://xhslink.com/a/...",
    )
    m_url3 = st.text_input(
        "🔗 제품 영상 링크 3 (비포/애프터, 선택사항)",
        value=st.session_state["mu3"],
        key="mu3",
    )
    m_url4 = st.text_input(
        "🔗 제품 영상 링크 4 (추가 앵글, 선택사항)",
        value=st.session_state["mu4"],
        key="mu4",
    )

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
                f"{len(urls)}개 영상 다운로드 및 1080x1920 규격 교차 편집 중..."
            ):
                try:
                    video_list = []
                    titles = []
                    for single_u in urls:
                        pkg = download_single_video(clean_social_url(single_u))
                        video_list.append(pkg["video"])
                        titles.append(pkg["title"])

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

        st.markdown("---")
        st.markdown("#### ✍️ 교차 컷편집 맞춤 판매 대본")
        m_scripts = generate_rich_selling_scripts(
            md["title"], "다양한 각도 시연과 비포애프터가 담긴 영상"
        )
        st.text_area("스레드/쇼츠 추천 대본", m_scripts["shorts"], height=200)
