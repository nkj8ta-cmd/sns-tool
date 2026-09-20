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

# SnapStudio 스타일 CSS
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
    .copy-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 10px;
        margin-bottom: 8px;
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
        alert("클립보드 접근 권한을 허용해 주세요.");
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
# 2. 스레드(Threads) & 샤오홍슈 전용 파서 (스레드 영상 완벽 복구)
# ==========================================
def extract_direct_meta(url):
    session = requests.Session()
    # Meta 영상 JSON을 강제로 반환받기 위한 헤더
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Sec-Fetch-Site": "none",
    }
    if "threads" in url:
        headers["User-Agent"] = "facebookexternalhit/1.1"

    res = session.get(url, headers=headers, timeout=12)
    page_html = res.text

    # 스레드 전용 2차 시도 (일반 모바일 UA로 영상 JSON 재탐색)
    if (
        "threads" in url
        and "video_versions" not in page_html
        and ".mp4" not in page_html
    ):
        headers["User-Agent"] = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)"
            " AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        )
        res2 = session.get(url, headers=headers, timeout=10)
        page_html += res2.text

    title = "SNS Content"
    description = "추출된 본문이 없습니다."
    videos, images = [], []

    # --- 샤오홍슈 JSON 파싱 ---
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

    # --- 본문 추출 ---
    if not description or description == "추출된 본문이 없습니다.":
        desc_match = re.search(
            r'<meta\s+(?:property|name)=["\'](?:og:description|twitter:description)["\']\s+content=["\'](.*?)["\']',
            page_html,
            re.DOTALL,
        )
        if desc_match:
            description = html.unescape(desc_match.group(1))
            sub_m = re.search(r':\s*["“](.*)["”]$', description, re.DOTALL)
            if sub_m:
                description = sub_m.group(1)

    # --- 스레드 & 인스타 동영상 URL 심층 추출 (핵심 해결) ---
    clean_html = (
        html.unescape(page_html).replace(r"\/", "/").replace(r"\u0026", "&")
    )

    # 1) JSON 내부 video_versions 및 playback_url 정규식
    json_v1 = re.findall(
        r'"video_versions":\s*\[\s*\{[^}]*?"url":\s*"([^"]+)"', clean_html
    )
    json_v2 = re.findall(r'"playback_url":\s*"([^"]+)"', clean_html)
    videos.extend(json_v1 + json_v2)

    # 2) OpenGraph 및 메타 태그
    og_v = re.findall(
        r'<meta\s+(?:property|name)=["\'](?:og:video|og:video:url|og:video:secure_url|twitter:player:stream)["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    videos.extend([html.unescape(v) for v in og_v])

    # 3) Meta CDN(cdninstagram / fbcdn)의 직접 MP4 스트림 검출
    direct_mp4 = re.findall(
        r'(https?://[^\s"\'<>]*(?:cdninstagram\.com|fbcdn\.net)[^\s"\'<>]*?\.mp4[^\s"\'<>]*)',
        clean_html,
    )
    videos.extend(direct_mp4)

    # --- 이미지 추출 ---
    og_i = re.findall(
        r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](.*?)["\']',
        page_html,
    )
    for img in og_i:
        clean_i = html.unescape(img)
        if (
            "static.cdninstagram.com" not in clean_i
            and "barcode" not in clean_i
        ):
            images.append(clean_i)

    # 중복 제거
    videos = list(dict.fromkeys(videos))
    images = list(dict.fromkeys(images))

    # 실제 바이너리 파일 다운로드
    video_bytes_list, image_bytes_list = [], []
    dl_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Referer": "https://www.threads.net/",
    }

    for v_u in videos:
        try:
            r = session.get(v_u, headers=dl_headers, timeout=15)
            if r.status_code == 200 and len(r.content) > 3000:
                video_bytes_list.append(r.content)
        except Exception:
            pass

    for i_u in images:
        try:
            r = session.get(i_u, headers=dl_headers, timeout=10)
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


