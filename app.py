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
    page_title="SnapStudio - SNS 무워터마크 다운로더 & 바이럴 스튜디오",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# SnapWC 스타일 CSS 주입
st.markdown(
    """
<style>
    .block-container {
        padding-top: 4.5rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 960px;
    }
    .snap-hero-title {
        text-align: center;
        font-size: 30px;
        font-weight: 800;
        background: linear-gradient(90deg, #2563eb, #db2777, #ea580c);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-top: 5px;
        margin-bottom: 4px;
    }
    .snap-hero-sub {
        text-align: center;
        font-size: 14.5px;
        color: #64748b;
        margin-bottom: 20px;
    }
    .preview-box {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 16px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    .stat-badge {
        display: inline-block;
        font-size: 11.5px;
        color: #64748b;
        background: #f1f5f9;
        padding: 2px 6px;
        border-radius: 4px;
        margin-right: 4px;
        margin-top: 4px;
    }
    .mashup-clip {
        background: #f8fafc;
        border: 1px dashed #cbd5e1;
        border-radius: 8px;
        padding: 8px;
        margin-top: 6px;
    }
</style>

<script>
async function pasteFromClipboard() {
    try {
        const text = await navigator.clipboard.readText();
        if (text) {
            const inputs = window.parent.document.querySelectorAll('input[type="text"]');
            for (let input of inputs) {
                if (input.placeholder && input.placeholder.includes("붙여넣어")) {
                    input.value = text;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    break;
                }
            }
        }
    } catch (e) {
        alert("클립보드 권한을 허용해 주세요.");
    }
}
</script>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "main_text_field" not in st.session_state:
    st.session_state["main_text_field"] = ""
if "data" not in st.session_state:
    st.session_state["data"] = None
if "remix_video" not in st.session_state:
    st.session_state["remix_video"] = None
if "mp3_bytes" not in st.session_state:
    st.session_state["mp3_bytes"] = None
if "auto_run" not in st.session_state:
    st.session_state["auto_run"] = False


# ==========================================
# 1. 번역 및 URL 정제 엔진
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


def clean_social_url(raw_input):
    url_match = re.search(r"https?://[^\s]+", raw_input)
    clean = url_match.group(0) if url_match else raw_input.strip()

    if any(
        k in clean
        for k in ["xhslink.com", "vt.tiktok.com", "/share/", "/t/", "youtu.be"]
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

    if "threads.com" in clean or "threads.net" in clean:
        clean = clean.split("?")[0].replace("threads.com", "threads.net")
    elif "youtube.com" in clean:
        clean = clean.split("&")[0]
        if "shorts/" in clean:
            clean = clean.split("?")[0]

    return clean


# ==========================================
# 2. 메타 크롤러 & 범용 yt-dlp 엔진
# ==========================================
def extract_direct_meta(url):
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,zh-CN;q=0.8,en-US;q=0.7",
    }
    if "threads" in url:
        headers["User-Agent"] = "facebookexternalhit/1.1"

    res = session.get(url, headers=headers, timeout=10)
    page_html = res.text

    title = "SNS Content"
    description = "추출된 본문이 없습니다."
    videos, images = [], []

    if "xiaohongshu.com" in url or "rednote" in url:
        try:
            json_match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*({.+?})</script>", page_html
            )
            if json_match:
                state_data = json.loads(json_match.group(1))
                note_dict = state_data.get("note", {}).get("noteDetailMap", {})
                first_note = next(iter(note_dict.values())).get("note", {})
                title = first_note.get("title") or title
                description = first_note.get("desc") or description

                if first_note.get("type") == "video":
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
                        videos.append(v_url)

                for img_item in first_note.get("imageList", []):
                    img_u = img_item.get("urlDefault") or img_item.get(
                        "infoList", [{}]
                    )[-1].get("url")
                    if img_u:
                        images.append(img_u)
        except Exception:
            pass

    if not description or description == "추출된 본문이 없습니다.":
        desc_match = re.search(
            r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
            page_html,
            re.DOTALL,
        )
        if desc_match:
            description = html.unescape(desc_match.group(1))

    if not videos:
        og_v = re.findall(
            r'<meta\s+(?:property|name)=["\']og:video(?::url)?["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        videos.extend([html.unescape(v) for v in og_v])

    if not images:
        og_i = re.findall(
            r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](.*?)["\']',
            page_html,
        )
        for img in og_i:
            clean_i = html.unescape(img)
            if "static.cdninstagram.com" not in clean_i:
                images.append(clean_i)

    video_bytes_list, image_bytes_list = [], []
    for v_u in list(dict.fromkeys(videos)):
        try:
            r = session.get(v_u, timeout=15)
            if r.status_code == 200 and len(r.content) > 3000:
                video_bytes_list.append(r.content)
        except Exception:
            pass

    for i_u in list(dict.fromkeys(images)):
        try:
            r = session.get(i_u, timeout=10)
            if r.status_code == 200:
                image_bytes_list.append(r.content)
        except Exception:
            pass

    thumb = (
        image_bytes_list[0]
        if image_bytes_list
        else (video_bytes_list[0] if video_bytes_list else None)
    )
    return {
        "title": title,
        "description": description,
        "videos": video_bytes_list,
        "images": image_bytes_list,
        "thumbnail": thumb,
    }


def download_media_package(target_url, progress_bar=None):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    def progress_hook(d):
        if d["status"] == "downloading" and progress_bar:
            p_str = d.get("_percent_str", "0%").replace("%", "").strip()
            try:
                val = min(int(float(p_str)), 95)
                progress_bar.progress(val, text=f"📥 다운로드 수신 중... {val}%")
            except Exception:
                pass

    ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "progress_hooks": [progress_hook],
    }

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["format"] = "best[ext=mp4]/best"
        ydl_opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "ios", "tv_embedded"],
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


# ==========================================
# 3. FFmpeg 가공 엔진 (자막 블러 기능 추가)
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


def process_video_remix(video_bytes, hflip, speed, mute, blur_subtitles):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_file:
        in_file.write(video_bytes)
        in_path = in_file.name

    out_path = in_path.replace(".mp4", "_remix.mp4")
    filters = []

    # 1) 좌우 반전
    if hflip:
        filters.append("hflip")

    # 2) 배속 조정
    if speed != 1.0:
        pts = 1.0 / speed
        filters.append(f"setpts={pts}*PTS")

    # 3) 하단 자막 블러 처리 (하단 20% 영역 흐림 처리)
    if blur_subtitles:
        # split 후 하단 20%를 crop -> boxblur -> 다시 overlay
        sub_filter = (
            "split[main][sub];"
            "[sub]crop=iw:ih*0.22:0:ih*0.78,boxblur=15:5[blurred];"
            "[main][blurred]overlay=0:H*0.78"
        )
        if filters:
            vf_cmd = ["-filter_complex", f"{','.join(filters)},{sub_filter}"]
        else:
            vf_cmd = ["-filter_complex", sub_filter]
    else:
        vf_cmd = ["-vf", ",".join(filters)] if filters else []

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
# 4. AI 바이럴 후킹 & 대본 생성기 (틱톡/쇼츠/릴스/스레드)
# ==========================================
def generate_viral_hooks(title, platform):
    title_clean = title.strip() or "이 제품"
    hooks = [
        f"🔥 '이거 모르면 평생 손해' 소리 나오는 {title_clean} 실물 체감",
        f"👀 자취 5년차인데 왜 이걸 이제야 알았을까요? ({title_clean})",
        f"🚨 틱톡에서 300만 뷰 터진 바로 그 영상 속 꿀템",
    ]
    script_outline = f"""[0~3초 시선 후킹]
"아직도 고생하면서 쓰시나요? 이거 하나면 3초 만에 끝납니다."

[3~15초 기능 시연 & 공감대 형성]
- 실제 문제 상황(불편함, 찌든 때, 정리 안 됨)을 2초간 노출
- {title_clean} 작동 모습 클로즈업

[15~25초 반전 결과 & 사용 팁]
- 사용 전/후 비교(Before & After)
- 다른 멀티 앵글 클립과 교차 편집하여 지루함 제거

[마무리 CTA]
"더 자세한 정보와 구매처는 프로필 링크에서 확인하세요!"""

    return hooks, script_outline


# ==========================================
# 5. 동일 제품 3~4개 교차 짜깁기 클러스터 DB
# ==========================================
@st.cache_data(ttl=3600)
def get_mashup_product_clusters():
    # 동일 제품에 대해 3~4개의 서로 다른 각도/내용의 클립 세트 제공
    return [
        {
            "product": "전동 틈새 회전 청소솔",
            "category": "생활/청소",
            "clean_subtitle": True,
            "clips": [
                {
                    "title": "클립 A: 패키지 언박싱 & 헤드 교체",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("电动缝隙刷 开箱")
                    ),
                    "likes": "18.4만",
                },
                {
                    "title": "클립 B: 욕실 타일 찌든때 고속 회전 시연",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("电动缝隙刷 清洁实测")
                    ),
                    "likes": "22.1만",
                },
                {
                    "title": "클립 C: 창문 틈새 먼지 세척 비포애프터",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("电动缝隙刷 窗户缝隙")
                    ),
                    "likes": "14.2만",
                },
                {
                    "title": "클립 D: 방수 테스트 & 간편 보관 거치",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("电动缝隙刷 防水收纳")
                    ),
                    "likes": "9.8만",
                },
            ],
        },
        {
            "product": "정량 토출 원터치 양념통",
            "category": "주방/요리",
            "clean_subtitle": True,
            "clips": [
                {
                    "title": "클립 A: 소금/설탕 0.5g 정량 토출 클로즈업",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("定量调料罐 控盐")
                    ),
                    "likes": "24.5만",
                },
                {
                    "title": "클립 B: 밀폐 실리콘 방습 뚜껑 분해",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("定量调料瓶 防潮密封")
                    ),
                    "likes": "16.8만",
                },
                {
                    "title": "클립 C: 실제 요리 중 한 손 조작 쾌감",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("按压出盐 厨房神器")
                    ),
                    "likes": "19.3만",
                },
            ],
        },
        {
            "product": "자취방 접이식 빨래바구니",
            "category": "1인가구/자취",
            "clean_subtitle": True,
            "clips": [
                {
                    "title": "클립 A: 벽면 틈새 3cm 납작 접기 시연",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("折叠脏衣篮 夹缝收纳")
                    ),
                    "likes": "31.2만",
                },
                {
                    "title": "클립 B: 대용량 세탁물 투입 내구성 테스트",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("壁挂脏衣篓 大容量")
                    ),
                    "likes": "15.0만",
                },
                {
                    "title": "클립 C: 손잡이 이동 및 세탁기 앞 거치",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("手提洗衣篮 独居好物")
                    ),
                    "likes": "12.7만",
                },
            ],
        },
        {
            "product": "초강력 무선 터보 에어건",
            "category": "아이디어/테크",
            "clean_subtitle": False,
            "clips": [
                {
                    "title": "클립 A: 키보드 속 과자부스러기 날리기",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("暴力风扇 键盘除尘")
                    ),
                    "likes": "45.0만",
                },
                {
                    "title": "클립 B: 세차 후 차량 물기 초고속 건조",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("涡轮风扇 汽车吹水")
                    ),
                    "likes": "38.6만",
                },
                {
                    "title": "클립 C: 캠핑 불피우기 송풍 시연",
                    "url": (
                        "https://www.rednote.com/search_result?keyword="
                        + quote("户外生火 暴力风扇")
                    ),
                    "likes": "21.4만",
                },
            ],
        },
    ]


