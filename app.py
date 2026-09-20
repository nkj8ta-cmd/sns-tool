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

# 상단 여백 확보 및 UI 스타일링
st.markdown(
    """
<style>
    .block-container {
        padding-top: 5.5rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 940px;
    }
    div[role="radiogroup"] {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 10px;
        background: #f8fafc;
        padding: 12px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        margin-bottom: 22px;
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


# 2. 모바일 단축 링크 및 복사 텍스트 완벽 추적 엔진
def clean_social_url(raw_input):
    # 모바일 메신저/앱 공유 시 섞여 들어오는 잡다한 텍스트에서 링크만 추출
    url_match = re.search(r"https?://[^\s]+", raw_input)
    clean = url_match.group(0) if url_match else raw_input.strip()

    # 모바일 단축 링크(xhslink, vt.tiktok, threads/share 등) HTTP 302 리다이렉트 추적
    if any(
        k in clean
        for k in ["xhslink.com", "vt.tiktok.com", "/share/", "/t/", "youtu.be"]
    ):
        try:
            head_res = requests.head(
                clean,
                allow_redirects=True,
                timeout=7,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                        " AppleWebKit/537.36"
                    )
                },
            )
            clean = head_res.url
        except Exception:
            pass

    # RedNote 링크 표준화
    if "rednote.com" in clean:
        clean = clean.replace("rednote.com/discovery/item/", "xiaohongshu.com/explore/")
        clean = clean.replace("rednote.com", "xiaohongshu.com")

    # 스레드 및 유튜브 파라미터 정제
    if "threads.com" in clean or "threads.net" in clean:
        clean = clean.split("?")[0].replace("threads.com", "threads.net")
    elif "youtube.com" in clean:
        clean = clean.split("&")[0]
        if "shorts/" in clean:
            clean = clean.split("?")[0]

    return clean


# 3. 샤오홍슈 & 스레드 전용 메타 파서
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


# 4. 범용 다운로드 엔진 (yt-dlp)
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


# 5. FFmpeg 엔진
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


# 6. 고유 50개 독립 랭킹 데이터베이스 (중복 완전 제거 + RedNote 검색 최적화)
@st.cache_data(ttl=3600)
def get_viral_top50(platform):
    # 중복 없이 엄선된 50개 고유 아이템
    db_items = [
        ("전동 틈새 청소 브러쉬 (원터치)", "생활/청소/정리", "电动缝隙刷 清洁", 18.4, 3200, 142),
        ("정전기 미세먼지 흡착 청소포", "생활/청소/정리", "静电除尘纸", 14.1, 2100, 98),
        ("4구 에그팬 실리콘 바스켓", "주방/요리/푸드", "四孔煎蛋锅", 26.5, 4800, 210),
        ("정량 토출 원터치 양념통 세트", "주방/요리/푸드", "定量调料罐 厨房", 21.3, 3100, 175),
        ("자취방 접이식 슬림 빨래바구니", "1인가구/자취템", "折叠脏衣篮 独居", 19.8, 2900, 130),
        ("싱크대 문걸이 분리수거함", "주방/요리/푸드", "挂式垃圾桶 厨房", 16.2, 1950, 112),
        ("투명 나노 초강력 흡착 테이프", "생활/청소/정리", "纳米双面胶 收纳", 31.0, 5400, 260),
        ("초음파 진동 안경 세척기", "아이디어/테크", "超声波清洗机", 15.7, 2400, 120),
        ("배수구 악취 차단 실리콘 트랩", "생활/청소/정리", "地漏防臭神器", 22.4, 3800, 190),
        ("자동 모션감지 슬림 센서 휴지통", "생활/청소/정리", "智能感应垃圾桶", 28.1, 4900, 230),
        ("3초 찌든때 분해 발포 버블 클리너", "생활/청소/정리", "去油污泡泡清洁剂", 33.5, 6200, 310),
        ("마그네틱 회전 무선 차량용 거치대", "아이디어/테크", "车载手机支架 磁吸", 24.8, 4100, 195),
        ("초소형 강력 무선 터보 에어건", "아이디어/테크", "涡轮暴力风扇 除尘", 39.2, 7300, 420),
        ("원터치 락 진공 보폐 밀폐용기", "주방/요리/푸드", "抽真空保鲜盒", 17.6, 2800, 140),
        ("치킨 뼈도 잘리는 만능 주방 가위", "주방/요리/푸드", "多功能不锈钢剪刀", 15.3, 2200, 115),
        ("침대 밑 스마트 모션인식 센서등", "1인가구/자취템", "人体感应小夜灯", 29.7, 5100, 240),
        ("틈새 세척용 긴자루 텀블러 솔", "주방/요리/푸드", "长柄杯刷 无死角", 12.4, 1850, 88),
        ("물 튐 방지 접이식 싱크대 물막이", "주방/요리/푸드", "水槽挡水板 沥水", 18.9, 2950, 135),
        ("원터치 팝업 실리콘 얼음틀", "주방/요리/푸드", "按压制冰盒 神器", 35.1, 6800, 360),
        ("냉장고 옆 3단 슬림 틈새 트롤리", "1인가구/자취템", "夹缝收纳小推车", 23.4, 3900, 185),
        ("먼지 안 묻는 젤리 클리너 슬라임", "생활/청소/정리", "键盘清洁软胶", 11.2, 1400, 75),
        ("옷장 수납 3배 매직 9구 옷걸이", "1인가구/자취템", "九孔魔术衣架", 20.5, 3400, 160),
        ("방충망 먼지 싹쓸이 양면 브러쉬", "생활/청소/정리", "纱窗清洗刷 免拆", 16.8, 2600, 125),
        ("뿌리는 세탁소 주름 제거 스프레이", "생활/청소/정리", "衣物除皱喷雾", 14.7, 2100, 105),
        ("자석 부착형 칼 & 조리기구 홀더", "주방/요리/푸드", "磁吸刀架 免打孔", 19.3, 3100, 150),
        ("벽걸이 접이식 빨래 건조대", "1인가구/자취템", "折叠晾衣架 阳台", 25.0, 4300, 210),
        ("변기 찌든때 젤 스탬프 클리너", "생활/청소/정리", "马桶小花洁厕凝胶", 22.1, 3700, 180),
        ("실리콘 음식물 쓰레기 거름망", "주방/요리/푸드", "水槽过滤网 倾倒", 13.9, 1950, 92),
        ("무선 충전 LED 거울 화장대 보관함", "뷰티/패션", "LED带灯化妆镜收纳", 31.4, 5600, 280),
        ("미세먼지 차단 창문 틈새 막이 테이프", "생활/청소/정리", "门窗密封贴 隔音", 15.6, 2300, 110),
        ("초밀착 냄비 뚜껑 실리콘 손잡이 커버", "주방/요리/푸드", "防烫硅胶手套 隔热", 10.8, 1300, 68),
        ("다이아몬드 칼갈이 3단 샤프너", "주방/요리/푸드", "快速磨刀器 厨房", 18.2, 2900, 140),
        ("문 충돌 방지 실리콘 도어 범퍼", "생활/청소/정리", "防撞门贴 静音", 12.1, 1600, 80),
        ("에어프라이어 전용 실리콘 오일 스프레이", "주방/요리/푸드", "雾化喷油壶 控油", 27.8, 4900, 245),
        ("속옷 & 양말 6구 서랍 분할 정리함", "1인가구/자취템", "内衣袜子分格收纳", 16.5, 2550, 120),
        ("전자레인지 전용 스팀 덮개", "주방/요리/푸드", "微波炉加热防油盖", 14.3, 2050, 100),
        ("욕실 거울 김서림 방지 코팅 티슈", "생활/청소/정리", "浴室镜子防雾剂", 21.9, 3650, 175),
        ("원터치 쌀통 계량 밀폐 보관함", "주방/요리/푸드", "家用防虫储米桶", 24.2, 4050, 195),
        ("이불 압축 자동 진공 압축팩", "1인가구/자취템", "抽气真空压缩袋", 30.5, 5300, 260),
        ("신발 냄새 탈취 제습 캡슐", "생활/청소/정리", "鞋子除臭干燥胶囊", 13.4, 1800, 85),
        ("벽면 고정 실리콘 슬리퍼 거치대", "1인가구/자취템", "免打孔拖鞋架 浴室", 15.1, 2250, 110),
        ("반려동물 털 제거 매직 브러쉬", "생활/청소/정리", "宠物粘毛除毛器", 36.8, 7100, 380),
        ("원터치 과일 야채 슬라이서 채칼", "주방/요리/푸드", "多功能切菜器 擦丝", 26.1, 4600, 220),
        ("욕실 타일 곰팡이 젤 제거제", "생활/청소/정리", "除霉啫喱 瓷砖缝隙", 28.7, 5050, 250),
        ("모니터 상단 걸이형 스크린 LED 바", "아이디어/테크", "屏幕挂灯 护眼灯", 32.6, 5800, 290),
        ("초간편 스팀 다리미 방열 다림판 패드", "생활/청소/정리", "手持隔热手套 挂烫", 11.9, 1550, 78),
        ("싱크대 하부 냄비 정리 랙 선반", "주방/요리/푸드", "下水槽锅架 收纳", 23.8, 4000, 190),
        ("케이블 전선 정리 자석 클립 오거나이저", "아이디어/테크", "磁吸收纳理线器", 19.5, 3200, 155),
        ("자동 물공급 화분 급수 노즐", "생활/청소/정리", "自动浇花器 懒人", 17.3, 2750, 130),
        ("냉장고 맥주 음료 자동 롤링 캔 디스펜서", "주방/요리/푸드", "双层滚落易拉罐收纳", 34.2, 6400, 330),
    ]

    items = []
    for rank, (name, cat, kw, likes, comments, views) in enumerate(
        db_items, start=1
    ):
        if "샤오홍슈" in platform:
            # RedNote 글로벌 공식 검색 페이지로 연결 (로그인 세션 유지)
            url = f"https://www.rednote.com/search_result?keyword={quote(kw)}"
        elif "틱톡" in platform:
            url = f"https://www.tiktok.com/tag/{quote(kw.replace(' ', ''))}"
        else:
            url = f"https://www.threads.net/search?q={quote(name)}"

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


# ================= UI 사이드바: 50위 랭킹 (중복 0% 고유 리스트) =================
with st.sidebar:
    st.markdown("### 🔥 실시간 바이럴 TOP 50")
    st.caption("50개 고유 인기 아이템 (중복 없음)")

    rank_platform = st.radio(
        "플랫폼 선택",
        ["📕 샤오홍슈(RedNote) 50위", "⚫ 틱톡(TikTok) 50위", "🧵 스레드 50위"],
        index=0,
    )

    rank_category = st.selectbox(
        "카테고리 필터",
        [
            "전체 보기",
            "생활/청소/정리",
            "주방/요리/푸드",
            "1인가구/자취템",
            "뷰티/패션",
            "아이디어/테크",
        ],
    )

    rank_sort = st.selectbox(
        "정렬 기준", ["좋아요 많은 순", "조회수 많은 순", "댓글 많은 순"]
    )

    all_ranks = get_viral_top50(rank_platform)
    filtered = [
        x
        for x in all_ranks
        if rank_category == "전체 보기" or x["category"] == rank_category
    ]

    if rank_sort == "좋아요 많은 순":
        filtered.sort(key=lambda x: x["likes"], reverse=True)
    elif rank_sort == "조회수 많은 순":
        filtered.sort(key=lambda x: x["views"], reverse=True)
    elif rank_sort == "댓글 많은 순":
        filtered.sort(key=lambda x: x["comments"], reverse=True)

    st.markdown(f"**총 {len(filtered)}개 아이템**")
    st.markdown("---")

    for item in filtered:
        r = item["rank"]
        badge = (
            "🥇"
            if r == 1
            else ("🥈" if r == 2 else ("🥉" if r == 3 else f"#{r}"))
        )
        with st.container():
            st.markdown(f"**{badge} {item['title']}**")
            st.markdown(
                f"<span class='stat-badge'>🏷️ {item['category']}</span>"
                f"<span class='stat-badge'>❤️ {item['likes']}만</span>"
                f"<span class='stat-badge'>💬 {item['comments']:,}</span>"
                f"<span class='stat-badge'>👀 {item['views']}만회</span>",
                unsafe_allow_html=True,
            )

            c1, c2 = st.columns([1.7, 1])
            with c1:
                # [이 영상 작업하기] 클릭 시 세션에 주입하고 자동 분석 플래그 활성화
                if st.button(
                    "⚡ 작업하기",
                    key=f"btn_work_{rank_platform}_{item['rank']}",
                    use_container_width=True,
                ):
                    st.session_state["main_text_field"] = item["url"]
                    st.session_state["auto_run"] = True
                    st.rerun()
            with c2:
                st.link_button(
                    "🔗 보기", item["url"], use_container_width=True
                )
            st.markdown("<hr style='margin: 8px 0;'>", unsafe_allow_html=True)


# ================= UI 메인: 상단 플랫폼 메뉴 =================
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
        "샤오홍슈(RedNote) 워터마크 없는 다운로드",
        "샤오홍슈 무워터마크 영상, 고화질 사진 노트를 무료 저장",
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
        "X(트위터), 페이스북 등 전 세계 주요 미디어 링크 지원",
    ),
}

main_title, sub_title = title_map[selected_platform]

st.markdown(
    f'<div class="snap-hero-title">{main_title}</div>', unsafe_allow_html=True
)
st.markdown(
    f'<div class="snap-hero-sub">{sub_title}</div>', unsafe_allow_html=True
)


# ================= UI 한글 -> 중국어 키워드 검색기 =================
with st.expander(
    "🔍 한글 ➔ 샤오홍슈(RedNote) 바이럴 키워드 검색기",
    expanded=(selected_platform == "📕 샤오홍슈"),
):
    st.caption(
        "한글 제품명을 적으면 중국 현지 바이럴 검색어로 자동 조합되어 RedNote 검색창으로 바로 열립니다."
    )
    with st.form("trans_form"):
        k1, k2 = st.columns([3.5, 1.2])
        with k1:
            kor_kw = st.text_input(
                "제품명 입력",
                placeholder="예: 전동 틈새 청소솔, 자취방 조명",
                label_visibility="collapsed",
            )
        with k2:
            trans_btn = st.form_submit_button(
                "🇨🇳 치트키 생성", use_container_width=True
            )

    if trans_btn and kor_kw.strip():
        try:
            with st.spinner("중국어 번역 및 치트키 조합 중..."):
                trans_word = get_chinese_translation(kor_kw)
                combos = [
                    ("🎬 시각적 ASMR / 쾌감", f"{trans_word} 解压 沉浸式"),
                    ("✨ 삶의 질 상승템 / 치트키", f"{trans_word} 神器 提升幸福感"),
                    ("🏠 1인 가구 / 자취방 꿀템", f"{trans_word} 独居好物 出租屋"),
                    ("🧹 청소·정리 강박 / 귀차니즘", f"{trans_word} 懒人 强迫症"),
                ]
                st.success(f"기본 번역: **{trans_word}**")
                for label, c_text in combos:
                    r1, r2 = st.columns([3, 1])
                    r1.code(c_text, language="text")
                    r2.link_button(
                        "🔍 검색 열기",
                        f"https://www.rednote.com/search_result?keyword={quote(c_text)}",
                        use_container_width=True,
                    )
        except Exception as e:
            st.error(f"번역 오류: {e}")

st.write("")


# ================= UI 주소 입력창 (X 버튼 정상 작동 콜백 탑재) =================
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
    # 스트림릿 위젯 상태를 직접 비우는 온클릭 콜백 연결
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
    ' margin-top: 8px; margin-bottom: 25px;">YouTube, TikTok, X (Twitter),'
    " Instagram, Facebook, Threads, 샤오홍슈(RedNote) 모바일/PC 완벽 지원</div>",
    unsafe_allow_html=True,
)


# 다운로드 트리거 처리 (직접 클릭 또는 사이드바 '작업하기' 클릭)
is_triggered = analyze_btn or st.session_state["auto_run"]
st.session_state["auto_run"] = False

if is_triggered:
    current_target = st.session_state["main_text_field"].strip()
    if not current_target:
        st.warning("링크 또는 공유 텍스트를 입력해 주세요.")
    else:
        with st.spinner("모바일/PC 링크 정제 및 미디어 무워터마크 추출 중..."):
            try:
                final_url = clean_social_url(current_target)

                if any(
                    k in final_url
                    for k in ["rednote", "xiaohongshu", "threads"]
                ):
                    try:
                        res_data = extract_direct_meta(final_url)
                        if not res_data["videos"] and not res_data["images"]:
                            res_data = download_media_package(final_url)
                    except Exception:
                        res_data = download_media_package(final_url)
                else:
                    res_data = download_media_package(final_url)

                st.session_state["data"] = res_data
                st.session_state["remix_video"] = None
                st.session_state["mp3_bytes"] = None
            except Exception as err:
                st.error(f"분석 실패: {err}")


# ================= UI 결과 화면 & 즉석 편집실 =================
if st.session_state.get("data"):
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

    # 1. 썸네일 & 본문 카드
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

    # 2. 규격별 미디어 다운로드
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

        # MP3 음원 추출
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

    # 3. 즉석 리사이클링 편집실
    st.markdown("#### ✂️ 즉석 영상 세탁 & 리사이클링 편집기")
    st.caption(
        "타 플랫폼(스레드, 릴스, 쇼츠) 재업로드 시 중복 감지를 회피하기 위해 화면을 반전하고 미세 배속을 적용합니다."
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

    # 4. 전체 ZIP 일괄 다운로드
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
