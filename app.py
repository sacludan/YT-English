import os
import re
import json
import glob
import streamlit as st
import streamlit.components.v1 as components
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

st.set_page_config(page_title="雲端 AI 雙語字幕神器", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem !important; padding-bottom: 1rem !important; padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
</style>
""", unsafe_allow_html=True)

# --- 定義 AI 輸出資料結構 ---
class VocabItem(BaseModel):
    word: str = Field(description="原句中較難的單字或關鍵片語（請還原為原型）")
    pos: str = Field(description="詞性，如 n., v., adj., phr.")
    meaning: str = Field(description="符合該句語境的台灣繁體中文精簡解釋")

class SubtitleSegment(BaseModel):
    id: int
    zh_text: str = Field(description="自然流暢、符合台灣用語習慣的繁體中文翻譯")
    vocab_list: list[VocabItem] = Field(description="挑選 1~3 個值得學習的進階單字或片語（CEFR B2以上或實用商務/科技詞彙），簡單句可留空")

class BatchOutput(BaseModel):
    segments: list[SubtitleSegment]

class FullAudioSegment(BaseModel):
    start: float = Field(description="該句開始秒數，例如 12.5")
    end: float = Field(description="該句結束秒數，例如 16.2")
    en_text: str = Field(description="英文原句")
    zh_text: str = Field(description="台灣繁體中文翻譯")
    vocab_list: list[VocabItem] = Field(description="1~3個重點單字")

class FullAudioOutput(BaseModel):
    segments: list[FullAudioSegment]

def extract_video_id(url):
    match = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

# --- 雲端極速抓取字幕與智慧重新斷句 ---
def fetch_cloud_transcript(video_id, max_words=14):
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
        except:
            transcript = next(iter(transcript_list))
        raw_data = transcript.fetch()
    except Exception:
        # 兼容舊版語法
        raw_data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US', 'en-GB'])

    words_with_time = []
    for item in raw_data:
        text = item['text'] if isinstance(item, dict) else item.text
        start = item['start'] if isinstance(item, dict) else item.start
        duration = item['duration'] if isinstance(item, dict) else item.duration
        
        clean_text = text.replace('\n', ' ').strip()
        words = clean_text.split()
        if not words:
            continue
        word_dur = duration / len(words)
        for i, w in enumerate(words):
            words_with_time.append({
                "word": w,
                "start": start + i * word_dur,
                "end": start + (i + 1) * word_dur
            })

    refined = []
    curr = []
    seg_id = 1
    for w in words_with_time:
        curr.append(w)
        w_str = w["word"]
        is_end = w_str.endswith(('.', '?', '!'))
        is_comma = w_str.endswith((',', ';', ':')) and len(curr) >= 7
        if is_end or is_comma or len(curr) >= max_words:
            refined.append({
                "id": seg_id,
                "start": round(curr[0]["start"], 2),
                "end": round(curr[-1]["end"], 2),
                "en_text": " ".join([x["word"] for x in curr])
            })
            seg_id += 1
            curr = []
    if curr:
        refined.append({
            "id": seg_id,
            "start": round(curr[0]["start"], 2),
            "end": round(curr[-1]["end"], 2),
            "en_text": " ".join([x["word"] for x in curr])
        })
    return refined

# --- 翻譯與萃取單字 ---
def translate_Subtitles(segments, api_key):
    client = genai.Client(api_key=api_key)
    batch_size = 30
    final_results = []
    progress_bar = st.progress(0, text="☁️ 雲端 AI 正在進行語境翻譯與重點單字萃取...")
    
    for i in range(0, len(segments), batch_size):
        batch = segments[i:i+batch_size]
        payload = [{"id": s["id"], "en_text": s["en_text"]} for s in batch]
        prompt = f"""
        你是一位專業的台灣英文教學頻道主編（風格類似「巨人英文」）。
        請將以下英文字幕（注意：部分無標點符號的句子請自動根據語意順暢翻譯）翻成自然、道地的台灣繁體中文，
        並從每句中挑出 1~3 個中高級（B2-C2）、少見或極具實戰價值的單字或片語。
        
        字幕資料：
        {json.dumps(payload, ensure_ascii=False)}
        """
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BatchOutput,
                    temperature=0.2,
                )
            )
            parsed = json.loads(resp.text)
            trans_map = {item["id"]: item for item in parsed.get("segments", [])}
        except Exception:
            trans_map = {}

        for s in batch:
            t = trans_map.get(s["id"], {})
            final_results.append({
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "en_text": s["en_text"],
                "zh_text": t.get("zh_text", "（翻譯生成失敗）"),
                "vocab_list": t.get("vocab_list", [])
            })
        progress_bar.progress(min((i + batch_size) / len(segments), 1.0), text=f"已處理 {min(i + batch_size, len(segments))} / {len(segments)} 句...")
        
    progress_bar.empty()
    return final_results

# --- 備用方案：若影片完全無內建CC字幕，直接交由 Gemini 雲端聽寫 ---
def fallback_gemini_audio(url, video_id, api_key):
    client = genai.Client(api_key=api_key)
    for f in glob.glob(f"audio_{video_id}.*"):
        os.remove(f)
    ydl_opts = {'format': 'worstaudio/worst', 'outtmpl': f'audio_{video_id}.%(ext)s', 'quiet': True, 'noplaylist': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    audio_file = glob.glob(f"audio_{video_id}.*")[0]
    
    uploaded = client.files.upload(file=audio_file)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[uploaded, "請將這段英文音訊轉錄為雙語字幕（每句約8~14字），並提供精確的開始與結束秒數、台灣繁體中文翻譯與1~3個重點單字解析。"],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FullAudioOutput,
            temperature=0.2,
        )
    )
    os.remove(audio_file)
    parsed = json.loads(resp.text).get("segments", [])
    for idx, item in enumerate(parsed):
        item["id"] = idx + 1
    return parsed

def render_interactive_player(video_id, data):
    data_json = json.dumps(data, ensure_ascii=False)
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Microsoft JhengHei', sans-serif; background: #0f172a; color: #f8fafc; }}
        .container {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; padding: 10px; height: 84vh; box-sizing: border-box; }}
        .left-col {{ display: flex; flex-direction: column; gap: 12px; height: 100%; }}
        .video-wrapper {{ position: relative; width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 10px; overflow: hidden; flex-shrink: 0; }}
        #player {{ width: 100%; height: 100%; }}
        .live-box {{ background: #1e293b; border-radius: 10px; padding: 14px 16px; text-align: center; border: 2px solid #334155; flex-grow: 1; display: flex; flex-direction: column; justify-content: center; }}
        .live-en {{ font-size: 20px; font-weight: 700; color: #38bdf8; margin-bottom: 8px; line-height: 1.35; }}
        .live-zh {{ font-size: 16px; color: #e2e8f0; margin-bottom: 10px; }}
        .live-vocab-area {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; min-height: 30px; }}
        .vocab-badge {{ background: #fef08a; color: #0f172a; padding: 4px 10px; border-radius: 6px; font-size: 14px; font-weight: bold; }}
        .vocab-badge span {{ color: #b45309; font-weight: normal; margin: 0 3px; }}
        .right-col {{ background: #1e293b; border-radius: 10px; padding: 12px; overflow-y: auto; height: 100%; box-sizing: border-box; border: 1px solid #334155; }}
        .seg-card {{ padding: 12px; border-radius: 8px; margin-bottom: 8px; background: #0f172a; cursor: pointer; border-left: 4px solid transparent; }}
        .seg-card.active {{ background: #1e3a8a; border-left: 4px solid #38bdf8; }}
        .time-tag {{ font-size: 11px; color: #94a3b8; font-family: monospace; margin-bottom: 3px; }}
        .card-en {{ font-size: 15px; font-weight: 600; color: #f1f5f9; margin-bottom: 4px; }}
        .card-zh {{ font-size: 13.5px; color: #cbd5e1; margin-bottom: 6px; }}
        .mini-vocab {{ display: inline-block; background: #334155; color: #fde047; padding: 2px 7px; border-radius: 4px; font-size: 11.5px; margin-right: 5px; margin-top: 4px; }}

        @media (max-width: 768px) {{
            .container {{ display: flex; flex-direction: column; height: 88vh; gap: 10px; padding: 4px; }}
            .left-col {{ height: auto; flex-shrink: 0; gap: 8px; }}
            .live-box {{ padding: 10px 12px; min-height: 95px; flex-grow: 0; }}
            .live-en {{ font-size: 15.5px; margin-bottom: 4px; }}
            .live-zh {{ font-size: 14px; margin-bottom: 6px; }}
            .vocab-badge {{ font-size: 12px; padding: 3px 8px; }}
            .right-col {{ flex-grow: 1; height: auto; overflow-y: auto; padding: 8px; }}
        }}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="left-col">
            <div class="video-wrapper"><div id="player"></div></div>
            <div class="live-box">
                <div class="live-en" id="live-en">▶ 點擊播放影片，字幕與單字將在此同步顯示</div>
                <div class="live-zh" id="live-zh">點擊下方或右側句子，可直接跳轉該秒數！</div>
                <div class="live-vocab-area" id="live-vocab"></div>
            </div>
        </div>
        <div class="right-col" id="transcript-list"></div>
    </div>

    <script>
        const subs = {data_json};
        let player;
        let activeIdx = -1;

        const listEl = document.getElementById('transcript-list');
        subs.forEach((s, idx) => {{
            const card = document.createElement('div');
            card.className = 'seg-card';
            card.id = 'seg-' + idx;
            card.onclick = () => {{ if(player && player.seekTo) {{ player.seekTo(s.start, true); player.playVideo(); }} }};
            
            const mins = Math.floor(s.start / 60).toString().padStart(2, '0');
            const secs = Math.floor(s.start % 60).toString().padStart(2, '0');
            
            let vHtml = '';
            if (s.vocab_list && s.vocab_list.length > 0) {{
                vHtml = '<div>' + s.vocab_list.map(v => `<span class="mini-vocab">💡 ${{v.word}} (${{v.pos}}) ${{v.meaning}}</span>`).join('') + '</div>';
            }}
            card.innerHTML = `<div class="time-tag">[${{mins}}:${{secs}}]</div><div class="card-en">${{s.en_text}}</div><div class="card-zh">${{s.zh_text}}</div>${{vHtml}}`;
            listEl.appendChild(card);
        }});

        var tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        var firstScriptTag = document.getElementsByTagName('script')[0];
        firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

        function onYouTubeIframeAPIReady() {{
            player = new YT.Player('player', {{
                videoId: '{video_id}',
                playerVars: {{ 'playsinline': 1, 'rel': 0 }},
                events: {{ 'onReady': startSync }}
            }});
        }}

        function startSync() {{
            setInterval(() => {{
                if (!player || !player.getCurrentTime) return;
                const t = player.getCurrentTime();
                const idx = subs.findIndex(s => t >= s.start && t <= s.end);
                if (idx !== -1 && idx !== activeIdx) {{
                    activeIdx = idx;
                    const cur = subs[idx];
                    document.getElementById('live-en').innerText = cur.en_text;
                    document.getElementById('live-zh').innerText = cur.zh_text;
                    
                    const vArea = document.getElementById('live-vocab');
                    if (cur.vocab_list && cur.vocab_list.length > 0) {{
                        vArea.innerHTML = cur.vocab_list.map(v => `<div class="vocab-badge">${{v.word}} <span>${{v.pos}}</span> ${{v.meaning}}</div>`).join('');
                    }} else {{
                        vArea.innerHTML = '';
                    }}
                    
                    document.querySelectorAll('.seg-card').forEach(el => el.classList.remove('active'));
                    const activeCard = document.getElementById('seg-' + idx);
                    if (activeCard) {{
                        activeCard.classList.add('active');
                        activeCard.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
                    }}
                }}
            }}, 150);
        }}
    </script>
    </body>
    </html>
    """
    components.html(html_code, height=780)

# --- 讀取雲端 Secrets 或使用者輸入的 API Key ---
default_key = st.secrets.get("GEMINI_API_KEY", "") if "GEMINI_API_KEY" in st.secrets else ""

st.subheader("☁️ 隨身 YouTube 雙語單字神器 (純雲端免開電腦版)")

with st.expander("⚙️ 設定 API Key 或切換歷史影片"):
    api_key = st.text_input("Gemini API Key", value=default_key, type="password", help="若在 Streamlit Cloud 後台設定 Secrets，此處即會自動帶入免重打！")
    cache_files = glob.glob("cache_*.json")
    if cache_files:
        vid_options = [f.replace("cache_", "").replace(".json", "") for f in cache_files]
        selected_vid = st.selectbox("📂 載入已翻譯過的影片：", ["請選擇..."] + vid_options)
        if selected_vid != "請選擇...":
            with open(f"cache_{selected_vid}.json", "r", encoding="utf-8") as f:
                st.session_state["subs_data"] = json.load(f)
                st.session_state["vid"] = selected_vid

col1, col2 = st.columns([3, 1])
with col1:
    url = st.text_input("🔗 貼上 YouTube 網址：", label_visibility="collapsed", placeholder="貼上 YouTube 影片或手機分享網址...")
with col2:
    run_btn = st.button("🚀 生成字幕", type="primary", use_container_width=True)

if run_btn:
    if not api_key:
        st.error("❌ 請先點開上方「⚙️ 設定 API Key」輸入金鑰！")
    elif not url:
        st.error("❌ 請輸入 YouTube 網址！")
    else:
        vid = extract_video_id(url)
        if not vid:
            st.error("❌ 網址格式無法辨識。")
        else:
            cache_file = f"cache_{vid}.json"
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    st.session_state["subs_data"] = json.load(f)
                    st.session_state["vid"] = vid
            else:
                try:
                    with st.spinner("1/2 正在從雲端極速擷取時間軸與斷句..."):
                        raw_segs = fetch_cloud_transcript(vid)
                    final_data = translate_Subtitles(raw_segs, api_key)
                except Exception:
                    with st.spinner("此影片無內建時間軸，正在啟動 Gemini 雲端語音聽寫與翻譯（約需 30 秒）..."):
                        final_data = fallback_gemini_audio(url, vid, api_key)
                
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(final_data, f, ensure_ascii=False, indent=2)
                st.session_state["subs_data"] = final_data
                st.session_state["vid"] = vid

if "subs_data" in st.session_state and "vid" in st.session_state:
    render_interactive_player(st.session_state["vid"], st.session_state["subs_data"])
