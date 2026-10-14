import os
import re
import json
import glob
import time
import urllib.request
import urllib.error
import streamlit as st
import streamlit.components.v1 as components
from youtube_transcript_api import YouTubeTranscriptApi

st.set_page_config(page_title="AI 隨身雙語單字神器 (Cookie 破解版)", layout="centered", initial_sidebar_state="collapsed")

def extract_video_id(url):
    match = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

def fetch_fast_transcript(video_id):
    raw_data = None
    errors = []
    
    # 檢查專案根目錄是否有 cookies.txt
    cookie_file = "cookies.txt"
    has_cookies = os.path.exists(cookie_file)

    try:
        api = YouTubeTranscriptApi()
        # 這裡正確帶入 cookies 參數以繞過雲端 IP 封鎖
        if has_cookies:
            transcript_list = api.list(video_id, cookies=cookie_file)
        else:
            transcript_list = api.list(video_id)
            
        try:
            transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        except Exception:
            transcript = next(iter(transcript_list))
        fetched = transcript.fetch()
        raw_data = list(fetched)
    except Exception as e:
        errors.append(str(e))

    if raw_data is None:
        detail = "；".join(errors)
        raise RuntimeError(
            "無法取得字幕。請確認是否已將 cookies.txt 上傳至 GitHub 專案根目錄！"
            + (f" (詳細錯誤: {detail})" if detail else "")
        )

    cleaned_blocks = []
    for item in raw_data:
        text = str(item["text"] if isinstance(item, dict) else item.text).replace("\n", " ").strip()
        start = float(item["start"] if isinstance(item, dict) else item.start)
        duration_value = item.get("duration") if isinstance(item, dict) else getattr(item, "duration", None)
        if duration_value is None:
            end_value = item.get("end") if isinstance(item, dict) else getattr(item, "end", None)
            duration_value = max(0.05, float(end_value) - start) if end_value is not None else 1.0
        duration = float(duration_value)
        if text:
            cleaned_blocks.append({"text": text, "start": start, "end": start + duration})

    for i in range(len(cleaned_blocks) - 1):
        if cleaned_blocks[i]["end"] > cleaned_blocks[i + 1]["start"]:
            cleaned_blocks[i]["end"] = cleaned_blocks[i + 1]["start"]

    words_with_time = []
    for b in cleaned_blocks:
        words = b["text"].split()
        if not words:
            continue
        dur = max(0.05, b["end"] - b["start"])
        word_dur = dur / len(words)
        for i, w in enumerate(words):
            words_with_time.append({
                "word": w,
                "start": b["start"] + i * word_dur,
                "end": b["start"] + (i + 1) * word_dur
            })

    NO_END_WORDS = {
        'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'about', 'into', 'than', 'after', 'before',
        'the', 'a', 'an', 'my', 'your', 'his', 'her', 'its', 'our', 'their', 'this', 'that', 'these', 'those',
        'and', 'or', 'but', 'if', 'because', 'as', 'until', 'while', 'who', 'whom', 'whose', 'which', 'what', 'when', 'where', 'how',
        'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
        'will', 'would', 'shall', 'should', 'can', 'could', 'may', 'might', 'must',
        'very', 'too', 'so', 'not', 'no', 'more', 'most', 'quite', 'pretty', 'much', 'such',
        'early', 'late', 'mid', 'next', 'last', 'every', 'each', 'another', 'other', 'new', 'old', 'good', 'best', 'first', 'second'
    }
    CLAUSE_STARTERS = {
        'because', 'although', 'though', 'while', 'if', 'unless', 'until', 'since',
        'when', 'whenever', 'where', 'wherever', 'which', 'who', 'whom', 'whose', 'whether'
    }
    ABBREVS = {'mr.', 'mrs.', 'ms.', 'dr.', 'st.', 'u.s.', 'inc.', 'co.', 'vs.', 'etc.', 'e.g.', 'i.e.'}

    def is_sentence_end(word_str):
        return word_str.endswith(('.', '?', '!')) and word_str.lower() not in ABBREVS

    def is_clause_end(word_str):
        return word_str.endswith((',', ';', ':', '—', '-'))

    refined = []
    curr = []
    seg_id = 1
    total_w = len(words_with_time)

    for i, w in enumerate(words_with_time):
        curr.append(w)
        w_str = w["word"]
        clean_w = re.sub(r'[^\w\']', '', w_str.lower())
        
        next_w = words_with_time[i + 1] if i + 1 < total_w else None
        next_clean = re.sub(r'[^\w\']', '', next_w["word"].lower()) if next_w else ""

        punct_coming_soon = False
        for look_idx in range(1, 4):
            if i + look_idx < total_w:
                future_w = words_with_time[i + look_idx]["word"]
                if is_sentence_end(future_w) or is_clause_end(future_w):
                    punct_coming_soon = True
                    break

        should_split = False

        if is_sentence_end(w_str):
            should_split = True
        elif is_clause_end(w_str) and len(curr) >= 5:
            should_split = True
        elif not punct_coming_soon and len(curr) >= 9 and next_clean in CLAUSE_STARTERS and clean_w not in NO_END_WORDS:
            should_split = True
        elif not punct_coming_soon and len(curr) >= 12 and next_clean in {'and', 'but', 'so', 'or'} and clean_w not in NO_END_WORDS:
            should_split = True
        elif not punct_coming_soon and len(curr) >= 16 and clean_w not in NO_END_WORDS:
            should_split = True
        elif len(curr) >= 24 and clean_w not in NO_END_WORDS:
            should_split = True
        elif len(curr) >= 28:
            should_split = True

        if should_split:
            refined.append({
                "id": seg_id,
                "start": round(curr[0]["start"], 2),
                "end": round(curr[-1]["end"], 2),
                "en_text": " ".join([x["word"] for x in curr])
            })
            seg_id += 1
            curr = []

    if curr:
        if len(curr) <= 2 and refined:
            refined[-1]["end"] = round(curr[-1]["end"], 2)
            refined[-1]["en_text"] += " " + " ".join([x["word"] for x in curr])
        else:
            refined.append({
                "id": seg_id,
                "start": round(curr[0]["start"], 2),
                "end": round(curr[-1]["end"], 2),
                "en_text": " ".join([x["word"] for x in curr])
            })

    for i in range(len(refined) - 1):
        if refined[i]["end"] >= refined[i + 1]["start"]:
            refined[i]["end"] = round(max(refined[i]["start"] + 0.1, refined[i + 1]["start"] - 0.02), 2)

    return refined

