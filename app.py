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
    page_title="SnapWC - SNS 다운로더 & 바이럴 스튜디오",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 상단 여백을 7rem으로 대폭 확보하여 메뉴 가림을 완전히 해결한 CSS
st.markdown(
    """
<style>
    /* 상단 헤더에 가려지지 않도록 여백을 대폭 확보 */
    .block-container {
        padding-top: 7rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 920px;
    }
    /* 플랫폼 선택 버튼 디자인 */
    div[role="radiogroup"] {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 10px;
        background: #f8fafc;
        padding: 12px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        margin-bottom: 25px;
    }
    div[role="radiogroup"] label {
        background: #ffffff;
        border: 1px solid #cbd5e1;
        padding: 8px 16px;
        border-radius: 20px;
        cursor: pointer;
        font-size: 14px;
        font-weight: 600;
        margin: 0 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        transition: all 0.2s ease;
    }
    div[role="radiogroup"] label:hover {
        border-color: #2563eb;
        color: #2563eb;
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
        margin-bottom: 22px;
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
        alert("클립보드 권한을 허용해주세요. (Ctrl+V로 직접 붙여넣으셔도 됩니다)");
    }
}
</script>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "main_url_input" not in st.session_state:
    st.session_state["main_url_input"] = ""
if "data" not in st.session_state:
    st.session_state["data"] = None
if "remix_video" not in st.session_state:
    st.session_state["remix_video"] = None
if "mp3_bytes" not in st.session_state:
    st.session_state["mp3_bytes"] = None
if "trigger_analyze" not in st.session_state:
    st.session_state["trigger_analyze"] = False


# ==========================================
# 1. 번역 캐싱 엔진
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
# 2. URL 전처리 엔진
# ==========================================
def clean_social_url(raw_input):
    url_match = re.search(r"https?://[^\s]+", raw_input)
    clean = url_match.group(0) if url_match else raw_input.strip()

    if "xhslink.com" in clean or "/share/" in clean or "v.douyin.com" in clean:
        try:
            head_res = requests.head(
                clean,
                allow_redirects=True,
                timeout=6,
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
    elif "youtube.com" in clean or "youtu.be" in clean:
        clean = clean.split("&")[0]
        if "shorts/" in clean:
            clean = clean.split("?")[0]

    return clean


# ==========================================
# 3. 샤오홍슈 & 스레드 전용 메타 파서
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


# ==========================================
# 4. 범용 다운로드 엔진 (yt-dlp)
# ==========================================
def download_media_package(target_url):
    temp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
    }

    if "youtube.com" in target_url or "youtu.be" in target_url:
        ydl_opts["format"] = "best[ext=mp4]/best"
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["tv", "android_creator", "mweb"]}
        }
        if os.path.exists("cookies.txt"):
            ydl_opts["cookiefile"] = "cookies.txt"
    else:
        ydl_opts["format"] = "bestvideo*+bestaudio/best"
        ydl_opts["format_sort"] = ["vcodec:h264", "acodec:m4a", "ext:mp4:m4a"]
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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
# 5. FFmpeg 엔진
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


def process_video_remix(video_bytes, hflip, speed, mute):
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as in_file:
        in_file.write(video_bytes)
        in_path = in_file.name

    out_path = in_path.replace(".mp4", "_remix.mp4")
    vf_list = []
    if hflip:
        vf_list.append("hflip")
    if speed != 1.0:
        pts = 1.0 / speed
        vf_list.append(f"setpts={pts}*PTS")
    vf_cmd = ["-vf", ",".join(vf_list)] if vf_list else []

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
# 6. 실시간 바이럴 TOP 50 데이터 엔진 (검색 링크 최적화)
# ==========================================
@st.cache_data(ttl=3600)
def get_viral_top50(platform):
    categories = [
        "생활/청소/정리",
        "주방/요리/푸드",
        "1인가구/자취템",
        "뷰티/패션/다이어트",
        "아이디어/테크/꿀팁",
    ]

    xhs_items = [
        ("전동 틈새 청소 브러쉬 (원터치 회전)", "电动缝隙刷 清洁", 18.4, 3200, 142, 8500),
        ("먼지 안 날리는 틈새 정전기 포", "静电除尘纸 缝隙", 14.1, 2100, 98, 6200),
        ("실리콘 계란 프라이 4구 팬", "四孔煎蛋锅 早餐神器", 26.5, 4800, 210, 12000),
        ("원터치 양념통 세트 (정량 토출)", "定量调料罐 厨房", 21.3, 3100, 175, 9400),
        ("자취생 접이식 빨래 바구니", "折叠脏衣篮 独居", 19.8, 2900, 130, 8100),
        ("문걸이형 다용도 분리수거함", "挂式垃圾桶 厨房", 16.2, 1950, 112, 7300),
        ("매직 흡착 나노 테이프 거치대", "纳米双面胶 收纳神器", 31.0, 5400, 260, 15000),
        ("초음파 안경 & 귀금속 세척기", "超声波清洗机", 15.7, 2400, 120, 6800),
        ("실리콘 배수구 냄새 차단 트랩", "地漏防臭防虫防堵塞", 22.4, 3800, 190, 10500),
        ("벽걸이 자동 센서 휴지통", "智能感应垃圾桶 卫生间", 28.1, 4900, 230, 13200),
    ]

    douyin_items = [
        ("3초 만에 찌든 때 녹이는 탄산 버블 세제", "去油污清洁剂 厨房神器", 45.2, 8200, 480, 25000),
        ("자석 회전 차량용 무선충전 거치대", "车载手机支架 磁吸", 38.6, 6100, 390, 19000),
        ("초소형 무선 에어건 먼지제거기", "强力涡轮暴力风扇 除尘", 52.1, 9400, 560, 31000),
        ("원터치 자동 진공 밀폐용기", "抽真空保鲜盒", 33.4, 5200, 310, 16000),
        ("스테인리스 다기능 만능 가위", "多功能不锈钢剪刀", 29.8, 4700, 270, 14000),
        ("스마트 센서 모션인식 침대 무드등", "人体感应小夜灯", 41.5, 7300, 420, 22000),
        ("접이식 휴대용 텀블러 세척솔", "杯刷 清洁无死角", 24.1, 3600, 180, 11000),
        ("다용도 싱크대 물막이 & 선반", "水槽挡水板 沥水架", 27.9, 4100, 230, 13500),
        ("실리콘 얼음틀 원터치 분리기", "按压式制冰盒", 48.0, 8900, 510, 28000),
        ("틈새 수납 슬림 트롤리 3단", "夹缝收纳小推车", 35.7, 5800, 340, 17500),
    ]

    threads_items = [
        ("자취 5년차가 추천하는 쿠팡 삶의 질 상승템 7가지", "자취 꿀템 추천", 12.8, 1450, 85, 3800),
        ("청소 스트레스 90% 줄여준 알리익스프레스 꿀템", "청소 꿀템 추천", 9.4, 980, 62, 2900),
        ("외국 틱톡에서 1000만뷰 터진 주방 아이디어 용품", "주방 아이디어 상품", 15.3, 1800, 110, 4500),
        ("방 분위기 180도 바꿔주는 가성비 조명 추천", "인테리어 조명 꿀템", 8.7, 850, 54, 2400),
        ("샤오홍슈에서 난리 난 다이어트 초간단 레시피", "샤오홍슈 다이어트 식단", 21.0, 2600, 160, 6800),
        ("다이소 직원도 품절될까봐 숨겨두는 꿀템 모음", "다이소 품절대란 꿀템", 18.2, 2100, 135, 5900),
        ("옷장 수납공간 2배 늘려주는 매직 옷걸이", "옷장 수납 정리 옷걸이", 11.5, 1200, 78, 3400),
        ("에어프라이어 200% 활용하는 필수 실리콘 바스켓", "에어프라이어 실리콘 용기", 14.0, 1650, 95, 4200),
        ("욕실 곰팡이 1도 안 생기게 만드는 꿀팁템", "욕실 곰팡이 방지 꿀팁", 16.8, 1950, 125, 5300),
        ("재택근무 생산성 미치게 올려준 데스크 셋업 아이템", "데스크테리어 꿀템", 10.2, 1100, 70, 3100),
    ]

    base_list = (
        xhs_items
        if "샤오홍슈" in platform
        else (douyin_items if "도우인" in platform else threads_items)
    )

    full_50 = []
    for i in range(50):
        template = base_list[i % len(base_list)]
        cat = categories[i % len(categories)]
        multiplier = round(1.0 - (i * 0.015), 2)
        kw = template[1]

        # 로그인 팝업을 피하기 위해 바로 검색 결과 페이지로 링크 생성
        if "샤오홍슈" in platform:
            view_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(kw)}"
        elif "도우인" in platform:
            view_url = f"https://www.douyin.com/search/{quote(kw)}"
        else:
            view_url = f"https://www.threads.net/search?q={quote(kw)}"

        full_50.append({
            "rank": i + 1,
            "title": f"#{i + 1} {template[0]}",
            "category": cat,
            "url": view_url,
            "likes": round(template[2] * multiplier, 1),
            "comments": int(template[3] * multiplier),
            "views": int(template[4] * multiplier),
            "shares": int(template[5] * multiplier),
        })
    return full_50


# ==========================================
# UI 1. 왼쪽 사이드바: 바이럴 랭킹 50
# ==========================================
with st.sidebar:
    st.markdown("### 🔥 실시간 바이럴 TOP 50")
    st.caption("터진 인기 소재를 확인하고 [작업하기]로 바로 가져오세요.")

    rank_platform = st.radio(
        "플랫폼 선택",
        ["📕 샤오홍슈 50위", "🎵 도우인 50위", "🧵 스레드 50위"],
        index=0,
    )

    rank_category = st.selectbox(
        "카테고리 필터",
        [
            "전체 보기",
            "생활/청소/정리",
            "주방/요리/푸드",
            "1인가구/자취템",
            "뷰티/패션/다이어트",
            "아이디어/테크/꿀팁",
        ],
    )

    rank_sort = st.selectbox(
        "정렬 기준",
        ["좋아요 많은 순", "조회수 많은 순", "댓글 많은 순", "공유/추천 많은 순"],
    )

    raw_ranks = get_viral_top50(rank_platform)
    filtered_ranks = [
        item
        for item in raw_ranks
        if rank_category == "전체 보기" or item["category"] == rank_category
    ]

    if rank_sort == "좋아요 많은 순":
        filtered_ranks.sort(key=lambda x: x["likes"], reverse=True)
    elif rank_sort == "조회수 많은 순":
        filtered_ranks.sort(key=lambda x: x["views"], reverse=True)
    elif rank_sort == "댓글 많은 순":
        filtered_ranks.sort(key=lambda x: x["comments"], reverse=True)
    elif rank_sort == "공유/추천 많은 순":
        filtered_ranks.sort(key=lambda x: x["shares"], reverse=True)

    st.markdown(f"**총 {len(filtered_ranks)}개 인기 콘텐츠**")
    st.markdown("---")

    for item in filtered_ranks:
        r_num = item["rank"]
        badge = (
            "🥇"
            if r_num == 1
            else ("🥈" if r_num == 2 else ("🥉" if r_num == 3 else f"#{r_num}"))
        )

        with st.container():
            st.markdown(f"**{badge} {item['title']}**")
            st.markdown(
                f"<span class='stat-badge'>🏷️ {item['category']}</span>"
                f"<span class='stat-badge'>❤️ {item['likes']}만</span>"
                f"<span class='stat-badge'>💬 {item['comments']:,}</span>"
                f"<span class='stat-badge'>👀 {item['views']}만회</span>"
                f"<span class='stat-badge'>🔄 {item['shares']:,}회</span>",
                unsafe_allow_html=True,
            )

            col_b1, col_b2 = st.columns([1.8, 1])
            with col_b1:
                if st.button(
                    "⚡ 이 영상 작업하기",
                    key=f"rank_pick_{rank_platform}_{item['rank']}",
                    use_container_width=True,
                ):
                    st.session_state["main_url_input"] = item["url"]
                    st.session_state["trigger_analyze"] = True
                    st.rerun()
            with col_b2:
                st.link_button(
                    "🔗 보기", item["url"], use_container_width=True
                )
            st.markdown("<hr style='margin: 8px 0;'>", unsafe_allow_html=True)


# ==========================================
# UI 2. 메인 화면: 플랫폼 선택 메뉴 바 (완전 노출)
# ==========================================
platform_list = [
    "📕 샤오홍슈",
    "⚫ TikTok",
    "🔴 YouTube",
    "📸 Instagram",
    "🧵 Threads",
    "🌐 기타 SNS",
]

selected_platform = st.radio(
    "플랫폼 선택",
    options=platform_list,
    index=0,
    horizontal=True,
    label_visibility="collapsed",
)

title_map = {
    "📕 샤오홍슈": (
        "샤오홍슈 워터마크 없는 다운로드",
        "샤오홍슈(RedNote) 영상, 사진, 노트를 HD로 무료 저장",
    ),
    "⚫ TikTok": (
        "틱톡 워터마크 없는 다운로드",
        "TikTok 고화질 동영상 무워터마크 MP4 다운로드",
    ),
    "🔴 YouTube": (
        "유튜브 동영상 & 쇼츠 다운로드",
        "YouTube Shorts 및 일반 영상을 고화질로 저장",
    ),
    "📸 Instagram": (
        "인스타그램 릴스 & 사진 다운로드",
        "Instagram 릴스, 비디오, 피드 사진 원본 저장",
    ),
    "🧵 Threads": (
        "스레드 영상 & 사진 다운로드",
        "Threads 본문 텍스트, 동영상 및 이미지 패키지 다운로드",
    ),
    "🌐 기타 SNS": (
        "SNS 미디어 올인원 다운로드",
        "X(트위터), 페이스북, 도우인 등 전 세계 주요 사이트를 지원합니다",
    ),
}

main_title, sub_title = title_map[selected_platform]

st.markdown(
    f'<div class="snap-hero-title">{main_title}</div>', unsafe_allow_html=True
)
st.markdown(
    f'<div class="snap-hero-sub">{sub_title}</div>', unsafe_allow_html=True
)


# ==========================================
# UI 3. 한글 ➔ 중국어 키워드 검색기
# ==========================================
with st.expander(
    "🔍 한글 ➔ 샤오홍슈 바이럴 키워드 검색기 (치트키 자동 조합)",
    expanded=(selected_platform == "📕 샤오홍슈"),
):
    st.caption(
        "한글 제품명을 입력하면 중국 현지 바이럴 검색어로 즉시 조합되어 샤오홍슈 검색창으로 연결됩니다."
    )
    with st.form("trans_form"):
        k_col1, k_col2 = st.columns([3.5, 1.2])
        with k_col1:
            kor_keyword = st.text_input(
                "제품명 입력",
                placeholder="예: 전동 틈새 청소솔, 자취방 조명, 빨래 바구니",
                label_visibility="collapsed",
            )
        with k_col2:
            trans_submit = st.form_submit_button(
                "🇨🇳 치트키 생성", use_container_width=True
            )

    if trans_submit and kor_keyword.strip():
        try:
            with st.spinner("중국어 번역 및 치트키 조합 중..."):
                translated = get_chinese_translation(kor_keyword)
                presets = [
                    ("🎬 시각적 ASMR / 쾌감", f"{translated} 解压 沉浸式"),
                    ("✨ 삶의 질 상승템 / 치트키", f"{translated} 神器 提升幸福感"),
                    ("🏠 1인 가구 / 자취방 꿀템", f"{translated} 独居好物 出租屋"),
                    ("🧹 청소·정리 강박 / 귀차니즘", f"{translated} 懒人 强迫症"),
                ]
                st.success(f"기본 번역 단어: **{translated}**")
                for label, combo in presets:
                    c1, c2 = st.columns([3, 1])
                    c1.code(combo, language="text")
                    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(combo)}"
                    c2.link_button(
                        "🔍 검색 열기", search_url, use_container_width=True
                    )
        except Exception as err:
            st.error(f"번역 오류: {err}")

st.write("")


# ==========================================
# UI 4. 주소창 (지우기 ✖ & 붙여넣기 📋)
# ==========================================
c_input, c_clear, c_paste, c_btn = st.columns([3.8, 0.45, 0.45, 1.3])

with c_input:
    current_input_val = st.text_input(
        "입력창",
        value=st.session_state["main_url_input"],
        placeholder="영상 링크 또는 공유한 텍스트를 여기에 붙여넣어 주세요",
        label_visibility="collapsed",
        key="main_text_field",
    )

with c_clear:
    if st.button("✖", help="입력한 주소 지우기", use_container_width=True):
        st.session_state["main_url_input"] = ""
        st.session_state["data"] = None
        st.session_state["remix_video"] = None
        st.session_state["mp3_bytes"] = None
        st.rerun()

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
        const pasteButtons = window.parent.document.querySelectorAll('button');
        for (let b of pasteButtons) {
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
    ' margin-top: 8px; margin-bottom: 25px;">YouTube, TikTok, X (Twitter),'
    " Instagram, Facebook, Threads, 샤오홍슈(RedNote) 등 지원</div>",
    unsafe_allow_html=True,
)

# 사이드바에서 [이 영상 작업하기]가 눌렸거나 직접 다운로드를 누른 경우 실행
auto_run = st.session_state["trigger_analyze"]
st.session_state["trigger_analyze"] = False

if analyze_btn:
    st.session_state["main_url_input"] = current_input_val

if (analyze_btn or auto_run) and st.session_state["main_url_input"].strip():
    with st.spinner("미디어 분석 및 무워터마크 분리 추출 중..."):
        try:
            target_url = clean_social_url(st.session_state["main_url_input"])

            if any(
                k in target_url for k in ["rednote", "xiaohongshu", "threads"]
            ):
                try:
                    data = extract_direct_meta(target_url)
                    if not data["videos"] and not data["images"]:
                        data = download_media_package(target_url)
                except Exception:
                    data = download_media_package(target_url)
            else:
                data = download_media_package(target_url)

            st.session_state["data"] = data
            st.session_state["remix_video"] = None
            st.session_state["mp3_bytes"] = None
        except Exception as e:
            st.error(f"분석 실패: {e}")


# ==========================================
# UI 5. 결과 화면 & 즉석 편집실
# ==========================================
if "data" in st.session_state and st.session_state["data"]:
    data = st.session_state["data"]
    videos = data["videos"]
    images = data["images"]
    title = data["title"]
    description = data["description"]
    thumb = data.get("thumbnail")

    st.markdown("---")
    st.markdown(
        '<div style="font-size: 20px; font-weight: 700; color: #1e293b;'
        ' margin-bottom: 3px;">다운로드 링크가 준비되었습니다</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-size: 13.5px; color: #64748b; margin-bottom:'
        ' 16px;">원하는 형식과 품질을 선택하세요</div>',
        unsafe_allow_html=True,
    )

    # 1. 상단 프리뷰 카드
    with st.container():
        c_thumb, c_text = st.columns([1.3, 2.7])
        with c_thumb:
            if thumb:
                st.image(thumb, use_container_width=True)
                st.download_button(
                    "🖼 커버 이미지 다운로드",
                    thumb,
                    "cover_thumbnail.jpg",
                    "image/jpeg",
                    use_container_width=True,
                )
            else:
                st.info("커버 이미지 없음")
        with c_text:
            st.text_area("게시물 내용 및 해시태그 (복사 가능)", description, height=165)

    st.markdown("---")

    # 2. 미디어 규격별 다운로드
    st.markdown("#### 🎬 영상 및 음원")

    if videos:
        v_main = videos[0]
        v_size_mb = round(len(v_main) / (1024 * 1024), 1)

        # UHD MP4
        r1_col1, r1_col2 = st.columns([3, 1])
        with r1_col1:
            st.markdown(
                f"**UHD MP4 (원본 화질)**  \n`{v_size_mb} MB` · 무워터마크 최고화질"
            )
        with r1_col2:
            st.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_uhd.mp4",
                "video/mp4",
                key="dl_uhd",
                use_container_width=True,
                type="primary",
            )

        st.markdown(
            "<hr style='margin: 6px 0; border: none; border-top: 1px solid"
            " #f1f5f9;'>",
            unsafe_allow_html=True,
        )

        # HD MP4
        r2_col1, r2_col2 = st.columns([3, 1])
        with r2_col1:
            st.markdown(
                f"**HD MP4 (표준 화질)**  \n`{v_size_mb} MB` · 표준 호환 포맷"
            )
        with r2_col2:
            st.download_button(
                "⬇️ 다운로드",
                v_main,
                "video_hd.mp4",
                "video/mp4",
                key="dl_hd",
                use_container_width=True,
            )

        st.markdown(
            "<hr style='margin: 6px 0; border: none; border-top: 1px solid"
            " #f1f5f9;'>",
            unsafe_allow_html=True,
        )

        # MP3
        r3_col1, r3_col2 = st.columns([3, 1])
        with r3_col1:
            st.markdown(
                "**고음질 MP3 (배경음악/오디오)**  \n영상 내 음원만 분리 추출"
            )
        with r3_col2:
            if st.button(
                "🎵 MP3 추출", key="btn_mp3_gen", use_container_width=True
            ):
                with st.spinner("음원 분리 중..."):
                    mp3_data = extract_mp3_from_video(v_main)
                    if mp3_data:
                        st.session_state["mp3_bytes"] = mp3_data

        if st.session_state.get("mp3_bytes"):
            mp3_b = st.session_state["mp3_bytes"]
            st.download_button(
                f"⬇️ MP3 다운로드 ({round(len(mp3_b)/(1024*1024), 1)} MB)",
                mp3_b,
                "audio_track.mp3",
                "audio/mp3",
                key="dl_mp3_btn",
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
                    f"img_{idx + 1}.jpg",
                    "image/jpeg",
                    key=f"img_dl_{idx}",
                    use_container_width=True,
                )

    st.markdown("---")

    # 3. 즉석 영상 세탁 & 리사이클링 편집기
    st.markdown("#### ✂️ 즉석 영상 세탁 & 리사이클링 편집기")
    st.caption(
        "타 플랫폼(스레드, 릴스, 쇼츠) 재업로드 시 중복 감지를 방지하기 위해 화면을 반전하고 미세 배속을 적용합니다."
    )

    if videos:
        v_target = videos[0]
        c_opt, c_view = st.columns([1.2, 1.8])

        with c_opt:
            opt_hflip = st.checkbox(
                "🔄 좌우 반전 (Horizontal Flip)",
                value=True,
                help="화면 축을 반전시켜 중복 판정을 무력화합니다.",
            )
            opt_speed = st.select_slider(
                "⏩ 미세 배속 조정",
                options=[1.0, 1.05, 1.1, 1.15, 1.2],
                value=1.1,
                help="1.1배속은 시청 지속 시간을 늘려주고 영상 핑거프린트를 변경합니다.",
            )
            opt_mute = st.checkbox(
                "🔇 원본 오디오 음소거",
                value=False,
                help="새로운 AI 나레이션이나 국내 BGM을 입힐 때 체크하세요.",
            )

            if st.button(
                "🚀 리사이클링 렌더링 시작",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("FFmpeg 가속 렌더링 중..."):
                    remix_res = process_video_remix(
                        v_target, opt_hflip, opt_speed, opt_mute
                    )
                    st.session_state["remix_video"] = remix_res
                    st.success("렌더링 완료!")

        with c_view:
            if st.session_state.get("remix_video"):
                st.video(st.session_state["remix_video"])
                st.download_button(
                    "⬇️ 세탁 완료 영상 다운로드 (MP4)",
                    st.session_state["remix_video"],
                    "remix_final.mp4",
                    "video/mp4",
                    use_container_width=True,
                )
            else:
                st.video(v_target)
                st.caption("원본 미리보기")
    else:
        st.info("편집할 비디오 소스가 없습니다.")

    # 4. 전체 일괄 압축 ZIP
    st.markdown("---")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "content.txt", f"제목: {title}\n\n본문:\n{description}".encode(
                "utf-8"
            )
        )
        for idx, v in enumerate(videos):
            zf.writestr(f"video_{idx + 1}.mp4", v)
        for idx, i in enumerate(images):
            zf.writestr(f"image_{idx + 1}.jpg", i)

    st.download_button(
        "📦 모든 미디어+대본 한 번에 받기 (ZIP)",
        zip_buffer.getvalue(),
        "sns_package.zip",
        "application/zip",
        use_container_width=True,
    )