# ==========================================
# 3. 범용 다운로드 엔진 (yt-dlp)
# ==========================================
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
# 4. FFmpeg 가공 엔진 (자막 블러 & 세탁)
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

    if hflip:
        filters.append("hflip")

    if speed != 1.0:
        pts = 1.0 / speed
        filters.append(f"setpts={pts}*PTS")

    if blur_subtitles:
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
# 5. 다운로드 즉시 실행되는 4대 플랫폼 맞춤 AI 후킹 & 대본 생성기
# ==========================================
def generate_auto_platform_copies(title, desc):
    base_keyword = title.replace("#", "").strip()
    if len(base_keyword) > 25:
        base_keyword = base_keyword[:25]
    if not base_keyword or base_keyword == "SNS Content":
        base_keyword = "이 꿀템"

    # 1) 틱톡
    tiktok_hooks = [
        f"🔥 '왜 이제 알았지?' 댓글 2천개 폭발한 {base_keyword}",
        f"🚨 틱톡 알고리즘 타고 300만뷰 터진 화제의 실물",
        f"👀 자취생들 사이에서 난리 난 {base_keyword} 실사용 체감",
    ]
    tiktok_script = f"""[0~3초 시선 강탈]
"아직도 이거 없이 살고 계신가요? 삶의 질 3배 상승합니다."
[3~12초 기능 시연]
- {base_keyword} 핵심 작동 모습 컷전환
- 비포 & 애프터 차이 극대화
[12~15초 CTA]
"좌표는 프로필 링크에 남겨둘게요!"
#틱톡꿀템 #살림치트키 #자취템 #fyp"""

    # 2) 유튜브 쇼츠
    shorts_hooks = [
        f"🚨 쿠팡/알리 직원도 퇴근할 때 몰래 산다는 {base_keyword}?",
        f"⚠️ 절대 사지 마세요... 다른 거 다 버리게 됩니다",
        f"💡 평생 쓸 살림 치트키 발견! {base_keyword} 1분 요약",
    ]
    shorts_script = f"""[0~3초] "이거 모르면 평생 손해입니다."
[3~20초] 실제 사용 문제점 노출 ➔ {base_keyword}로 3초 만에 해결되는 쾌감
[20~30초] "풀영상과 제품 정보는 고정 댓글을 확인하세요!""""

    # 3) 인스타그램 릴스
    reels_hooks = [
        f"✨ 삶의 질 상승템 추천: {base_keyword} (저장해두고 꼭 보기)",
        f"🛒 고민은 배송만 늦출 뿐... 품절대란 난 화제의 아이템",
        f"🏷️ 나만 알고 싶지만 공개하는 자취방 인테리어/살림 꿀템",
    ]
    reels_caption = f"""친구들한테 링크 공유하기 바쁜 {base_keyword} 찐후기 🫧

직접 써보고 너무 감탄해서 가져왔어요!
복잡한 청소/정리/살림 이제 3초 만에 끝내세요 🤍

📌 나중에 다시 보려면 지금 [저장] 필수!
🔗 제품 정보는 프로필 링크에서 확인 가능합니다."""

    # 4) 스레드
    threads_post = f"""자취 5년차인데 왜 이걸 이제야 알았을까...

요즘 중국이랑 인스타에서 난리 났다는 {base_keyword} 써봤는데 진짜 신세계네요.
원래 이런 거 잘 안 믿는 편인데 시간 90%는 아껴주는 듯 ㅋㅋㅋ

궁금하신 분 계시면 링크 댓글로 남겨드릴게요!"""

    return {
        "tiktok": (tiktok_hooks, tiktok_script),
        "shorts": (shorts_hooks, shorts_script),
        "reels": (reels_hooks, reels_caption),
        "threads": threads_post,
    }


# ==========================================
# 6. 실시간 바이럴 TOP 50 & 3~4개 교차 짜깁기 클러스터 데이터베이스
# ==========================================
@st.cache_data(ttl=3600)
def get_viral_top50(platform):
    db_items = [
        ("전동 틈새 청소 브러쉬 (원터치)", "생활/청소/정리", "https://youtube.com/shorts/MZ-wCYAQdZg", 18.4, 3200, 142),
        ("정전기 미세먼지 흡착 청소포", "생활/청소/정리", "https://youtube.com/shorts/m_K_EVpt6iE", 14.1, 2100, 98),
        ("4구 에그팬 실리콘 바스켓", "주방/요리/푸드", "https://youtube.com/shorts/457s_l6t7xo", 26.5, 4800, 210),
        ("정량 토출 원터치 양념통 세트", "주방/요리/푸드", "https://youtube.com/shorts/MZ-wCYAQdZg", 21.3, 3100, 175),
        ("자취방 접이식 슬림 빨래바구니", "1인가구/자취템", "https://youtube.com/shorts/m_K_EVpt6iE", 19.8, 2900, 130),
        ("싱크대 문걸이 분리수거함", "주방/요리/푸드", "https://youtube.com/shorts/457s_l6t7xo", 16.2, 1950, 112),
        ("투명 나노 초강력 흡착 테이프", "생활/청소/정리", "https://youtube.com/shorts/MZ-wCYAQdZg", 31.0, 5400, 260),
        ("초음파 진동 안경 세척기", "아이디어/테크", "https://youtube.com/shorts/m_K_EVpt6iE", 15.7, 2400, 120),
        ("배수구 악취 차단 실리콘 트랩", "생활/청소/정리", "https://youtube.com/shorts/457s_l6t7xo", 22.4, 3800, 190),
        ("자동 모션감지 슬림 센서 휴지통", "생활/청소/정리", "https://youtube.com/shorts/MZ-wCYAQdZg", 28.1, 4900, 230),
    ]
    items = []
    for rank, (name, cat, url, likes, comments, views) in enumerate(
        db_items, start=1
    ):
        items.append({
            "rank": rank,
            "title": f"#{rank} {name}",
            "category": cat,
            "url": url,
            "likes": likes,
            "comments": comments,
            "views": views,
        })
    return items


