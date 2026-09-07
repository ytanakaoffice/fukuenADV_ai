import base64
from datetime import datetime, timezone
import json
import os
import requests
import stripe
import streamlit as st
from supabase import create_client

# ページ設定
st.set_page_config(
    page_title="復縁アドバイザーT AI｜リアルタイム復縁相談室",
    page_icon="💬",
    layout="wide"
)

# セッション状態の初期化
if "user" not in st.session_state:
    st.session_state["user"] = None
if "is_premium" not in st.session_state:
    st.session_state["is_premium"] = False
if "chat_threads" not in st.session_state:
    st.session_state["chat_threads"] = []
if "current_thread_id" not in st.session_state:
    st.session_state["current_thread_id"] = None
if "thread_messages" not in st.session_state:
    st.session_state["thread_messages"] = {}
if "dify_conv_ids" not in st.session_state:
    st.session_state["dify_conv_ids"] = {}
if "trial_chat_count" not in st.session_state:
    st.session_state["trial_chat_count"] = 0
if "history_loaded" not in st.session_state:
    st.session_state["history_loaded"] = False

# Supabase初期化
@st.cache_resource
def init_supabase():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    key = st.secrets["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

def init_admin_supabase():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    service_role_key = st.secrets["supabase"].get("SUPABASE_SERVICE_ROLE_KEY", "")
    if service_role_key:
        return create_client(url, service_role_key)
    return None

supabase_admin = init_admin_supabase()

if "stripe" in st.secrets and "STRIPE_SECRET_KEY" in st.secrets["stripe"]:
    stripe.api_key = st.secrets["stripe"]["STRIPE_SECRET_KEY"]

# データベース履歴操作用関数（発言数をDBから自動カウント）
def load_user_chats(user_id):
    try:
        res = supabase.table("fukuenadv_chat_messages").select("thread_id, role, content, created_at").eq("user_id", user_id).order("created_at", desc=False).execute()
        if res.data:
            threads = []
            thread_messages = {}
            user_msg_count = 0
            for row in res.data:
                tid = row["thread_id"]
                if tid not in threads:
                    threads.append(tid)
                    thread_messages[tid] = []
                thread_messages[tid].append({
                    "role": row["role"],
                    "content": row["content"]
                })
                if row["role"] == "user":
                    user_msg_count += 1
            st.session_state["chat_threads"] = threads
            st.session_state["thread_messages"] = thread_messages
            st.session_state["trial_chat_count"] = user_msg_count
            if threads and not st.session_state.get("current_thread_id"):
                st.session_state["current_thread_id"] = threads[-1]
    except Exception as e:
        st.error(f"履歴取得エラー: {e}")

def save_chat_message(user_id, user_email, thread_id, role, content):
    if not user_id:
        return
    try:
        supabase.table("fukuenadv_chat_messages").insert({
            "user_id": user_id,
            "user_email": user_email,
            "thread_id": thread_id,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        st.error(f"履歴保存エラー: {e}")

# 認証・サブスク連携関数
def login(email, password):
    try:
        clean_email = email.strip().lower()
        res = supabase.auth.sign_in_with_password({
            "email": clean_email,
            "password": password
        })
        if res.user:
            if res.user.email_confirmed_at is None:
                st.error("メール認証が完了していません。届いた確認メールのリンクをクリックしてください。")
                return None
            return res.user
        return None
    except Exception:
        st.error("メールアドレスまたはパスワードが正しくありません。")
        return None

def signup(email, password):
    try:
        clean_email = email.strip().lower()
        res = supabase.auth.sign_up({
            "email": clean_email,
            "password": password
        })
        return res
    except Exception as e:
        st.error(f"登録エラー: {e}")
        return None

def check_access(email):
    if not email:
        return False
    try:
        clean_email = email.strip().lower()
        response = supabase.table("subscriptions_fukuenADV").select("status, current_period_end").eq("email", clean_email).execute()
        
        if response.data:
            sub = response.data[0]
            status = sub.get("status")
            period_end = sub.get("current_period_end")

            if status in ["active", "trialing"]:
                return True

            if period_end and period_end != "1970-01-01T00:00:00+00:00":
                try:
                    end_date = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if now <= end_date:
                        return True
                except Exception as parse_err:
                    pass
        return False
    except Exception as e:
        return False

def execute_account_deletion(user_email, user_id):
    try:
        clean_email = user_email.strip().lower()
        if stripe.api_key:
            try:
                customers = stripe.Customer.list(email=clean_email, limit=1)
                if customers.data:
                    subs = stripe.Subscription.list(customer=customers.data[0].id, status="active")
                    for s in subs.data:
                        stripe.Subscription.cancel(s.id)
            except Exception:
                pass

        if supabase_admin:
            supabase_admin.table("subscriptions_fukuenADV").delete().eq("email", clean_email).execute()
            supabase_admin.table("fukuenadv_chat_messages").delete().eq("user_id", user_id).execute()
            supabase_admin.auth.admin.delete_user(user_id)
        else:
            supabase.table("subscriptions_fukuenADV").delete().eq("email", clean_email).execute()
            supabase.table("fukuenadv_chat_messages").delete().eq("user_id", user_id).execute()
        return True
    except Exception as e:
        st.error(f"退会処理エラー: {e}")
        return False

# Dify API連携関数
def call_dify_fukuen(query, conversation_id=""):
    dify_endpoint = st.secrets["dify"]["DIFY_ENDPOINT"]
    dify_api_key = st.secrets["dify"]["DIFY_API_KEY"]
    
    headers = {
        "Authorization": f"Bearer {dify_api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    
    user_identifier = "guest"
    if st.session_state.get("user"):
        user_identifier = st.session_state["user"].get("id", "guest")

    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "user": user_identifier,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        response = requests.post(
            dify_endpoint,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        if response.status_code == 200:
            res_data = response.json()
            return res_data.get("answer", "回答を取得できませんでした。"), res_data.get("conversation_id", "")
        else:
            return f"エラーが発生しました（ステータス: {response.status_code}）", conversation_id
    except Exception as e:
        return f"通信エラー: {e}", conversation_id

# メールアドレス探索用ヘルパー関数
def get_secrets_email():
    if "receiver" in st.secrets and st.secrets["receiver"]:
        return st.secrets["receiver"]
    if "sender" in st.secrets and st.secrets["sender"]:
        return st.secrets["sender"]
    
    for key in st.secrets:
        try:
            sec = st.secrets[key]
            if hasattr(sec, "get"):
                if sec.get("receiver"):
                    return sec.get("receiver")
                if sec.get("sender"):
                    return sec.get("sender")
        except Exception:
            pass
    return "担当メールアドレス"

# ダイアログ定義
@st.dialog("利用規約", width="large")
def show_terms_dialog():
    if os.path.exists("TERMS.md"):
        with open("TERMS.md", "r", encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.error("TERMS.md ファイルが見つかりません。")

@st.dialog("ログイン / 新規会員登録", width="large")
def show_auth_dialog():
    tab_login, tab_signup = st.tabs(["ログイン", "新規会員登録"])
    
    with tab_login:
        st.markdown("### ログイン")
        email = st.text_input("メールアドレス", key="dlg_login_email")
        password = st.text_input("パスワード", type="password", key="dlg_login_password")
        if st.button("ログインする", key="dlg_btn_login", use_container_width=True, type="primary"):
            if email and password:
                user_info = login(email, password)
                if user_info:
                    st.session_state["user"] = {"email": user_info.email, "id": user_info.id}
                    st.session_state["is_premium"] = check_access(user_info.email)
                    st.session_state["history_loaded"] = False
                    st.rerun()
            else:
                st.warning("メールアドレスとパスワードを入力してください。")

    with tab_signup:
        st.markdown("### 新規会員登録")
        with st.expander("利用規約を確認する"):
            if os.path.exists("TERMS.md"):
                with open("TERMS.md", "r", encoding="utf-8") as f:
                    st.markdown(f.read())
            else:
                st.error("TERMS.md ファイルが見つかりません。")
            
        with st.form("dlg_signup_form"):
            new_email = st.text_input("メールアドレス", key="dlg_signup_email")
            new_password = st.text_input("パスワード (6文字以上)", type="password", key="dlg_signup_password")
            confirm_password = st.text_input("パスワード (確認用)", type="password", key="dlg_signup_confirm")
            agree_terms = st.checkbox("利用規約に同意する", key="dlg_chk_terms")
            submit_signup = st.form_submit_button("アカウントを作成する", use_container_width=True, type="primary")
            
            if submit_signup:
                if not new_email or not new_password or not confirm_password:
                    st.warning("すべての項目を入力してください。")
                elif new_password != confirm_password:
                    st.error("パスワードが一致しません。")
                elif len(new_password) < 6:
                    st.error("パスワードは6文字以上で設定してください。")
                elif not agree_terms:
                    st.error("利用規約への同意が必要です。")
                else:
                    res = signup(new_email, new_password)
                    if res and res.user:
                        st.success("仮登録が完了しました！届いたメール内の承認リンクをクリックしてログインしてください。")

@st.dialog("有料プランへのご登録（月額2,800円）", width="medium")
def show_payment_dialog():
    st.write("無制限に復縁相談を行うには、月額会員プランへのご登録が必要です。")
    user_email = st.session_state["user"]["email"] if st.session_state.get("user") else ""
    user_id = st.session_state["user"]["id"] if st.session_state.get("user") else ""
    
    base_stripe_url = st.secrets["stripe"].get("STRIPE_PAYMENT_LINK", "#")
    stripe_url = f"{base_stripe_url}?prefilled_email={user_email}&client_reference_id={user_id}"
    
    st.link_button("決済画面へ進む（Stripe）", stripe_url, type="primary", use_container_width=True)
    if st.button("🔄 決済完了後にステータスを更新する", use_container_width=True):
        st.session_state["is_premium"] = check_access(user_email)
        st.rerun()

@st.dialog("特定商取引法に基づく表記", width="large")
def show_tokusho_dialog():
    contact_email = get_secrets_email()

    st.markdown(f"""
    <h3>特定商取引法に基づく表記</h3>
    <b>・事業者名：</b>請求があった場合、遅滞なく開示いたします。<br>
    <b>・お問い合わせ先：</b>{contact_email}<br>
    <b>・販売価格：</b>月額 2,800円（税込）<br>
    <b>・お支払い方法：</b>クレジットカード決済（Stripe）<br>
    <b>・サービス提供時期：</b>決済手続き完了後、即時ご利用いただけます。<br>
    <b>・解約および返品：</b>解約手続き後は次回更新日まで利用可能です。返金・日割計算は行っておりません。
    """, unsafe_allow_html=True)

@st.dialog("退会手続き", width="medium")
def show_delete_account_dialog():
    if not st.session_state.get("user"):
        st.warning("ログインしていません。")
        return
    curr_email = st.session_state["user"]["email"]
    curr_id = st.session_state["user"]["id"]

    # データベースからサブスクリプションの状態を取得
    sub_info = None
    try:
        clean_email = curr_email.strip().lower()
        res = supabase.table("subscriptions_fukuenADV").select("cancel_at_period_end, status").eq("email", clean_email).execute()
        if res.data:
            sub_info = res.data[0]
    except Exception as e:
        st.error(f"契約情報の確認に失敗しました: {e}")

    # 有料プラン契約中かつ自動更新が停止していない（cancel_at_period_end が False）場合は退会をブロック
    is_active = sub_info and sub_info.get("status") in ["active", "trialing"]
    cancel_at_period_end = sub_info.get("cancel_at_period_end", False) if sub_info else False

    if is_active and not cancel_at_period_end:
        st.error("定期決済の自動更新が解除されていません。")
        st.write("アカウントを削除する前に、まず「契約管理・解約」ボタンから定期決済の自動更新を停止（解約手続き）を行ってください。")
        
        stripe_portal = st.secrets.get("stripe", {}).get("STRIPE_PORTAL_URL", "#")
        st.markdown(f'<a href="{stripe_portal}" target="_blank"><button style="width:100%; padding:10px; border-radius:4px; background:#4F46E5; color:white; border:none; cursor:pointer; font-weight:bold;">契約管理・解約画面を開く</button></a>', unsafe_allow_html=True)
        return

    st.warning("アカウントを削除すると、相談履歴が全削除されます。")
    agree = st.checkbox("注意事項（復元不可・返金不可）に同意します", key="chk_delete_agree")
    
    if st.button("アカウントを完全に削除して退会する", type="primary", disabled=not agree, use_container_width=True):
        if execute_account_deletion(curr_email, curr_id):
            st.success("退会手続きが完了しました。")
            supabase.auth.sign_out()
            st.session_state.clear()
            st.rerun()

# ユーザー状態のチェックおよびデータベースからの履歴読み込み
if st.session_state.get("user"):
    if not st.session_state.get("is_premium"):
        st.session_state["is_premium"] = check_access(st.session_state["user"]["email"])
    if not st.session_state.get("history_loaded"):
        load_user_chats(st.session_state["user"]["id"])
        st.session_state["history_loaded"] = True

is_premium = st.session_state.get("is_premium", False)
MAX_FREE_TURNS = 5

# サイドバー管理
st.sidebar.title("復縁アドバイザーT AI")

# 会員状態表示
if is_premium:
    st.sidebar.success(f"👑 有料プラン適用中\n{st.session_state['user']['email']}")
    stripe_portal = st.secrets.get("stripe", {}).get("STRIPE_PORTAL_URL", "#")
    st.sidebar.markdown(f'<a href="{stripe_portal}" target="_blank"><button style="width:100%; padding:6px; border-radius:4px; background:#4F46E5; color:white; border:none; cursor:pointer; font-weight:bold;">契約管理・解約</button></a>', unsafe_allow_html=True)
    if st.sidebar.button("退会手続き", use_container_width=True):
        show_delete_account_dialog()
    if st.sidebar.button("ログアウト", use_container_width=True):
        supabase.auth.sign_out()
        st.session_state.clear()
        st.rerun()
elif st.session_state.get("user"):
    remains = max(0, MAX_FREE_TURNS - st.session_state["trial_chat_count"])
    st.sidebar.info(f"👤 無料会員（決済未完了）\n{st.session_state['user']['email']}\n残り質問可能回数: {remains} / {MAX_FREE_TURNS} 回")
    if st.sidebar.button("🔓 有料プラン（2,800円/月）に登録", type="primary", use_container_width=True):
        show_payment_dialog()
    if st.sidebar.button("ログアウト", use_container_width=True):
        supabase.auth.sign_out()
        st.session_state.clear()
        st.rerun()
else:
    remains = max(0, MAX_FREE_TURNS - st.session_state["trial_chat_count"])
    st.sidebar.info(f"👤 ゲスト（無料お試しモード）\n残り質問可能回数: {remains} / {MAX_FREE_TURNS} 回")
    if st.sidebar.button("🔑 ログイン / 新規会員登録", type="primary", use_container_width=True):
        show_auth_dialog()

if st.sidebar.button("利用規約・特商法表記", use_container_width=True):
    show_tokusho_dialog()

# 新規チャット作成
st.sidebar.markdown("---")
if st.sidebar.button("➕ 新しい相談を始める", use_container_width=True, type="primary"):
    new_thread_id = f"thread_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    st.session_state["chat_threads"].append(new_thread_id)
    st.session_state["current_thread_id"] = new_thread_id
    st.session_state["thread_messages"][new_thread_id] = []
    st.rerun()

# 初回スレッド自動生成
if not st.session_state["current_thread_id"]:
    default_id = f"thread_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    st.session_state["chat_threads"].append(default_id)
    st.session_state["current_thread_id"] = default_id
    st.session_state["thread_messages"][default_id] = []

# スレッド一覧の表示・切り替え
st.sidebar.subheader("過去の相談履歴")
for tid in reversed(st.session_state["chat_threads"]):
    messages_in_tid = st.session_state["thread_messages"].get(tid, [])
    label_text = "💬 新しい相談"
    if messages_in_tid:
        first_user_msg = next((m["content"] for m in messages_in_tid if m["role"] == "user"), "")
        if first_user_msg:
            label_text = f"💬 {first_user_msg[:12]}..."
            
    is_active = (tid == st.session_state["current_thread_id"])
    if st.sidebar.button(label_text, key=f"btn_{tid}", use_container_width=True, disabled=is_active):
        st.session_state["current_thread_id"] = tid
        st.rerun()

# メイン画面処理
current_tid = st.session_state["current_thread_id"]
current_messages = st.session_state["thread_messages"].get(current_tid, [])

st.title("復縁アドバイザーT リアルタイムAI相談室")
st.caption("心理学・行動科学に基づき、元彼・元カノとの復縁に向けたアプローチ方法をいつでもアドバイスします。")

# チャット履歴の描画
for msg in current_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 質問制限の判定・制限動作
if not is_premium and st.session_state["trial_chat_count"] >= MAX_FREE_TURNS:
    st.warning("無料お試し（5往復）の上限に達しました。続けて相談するには月額プラン（2,800円/月）へのご登録が必要です。")
    
    col1, col2 = st.columns(2)
    with col1:
        if not st.session_state.get("user"):
            if st.button("🔑 会員登録 / ログインする", type="primary", use_container_width=True):
                show_auth_dialog()
    with col2:
        if st.button("🔓 有料プラン（2,800円/月）に登録する", type="primary", use_container_width=True):
            if not st.session_state.get("user"):
                show_auth_dialog()
            else:
                show_payment_dialog()
else:
    prompt = st.chat_input("悩みや現状を相談してください（例: 連絡を送るべきタイミングについて迷っています）")
    if prompt:
        st.session_state["trial_chat_count"] += 1
        
        # ユーザー発言の記録
        current_messages.append({"role": "user", "content": prompt})
        st.session_state["thread_messages"][current_tid] = current_messages
        
        # データベースへ送信メッセージを保存
        if st.session_state.get("user"):
            save_chat_message(
                st.session_state["user"]["id"],
                st.session_state["user"]["email"],
                current_tid,
                "user",
                prompt
            )
        
        # 画面更新用に会話IDを取得
        conv_id = st.session_state["dify_conv_ids"].get(current_tid, "")
        
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("復縁アドバイザーTが思考・回答作成中..."):
                reply_text, new_conv_id = call_dify_fukuen(prompt, conv_id)
                st.session_state["dify_conv_ids"][current_tid] = new_conv_id
                st.markdown(reply_text)

        # AI回答の記録
        current_messages.append({"role": "assistant", "content": reply_text})
        st.session_state["thread_messages"][current_tid] = current_messages
        
        # データベースへAI回答メッセージを保存
        if st.session_state.get("user"):
            save_chat_message(
                st.session_state["user"]["id"],
                st.session_state["user"]["email"],
                current_tid,
                "assistant",
                reply_text
            )
            
        st.rerun()