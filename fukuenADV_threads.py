import os
import sys
import time
import random
import joblib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from google import genai

# ==================================================================
# --- 1. 環境変数と設定 ---
# ==================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

USER_ID = os.getenv("USER_ID", "")
USER_PASS = os.getenv("USER_PASS", "")
GEMINI_API_KEY2 = os.getenv("GEMINI_API_KEY2", "")

# 復縁アドバイザーT専用のファイルパス
SESSION_FILE = os.path.join(BASE_DIR, "fukuenADV_state.json")
POSTED_NOTES_FILE = os.path.join(BASE_DIR, "posted_notes_fukuenadv.pkl")

# noteのRSSフィードURL
NOTE_RSS_URL = "https://note.com/fukuenadv/rss"

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY2) if GEMINI_API_KEY2 else None
except Exception as e:
    print(f"Gemini初期化エラー: {e}")
    gemini_client = None

# ==================================================================
# --- 2. 過去投稿データの管理 ---
# ==================================================================
posted_notes_list = []
if os.path.exists(POSTED_NOTES_FILE):
    try:
        posted_notes_list = list(joblib.load(POSTED_NOTES_FILE))
    except Exception:
        pass

def save_posted_note(note_url):
    global posted_notes_list
    if note_url and note_url not in posted_notes_list:
        posted_notes_list.append(note_url)
        if len(posted_notes_list) > 100:
            posted_notes_list = posted_notes_list[-100:]
        try:
            joblib.dump(posted_notes_list, POSTED_NOTES_FILE)
        except Exception:
            pass

# ==================================================================
# --- 3. note記事参照（RSS自動取得） ---
# ==================================================================
def fetch_note_articles():
    """noteのRSSから記事一覧（タイトル、URL、説明）を取得する"""
    try:
        response = requests.get(NOTE_RSS_URL, timeout=10)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            articles = []
            for item in root.findall(".//item"):
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                description = item.find("description").text if item.find("description") is not None else ""
                
                # クエリパラメータを除去したクリーンなURL
                clean_link = link.split('?')[0]
                articles.append({
                    "title": title,
                    "link": clean_link,
                    "description": description
                })
            return articles
    except Exception as e:
        print(f"⚠️ note記事の取得に失敗しました: {e}")
    return []

# ==================================================================
# --- 4. 人間味エミュレーション関数 ---
# ==================================================================
def human_hesitation(min_sec=1.5, max_sec=3.5):
    time.sleep(random.uniform(min_sec, max_sec))

def human_click_and_hover(page, selector):
    element = page.locator(selector).first
    element.wait_for(state="visible", timeout=15000)
    box = element.bounding_box()
    if box:
        x = box["x"] + random.uniform(5, box["width"] - 5)
        y = box["y"] + random.uniform(5, box["height"] - 5)
        page.mouse.move(x, y, steps=random.randint(5, 15))
        human_hesitation(0.3, 0.8)
        page.mouse.click(x, y)
    else:
        element.click(force=True)

