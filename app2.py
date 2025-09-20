# app2.py — 🍜 ג'ירף מטבחים – איכויות אוכל
# דרישות חובה: streamlit, pandas, python-dotenv
# אופציונלי: gspread, google-auth (ל-Google Sheets), openai>=1.0.0 (לניתוח GPT)
# הרצה: streamlit run app2.py

from __future__ import annotations
import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# נסיון ייבוא של Google Sheets (אופציונלי)
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

# =========================
# ------- SETTINGS --------
# =========================
st.set_page_config(page_title="🍜 ג'ירף מטבחים – איכויות אוכל", layout="wide")
load_dotenv()  # יטעין .env אם קיים

# סניפים
BRANCHES: List[str] = ["חיפה", "ראשל״צ", "רמה״ח", "נס ציונה", "לנדמרק", "פתח תקווה", "הרצליה"]

# מנות
DISHES: List[str] = [
    "פאד תאי", "מלאזית", "פיליפינית", "אפגנית", "קארי דלעת", "סצ'ואן",
    "ביף רייס", "אורז מטוגן", "מאקי סלמון", "מאקי טונה", "ספייסי סלמון", "נודלס ילדים"
]

DB_PATH = "food_quality.db"
DUP_HOURS = 12            # חלון כפילויות — בדיקה זהה ב-12 שעות אחרונות
MIN_BRANCH_LEADER_N = 3   # מינימום תצפיות לסניף מוביל לפי ממוצע
MIN_CHEF_TOP_M = 5        # מינימום תצפיות לטבח מצטיין

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# =========================
# ---------- STYLE --------
# =========================
st.markdown(
    """
<style>
/* RTL לגוף הדף בלבד */
.main .block-container { direction: rtl; font-family: "Rubik", -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
/* שמירה על סרגל צד LTR */
.sidebar .sidebar-content { direction: ltr !important; }

/* Header */
.header-wrap {
  background: linear-gradient(135deg, #0f172a 0%, #1f2937 50%, #0b1324 100%);
  color: #fff; padding: 26px 22px; border-radius: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.25);
  border: 1px solid rgba(255,255,255,.06); margin-bottom: 22px;
}
.header-title { font-size: 28px; font-weight: 800; margin: 0 0 6px 0; }
.header-sub { opacity: .9; font-size: 14px; }

/* Cards + KPIs */
.card { background:#fff; border:1px solid #e9edf5; border-radius:16px; padding:18px; box-shadow:0 8px 20px rgba(16,24,40,.06); margin-bottom:16px; }
.kpi { padding:16px; border-radius:14px; border:1px solid #eef2f7; box-shadow:0 4px 14px rgba(16,24,40,.06); }
.kpi .big { font-size:26px; font-weight:900; }
.kpi .num { font-size:20px; font-weight:800; }
.hint { color:#6b7280; font-size:12px; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; background:#f3f4f6; font-size:12px; margin-right:6px; }
.btn-primary > button { background: linear-gradient(135deg, #f59e0b, #ff9800); color:white; border:0; border-radius:12px; padding:10px 16px; font-weight:700; width:100%; }

/* שדות טקסט RTL */
.stTextInput > div > div > input { text-align: right; }
.stTextArea > div > div > textarea { text-align: right; }
.stSelectbox > div > div { text-align: right; }

/* ===== Status Bar ===== */
.status-bar {
  display:flex; align-items:center; justify-content:space-between;
  padding:12px 16px; border-radius:14px; margin:8px 0 16px 0;
  color:#fff; font-weight:700; box-shadow:0 8px 20px rgba(16,24,40,.08);
  border:1px solid rgba(255,255,255,.08);
}
.status-bar .right { display:flex; gap:12px; align-items:center; }
.status-bar .left  { opacity:.95; }
.status-bar .tag   { padding:4px 10px; border-radius:999px; background:rgba(255,255,255,.16); font-weight:600; }

/* צבעים לפי תפקיד */
.status-bar.meta   { background:linear-gradient(135deg,#0ea5e9,#2563eb); }  /* מטה – כחול */
.status-bar.branch { background:linear-gradient(135deg,#10b981,#059669); }  /* סניף – ירוק */
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="header-wrap">
  <div class="header-title">🍜 ג'ירף מטבחים – איכויות אוכל</div>
  <div class="header-sub">טופס הזנת בדיקות איכות + ניתוחים ומדדים חיים</div>
</div>
""",
    unsafe_allow_html=True,
)

# =========================
# ------- DATABASE --------
# =========================
def conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False)