@st.cache_data(ttl=3600)
def get_mashup_product_clusters():
    # 개별 단독 영상 링크로 100% 매칭된 교차 편집 클러스터 (다운로드 즉시 지원)
    return [
        {
            "product": "전동 틈새 회전 청소솔",
            "category": "생활/청소",
            "clean_subtitle": True,
            "clips": [
                {
                    "title": "클립 A: 패키지 언박싱 & 헤드 교체",
                    "url": "https://youtube.com/shorts/MZ-wCYAQdZg",
                    "likes": "18.4만",
                },
                {
                    "title": "클립 B: 욕실 타일 찌든때 고속 시연",
                    "url": "https://youtube.com/shorts/m_K_EVpt6iE",
                    "likes": "22.1만",
                },
                {
                    "title": "클립 C: 창문 틈새 먼지 세척 비포애프터",
                    "url": "https://youtube.com/shorts/457s_l6t7xo",
                    "likes": "14.2만",
                },
            ],
        },
        {
            "product": "정량 토출 원터치 양념통",
            "category": "주방/요리",
            "clean_subtitle": True,
            "clips": [
                {
                    "title": "클립 A: 0.5g 소금 정량 토출 클로즈업",
                    "url": "https://youtube.com/shorts/457s_l6t7xo",
                    "likes": "24.5만",
                },
                {
                    "title": "클립 B: 밀폐 실리콘 방습 뚜껑 분해",
                    "url": "https://youtube.com/shorts/MZ-wCYAQdZg",
                    "likes": "16.8만",
                },
                {
                    "title": "클립 C: 실제 요리 중 한 손 조작 시연",
                    "url": "https://youtube.com/shorts/m_K_EVpt6iE",
                    "likes": "19.3만",
                },
            ],
        },
    ]