# ==================================================================
# --- 5. Threads自動操作 ---
# ==================================================================
def post_to_threads_with_session(playwright, post_text, comment_text=None):
    print("🤖 【Threads】ブラウザ起動中...")
    
    browser = playwright.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    
    if os.path.exists(SESSION_FILE):
        print("🔑 保存済みセッション情報を読み込みます（ログイン処理をスキップ）")
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": random.randint(1180, 1280), "height": random.randint(750, 850)}
        )
        page = context.new_page()
    else:
        print("🔐 初回ログインを実行します...")
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1200, "height": 800}
        )
        page = context.new_page()
        page.goto("https://www.threads.net/login", wait_until="domcontentloaded")
        print("=====================================================")
        print("⚠️ 初回手動ログインのお願い（復縁アドバイザーT アカウント） ⚠️")
        print("Threadsにログイン完了後、ホーム画面が表示されたらEnterを押してください。")
        input(">>> このターミナルで【Enter】キーを押してください <<<")
        print("=====================================================")
        context.storage_state(path=SESSION_FILE)
        print("💾 セッション情報を保存しました！")

    try:
        page.goto("https://www.threads.net/", wait_until="domcontentloaded")
        human_hesitation(3.0, 5.0)

        login_btn = page.locator("a:has-text('ログイン')").first
        if login_btn.is_visible() or "login" in page.url:
            print("❌ ログインセッションが無効です。手動ログインをやり直してください。")
            return False

        print("📝 スレッド作成ボタンを探しています...")
        human_hesitation(2.0, 4.0)

        create_btn_selector = None
        for s in ["svg[aria-label='新しいスレッド']", "[aria-label='作成']", "span:has-text('スレッドを開始')"]:
            if page.locator(s).first.is_visible():
                create_btn_selector = s
                break

        if not create_btn_selector:
            page.goto("https://www.threads.net/", wait_until="domcontentloaded")
            human_hesitation(3.0, 5.0)
            create_btn_selector = "[aria-label='作成']"

        human_click_and_hover(page, create_btn_selector)
        human_hesitation(2.0, 3.5)

        editor = page.locator("div[role='textbox']").first
        editor.click(force=True)
        human_hesitation(0.8, 1.5)

        for line in post_text.split('\n'):
            for char in line:
                page.keyboard.type(char)
                time.sleep(random.uniform(0.02, 0.08))
            page.keyboard.press("Shift+Enter")
            time.sleep(random.uniform(0.1, 0.3))

        human_hesitation(2.0, 4.0)
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(7000)
        print("✨ 本文の投稿が完了しました！")

        # --- 1コメ欄へのnote URL投稿処理 ---
        if comment_text:
            print("💬 続けて1コメ欄にURLを投稿します...")
            try:
                # USER_IDから@を除去して安全にURLを生成
                clean_user_id = USER_ID.strip().lstrip("@")
                if not clean_user_id:
                    print("⚠️ USER_IDが設定されていないため、ホーム画面から最新投稿を探します...")
                    page.goto("https://www.threads.net/", wait_until="domcontentloaded")
                else:
                    page.goto(f"https://www.threads.net/@{clean_user_id}", wait_until="domcontentloaded")
                
                human_hesitation(4.0, 6.0)

                # 最新投稿（自分の最新スレッド）をクリックして開く
                first_line = post_text.split('\n')[0][:10]
                post_card = page.locator(f"text='{first_line}'").first
                
                if post_card.is_visible():
                    post_card.click()
                else:
                    # 見つからない場合は一番上の投稿カードをクリック
                    page.locator("div[data-pressable-container='true']").first.click()
                
                human_hesitation(2.5, 4.0)

                # 返信用の入力欄を取得してURLを入力
                reply_editor = page.locator("div[role='textbox']").first
                reply_editor.wait_for(state="visible", timeout=15000)
                reply_editor.click(force=True)
                human_hesitation(1.0, 2.0)
                
                # URLを入力して送信
                page.keyboard.insert_text(comment_text)
                time.sleep(1.5)
                
                page.keyboard.press("Control+Enter")
                page.wait_for_timeout(5000)
                print("✅ 1コメへのnote URL投稿が完了しました！")
            except Exception as e:
                print(f"⚠️ コメント投稿に失敗しました: {e}")

        context.storage_state(path=SESSION_FILE)
        context.close()
        browser.close()
        return True

    except Exception as e:
        print(f"❌ 操作エラー: {e}")
        try:
            context.close()
            browser.close()
        except: pass
        return False