SCHEMA = """
CREATE TABLE IF NOT EXISTS food_quality (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  branch TEXT NOT NULL,
  chef_name TEXT NOT NULL,
  dish_name TEXT NOT NULL,
  score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  submitted_by TEXT
);
"""
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_food_branch_time ON food_quality(branch, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_food_chef_dish_time ON food_quality(chef_name, dish_name, created_at)",
]

def init_db():
    c = conn()
    cur = c.cursor()
    cur.execute(SCHEMA)
    for q in INDEXES:
        cur.execute(q)
    c.commit()
    c.close()

init_db()

# =========================
# -------- HELPERS --------
# =========================
@st.cache_data(ttl=15)
def load_df() -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query(
        "SELECT id, branch, chef_name, dish_name, score, notes, created_at FROM food_quality ORDER BY created_at DESC",
        c,
    )
    c.close()
    return df

def insert_record(branch: str, chef: str, dish: str, score: int, notes: str = "", submitted_by: Optional[str] = None):
    """שומר ל-SQLite, ואז מנסה לשמור ל-Google Sheets (אם מוגדר)."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    c = conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO food_quality (branch, chef_name, dish_name, score, notes, created_at, submitted_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (branch.strip(), chef.strip(), dish.strip(), int(score), (notes or "").strip(), timestamp, submitted_by),
    )
    c.commit()
    c.close()
    try:
        save_to_google_sheets(branch, chef, dish, score, notes, timestamp)
    except Exception as e:
        st.warning(f"נשמר מקומית, אבל לא ניתן לשמור ב-Google Sheets: {e}")

def _load_google_creds() -> tuple[dict, str]:
    """מעמיס קרדנציאלס בצורה בטוחה (Secrets קודם, אחרת .env) ומחזיר (creds, sheet_url)."""
    sheet_url = st.secrets.get("GOOGLE_SHEET_URL", "") or os.getenv("GOOGLE_SHEET_URL", "")
    creds = st.secrets.get("google_service_account", {})
    if not creds:
        env_json = os.getenv("GOOGLE_SERVICE_ACCOUNT", "")
        if env_json:
            try:
                creds = json.loads(env_json)  # ← תקין (במקום eval)
            except Exception:
                creds = {}
    return creds, sheet_url

def save_to_google_sheets(branch: str, chef: str, dish: str, score: int, notes: str, timestamp: str):
    """שמירה ל-Google Sheets (אם יש ספריות והגדרות)."""
    if not GSHEETS_AVAILABLE:
        return
    creds, sheet_url = _load_google_creds()
    if not (creds and sheet_url):
        return
    try:
        credentials = Credentials.from_service_account_info(creds).with_scopes(SCOPES)
        gc = gspread.authorize(credentials)
        sheet = gc.open_by_url(sheet_url).sheet1
        sheet.append_row([timestamp, branch, chef, dish, score, notes or ""])
    except Exception as e:
        st.warning(f"Google Sheets: {e}")

def has_recent_duplicate(branch: str, chef: str, dish: str, hours: int = DUP_HOURS) -> bool:
    """בודקת אם קיימת בדיקה זהה (branch+chef+dish) ב-X שעות האחרונות (UTC)."""
    if hours <= 0:
        return False
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    c = conn()
    cur = c.cursor()
    cur.execute(
        """SELECT 1 FROM food_quality
           WHERE branch=? AND chef_name=? AND dish_name=? AND created_at >= ?
           LIMIT 1""",
        (branch.strip(), chef.strip(), dish.strip(), cutoff),
    )
    exists = cur.fetchone() is not None
    c.close()
    return exists

def kpi_best_branch_by_count(df: pd.DataFrame) -> Tuple[Optional[str], int]:
    if df.empty: return None, 0
    s = df.groupby("branch")["id"].count().sort_values(ascending=False)
    return s.index[0], int(s.iloc[0])

def kpi_current_branch_count(df: pd.DataFrame, branch: Optional[str]) -> int:
    if df.empty or not branch: return 0
    return int((df["branch"] == branch).sum())

def kpi_best_avg_branch(df: pd.DataFrame, min_n: int = MIN_BRANCH_LEADER_N) -> Tuple[Optional[str], Optional[float], int]:
    if df.empty: return None, None, 0
    g = df.groupby("branch").agg(n=("id","count"), avg=("score","mean")).reset_index()
    g = g.sort_values(["avg","n"], ascending=[False, False])
    leader = g[g["n"] >= min_n]
    if leader.empty:
        leader = g.iloc[:1]
    row = leader.iloc[0]
    return str(row["branch"]), float(row["avg"]), int(row["n"])

def kpi_top_chef(df: pd.DataFrame, min_m: int = MIN_CHEF_TOP_M) -> Tuple[Optional[str], Optional[float], int]:
    if df.empty: return None, None, 0
    g = df.groupby("chef_name").agg(n=("id","count"), avg=("score","mean")).reset_index()
    g = g.sort_values(["n","avg"], ascending=[False, False])
    qual = g[g["n"] >= min_m]
    pick = qual.iloc[0] if not qual.empty else g.iloc[0]
    return str(pick["chef_name"]), float(pick["avg"]), int(pick["n"])

def kpi_top_dish(df: pd.DataFrame) -> Tuple[Optional[str], int]:
    if df.empty: return None, 0
    s = df.groupby("dish_name")["id"].count().sort_values(ascending=False)
    return s.index[0], int(s.iloc[0])

def score_hint(x: int) -> str:
    return "😟 חלש" if x <= 3 else ("🙂 סביר" if x <= 6 else ("😀 טוב" if x <= 8 else "🤩 מצוין"))

def refresh_df():
    load_df.clear()

# =========================
# ------ LOGIN & CONTEXT --
# =========================
def require_auth() -> dict:
    """מסך כניסה: 'סניף' (בחירת סניף) או 'מטה' (ללא בחירת סניף)."""
    if "auth" not in st.session_state:
        st.session_state.auth = {"role": None, "branch": None}
    auth = st.session_state.auth

    if not auth["role"]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.subheader("👋 מסך כניסה")
        role = st.radio("בחר סוג משתמש", options=["סניף", "מטה"], horizontal=True, index=0)

        if role == "סניף":
            branch_choice = st.selectbox("בחר סניף", options=["— בחר —"] + BRANCHES, index=0)
            if st.button("המשך"):
                if branch_choice == "— בחר —":
                    st.error("בחר סניף כדי להמשיך.")
                else:
                    st.session_state.auth = {"role": "branch", "branch": branch_choice}
                    st.rerun()
        else:
            if st.button("המשך כ'מטה'"):
                st.session_state.auth = {"role": "meta", "branch": None}
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()
    return auth

auth = require_auth()  # מכאן auth מוגדר

# ===== פס סטטוס עליון =====
role_class = "meta" if auth["role"] == "meta" else "branch"
branch_html = "" if auth["role"] == "meta" else f'— <span class="tag">{auth["branch"]}</span>'
st.markdown(
    f"""
