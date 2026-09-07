import os
import sys
import time
import random
import re
import joblib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from google import genai

# ==================================================================
# --- 1. 環境変数と設定 ---
# ==================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SESSION_FILE = os.path.join(BASE_DIR, "fukuenadv_note_state.json")
PAST_TITLES_FILE = os.path.join(BASE_DIR, "past_titles_fukuenadv.pkl")
USER_RSS_URL = "https://note.com/fukuenadv/rss"
DIAGNOSIS_URL = "https://note.com/fukuenadv/n/nd87206eaca7b" # 実際の無料診断記事のURLに変更してください

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except Exception as e:
    print(f"Gemini初期化エラー: {e}")
    gemini_client = None

# ==================================================================
# --- 2. 過去投稿タイトルの管理 ---
# ==================================================================
past_titles_list = []
if os.path.exists(PAST_TITLES_FILE):
    try:
        past_titles_list = list(joblib.load(PAST_TITLES_FILE))
    except Exception:
        pass

def save_generated_title(title):
    global past_titles_list
    if title and title not in past_titles_list:
        past_titles_list.append(title)
        if len(past_titles_list) > 200:
            past_titles_list = past_titles_list[-200:]
        try:
            joblib.dump(past_titles_list, PAST_TITLES_FILE)
        except Exception:
            pass

# ==================================================================
# --- 3. 自分のnote記事（RSS）取得 ---
# ==================================================================
def fetch_user_rss_articles():
    articles = []
    try:
        response = requests.get(USER_RSS_URL, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            for item in root.findall(".//item"):
                title = item.find("title").text if item.find("title") is not None else ""
                description = item.find("description").text if item.find("description") is not None else ""
                articles.append({"title": title, "description": description})
    except Exception as e:
        print(f"自分のnote RSS取得エラー: {e}")
    return articles

# ==================================================================
# --- 4. テキストクレンジング（見出し・改行の整形） ---
# ==================================================================
def clean_ai_formatting(text):
    if not text:
        return ""
    
    lines = text.split("\n")
    cleaned_lines = []
    
    for line in lines:
        l = line.strip()
        
        # 見出し記号の正規化
        if l.startswith("#"):
            if l.startswith("###"):
                l = re.sub(r"^#+\s{0,}", "### ", l)
            else:
                l = re.sub(r"^#+\s{0,}", "## ", l)
            
        # 箇条書き記号の変換 (\x2a は禁止記号の文字コード回避)
        if re.match(r"^\x2a\s+", l):
            l = re.sub(r"^\x2a\s+", "・ ", l)
            
        # 残った禁止記号を除去 (chr(42)を使用)
        l = l.replace(chr(42), "")
        
        cleaned_lines.append(l)
        
    return "\n".join(cleaned_lines).strip()

# ==================================================================
# --- 5. Geminiによる売れる記事＆タイトル自動生成 ---
# ==================================================================
def generate_fukuen_note_article():
    if not gemini_client:
        print("Gemini APIキーが設定されていません。")
        return None, None, False

    user_articles = fetch_user_rss_articles()
    past_titles_summary = "\n".join([f"・{a['title']}" for a in user_articles[:15]])
    past_titles_history = "\n".join([f"・{t}" for t in past_titles_list[:30]])

    is_paid_article = random.random() < 0.80
    article_type_str = "有料記事" if is_paid_article else "無料フルオープン記事"

    print(f"【記事種別選定】本日は『{article_type_str}』を執筆・構成します。")

    prompt = f"""
あなたは「復縁アドバイザーT｜現役恋愛カウンセラー＆5人と復縁した人間」（note: https://note.com/fukuenadv）を運営するプロの復縁心理カウンセラー「T」です。
元カノ5人と復縁した経験を持ち、恋愛カウンセラーとして“ヨリを戻す”ためのリアルな方法と男目線の本音を発信しています。

過去の履歴と重複しないように、売れ行きが良いテーマから「売れるタイトル」と「記事本文」を作成してください。

【過去記事参照】
{past_titles_summary if past_titles_summary else "（なし）"}

【重複回避タイトル履歴】
{past_titles_history if past_titles_history else "（なし）"}

【執筆形式：{article_type_str}】

【テーマ選定と「無料復縁診断」の案内ルール：重要】
今回の記事テーマは、以下の(A)か(B)のいずれかをランダムに選択して執筆してください。

(A) 「愛着タイプ（不安型・回避型・混合型・安定型）」をテーマにする場合
記事の冒頭（導入文）で、必ず無料診断への案内を入れてください。
文章の言い回しは、あなたの以下の語り口を参考にしてください。
「復縁したいと思ったとき、まず知っておくべきなのは──『あの人がどんな恋愛タイプだったのか』」
「なぜなら、復縁でうまくいかない多くのケースが『相手に合わないアプローチ』をしてしまっているからです。」

【超重要】：noteのエディタでURLを「ブログカード（アイキャッチ画像付きのリンク）」として表示させるため、URLは絶対に他の文字と混ぜず、完全に単独の行として出力してください。

（良い例）
まずは簡単！相手の行動でチェックする無料の恋愛タイプ診断を受けてみてください。

{DIAGNOSIS_URL}

診断結果が『回避型』だった方には、この記事のアプローチがまさにピッタリです。
記事の中にはタイプ別の無料マガジンも置いているので、ぜひ参考にしてみてくださいね。

（ダメな例：これだとブログカードになりません）
無料診断URL： {DIAGNOSIS_URL}

(B) 愛着タイプ以外の独立したテーマ（LINEブロック解除、冷却期間の過ごし方など）にする場合
無料診断URLの紹介や、特定の恋愛タイプへの誘導は一切書かないでください。そのまま通常の専門的な復縁記事として書き出してください。
「焦らず、感情ではなく『構造』を理解することで、あなたの一手がきっと意味あるものになるはずです。」といった、論理的かつ説得力のあるトーンを取り入れてください。

【文章レイアウト・読みやすさの絶対ルール】
1. スマホ読者を意識し、1〜2文章ごとに必ず空行（改行）を入れてください。文章を凝縮させず、ゆったりと読みやすい空間を作ってください。
2. 大見出しには「## 見出しタイトル」、小見出しには「### 見出しタイトル」のMarkdown記法を必ず使用してください。
3. 文章の強調（太字）を目的とした記号は絶対に使用しないでください。
4. 箇条書きの際は「・」を使用してください。
5. タイトルに「有料記事」「無料記事」などの不要な言葉は入れないでください。
6. 【有料記事の場合】無料公開部分の最後に「この記事を読むメリット」「購入訴求」を提示し、その直後に必ず「--- ここから有料エリア ---」という境界線テキストを単独行で入れてください。
7. 全体文字数は2,500〜3,500文字程度としてください。

【出力フォーマット】
1行目：タイトル: （ここに売れるタイトルのみ記載）
2行目以降：本文
"""

    try:
        res = gemini_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt
        )
        if res and res.text:
            raw_text = res.text.strip()
            cleaned_text = clean_ai_formatting(raw_text)

            lines = cleaned_text.split("\n")
            title = ""
            body_lines = []

            for i, line in enumerate(lines):
                if line.startswith("タイトル:") or line.startswith("タイトル："):
                    title = line.replace("タイトル:", "").replace("タイトル：", "").strip()
                elif i == 0 and not title:
                    title = line.strip()
                else:
                    body_lines.append(line)

            body = "\n".join(body_lines).strip()
            title = clean_ai_formatting(title)

            return title, body, is_paid_article

    except Exception as e:
        print(f"記事生成エラー: {e}")

    return None, None, False