# ==================================================================
# --- 6. Geminiテキスト生成（復縁アドバイザーT） ---
# ==================================================================
def generate_fukuenadv_content():
    if not gemini_client:
        return None, None, None

    mode_rand = random.random()

    # パターンA: バズ・インプレッション重視の復縁フック＆アドバイスつぶやき（60%）
    if mode_rand < 0.60:
        print("🔥 【モード】『バズ・疑問符フック復縁アドバイス』を生成します。")
        prompt = """
あなたは圧倒的実績を持つプロの「復縁アドバイザーT」です。
Threadsでインプレッション（ビュー数）とエンゲージメントを最大化させるための短文アドバイスを作成してください。

【プロンプト戦略・指示】
1. 冒頭1行目で読者の目を引くフック（疑問符「？」やショッキングな問いかけ、あるある、NG行動の指摘）を入れ込んでください。
   例：「元彼に『LINE返して？』って送ってない？」「実は男が元カノを思い出す瞬間、知ってる？」など
2. 読者が「自分のことだ」「共感できる」「答えが知りたい」と思うような心理的アプローチを意識してください。
3. 文字数は2〜3行（100文字以内）で、サクッと読める構成にしてください。
4. 出力は投稿する本文のみにしてください。見出し、指示文、記号による装飾は絶対に含まないでください。
"""
        try:
            res = gemini_client.models.generate_content(model='gemini-3.6-flash', contents=prompt)
            if res and res.text:
                post_text = res.text.strip().replace("*", "")
                return "daily_mutter", post_text, None
        except Exception as e:
            print(f"つぶやき生成エラー: {e}")
            return None, None, None

    # パターンB: note有料記事への強力誘導＆購入訴求投稿（40%）
    print("💎 【モード】『note有料記事購入誘導投稿』を生成します。")
    articles = fetch_note_articles()
    
    if not articles:
        print("⚠️ note記事が取得できなかったため、通常のつぶやきに切り替えます。")
        return generate_fukuenadv_content()

    # 未投稿の記事を優先的に選択
    unposted_articles = [a for a in articles if a["link"] not in posted_notes_list]
    target_article = random.choice(unposted_articles) if unposted_articles else random.choice(articles)

    article_title = target_article["title"]
    article_url = target_article["link"]

    prompt = f"""
あなたはプロの「復縁アドバイザーT」です。
以下の私のnote記事へ読者を惹きつけ、記事を購入・購読してもらうためのThreads投稿文を作成してください。

記事タイトル：{article_title}

【プロンプト戦略・指示】
1. 冒頭は疑問符「？」や問いかけを使って、元彼・元カノとの復縁で悩む人の興味を強く惹きつけてください。
2. 記事の核心部分はあえて伏せ、「本気で復縁したい人だけ読んでほしい」「具体的な心理戦とステップを公開中」という形で有料記事への強い購入・読了モチベーションを高めてください。
3. 末尾は必ず改行して「本気で復縁を引き寄せたい方は1コメのnoteへ👇」または「秘密の攻略法はコメント欄の有料noteで公開中👇」で締めくくってください。
4. 全体で3〜4行程度にし、テンポよく読ませる文体にしてください。
5. 出力は投稿する本文のみにしてください。見出しや解説は絶対に入れなでください。
"""
    try:
        res = gemini_client.models.generate_content(model='gemini-3.6-flash', contents=prompt)
        if res and res.text:
            post_text = res.text.strip().replace("*", "")
            return article_url, post_text, article_url
    except Exception as e:
        print(f"note紹介生成エラー: {e}")

    return None, None, None

# ==================================================================
# --- 7. メイン実行ループ ---
# ==================================================================
def main_loop():
    print("--- 🔥 復縁アドバイザーT 自動運用プログラムを開始します ---")
    while True:
        try:
            # 深夜帯（2:00〜7:00）は停止
            if 2 <= datetime.now().hour < 7:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 💤 深夜帯のため休止します。")
                wait_seconds = random.randint(3600, 7200)
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 投稿プロセスを実行中...")
                note_url, post_text, comment_text = generate_fukuenadv_content()
                
                if not post_text:
                    print("⚠️ 文章生成スキップ。時間をおいてリトライします。")
                    wait_seconds = random.randint(1800, 3600)
                else:
                    print(f"\n--- 📝 投稿予定テキスト ---\n{post_text}\n-----------------------------")
                    if comment_text:
                        print(f"--- 💬 1コメ予定note URL ---\n{comment_text}\n-----------------------------")
                    
                    with sync_playwright() as p:
                        success = post_to_threads_with_session(p, post_text, comment_text)
                    
                    if success:
                        if note_url and note_url != "daily_mutter":
                            save_posted_note(note_url)
                        
                        # 投稿間隔（約3.5〜4.5時間）
                        base_wait = random.randint(12600, 16200)
                        wait_seconds = max(9000, base_wait + random.randint(-600, 600))
                    else:
                        print("⚠️ 投稿失敗。リトライまで待機します。")
                        wait_seconds = random.randint(1800, 3600)
            
            next_run = datetime.now() + timedelta(seconds=wait_seconds)
            print(f"⏳ 次回投稿予定時刻: {next_run.strftime('%Y-%m-%d %H:%M:%S')}（約 {wait_seconds // 3600} 時間後）")
            print("=" * 60)
            time.sleep(wait_seconds)

        except KeyboardInterrupt:
            print("\n🛑 手動停止されました。プログラムを終了します。")
            break
        except Exception as e:
            print(f"💥 エラー発生: {e}")
            time.sleep(1200)

if __name__ == "__main__":
    main_loop()