<div class="status-bar {role_class}">
  <div class="left">
    אתה עובד כעת במצב <span class="tag">{'מטה' if auth['role']=='meta' else 'סניף'}</span> {branch_html}
  </div>
  <div class="right">
    <span class="tag">אפשר להתנתק ולבחור סניף אחר בכל רגע</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# =========================
# ---------- FORM ---------
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("✍️ הזנת בדיקת איכות חדשה")

colA, colB, colC = st.columns([1,1,1])

# בחירת סניף (מטה: בורר; סניף: תצוגה בלבד)
if auth["role"] == "meta":
    with colA:
        selected_branch = st.selectbox("סניף *", options=BRANCHES, index=0)
else:
    selected_branch = auth["branch"]
    with colA:
        st.text_input("סניף", value=selected_branch, disabled=True)

with colB:
    chef = st.text_input("שם הטבח *", placeholder="הקלד שם טבח...")
with colC:
    dish = st.selectbox("שם המנה *", options=DISHES, index=0)

colD, colE = st.columns([1,1])
with colD:
    score = st.selectbox("ציון איכות *", options=list(range(1, 11)), index=7,
                         format_func=lambda x: f"{x} - {score_hint(x)}")
with colE:
    notes = st.text_area("הערות (לא חובה)", placeholder="מרקם, טמפרטורה, תיבול, עקביות...")

override = st.checkbox("שמור גם אם קיימת בדיקה דומה ב־12 השעות האחרונות (כפילויות)")

save_col1, save_col2 = st.columns([1,3])
with save_col1:
    save = st.button("💾 שמור בדיקה", type="primary")

if save:
    if not selected_branch or not chef.strip() or not dish:
        st.error("חובה לבחור/להציג סניף, להזין שם טבח ולבחור מנה.")
    else:
        if (not override) and has_recent_duplicate(selected_branch, chef, dish, DUP_HOURS):
            st.warning("נמצאה בדיקה קודמת לאותו סניף/טבח/מנה ב־12 השעות האחרונות. סמן 'שמור גם אם…' כדי לאשר בכל זאת.")
        else:
            insert_record(selected_branch, chef, dish, score, notes, submitted_by=auth["role"])
            st.success(f"✅ נשמר: **{selected_branch} · {chef} · {dish}** • ציון **{score}**")
            refresh_df()

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# --------- KPIs ----------
# =========================
df = load_df()