# ==================================================================
# --- 6. Playwrightによるnote下書き自動保存 ---
# ==================================================================
def save_note_draft(playwright, title, body, is_paid):
    print("ブラウザを起動して下書き保存を実行します...")

    browser = playwright.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )

    if os.path.exists(SESSION_FILE):
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
    else:
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto("https://note.com/login", wait_until="domcontentloaded")
        input(">>> 手動ログイン後、Enterキーを押してください <<<")
        context.storage_state(path=SESSION_FILE)

    page = context.new_page()

    try:
        page.goto("https://note.com/notes/new", wait_until="domcontentloaded")
        time.sleep(3)

        # 1. タイトル入力 (chr(42)を使用)
        title_selector = "textarea[placeholder" + chr(42) + "='タイトル'], input[placeholder" + chr(42) + "='タイトル']"
        title_locator = page.locator(title_selector).first
        title_locator.wait_for(state="visible", timeout=15000)
        title_locator.click()
        time.sleep(0.5)
        page.keyboard.insert_text(title)
        time.sleep(1.0)

        # 2. 本文入力（見出し記号のタイピング入力と空行保持）
        body_selector = "div[data-placeholder" + chr(42) + "='本文'], .ProseMirror, div[contenteditable='true']"
        body_locator = page.locator(body_selector).first
        body_locator.wait_for(state="visible", timeout=15000)
        body_locator.click()
        time.sleep(0.5)

        paragraphs = body.split("\n")
        for para in paragraphs:
            para_str = para.strip()
            
            if not para_str:
                page.keyboard.press("Enter")
                time.sleep(0.05)
                continue

            # ## や ### の場合はキー入力を再現してnoteの見出しスタイルを発動させる
            if para_str.startswith("## "):
                heading_text = para_str[3:].strip()
                page.keyboard.type("## ", delay=50)
                time.sleep(0.2)
                page.keyboard.insert_text(heading_text)
            elif para_str.startswith("### "):
                heading_text = para_str[4:].strip()
                page.keyboard.type("### ", delay=50)
                time.sleep(0.2)
                page.keyboard.insert_text(heading_text)
            elif para_str.startswith("http"):
                # URL単独行の場合は、カード化を待つために長めに待機
                page.keyboard.insert_text(para_str)
                page.keyboard.press("Enter")
                time.sleep(2.0) # ここでブログカードが生成されるのを待つ
                continue
            else:
                page.keyboard.insert_text(para_str)

            page.keyboard.press("Enter")
            time.sleep(0.05)

        time.sleep(4.0)

        # 3. 下書き保存ボタンクリック
        save_btn = page.locator("button:has-text('下書き保存'), button:has-text('下書き保存中')").first
        if save_btn.is_visible():
            save_btn.click(force=True)
            time.sleep(3.0)

        print(f"記事『{title}』の文脈に合わせた下書き保存が完了しました！")

        context.storage_state(path=SESSION_FILE)
        context.close()
        browser.close()
        return True

    except Exception as e:
        print(f"note下書き保存エラー: {e}")
        try:
            context.close()
            browser.close()
        except: pass
        return False

# ==================================================================
# --- 7. メイン実行ループ ---
# ==================================================================
def main_loop():
    while True:
        try:
            current_hour = datetime.now().hour
            if 2 <= current_hour < 7:
                time.sleep(3600)
                continue

            title, body, is_paid = generate_fukuen_note_article()
            if title and body:
                with sync_playwright() as p:
                    save_note_draft(p, title, body, is_paid)
                save_generated_title(title)

            wait_seconds = 86400 + random.randint(-1800, 1800)
            time.sleep(wait_seconds)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"エラー: {e}")
            time.sleep(1800)

if __name__ == "__main__":
    main_loop()