def call_groq_api(payload, api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"

    prompt = f"""你是一位專業的台灣進階英文教學編輯。
請結合前後句上下文語境，將以下英文字幕翻譯成自然、順暢、道地的台灣繁體中文，並從每句中嚴格挑選 0~2 個符合以下條件的高級單字或實用片語：
1. **難度限制：** 必須是 CEFR B2 至 C2 等級（多益 800 分以上、托福或雅思、GRE 等級）的進階字彙、動詞片語或慣用語。絕對不要挑選太簡單的基礎詞彙。
2. **語境精準：** 解釋必須完全符合該影片句子的當下語境。
3. **格式要求：** 簡單句或沒有高階難字的句子請直接回傳空陣列 []。

規則：
1. 每個輸入 id 都要有且只有一筆翻譯，不可漏掉或自行新增 id。
2. 不可合併不同字幕句子。
3. word 使用英文單字原型或片語；pos 使用簡短英文詞性；meaning 使用繁體中文。
4. 只輸出合法 JSON，不要輸出 Markdown 或其他說明。
5. JSON 格式如下：
{{"segments":[{{"id":1,"zh_text":"繁體中文翻譯","vocab_list":[{{"word":"example","pos":"n.","meaning":"例子"}}]}}]}}

字幕資料：
{json.dumps(payload, ensure_ascii=False)}"""

    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {
                "role": "system",
                "content": "Translate English subtitles into natural Traditional Chinese used in Taiwan. Return only a valid JSON object.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 12000,
        "reasoning_effort": "low",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=90) as resp:
        response = json.loads(resp.read().decode("utf-8"))

    content = response["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    results = parsed.get("segments", [])

    expected_ids = {int(item["id"]) for item in payload}
    result_map = {}

    for item in results:
        sid = int(item.get("id"))
        if sid in expected_ids:
            result_map[sid] = {
                "id": sid,
                "zh_text": item.get("zh_text", "").strip(),
                "vocab_list": item.get("vocab_list", [])
            }
    return result_map

def translate_Subtitles(segments, api_key):
    if not segments:
        return []

    batch_size = 30
    total = len(segments)
    total_batches = (total + batch_size - 1) // batch_size
    final_results = []

    progress_bar = st.progress(0, text=f"🚀 啟動 Groq 翻譯引擎（共 {total} 句）...")
    status_box = st.empty()

    for i in range(0, total, batch_size):
        batch_num = i // batch_size + 1
        batch = segments[i:i + batch_size]
        pending = [{"id": int(s["id"]), "en_text": s["en_text"]} for s in batch]
        trans_map = {}

        for attempt in range(3):
            if not pending:
                break
            status_box.caption(f"⚡ 正在翻譯第 {batch_num}/{total_batches} 批...")
            try:
                partial_map = call_groq_api(pending, api_key)
                trans_map.update(partial_map)
                pending = [item for item in pending if int(item["id"]) not in trans_map]
                if not pending:
                    break
                time.sleep(1)
            except Exception as e:
                if attempt == 2:
                    break
                time.sleep(2)

        for s in batch:
            t = trans_map.get(int(s["id"]), {})
            final_results.append({
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "en_text": s["en_text"],
                "zh_text": t.get("zh_text", "（未翻譯）"),
                "vocab_list": t.get("vocab_list", []),
            })

        progress_bar.progress(min((i + len(batch)) / total, 1.0), text=f"✅ 已處理 {min(i + len(batch), total)} / {total} 句...")

    status_box.empty()
    progress_bar.empty()
    return final_results

def render_interactive_player(video_id, data):
    data_json = json.dumps(data, ensure_ascii=False)
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Microsoft JhengHei', sans-serif; background: #0f172a; color: #f8fafc; padding: 4px; }}
        .main-container {{ display: flex; flex-direction: column; gap: 12px; max-width: 800px; margin: 0 auto; }}
        .video-wrapper {{ position: relative; width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
        #player {{ width: 100%; height: 100%; }}
        .transcript-list {{ display: flex; flex-direction: column; gap: 8px; max-height: 60vh; overflow-y: auto; padding-right: 4px; }}
        .seg-card {{ padding: 12px 14px; border-radius: 10px; background: #1e293b; cursor: pointer; border-left: 4px solid #334155; transition: all 0.2s ease; }}
        .seg-card:hover {{ background: #2563eb; border-left-color: #60a5fa; }}
        .seg-card.active {{ background: #1e3a8a; border-left: 4px solid #38bdf8; box-shadow: 0 0 10px rgba(56,189,248,0.2); }}
        .time-tag {{ font-size: 11px; color: #94a3b8; font-family: monospace; margin-bottom: 4px; }}
        .card-en {{ font-size: 16px; font-weight: 600; color: #f1f5f9; margin-bottom: 4px; line-height: 1.4; }}
        .card-zh {{ font-size: 14px; color: #cbd5e1; margin-bottom: 6px; line-height: 1.35; }}
        .vocab-container {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
        .mini-vocab {{ background: #334155; color: #fde047; padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 500; }}
        .mini-vocab span {{ color: #cbd5e1; font-weight: normal; margin-left: 3px; }}
    </style>
    </head>
    <body>
    <div class="main-container">
        <div class="video-wrapper"><div id="player"></div></div>
        <div class="transcript-list" id="transcript-list"></div>
    </div>
    <script>
        const subs = {data_json};
        let player;
        let activeIdx = -1;
        let manualSeekLockUntil = 0;

        function activateSegment(idx, smoothScroll = true) {{
            if (idx < 0 || idx >= subs.length) return;
            if (activeIdx === idx) return;
            activeIdx = idx;
            document.querySelectorAll('.seg-card').forEach(el => el.classList.remove('active'));
            const activeCard = document.getElementById('seg-' + idx);
            if (activeCard) {{
                activeCard.classList.add('active');
                if (smoothScroll) {{
                    activeCard.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
                }}
            }}
        }}

        const listEl = document.getElementById('transcript-list');
        subs.forEach((s, idx) => {{
            const card = document.createElement('div');
            card.className = 'seg-card';
            card.id = 'seg-' + idx;
            card.onclick = () => {{
                activateSegment(idx, false);
                manualSeekLockUntil = Date.now() + 750;
                if (player && player.seekTo) {{
                    player.seekTo(s.start + 0.05, true);
                    player.playVideo();
                }}
            }};
            const mins = Math.floor(s.start / 60).toString().padStart(2, '0');
            const secs = Math.floor(s.start % 60).toString().padStart(2, '0');
            let vHtml = '';
            if (s.vocab_list && s.vocab_list.length > 0) {{
                vHtml = '<div class="vocab-container">' + s.vocab_list.map(v => `<span class="mini-vocab">💡 ${{v.word}}<span>(${{v.pos}}) ${{v.meaning}}</span></span>`).join('') + '</div>';
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
                if (Date.now() < manualSeekLockUntil) return;
                const t = player.getCurrentTime();
                
                let idx = 0;
                for (let i = 0; i < subs.length; i++) {{
                    if (t >= subs[i].start) {{
                        idx = i;
                    }} else {{
                        break;
                    }}
                }}

                if (idx !== -1) {{
                    activateSegment(idx, true);
                }}
            }}, 100);
        }}
    </script>
    </body>
    </html>
    """
    components.html(html_code, height=720)

KEY_FILE = "saved_api_key.txt"
saved_key = ""
if os.path.exists(KEY_FILE):
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        saved_key = f.read().strip()

st.subheader("🎬 隨身 YouTube 雙語單字神器 (Cookie 破解版)")

with st.expander("⚙️ 設定 Groq API Key", expanded=(not saved_key)):
    api_key = st.text_input("請輸入 Groq API Key (gsk_...)", value=saved_key, type="password")
    if api_key and api_key.strip() != saved_key:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(api_key.strip())
        st.success("✅ 已儲存 Groq API Key！")
        
    if st.button("🗑️ 清除舊快取"):
        for f in glob.glob("cache_*.json"):
            os.remove(f)
        st.session_state.pop("subs_data", None)
        st.success("✅ 已清除舊快取！")

col1, col2 = st.columns([3, 1])
with col1:
    url = st.text_input("🔗 貼上 YouTube 網址：", label_visibility="collapsed", placeholder="貼上 YouTube 影片網址...")
with col2:
    run_btn = st.button("🚀 生成字幕", type="primary", use_container_width=True)

if run_btn:
    if not api_key or not api_key.strip().startswith("gsk_"):
        st.error("❌ 請輸入正確的 Groq API Key！")
    elif not url:
        st.error("❌ 請輸入 YouTube 網址！")
    else:
        vid = extract_video_id(url)
        if not vid:
            st.error("❌ 網址格式無法辨識。")
        else:
            cache_file = f"cache_{vid}.json"
            use_cache = False
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    if isinstance(cached_data, list) and cached_data:
                        use_cache = True
                        st.session_state["subs_data"] = cached_data
                        st.session_state["vid"] = vid
                except Exception:
                    pass
            
            if not use_cache:
                try:
                    with st.spinner("1/2 正在透過 Cookie 驗證擷取英文字幕與 AI 單字..."):
                        raw_segs = fetch_fast_transcript(vid)
                    final_data = translate_Subtitles(raw_segs, api_key)
                    if any(item.get("zh_text") != "（未翻譯）" for item in final_data):
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(final_data, f, ensure_ascii=False, indent=2)
                    st.session_state["subs_data"] = final_data
                    st.session_state["vid"] = vid
                except Exception as e:
                    st.error(f"❌ 發生錯誤：{e}")

if "subs_data" in st.session_state and "vid" in st.session_state:
    render_interactive_player(st.session_state["vid"], st.session_state["subs_data"])