st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 מדדי ביצוע (מתעדכן מיד)")

best_branch, best_branch_count = kpi_best_branch_by_count(df)
current_branch_count = kpi_current_branch_count(df, selected_branch)
best_avg_branch, best_avg_value, best_avg_n = kpi_best_avg_branch(df, MIN_BRANCH_LEADER_N)
top_chef, top_chef_avg, top_chef_n = kpi_top_chef(df, MIN_CHEF_TOP_M)
top_dish, top_dish_count = kpi_top_dish(df)

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="kpi">', unsafe_allow_html=True)
    st.markdown("#### הסניף המוביל בבדיקות")
    if best_branch is None:
        st.write("אין נתונים")
    else:
        current_html = f'<span class="big">{current_branch_count}</span>' if selected_branch else '<span class="num">—</span>'
        st.write(f"הנוכחי: {current_html} | **{best_branch}** — **{best_branch_count}** בדיקות", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with k2:
    st.markdown('<div class="kpi">', unsafe_allow_html=True)
    st.markdown("#### ממוצע ציון — נוכחי מול המוביל")
    if best_avg_branch is None:
        st.write("אין נתונים")
    else:
        cur_avg = df[df["branch"] == selected_branch]["score"].mean() if selected_branch else None
        cur_avg_str = f'<span class="big">{cur_avg:.2f}</span>' if cur_avg is not None and not pd.isna(cur_avg) else '—'
        st.write(
            f"הנוכחי: {cur_avg_str} | המוביל: **{best_avg_branch}** ({best_avg_value:.2f})",
            unsafe_allow_html=True
        )
        if best_avg_n < MIN_BRANCH_LEADER_N:
            st.caption("הערה: הסניף המוביל לפי ממוצע עומד על מדגם קטן.")
    st.markdown('</div>', unsafe_allow_html=True)

with k3:
    st.markdown('<div class="kpi">', unsafe_allow_html=True)
    st.markdown("#### הטבח המצטיין ברשת")
    if top_chef is None:
        st.write("אין נתונים")
    else:
        st.write(f"**{top_chef}** — ממוצע **{top_chef_avg:.2f}** (על סמך {top_chef_n} בדיקות)")
        if top_chef_n < MIN_CHEF_TOP_M:
            st.caption("מדגם קטן — מוצג המצטיין הזמין.")
    st.markdown('</div>', unsafe_allow_html=True)

with k4:
    st.markdown('<div class="kpi">', unsafe_allow_html=True)
    st.markdown("#### המנה הכי נבחנת")
    if top_dish is None:
        st.write("אין נתונים")
    else:
        st.write(f"**{top_dish}** — {top_dish_count} בדיקות")
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# ------ GPT ANALYSIS -----
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("🤖 ניתוח עם ChatGPT")

api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
org_id  = st.secrets.get("OPENAI_ORG") or os.getenv("OPENAI_ORG", "")
# project_id אופציונלי — לעתים לא נתמך, נמנע כברירת מחדל

if not api_key:
    st.warning("🔑 לא נמצא מפתח OpenAI. הוסף OPENAI_API_KEY ל-Secrets או ל-.env כדי להפעיל ניתוח AI.")
    st.info("💡 ללא מפתח, עדיין ניתן להשתמש בכל יתר התכונות של האפליקציה.")
else:
    gpt_col1, gpt_col2 = st.columns([2,1])
    with gpt_col1:
        user_q = st.text_input("שאלה על הנתונים (למשל: מה המנה הכי נבחנת בכל סניף?)", placeholder="כתוב כאן שאלה חופשית...")
    with gpt_col2:
        do_insights = st.button("בצע ניתוח כללי")
        ask_btn = st.button("שלח שאלה")

    def df_to_csv_for_llm(df_in: pd.DataFrame, max_rows: int = 400) -> str:
        d = df_in.copy()
        if len(d) > max_rows:
            d = d.head(max_rows)
        return d.to_csv(index=False)

    def call_openai(system_prompt: str, user_prompt: str) -> str:
        try:
            from openai import OpenAI
            client_kwargs = {"api_key": api_key}
            if org_id:
                client_kwargs["organization"] = org_id
            client = OpenAI(**client_kwargs)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"❌ שגיאה בקריאה ל-OpenAI: {e}"

    SYSTEM_ANALYST = (
        "אתה אנליסט דאטה דובר עברית. מוצגת לך טבלת בדיקות עם עמודות: "
        "id, branch, chef_name, dish_name, score, notes, created_at. "
        "סכם תובנות מרכזיות, דגשים, חריגים והמלצות קצרות. השתמש בשפה פשוטה וברורה."
    )

    if do_insights or (user_q and ask_btn):
        if df.empty:
            st.info("אין נתונים לניתוח עדיין. התחל למלא בדיקות!")
        else:
            table_csv = df_to_csv_for_llm(df)
            if do_insights:
                user_prompt = f"הנה הטבלה בפורמט CSV:\n{table_csv}\n\nהפק תובנות מרכזיות בעברית."
            else:
                user_prompt = f"שאלה: {user_q}\n\nהנה הטבלה בפורמט CSV (עד 400 שורות):\n{table_csv}\n\nענה בעברית וקשר לנתונים."
            with st.spinner("חושב..."):
                answer = call_openai(SYSTEM_ANALYST, user_prompt)
            st.markdown(answer)

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# ----- ADMIN PANEL -------
# =========================
admin_password = st.secrets.get("ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "admin123"))

st.markdown("---")
st.markdown('<div class="card">', unsafe_allow_html=True)

if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False

# כפתור התנתקות משתמש (לא מנהל)
logout_col1, logout_col2 = st.columns([4,1])
with logout_col1:
    st.caption("אם נכנסת לסניף שגוי, אפשר להתנתק ולבחור שוב.")
with logout_col2:
    if st.button("התנתק משתמש"):
        st.session_state.auth = {"role": None, "branch": None}
        st.rerun()

# בדיקת סיסמת מנהל
if not st.session_state.admin_logged_in:
    st.subheader("🔐 כניסה למנהל")
    col1, col2, col3 = st.columns([2,1,2])
    with col2:
        password_input = st.text_input("סיסמת מנהל:", type="password", key="admin_password")
        if st.button("התחבר", use_container_width=True):
            if password_input == admin_password:
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("סיסמה שגויה")
else:
    col1, col2 = st.columns([4,1])
    with col1:
        st.success("🔐 מחובר כמנהל")
    with col2:
        if st.button("התנתק מנהל"):
            st.session_state.admin_logged_in = False
            st.rerun()

st.markdown('</div>', unsafe_allow_html=True)

# חלק ייצוא - רק למנהלים
if st.session_state.get("admin_logged_in", False):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📥 ייצוא ומידע - אזור מנהל")

    # הורדת CSV
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ הורדת קובץ CSV", data=csv_bytes, file_name="food_quality_export.csv", mime="text/csv")

    # בדיקות חיבור ל-Google Sheets
    debug_info = []
    try:
        creds, sheet_url = _load_google_creds()
        debug_info.append(f"gspread זמין: {GSHEETS_AVAILABLE}")
        debug_info.append(f"google_service_account קיים: {bool(creds)}")
        debug_info.append(f"GOOGLE_SHEET_URL קיים: {bool(sheet_url)}")
        if creds:
            debug_info.append(f"client_email: {creds.get('client_email','חסר')}")
        sheets_configured = bool(GSHEETS_AVAILABLE and creds and sheet_url)
    except Exception as e:
        debug_info.append(f"שגיאה בקריאת קונפיג: {e}")
        sheets_configured = False

    if sheets_configured:
        st.success("📊 Google Sheets מחובר")
        st.markdown(f'<a href="{sheet_url}" target="_blank">🔗 פתח Google Sheet</a>', unsafe_allow_html=True)
    else:
        st.error("📊 Google Sheets לא מוגדר")

    with st.expander("🔍 מידע טכני"):
        for info in debug_info:
            st.text(info)
        with st.expander("הוראות הגדרה"):
            st.markdown("""
            **להגדרת Google Sheets:**
            1. צור Google Sheet חדש
            2. צור Service Account ב-Google Cloud Console
            3. הורד את קובץ ה-JSON
            4. אפשרות A (ענן/Secrets): הוסף ל-Streamlit Secrets:
               - `google_service_account` — JSON מלא
               - `GOOGLE_SHEET_URL` — קישור לגיליון
               אפשרות B (מקומית): שים את שני הערכים ב-.env:
               - `GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'`
               - `GOOGLE_SHEET_URL=https://...`
            5. שתף את הגיליון עם ה-`client_email` מהרשאות (Editor)
            """)

    st.markdown('</div>', unsafe_allow_html=True)
