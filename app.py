import os
import re
import json
import glob
import time
import subprocess
import urllib.request
import urllib.error
import streamlit as st
import streamlit.components.v1 as components
from youtube_transcript_api import YouTubeTranscriptApi

st.set_page_config(page_title="AI 隨身雙語單字神器", page_icon="🚀", layout="centered", initial_sidebar_state="expanded")

def extract_video_id(url):
    match = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

def fetch_fast_transcript(video_id):
    raw_data = None
    errors = []
    cookie_file = "cookies.txt"
    has_cookies = os.path.exists(cookie_file)

    try:
        cmd = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-lang", "en,en-US,en-GB", "--print-json", f"https://www.youtube.com/watch?v={video_id}"]
        if has_cookies:
            cmd.extend(["--cookies", cookie_file])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.strip())
            subs = info.get('subtitles') or info.get('automatic_captions')
            if subs:
                en_subs = None
                for lang in ['en', 'en-US', 'en-GB']:
                    if lang in subs:
                        en_subs = subs[lang]
                        break
                if not en_subs and subs:
                    en_subs = list(subs.values())[0]
                
                sub_url = None
                for sub in en_subs:
                    if sub.get('ext') in ['json3', 'vtt', 'srv1', 'srv2', 'srv3']:
                        sub_url = sub.get('url')
                        break
                if not sub_url and en_subs:
                    sub_url = en_subs[0].get('url')

                if sub_url:
                    req = urllib.request.Request(sub_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as resp:
                        sub_data = resp.read().decode('utf-8')

                    try:
                        json_content = json.loads(sub_data)
                        if 'events' in json_content:
                            raw_blocks = []
                            for ev in json_content['events']:
                                if 'segs' in ev:
                                    text = "".join([s.get('utf8', '') for s in ev['segs']]).strip()
                                    start = float(ev.get('tStartMs', 0)) / 1000.0
                                    duration = float(ev.get('dDurationMs', 1000)) / 1000.0
                                    if text and text != '\n':
                                        raw_blocks.append({"text": text, "start": start, "end": start + duration})
                            if raw_blocks:
                                raw_data = raw_blocks
                    except Exception:
                        pass
    except Exception as e:
        errors.append(str(e))

    if raw_data is None:
        try:
            api = YouTubeTranscriptApi()
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
        raise RuntimeError(f"無法取得字幕。(詳細錯誤: {detail})")

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

def call_groq_translate_only(payload, api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    prompt = f"""你是一位專業的台灣進階英文教學編輯。
請將以下英文字幕翻譯成自然、順暢、道地的台灣繁體中文。絕對不要額外挑選單字。

規則：
1. 每個輸入 id 都要有且只有一筆翻譯，不可漏掉或自行新增 id。
2. 不可合併不同字幕句子。
3. 只輸出合法 JSON，不要輸出 Markdown 或其他說明。
4. JSON 格式如下：
{{"segments":[{{"id":1,"zh_text":"繁體中文翻譯"}}]}}

字幕資料：
{json.dumps(payload, ensure_ascii=False)}"""

    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {"role": "system", "content": "Translate English subtitles into natural Traditional Chinese used in Taiwan. Return only a valid JSON object."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 12000,
        "reasoning_effort": "low",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
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
            result_map[sid] = item.get("zh_text", "").strip()
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
                partial_map = call_groq_translate_only(pending, api_key)
                trans_map.update(partial_map)
                pending = [item for item in pending if int(item["id"]) not in trans_map]
                if not pending:
                    break
                time.sleep(1)
            except Exception:
                if attempt == 2:
                    break
                time.sleep(2)

        for s in batch:
            zh = trans_map.get(int(s["id"]), "（未翻譯）")
            final_results.append({
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "en_text": s["en_text"],
                "zh_text": zh,
            })

        progress_bar.progress(min((i + len(batch)) / total, 1.0), text=f"✅ 已處理 {min(i + len(batch), total)} / {total} 句...")

    status_box.empty()
    progress_bar.empty()
    return final_results

def render_interactive_player(video_id, data, api_key):
    data_json = json.dumps(data, ensure_ascii=False)
    
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Microsoft JhengHei', sans-serif; background: #0f172a; color: #f8fafc; padding: 4px; }
        .main-container { display: flex; flex-direction: column; gap: 12px; max-width: 800px; margin: 0 auto; }
        .video-wrapper { position: relative; width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        #player { width: 100%; height: 100%; }
        .transcript-list { display: flex; flex-direction: column; gap: 8px; max-height: 55vh; overflow-y: auto; padding-right: 4px; }
        .seg-card { padding: 12px 14px; border-radius: 10px; background: #1e293b; cursor: pointer; border-left: 4px solid #334155; transition: background 0.15s ease; }
        .seg-card.active { background: #1e3a8a !important; border-left: 4px solid #38bdf8 !important; box-shadow: 0 0 10px rgba(56,189,248,0.2); }
        .time-tag { font-size: 11px; color: #94a3b8; font-family: monospace; margin-bottom: 4px; }
        
        .card-en { font-size: 16px; font-weight: 600; color: #f1f5f9; margin-bottom: 4px; line-height: 1.4; }
        .clickable-word { cursor: pointer; border-bottom: 1px dotted #38bdf8; transition: color 0.1s; }
        .clickable-word:hover { color: #38bdf8; background: rgba(56,189,248,0.1); border-radius: 3px; }

        .card-zh { font-size: 14px; color: #cbd5e1; margin-bottom: 2px; line-height: 1.35; }

        #modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); z-index: 9999; justify-content: center; align-items: center; }
        .modal-box { background: #1e293b; border: 1px solid #334155; width: 90%; max-width: 400px; border-radius: 14px; padding: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); color: #f8fafc; position: relative; }
        .modal-title { font-size: 20px; font-weight: bold; color: #38bdf8; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; }
        .modal-pos { font-size: 13px; color: #94a3b8; margin-bottom: 10px; }
        .modal-meaning { font-size: 16px; font-weight: 500; margin-bottom: 12px; color: #fde047; }
        .modal-examples { font-size: 13px; color: #cbd5e1; background: #0f172a; padding: 10px; border-radius: 8px; margin-bottom: 16px; line-height: 1.4; }
        .modal-btns { display: flex; gap: 10px; justify-content: flex-end; }
        .btn-action { padding: 8px 14px; border-radius: 8px; border: none; font-size: 13px; font-weight: bold; cursor: pointer; }
        .btn-save { background: #0284c7; color: #fff; }
        .btn-save:hover { background: #0369a1; }
        .btn-close { background: #475569; color: #fff; }
        .btn-close:hover { background: #334155; }
    </style>
    </head>
    <body>
    <div class="main-container">
        <div class="video-wrapper"><div id="player"></div></div>
        <div class="transcript-list" id="transcript-list"></div>
    </div>

    <div id="modal-overlay">
        <div class="modal-box">
            <div class="modal-title"><span id="m-word">Loading...</span> <span id="m-loading" style="font-size: 12px; color: #94a3b8; font-weight: normal;">查詢中...</span></div>
            <div class="modal-pos" id="m-pos"></div>
            <div class="modal-meaning" id="m-meaning"></div>
            <div class="modal-examples" id="m-examples"></div>
            <div class="modal-btns">
                <button class="btn-action btn-close" onclick="closeModal()">關閉</button>
                <button class="btn-action btn-save" id="btn-save-vocab" onclick="saveCurrentWord()">⭐ 加入收藏</button>
            </div>
        </div>
    </div>

    <script>
        const subs = __DATA_JSON__;
        const apiKey = "__API_KEY__";
        let player;
        let activeIdx = -1;
        let manualSeekLockUntil = 0;
        let currentWordData = null;

        function activateSegment(idx, smoothScroll = true) {
            if (idx < 0 || idx >= subs.length) return;
            activeIdx = idx;
            document.querySelectorAll('.seg-card').forEach(el => el.classList.remove('active'));
            const activeCard = document.getElementById('seg-' + idx);
            if (activeCard) {
                activeCard.classList.add('active');
                if (smoothScroll) {
                    activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        }

        const listEl = document.getElementById('transcript-list');
        subs.forEach((s, idx) => {
            const card = document.createElement('div');
            card.className = 'seg-card';
            card.id = 'seg-' + idx;
            card.onclick = (e) => {
                if (e.target.classList.contains('clickable-word')) return;
                activateSegment(idx, false);
                manualSeekLockUntil = Date.now() + 750;
                if (player && player.seekTo) {
                    player.seekTo(s.start + 0.05, true);
                    player.playVideo();
                }
            };

            const mins = Math.floor(s.start / 60).toString().padStart(2, '0');
            const secs = Math.floor(s.start % 60).toString().padStart(2, '0');
            
            const words = s.en_text.split(' ').map(w => {
                const clean = w.replace(/[^a-zA-Z]/g, '');
                if (clean.length > 0) {
                    return `<span class="clickable-word" onclick="lookupWord('${clean}')">${w}</span>`;
                }
                return w;
            }).join(' ');

            card.innerHTML = `<div class="time-tag">[${mins}:${secs}]</div><div class="card-en">${words}</div><div class="card-zh">${s.zh_text}</div>`;
            listEl.appendChild(card);
        });

        async function lookupWord(word) {
            const overlay = document.getElementById('modal-overlay');
            const mWord = document.getElementById('m-word');
            const mLoading = document.getElementById('m-loading');
            const mPos = document.getElementById('m-pos');
            const mMeaning = document.getElementById('m-meaning');
            const mExamples = document.getElementById('m-examples');
            const saveBtn = document.getElementById('btn-save-vocab');

            overlay.style.display = 'flex';
            mWord.innerText = word;
            mLoading.innerText = '🤖 AI 查字中...';
            mPos.innerText = '';
            mMeaning.innerText = '';
            mExamples.innerText = '';
            saveBtn.innerText = '⭐ 加入收藏';
            saveBtn.disabled = false;

            currentWordData = { word: word, pos: '', meaning: '', examples: '' };

            try {
                const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
                    method: "POST",
                    headers: {
                        "Authorization": "Bearer " + apiKey,
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        "model": "openai/gpt-oss-120b",
                        "messages": [
                            {"role": "system", "content": "You are a professional English-Chinese dictionary. Return valid JSON only."},
                            {"role": "user", "content": `請查詢英文單字 "${word}"，並以台灣繁體中文回傳以下 JSON 格式：{"pos":"詞性(例如 n. 或 v.)","meaning":"中文含意","examples":"1. 英文例句\\n中文翻譯"}。不要回傳其他內容。`}
                        ],
                        "response_format": { "type": "json_object" },
                        "max_completion_tokens": 500
                    })
                });
                const data = await res.json();
                const content = JSON.parse(data.choices[0].message.content);
                
                mPos.innerText = content.pos || '';
                mMeaning.innerText = content.meaning || '';
                mExamples.innerText = content.examples || '';
                mLoading.innerText = '';

                currentWordData.pos = content.pos || '';
                currentWordData.meaning = content.meaning || '';
                currentWordData.examples = content.examples || '';
            } catch (err) {
                mLoading.innerText = '查詢失敗，請檢查 API Key';
            }
        }

        function closeModal() {
            document.getElementById('modal-overlay').style.display = 'none';
        }

        function saveCurrentWord() {
            if (!currentWordData || !currentWordData.word) return;
            
            const saveBtn = document.getElementById('btn-save-vocab');
            saveBtn.innerText = '✅ 已成功加入收藏！';
            saveBtn.disabled = true;

            // 直接存入瀏覽器 localStorage，完全不透過後端、不重整網頁！
            let savedList = [];
            try {
                const existing = localStorage.getItem('yt_saved_vocab');
                if (existing) savedList = JSON.parse(existing);
            } catch(e) {}

            // 檢查是否重複
            const exists = savedList.some(item => item.word.toLowerCase() === currentWordData.word.toLowerCase());
            if (!exists) {
                savedList.push(currentWordData);
                localStorage.setItem('yt_saved_vocab', JSON.stringify(savedList));
            }

            setTimeout(() => {
                closeModal();
            }, 700);
        }

        var tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        var firstScriptTag = document.getElementsByTagName('script')[0];
        firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

        function onYouTubeIframeAPIReady() {
            player = new YT.Player('player', {
                videoId: '__VIDEO_ID__',
                playerVars: { 'playsinline': 1, 'rel': 0 },
                events: { 'onReady': startSync }
            });
        }

        function startSync() {
            setInterval(() => {
                if (!player || !player.getCurrentTime) return;
                if (Date.now() - manualSeekLockUntil < 0) return;
                const t = player.getCurrentTime();
                
                let idx = -1;
                for (let i = 0; i < subs.length; i++) {
                    const currentStart = subs[i].start;
                    const nextStart = (i + 1 < subs.length) ? subs[i + 1].start : 999999;
                    if (t >= currentStart && t < nextStart) {
                        idx = i;
                        break;
                    }
                }

                if (idx !== -1 && idx !== activeIdx) {
                    activateSegment(idx, true);
                }
            }, 100);
        }
    </script>
    </body>
    </html>
    """
    
    html_code = html_template.replace("__DATA_JSON__", data_json).replace("__API_KEY__", api_key).replace("__VIDEO_ID__", video_id)
    components.html(html_code, height=680)

KEY_FILE = "saved_api_key.txt"
saved_key = ""
if os.path.exists(KEY_FILE):
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        saved_key = f.read().strip()

with st.sidebar:
    st.markdown("### 🧭 功能選單")
    nav_choice = st.radio("選擇頁面", ["🏠 首頁", "📚 我的收藏", "⚙️ API 設定"], label_visibility="collapsed")

    st.markdown("---")

    if nav_choice == "🏠 首頁":
        st.info("💡 **使用說明**\n1. 請先至 **⚙️ API 設定** 輸入 Groq API Key。\n2. 於右側貼上 YouTube 網址生成雙語字幕。\n3. 點擊字幕中的任何單字即可查義與加入收藏！")

    elif nav_choice == "📚 我的收藏":
        st.markdown("### 📚 收藏生字本")
        # 透過前端 JavaScript 讀取瀏覽器 localStorage 的生字並顯示在側邊欄
        components.html("""
        <div style="font-family: -apple-system, sans-serif; color: inherit; font-size: 14px;">
            <div id="vocab-list">載入中...</div>
        </div>
        <script>
            function renderVocab() {
                const container = document.getElementById('vocab-list');
                let savedList = [];
                try {
                    const existing = localStorage.getItem('yt_saved_vocab');
                    if (existing) savedList = JSON.parse(existing);
                } catch(e) {}

                if (savedList.length === 0) {
                    container.innerHTML = '<p style="color: #94a3b8;">目前還沒有收藏任何單字。在右側影片字幕中點擊單字即可加入！</p>';
                    return;
                }

                let html = '<p style="color: #94a3b8; font-size: 12px; margin-bottom: 10px;">共收藏 ' + savedList.length + ' 個單字</p>';
                savedList.forEach((item, idx) => {
                    html += `
                        <div style="background: #1e293b; color: #f8fafc; padding: 10px; border-radius: 8px; margin-bottom: 8px; border-left: 3px solid #38bdf8;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <strong style="color: #38bdf8; font-size: 15px;">⭐ ${item.word} <span style="font-size: 12px; color: #94a3b8;">(${item.pos})</span></strong>
                                <button onclick="deleteVocab(${idx})" style="background: #ef4444; color: white; border: none; padding: 3px 8px; border-radius: 4px; font-size: 11px; cursor: pointer;">刪除</button>
                            </div>
                            <div style="font-size: 13px; margin-bottom: 4px; color: #fde047;">${item.meaning}</div>
                            <div style="font-size: 11px; color: #cbd5e1; background: #0f172a; padding: 6px; border-radius: 4px; white-space: pre-line;">${item.examples}</div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            }

            window.deleteVocab = function(idx) {
                let savedList = [];
                try {
                    const existing = localStorage.getItem('yt_saved_vocab');
                    if (existing) savedList = JSON.parse(existing);
                } catch(e) {}
                savedList.splice(idx, 1);
                localStorage.setItem('yt_saved_vocab', JSON.stringify(savedList));
                renderVocab();
            }

            renderVocab();
        </script>
        """, height=500)

    elif nav_choice == "⚙️ API 設定":
        st.markdown("### ⚙️ 系統與 API")
        api_key = st.text_input("Groq API Key (gsk_...)", value=saved_key, type="password")
        if api_key and api_key.strip() != saved_key:
            with open(KEY_FILE, "w", encoding="utf-8") as f:
                f.write(api_key.strip())
            st.success("✅ 已儲存 API Key！")
            
        if st.button("🗑️ 清除舊快取"):
            for f in glob.glob("cache_*.json"):
                os.remove(f)
            st.session_state.pop("subs_data", None)
            st.success("✅ 已清除舊快取！")

if not os.path.exists(KEY_FILE) or not saved_key:
    api_key = saved_key
else:
    api_key = saved_key

st.subheader("🎬 隨身 YouTube 雙語單字神器")

col1, col2 = st.columns([3, 1])
with col1:
    url = st.text_input("🔗 貼上 YouTube 網址：", label_visibility="collapsed", placeholder="貼上 YouTube 影片網址...")
with col2:
    run_btn = st.button("🚀 生成字幕", type="primary", use_container_width=True)

if run_btn:
    if not api_key or not api_key.strip().startswith("gsk_"):
        st.error("❌ 請先從左上角側邊欄切換到 **⚙️ API 設定** 輸入正確的 Groq API Key！")
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
                    with st.spinner("1/2 正在智慧重組字幕並由 AI 產出流暢中文..."):
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
    render_interactive_player(st.session_state["vid"], st.session_state["subs_data"], api_key)