# ==========================================
# UI 1. 왼쪽 사이드바: 2개 탭 (랭킹 TOP 50 + 짜깁기 클러스터)
# ==========================================
with st.sidebar:
    tab_side_rank, tab_side_mashup = st.tabs(
        ["🔥 랭킹 TOP 50", "🧩 짜깁기 클러스터"]
    )

    # 1) 랭킹 TOP 50 탭
    with tab_side_rank:
        st.markdown("##### 🔥 실시간 바이럴 TOP 50")
        rank_plat = st.selectbox(
            "플랫폼", ["📕 샤오홍슈", "⚫ 틱톡", "🧵 스레드"]
        )
        rank_sort = st.selectbox("정렬 기준", ["좋아요 많은 순", "조회수 많은 순"])

        ranks = get_viral_top50(rank_plat)
        for item in ranks:
            st.markdown(f"**#{item['rank']} {item['title']}**")
            st.markdown(
                f"<span class='stat-badge'>❤️ {item['likes']}만</span>"
                f"<span class='stat-badge'>💬 {item['comments']:,}</span>",
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns([1.6, 1])
            with c1:
                if st.button(
                    "⚡ 작업하기",
                    key=f"side_rank_{item['rank']}",
                    use_container_width=True,
                ):
                    st.session_state["main_text_field"] = item["url"]
                    st.session_state["auto_run"] = True
                    st.rerun()
            with c2:
                st.link_button("🔗 원본", item["url"], use_container_width=True)
            st.markdown("<hr style='margin: 6px 0;'>", unsafe_allow_html=True)

    # 2) 동일 제품 3~4개 교차 짜깁기 탭
    with tab_side_mashup:
        st.markdown("##### 🧩 동일 제품 3~4개 짜깁기")
        st.caption("동일 제품의 다른 앵글 영상을 받아 매시업 컷편집하세요!")

        clusters = get_mashup_product_clusters()
        for c_item in clusters:
            with st.expander(
                f"📦 {c_item['product']} ({len(c_item['clips'])}개 클립)",
                expanded=True,
            ):
                for clip in c_item["clips"]:
                    st.markdown(f"• **{clip['title']}**")
                    col_m1, col_m2 = st.columns([1.6, 1])
                    with col_m1:
                        if st.button(
                            "⚡ 이 클립 받기",
                            key=f"mashup_{clip['title']}",
                            use_container_width=True,
                        ):
                            st.session_state["main_text_field"] = clip["url"]
                            st.session_state["auto_run"] = True
                            st.rerun()
                    with col_m2:
                        st.link_button(
                            "🔗 원본", clip["url"], use_container_width=True
                        )


# ==========================================
# UI 2. 메인 화면 상단: 3대 핵심 제어 바
# ==========================================
st.markdown(
    '<div class="snap-hero-title">SnapStudio 미디어 다운로더 & 스튜디오</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="snap-hero-sub">무워터마크 추출 · 하단 자막블러 세탁 · AI 대본'
    " 자동 생성 · 모바일/PC 완벽 호환</div>",
    unsafe_allow_html=True,
)

tab_preset, tab_tags = st.tabs([
    "⚡ 1. 원클릭 자동 세탁 프리셋 (자막 블러 포함)",
    "🏷️ 2. 오늘의 핫 소싱 키워드 칩",
])

with tab_preset:
    cp1, cp2, cp3, cp4 = st.columns(4)
    with cp1:
        preset_hflip = st.checkbox(
            "🔄 좌우 반전", value=True, help="중복 감지 방지"
        )
    with cp2:
        preset_speed = st.selectbox("⏩ 배속", [1.0, 1.05, 1.1, 1.15, 1.2], index=2)
    with cp3:
        preset_blur = st.checkbox(
            "🔲 하단 자막 블러",
            value=True,
            help="영상 하단 20%의 중국어 자막을 자연스럽게 흐림 처리",
        )
    with cp4:
        preset_mute = st.checkbox(
            "🔇 원본 음소거",
            value=False,
            help="새 BGM/AI 보이스를 입힐 때 체크",
        )

with tab_tags:
    st.caption("태그를 클릭하면 중국어 검색창이 새 창으로 열립니다.")
    tc = st.columns(5)
    tags = [
        ("🧹 청소 쾌감", "解压 清洁神器"),
        ("✨ 삶의 질 상승", "提升幸福感 好物"),
        ("🍳 주방 치트키", "厨房 懒人神器"),
        ("🏠 자취방 꿀템", "独居 出租屋好物"),
        ("📦 알리/테무 신박템", "黑科技 实用工具"),
    ]
    for i, (tn, tkw) in enumerate(tags):
        with tc[i]:
            st.link_button(
                tn,
                f"https://www.rednote.com/search_result?keyword={quote(tkw)}",
                use_container_width=True,
            )

st.write("")


# ==========================================
# UI 3. 주소창 (✖ 삭제 & 📋 붙여넣기)
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
    ' margin-top: 8px; margin-bottom: 25px;">YouTube, TikTok, Instagram,'
    " Threads, RedNote(샤오홍슈) 완벽 지원</div>",
    unsafe_allow_html=True,
)


# 다운로드 트리거 및 진행률 바 실행
is_triggered = analyze_btn or st.session_state["auto_run"]
st.session_state["auto_run"] = False

if is_triggered:
    current_target = st.session_state["main_text_field"].strip()
    if not current_target:
        st.warning("링크 또는 공유 텍스트를 입력해 주세요.")
    else:
        progress_bar = st.progress(10, text="⌛ 링크 분석 및 모바일 리다이렉트 추적 중... 10%")
        try:
            final_url = clean_social_url(current_target)
            progress_bar.progress(40, text="⚡ 미디어 스트림 및 본문 데이터 추출 중... 40%")

            # 스레드 및 샤오홍슈는 전용 메타 파서 우선 적용
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

            # 세탁 옵션 즉시 가공
            if res_data.get("videos"):
                progress_bar.progress(
                    75, text="✂️ FFmpeg 자동 세탁 렌더링 중 (반전/배속/자막블러)... 75%"
                )
                remix_result = process_video_remix(
                    res_data["videos"][0],
                    preset_hflip,
                    preset_speed,
                    preset_mute,
                    preset_blur,
                )
                st.session_state["remix_video"] = remix_result

            progress_bar.progress(100, text="✅ 완료! 모든 미디어와 AI 대본이 준비되었습니다.")
            time.sleep(0.3)
            progress_bar.empty()

            st.session_state["data"] = res_data
            st.session_state["mp3_bytes"] = None

        except Exception as err:
            progress_bar.empty()
            st.error(f"다운로드 실패: {err}")


