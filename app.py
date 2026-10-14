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

st.set_page_config(page_title="AI 支援 YouTube 雙語單字神器 (Groq 完整版)", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem !important; padding-bottom: 1rem !important; padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
</style>
""", unsafe_allow_html=True)

def extract_video_id(url):
    match = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

# --- 自然語意斷句 ---
def fetch_fast_transcript(video_id):
    """擷取英文字幕，兼容新版與舊版 youtube-transcript-api。"""
    raw_data = None
    errors = []

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
        legacy_get = getattr(YouTubeTranscriptApi, "get_transcript", None)
        if callable(legacy_get):
            try:
                raw_data = legacy_get(
                    video_id, languages=["en", "en-US", "en-GB"]
                )
            except Exception as e:
                errors.append(str(e))

    if raw_data is None:
        detail = "；".join(errors[-2:])
        raise RuntimeError(
            "無法取得這支影片的字幕。請確認影片有字幕、沒有年齡/地區限制，"
            "並確認 youtube-transcript-api 版本與網路連線。"
            + (f" 詳細資訊：{detail}" if detail else "")
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

# --- 透過 Groq API 進行翻譯（已加入 User-Agent 瀏覽器偽裝以避開 403 阻擋） ---
def call_groq_api(payload, api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"

    prompt = f"""你是一位專業的台灣英文教學編輯。
請結合同一批字幕的前後文，將英文翻譯成自然、準確、道地的台灣繁體中文。

規則：
1. 每個輸入 id 都要有且只有一筆翻譯，不可漏掉或自行新增 id。
2. 不可合併不同字幕句子。
3. 每句挑選 0 至 2 個實用的中高級（B2-C2）或值得學習的單字片語。
4. 簡單句可以使用空陣列。
5. word 使用英文單字原型或片語；pos 使用簡短英文詞性；meaning 使用繁體中文。
6. 只輸出合法 JSON，不要輸出 Markdown 或其他說明。
7. JSON 格式如下：
{{"segments":[{{"id":1,"zh_text":"繁體中文翻譯","vocab_list":[{{"word":"example","pos":"n.","meaning":"例子"}}]}}]}}

字幕資料：
{json.dumps(payload, ensure_ascii=False)}"""

    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate English subtitles into natural Traditional Chinese "
                    "used in Taiwan. Return only a valid JSON object."
                ),
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"  # 關鍵偽裝
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Groq API HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Groq 連線失敗或逾時：{e}") from e

    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq 沒有回傳 choices：{response}")

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError("模型沒有回傳文字內容。")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"模型回傳的 JSON 格式錯誤：{content[:500]}") from e

    results = parsed.get("segments")
    if not isinstance(results, list):
        raise RuntimeError("模型回傳的 JSON 缺少 segments 陣列。")

    expected_ids = {int(item["id"]) for item in payload}
    result_map = {}

    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if sid not in expected_ids:
            continue

        zh_text = item.get("zh_text")
        if not isinstance(zh_text, str) or not zh_text.strip():
            continue

        vocab = item.get("vocab_list", [])
        if not isinstance(vocab, list):
            vocab = []

        clean_vocab = []
        for v in vocab:
            if not isinstance(v, dict):
                continue
            word = v.get("word")
            meaning = v.get("meaning")
            if isinstance(word, str) and isinstance(meaning, str):
                clean_vocab.append({
                    "word": word.strip(),
                    "pos": str(v.get("pos", "")).strip(),
                    "meaning": meaning.strip(),
                })
            if len(clean_vocab) >= 2:
                break

        result_map[sid] = {
            "id": sid,
            "zh_text": zh_text.strip(),
            "vocab_list": clean_vocab,
        }

    return result_map


def translate_Subtitles(segments, api_key):
    """分批翻譯；非暫時性 API 錯誤不重試，並保留成功的個別字幕。"""
    if not segments:
        return []

    batch_size = 30
    total = len(segments)
    total_batches = (total + batch_size - 1) // batch_size
    final_results = []
    errors = []

    progress_bar = st.progress(
        0,
        text=f"🚀 啟動 Groq 翻譯引擎（共 {total} 句，分為 {total_batches} 批）...",
    )
    status_box = st.empty()

    for i in range(0, total, batch_size):
        batch_num = i // batch_size + 1
        batch = segments[i:i + batch_size]
        pending = [
            {"id": int(s["id"]), "en_text": s["en_text"]}
            for s in batch
        ]
        trans_map = {}
        last_err = ""

        for attempt in range(3):
            if not pending:
                break

            status_box.caption(
                f"⚡ 正在翻譯第 {batch_num}/{total_batches} 批 "
                f"（{i + 1}～{min(i + len(batch), total)} 句；"
                f"嘗試 {attempt + 1}/3）"
            )

            try:
                partial_map = call_groq_api(pending, api_key)
                trans_map.update(partial_map)
                pending = [
                    item for item in pending
                    if int(item["id"]) not in trans_map
                ]
                if not pending:
                    last_err = ""
                    break
                if attempt < 2:
                    time.sleep(1)

            except Exception as e:
                last_err = str(e)
                match = re.search(r"HTTP\s+(\d{3})", last_err)
                status_code = int(match.group(1)) if match else None
                retryable = (
                    status_code is None
                    or status_code == 429
                    or status_code >= 500
                )
                if not retryable or attempt == 2:
                    break

                wait_seconds = min(2 ** (attempt + 1), 8)
                status_box.caption(f"暫時性錯誤，{wait_seconds} 秒後重試...")
                time.sleep(wait_seconds)

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

        progress_bar.progress(
            min((i + len(batch)) / total, 1.0),
            text=f"✅ 已完成 {min(i + len(batch), total)} / {total} 句...",
        )

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
        .seg-card {{ padding: 12px; border-radius: 8px; margin-bottom: 8px; background: #0f172a; cursor: pointer; border-left: 4px solid transparent; transition: background 0.15s; }}
        .seg-card:hover {{ background: #1e3a8a; }}
        .seg-card.active {{ background: #1e3a8a; border-left: 4px solid #38bdf8; }}
        .time-tag {{ font-size: 11px; color: #94a3b8; font-family: monospace; margin-bottom: 3px; }}
        .card-en {{ font-size: 15px; font-weight: 600; color: #f1f5f9; margin-bottom: 4px; line-height: 1.4; }}
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
        let manualSeekLockUntil = 0;

        function activateSegment(idx, smoothScroll = true) {{
            if (idx < 0 || idx >= subs.length) return;
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
            if (activeCard && smoothScroll) {{
                activeCard.classList.add('active');
                activeCard.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }} else if (activeCard) {{
                activeCard.classList.add('active');
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
                if (Date.now() < manualSeekLockUntil) return;

                const t = player.getCurrentTime();
                let idx = -1;
                for (let i = subs.length - 1; i >= 0; i--) {{
                    if (t >= subs[i].start && t <= subs[i].end + 0.15) {{
                        idx = i;
                        break;
                    }}
                }}
                if (idx !== -1 && idx !== activeIdx) {{
                    activateSegment(idx, true);
                }}
            }}, 100);
        }}
    </script>
    </body>
    </html>
    """
    components.html(html_code, height=780)

KEY_FILE = "saved_api_key.txt"
saved_key = ""
if os.path.exists(KEY_FILE):
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        saved_key = f.read().strip()

st.subheader("🎬 隨身 YouTube 雙語單字神器 (Groq 最終完整版)")

with st.expander("⚙️ 設定 Groq API Key 或管理快取", expanded=(not saved_key)):
    api_key = st.text_input("請輸入 Groq API Key (gsk_...)", value=saved_key, type="password")
    if api_key and api_key.strip() != saved_key:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(api_key.strip())
        st.success("✅ 已儲存 Groq API Key！")
        
    if st.button("🗑️ 清除舊的快取紀錄"):
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
        st.error("❌ 請輸入正確的 Groq API Key（必須以 gsk_ 開頭）！")
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
                        untranslated_count = sum(
                            1 for item in cached_data
                            if item.get("zh_text") in [
                                "（未翻譯）", item.get("en_text"), None, ""
                            ]
                        )
                        if untranslated_count < len(cached_data) * 0.2:
                            use_cache = True
                            st.session_state["subs_data"] = cached_data
                            st.session_state["vid"] = vid
                except (OSError, json.JSONDecodeError, TypeError) as e:
                    st.warning(f"舊快取無法讀取，將重新產生：{e}")
            
            if not use_cache:
                try:
                    with st.spinner("1/2 正在擷取英文字幕與智慧語意斷句..."):
                        raw_segs = fetch_fast_transcript(vid)
                    final_data = translate_Subtitles(raw_segs, api_key)
                    if any(item.get("zh_text") != "（未翻譯）" for item in final_data):
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(final_data, f, ensure_ascii=False, indent=2)
                    else:
                        st.warning("這次沒有任何字幕翻譯成功，因此不寫入快取。")
                    st.session_state["subs_data"] = final_data
                    st.session_state["vid"] = vid
                except Exception as e:
                    st.error(f"❌ 發生錯誤：{e}")

if "subs_data" in st.session_state and "vid" in st.session_state:
    render_interactive_player(st.session_state["vid"], st.session_state["subs_data"])