# ==========================================
# UI 1. 왼쪽 사이드바: 무자막 우선 & 동일 제품 3~4개 교차 클러스터
# ==========================================
with st.sidebar:
    st.markdown("### 🧩 동일 제품 3~4개 교차 짜깁기 클러스터")
    st.caption(
        "영상 1개로 세탁하면 저작권/중복에 걸립니다. 동일 제품의 다른 각도 영상을 3~4개 받아 컷편집(매시업)하세요!"
    )

    filter_no_sub = st.toggle("✨ 자막 없는(클린) 영상 우선 모드", value=True)
    clusters = get_mashup_product_clusters()

    for idx, c_item in enumerate(clusters):
        if filter_no_sub and not c_item["clean_subtitle"]:
            continue

        with st.expander(
            f"📦 #{idx+1} {c_item['product']} ({len(c_item['clips'])}개 클립)",
            expanded=(idx == 0),
        ):
            st.markdown(
                f"<span class='stat-badge'>카테고리: {c_item['category']}</span>"
                + (
                    "<span class='stat-badge' style='color:#16a34a;'>무자막"
                    " 클린</span>"
                    if c_item["clean_subtitle"]
                    else ""
                ),
                unsafe_allow_html=True,
            )

            for clip in c_item["clips"]:
                st.markdown(
                    f"<div class='mashup-clip'>"
                    f"<b>{clip['title']}</b> (❤️ {clip['likes']})"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                col_c1, col_c2 = st.columns([1.6, 1])
                with col_c1:
                    if st.button(
                        "⚡ 이 클립 받기",
                        key=f"clip_load_{clip['title']}",
                        use_container_width=True,
                    ):
                        st.session_state["main_text_field"] = clip["url"]
                        st.session_state["auto_run"] = True
                        st.rerun()
                with col_c2:
                    st.link_button("🔗 원본", clip["url"], use_container_width=True)


# ==========================================
# UI 2. 상단 3대 핵심 제어 바 (가로 탭 배치)
# ==========================================
st.markdown(
    '<div class="snap-hero-title">SnapStudio 미디어 리사이클링 허브</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="snap-hero-sub">무워터마크 추출 · 원클릭 자막블러 세탁 · AI 대본'
    " 생성 · 교차 짜깁기</div>",
    unsafe_allow_html=True,
)

tab_preset, tab_hook, tab_tags = st.tabs([
    "⚡ 1. 원클릭 자동 세탁 프리셋 바",
    "✍️ 2. AI 바이럴 후킹 & 대본 (틱톡/쇼츠/릴스/스레드)",
    "🏷️ 3. 오늘의 핫 소싱 키워드 칩",
])

# [탭 1] 원클릭 자동 세탁 프리셋
with tab_preset:
    col_p1, col_p2, col_p3, col_p4 = st.columns(4)
    with col_p1:
        preset_hflip = st.checkbox(
            "🔄 좌우 반전",
            value=True,
            help="화면 축을 반전시켜 중복 인식을 우회합니다.",
        )
    with col_p2:
        preset_speed = st.selectbox(
            "⏩ 미세 배속",
            [1.0, 1.05, 1.1, 1.15, 1.2],
            index=2,
            help="1.1배속은 시청 지속 시간을 끌어올립니다.",
        )
    with col_p3:
        preset_blur = st.checkbox(
            "🔲 하단 자막 블러",
            value=True,
            help="중국어 하드코딩 자막 위치를 자연스럽게 블러 처리합니다.",
        )
    with col_p4:
        preset_mute = st.checkbox(
            "🔇 원본 오디오 음소거",
            value=False,
            help="음소거 후 새 음원/나레이션을 입힐 때 유용합니다.",
        )

# [탭 2] AI 바이럴 후킹 & 대본 생성기
with tab_hook:
    c_hook_plat, c_hook_title = st.columns([1.5, 3.5])
    with c_hook_plat:
        target_platform = st.selectbox(
            "타겟 플랫폼",
            ["⚫ 틱톡 (TikTok)", "🔴 유튜브 쇼츠", "📸 인스타 릴스", "🧵 스레드 (Threads)"],
        )
    with c_hook_title:
        sample_title = st.text_input(
            "소재 키워드",
            value="전동 틈새 청소솔",
            placeholder="영상 주제나 제품명을 적으세요",
        )

    hooks, script = generate_viral_hooks(sample_title, target_platform)
    st.markdown("**🎯 추천 3초 후킹 카피 (복사해서 썸네일/첫 대사로 활용):**")
    for h in hooks:
        st.code(h, language="text")

    with st.expander("📄 숏폼 30초 교차 편집 대본 가이드 보기"):
        st.text_area("대본 구조", script, height=180)

# [탭 3] 오늘의 핫 소싱 키워드 칩
with tab_tags:
    st.caption("클릭하면 아래 주소창에 중국어 검색어가 자동으로 채워집니다.")
    tag_cols = st.columns(5)
    tags = [
        ("🧹 청소 쾌감", "解压 清洁神器"),
        ("✨ 삶의 질 상승", "提升幸福感 好物"),
        ("🍳 주방 치트키", "厨房 懒人神器"),
        ("🏠 자취방 꿀템", "独居 出租屋好物"),
        ("📦 알리/테무 신박템", "黑科技 实用工具"),
    ]
    for i, (t_name, t_kw) in enumerate(tags):
        with tag_cols[i]:
            if st.button(t_name, use_container_width=True):
                st.session_state["main_text_field"] = (
                    f"https://www.rednote.com/search_result?keyword={quote(t_kw)}"
                )
                st.rerun()

st.write("")


# ==========================================
# UI 3. 주소창 (지우기 ✖ & 붙여넣기 📋)
# ==========================================
def clear_url_callback():
    st.session_state["main_text_field"] = ""
    st.session_state["data"] = None
    st.session_state["remix_video"] = None
    st.session_state["mp3_bytes"] = None


c_input, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_input:
    url_input_val = st.text_input(
        "입력창",
        key="main_text_field",
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
        on_click=None,
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
    analyze_btn = st.button(
        "다운로드 링크 받기", use_container_width=True, type="primary"
    )

st.markdown(
    '<div style="text-align: center; font-size: 12.5px; color: #94a3b8;'
    ' margin-top: 8px; margin-bottom: 25px;">YouTube Shorts, TikTok,'
    " Instagram, Threads, RedNote(샤오홍슈) 완벽 지원</div>",
    unsafe_allow_html=True,
)


# 다운로드 트리거 및 프로그레스 바 실행
is_triggered = analyze_btn or st.session_state["auto_run"]
st.session_state["auto_run"] = False

if is_triggered:
    current_target = st.session_state["main_text_field"].strip()
    if not current_target:
        st.warning("링크 또는 공유 텍스트를 입력해 주세요.")
    else:
        # SnapWC 스타일 진행률 게이지 바
        progress_bar = st.progress(5, text="⌛ 처리 중... 5%")
        try:
            time.sleep(0.2)
            progress_bar.progress(25, text="🔍 소셜 링크 분석 및 모바일 리다이렉트 추적... 25%")
            final_url = clean_social_url(current_target)

            progress_bar.progress(50, text="⚡ 미디어 패키지 스트림 및 무워터마크 데이터 추출... 50%")
            if any(
                k in final_url for k in ["rednote", "xiaohongshu", "threads"]
            ):
                try:
                    res_data = extract_direct_meta(final_url)
                    if not res_data["videos"] and not res_data["images"]:
                        res_data = download_media_package(
                            final_url, progress_bar
                        )
                except Exception:
                    res_data = download_media_package(final_url, progress_bar)
            else:
                res_data = download_media_package(final_url, progress_bar)

            # 상단 프리셋 자동 세탁이 켜져 있는 경우 FFmpeg 즉시 자동 렌더링
            if res_data.get("videos"):
                progress_bar.progress(
                    80, text="✂️ 원클릭 프리셋 자동 세탁 렌더링 중 (반전/배속/자막블러)... 80%"
                )
                remix_result = process_video_remix(
                    res_data["videos"][0],
                    preset_hflip,
                    preset_speed,
                    preset_mute,
                    preset_blur,
                )
                st.session_state["remix_video"] = remix_result

            progress_bar.progress(100, text="✅ 완료! 다운로드 링크가 준비되었습니다.")
            time.sleep(0.4)
            progress_bar.empty()

            st.session_state["data"] = res_data
            st.session_state["mp3_bytes"] = None

        except Exception as err:
            progress_bar.empty()
            st.error(f"다운로드 실패: {err}")


# ==========================================
# UI 4. 파일 미리보기 및 해상도별 다운로드 리스트 (스크린샷 15, 16 1:1 구현)
# ==========================================
if st.session_state.get("data"):
    data = st.session_state["data"]
    videos = data["videos"]
    images = data["images"]
    title = data["title"]
    description = data["description"]
    thumb = data.get("thumbnail")
    remix_v = st.session_state.get("remix_video")

    st.markdown("---")

    # 스크린샷 15 스타일: 파일 미리보기 전용 카드
    st.markdown(f"#### 🎬 파일 미리보기 및 다운로드: `{title[:40]}...`")

    with st.container():
        c_prev_vid, c_prev_info = st.columns([1.5, 1.2])

        with c_prev_vid:
            if remix_v:
                st.video(remix_v)
                st.caption(
                    "✨ [자동 세탁 완료] 좌우반전 + 배속 + 하단자막블러가 적용된"
                    " 영상입니다."
                )
            elif videos:
                st.video(videos[0])
                st.caption("원본 영상 미리보기")
            elif thumb:
                st.image(thumb, use_container_width=True)

        with c_prev_info:
            st.markdown(f"**제목:** {title}")
            st.text_area("내용/해시태그 (복사 가능)", description, height=130)

            c_btn_a, c_btn_b = st.columns(2)
            with c_btn_a:
                if thumb:
                    st.download_button(
                        "🖼 커버 이미지 다운로드",
                        thumb,
                        "cover.jpg",
                        "image/jpeg",
                        use_container_width=True,
                    )
            with c_btn_b:
                # 세탁 완료 영상 최우선 다운로드
                if remix_v:
                    st.download_button(
                        "⚡ 세탁 영상 받기 (MP4)",
                        remix_v,
                        "remix_complete.mp4",
                        "video/mp4",
                        type="primary",
                        use_container_width=True,
                    )

    st.markdown("---")

    # 스크린샷 16 스타일: 영상 및 오디오 2열 분리 다운로드 리스트
    st.markdown("#### 📥 규격별 미디어 다운로드")

    if videos:
        v_main = videos[0]
        v_size_mb = round(len(v_main) / (1024 * 1024), 1)

        c_v_list, c_a_list = st.columns(2)

        # 좌측 열: 영상 규격
        with c_v_list:
            st.markdown("🎬 **영상 (Video)**")

            # 1) UHD / 원본 화질
            r1, r2 = st.columns([2, 1.2])
            r1.markdown(f"**UHD MP4 (최고화질)**  \n`{v_size_mb} MB` · 무워터마크")
            r2.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_uhd.mp4",
                "video/mp4",
                key="btn_uhd",
                use_container_width=True,
                type="primary",
            )
            st.markdown("<hr style='margin:4px 0;'>", unsafe_allow_html=True)

            # 2) HD / 표준 화질
            r3, r4 = st.columns([2, 1.2])
            r3.markdown(f"**HD MP4 (표준화질)**  \n`{v_size_mb} MB` · 인코딩 최적화")
            r4.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_hd.mp4",
                "video/mp4",
                key="btn_hd",
                use_container_width=True,
            )

        # 우측 열: 오디오 규격
        with c_a_list:
            st.markdown("🎵 **오디오 (Audio)**")

            r5, r6 = st.columns([2, 1.2])
            r5.markdown("**고음질 MP3 음원**  \nBGM·목소리 분리 추출")
            if r6.button(
                "🎵 음원 분리", key="btn_ext_mp3", use_container_width=True
            ):
                with st.spinner("MP3 변환 중..."):
                    st.session_state["mp3_bytes"] = extract_mp3_from_video(
                        v_main
                    )

            if st.session_state.get("mp3_bytes"):
                mp3_b = st.session_state["mp3_bytes"]
                st.download_button(
                    f"⬇️ MP3 다운로드 ({round(len(mp3_b)/(1024*1024), 1)} MB)",
                    mp3_b,
                    "audio.mp3",
                    "audio/mp3",
                    key="dl_mp3_real",
                    use_container_width=True,
                )

    # 사진/노트 포스트
    if images and not videos:
        st.markdown(f"**고화질 사진 ({len(images)}장)**")
        cols = st.columns(3)
        for idx, img_b in enumerate(images):
            with cols[idx % 3]:
                st.image(img_b, use_container_width=True)
                st.download_button(
                    f"⬇️ 사진 #{idx + 1} 받기",
                    img_b,
                    f"image_{idx + 1}.jpg",
                    "image/jpeg",
                    key=f"img_dl_{idx}",
                    use_container_width=True,
                )

    # 전체 일괄 ZIP
    st.markdown("---")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "content.txt", f"제목: {title}\n\n본문:\n{description}".encode(
                "utf-8"
            )
        )
        if remix_v:
            zf.writestr("remix_complete.mp4", remix_v)
        for idx, v in enumerate(videos):
            zf.writestr(f"video_raw_{idx + 1}.mp4", v)
        for idx, i in enumerate(images):
            zf.writestr(f"image_{idx + 1}.jpg", i)

    st.download_button(
        "📦 모든 미디어+세탁영상+대본 일괄 압축팩 받기 (ZIP)",
        zip_buffer.getvalue(),
        "snap_studio_package.zip",
        "application/zip",
        use_container_width=True,
    )