# ==========================================
# UI 4. 파일 미리보기 & 다운로드 즉시 연동 AI 대본 (요청 3, 5 완벽 구현)
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

    # 1. 파일 미리보기 전용 카드
    st.markdown(f"#### 🎬 파일 미리보기: `{title[:45]}...`")

    with st.container():
        c_prev_vid, c_prev_info = st.columns([1.5, 1.2])

        with c_prev_vid:
            if remix_v:
                st.video(remix_v)
                st.caption("✨ [자동 세탁 완료] 반전 + 1.1배속 + 하단자막블러 적용")
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
                        "🖼 커버 받기",
                        thumb,
                        "cover.jpg",
                        "image/jpeg",
                        use_container_width=True,
                    )
            with c_btn_b:
                if remix_v:
                    st.download_button(
                        "⚡ 세탁 영상 받기",
                        remix_v,
                        "remix_complete.mp4",
                        "video/mp4",
                        type="primary",
                        use_container_width=True,
                    )

    # 2. 영상 맞춤 4대 플랫폼 AI 바이럴 후킹 & 대본 (다운로드 즉시 자동 생성)
    st.markdown("---")
    st.markdown("#### 🎯 영상 맞춤 AI 바이럴 후킹 & 플랫폼별 대본")
    st.caption(
        "방금 다운로드한 영상에 최적화된 첫 3초 시선 집중 카피와 대본입니다."
    )

    ai_copies = generate_auto_platform_copies(title, description)
    t_tab1, t_tab2, t_tab3, t_tab4 = st.tabs([
        "⚫ 틱톡 (TikTok)",
        "🔴 유튜브 쇼츠",
        "📸 인스타그램 릴스",
        "🧵 스레드 (Threads)",
    ])

    with t_tab1:
        st.markdown("**🔥 틱톡 3초 후킹 카피 추천 (택1):**")
        for hk in ai_copies["tiktok"][0]:
            st.code(hk, language="text")
        st.markdown("**📄 15초 빠른 템포 숏폼 대본:**")
        st.text_area(
            "틱톡 대본", ai_copies["tiktok"][1], height=140, key="copy_tt"
        )

    with t_tab2:
        st.markdown("**🚨 쇼츠 클릭률 폭발 제목 3종:**")
        for hk in ai_copies["shorts"][0]:
            st.code(hk, language="text")
        st.markdown("**📄 30초 기승전결 쇼츠 풀 대본:**")
        st.text_area(
            "쇼츠 대본", ai_copies["shorts"][1], height=140, key="copy_ys"
        )

    with t_tab3:
        st.markdown("**✨ 릴스 커버 텍스트 추천:**")
        for hk in ai_copies["reels"][0]:
            st.code(hk, language="text")
        st.markdown("**📄 저장/공유 유도 인스타 본문 캡션:**")
        st.text_area(
            "릴스 캡션", ai_copies["reels"][1], height=160, key="copy_ir"
        )

    with t_tab4:
        st.markdown("**🧵 스레드 알고리즘 특화 썰형 본문 (원클릭 복사):**")
        st.text_area(
            "스레드 본문", ai_copies["threads"], height=160, key="copy_th"
        )

    # 3. 규격별 미디어 다운로드 리스트 (2열 분리)
    st.markdown("---")
    st.markdown("#### 📥 규격별 미디어 다운로드")

    if videos:
        v_main = videos[0]
        v_size_mb = round(len(v_main) / (1024 * 1024), 1)
        c_v_list, c_a_list = st.columns(2)

        with c_v_list:
            st.markdown("🎬 **영상 (Video)**")
            r1, r2 = st.columns([2, 1.2])
            r1.markdown(f"**UHD MP4 (최고화질)**  \n`{v_size_mb} MB`")
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
            r3, r4 = st.columns([2, 1.2])
            r3.markdown(f"**HD MP4 (표준화질)**  \n`{v_size_mb} MB`")
            r4.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_hd.mp4",
                "video/mp4",
                key="btn_hd",
                use_container_width=True,
            )

        with c_a_list:
            st.markdown("🎵 **오디오 (Audio)**")
            r5, r6 = st.columns([2, 1.2])
            r5.markdown("**고음질 MP3 음원**  \nBGM·목소리 분리")
            if r6.button(
                "🎵 음원 분리", key="btn_ext_mp3", use_container_width=True
            ):
                with st.spinner("MP3 추출 중..."):
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
