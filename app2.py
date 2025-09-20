# app2.py — 🍜 ג'ירף מטבחים · איכויות אוכל
# רץ על Streamlit Cloud עם st.secrets בלבד (אין .env)
# תלות: streamlit, pandas, gspread, google-auth, (אופציונלי: openai)
# הרצה: streamlit run app2.py

from __future__ import annotations
import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st

# =============== Page & Styles ===============
st.set_page_config(page_title="🍜 ג'ירף מטבחים – איכויות אוכל", layout="wide")

st.markdown(
    """
<style>
.main .block-container { direction: rtl; font-family: "Rubik",-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
.sidebar .sidebar-content { direction: ltr !important; }
.card { background:#fff; border:1px solid #e9edf5; border-radius:16px; padding:18px; box-shadow:0 8px 20px rgba(16,24,40,.06); margin-bottom:16px; }
.status-bar { display:flex; align-items:center; justify-content:space-between; padding:12px 16px; border-radius:14px; margin:8px 0 16px; color:#fff; font-weight:700;
  box-shadow:0 8px 20px rgba(16,24,40,.08); border:1px solid rgba(255,255,255,.08);}
.status-bar.meta{background:linear-gradient(135deg,#0ea5e9,#2563eb);} .status-bar.branch{background:linear-gradient(135deg,#10b981,#059669);}
.status-bar .tag{padding:4px 10px; border-radius:999px; background:rgba(255,255,255,.18); font-weight:700;}
.stTextInput input, .stTextArea textarea { text-align:right; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="card" style="background:linear-gradient(135deg,#0f172a,#1f2937);color:#fff">
  <div style="font-size:26px;font-weight:900">🍜 ג'ירף מטבחים – איכויות אוכל</div>
  <div style="opacity:.9">טופס הזנה, מדדים, ושמירה אוטומטית לגיליון Google</div>
</div>
""",
    unsafe_allow_html=True,
)

# =============== App Settings ===============
BRANCHES: List[str] = ["חיפה", "ראשל״צ", "רמה״ח", "נס ציונה", "לנדמרק", "פתח תקווה", "הרצליה", "סביון"]
DISHES: List[str] = [
    "פאד תאי", "מלאזית", "פיליפינית", "אפגנית", "קארי דלעת", "סצ'ואן",
    "ביף רייס", "אורז מטוגן", "מאקי סלמון", "מאקי טונה", "ספייסי סלמון", "נודלס ילדים"
]
DB_PATH = "food_quality.db"
DUP_HOURS = 12
MIN_BRANCH_LEADER_N = 3
MIN_CHEF_TOP_M = 5

# =============== Database ===============
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

@st.cache_data(ttl=15)
def load_df() -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query(
        "SELECT id, branch, chef_name, dish_name, score, notes, created_at FROM food_quality ORDER BY created_at DESC",
        c,
    )
    c.close()
    return df

def refresh_df():
    load_df.clear()

# =============== Secrets Layer (Sheets + GPT) ===============
# --- Google Sheets (secrets only) ---
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

def _normalize_private_key(creds: dict) -> dict:
    """ממיר \\n לשורות אמיתיות אם המפתח הודבק כשורה אחת."""
    if not isinstance(creds, dict):
        return {}
    pk = creds.get("private_key")
    if isinstance(pk, str) and "\\n" in pk:
        creds = creds.copy()
        creds["private_key"] = pk.replace("\\n", "\n")
    return creds

def get_google_config() -> tuple[dict, Optional[str], Optional[str], Optional[str]]:
    """
    קורא אך ורק מתוך st.secrets:
      - [google_service_account]  (טבלה TOML עם JSON של Service Account)
      - GOOGLE_SHEET_ID או GOOGLE_SHEET_URL או GOOGLE_SHEET_TITLE (אחד מהם חובה)
      - GOOGLE_SHEET_WORKSHEET (לא חובה; ברירת מחדל: sheet1)
    """
    try:
        creds = dict(st.secrets["google_service_account"])
    except Exception:
        creds = {}
    creds = _normalize_private_key(creds) if creds else {}

    sheet_id    = st.secrets.get("GOOGLE_SHEET_ID")
    sheet_url   = st.secrets.get("GOOGLE_SHEET_URL")
    sheet_title = st.secrets.get("GOOGLE_SHEET_TITLE")  # למשל: "ג'ירף מטבחים בדיקת איכות"
    ws_name     = st.secrets.get("GOOGLE_SHEET_WORKSHEET") or "sheet1"
    # נחזיר title ב-slot של URL אם רק הוא קיים (פונקציית הפתיחה יודעת לטפל בזה)
    identifier = sheet_id or sheet_url or sheet_title
    return creds, identifier, ws_name, sheet_id  # נחזיר גם sheet_id לבדיקה בדיבוג

def _open_spreadsheet(gc, identifier: str):
    # מזהה יכול להיות: ID, URL או TITLE. ננסה לפי סדר:
    if identifier.startswith("http"):
        return gc.open_by_url(identifier)
    # אם זה נראה כמו ID (ללא רווחים/סלאשים) — ננסה by_key
    if "/" not in identifier and " " not in identifier:
        try:
            return gc.open_by_key(identifier)
        except Exception:
            # אולי זה לא ID — ננסה כותרת
            pass
    # אחרת ננסה לפי שם קובץ (דורש הרשאת Drive ולשים לב לדו־משמעות שמות)
    return gc.open(identifier)

def save_to_google_sheets(branch: str, chef: str, dish: str, score: int, notes: str, timestamp: str) -> bool:
    """כותב שורה ל-Google Sheets. מחזיר True/False ומציג סיבה ברורה במקרה כשל."""
    if not GSHEETS_AVAILABLE:
        st.warning("Google Sheets לא זמין (gspread/google-auth לא מותקנות).")
        return False

    creds, identifier, ws_name, _ = get_google_config()
    if not creds:
        st.warning("חסר [google_service_account] ב-st.secrets.")
        return False
    if not identifier:
        st.warning("חסר מזהה גיליון: GOOGLE_SHEET_ID או GOOGLE_SHEET_URL או GOOGLE_SHEET_TITLE ב-st.secrets.")
        return False

    try:
        credentials = Credentials.from_service_account_info(creds).with_scopes(SCOPES)
        gc = gspread.authorize(credentials)
        sh = _open_spreadsheet(gc, identifier)

        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            ws = sh.add_worksheet(title=ws_name, rows=1000, cols=12)

        ws.append_row([timestamp, branch, chef, dish, score, notes or ""],
                      value_input_option="USER_ENTERED")
        return True

    except Exception as e:
        st.warning(f"שגיאת Google Sheets: {e}")
        return False

# --- OpenAI (GPT) via secrets only ---
def get_openai_client():
    """יוצר ומחזיר לקוח OpenAI אם הוגדר מפתח. תומך בארגון/פרויקט מסיקרטס."""
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    if not api_key:
        return None, "לא הוגדר OPENAI_API_KEY ב-Secrets."
    org_id = st.secrets.get("OPENAI_ORG", "")
    project_id = st.secrets.get("OPENAI_PROJECT", "")

    try:
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if org_id:
            kwargs["organization"] = org_id
        if project_id:
            kwargs["project"] = project_id
        client = OpenAI(**kwargs)
        return client, None
    except Exception as e:
        return None, f"שגיאה ביצירת לקוח OpenAI: {e}"

# =============== Domain Logic ===============
def score_hint(x: int) -> str:
    return "😟 חלש" if x <= 3 else ("🙂 סביר" if x <= 6 else ("😀 טוב" if x <= 8 else "🤩 מצוין"))

def has_recent_duplicate(branch: str, chef: str, dish: str, hours: int = DUP_HOURS) -> bool:
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

def insert_record(branch: str, chef: str, dish: str, score: int, notes: str, submitted_by: Optional[str] = None):
    """שומר ל-SQLite ואז מנסה לשמור ל-Google Sheets (Secrets בלבד)."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # כתיבה ל-SQLite
    c = conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO food_quality (branch, chef_name, dish_name, score, notes, created_at, submitted_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (branch.strip(), chef.strip(), dish.strip(), int(score), (notes or "").strip(), timestamp, submitted_by),
    )
    c.commit()
    c.close()

    # כתיבה ל-Google Sheets
    ok = save_to_google_sheets(branch, chef, dish, score, notes, timestamp)
    st.toast("נשמר גם ל-Google Sheets ✅" if ok else "נשמר מקומית בלבד ℹ️", icon="✅" if ok else "ℹ️")

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

# =============== Auth (מסך כניסה) ===============
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

auth = require_auth()
role_class = "meta" if auth["role"] == "meta" else "branch"
branch_html = "" if auth["role"] == "meta" else f'— <span class="tag">{auth["branch"]}</span>'
st.markdown(
    f"""
<div class="status-bar {role_class}">
  <div>מצב עבודה: <span class="tag">{'מטה' if auth['role']=='meta' else 'סניף'}</span> {branch_html}</div>
  <div><span class="tag">אפשר להתנתק ולבחור סניף אחר בכל רגע</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# =============== Form ===============
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("✍️ הזנת בדיקת איכות חדשה")

colA, colB, colC = st.columns([1,1,1])
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
    score = st.selectbox(
        "ציון איכות *",
        options=list(range(1, 11)),
        index=7,
        format_func=lambda x: f"{x} - {score_hint(x)}"
    )
with colE:
    notes = st.text_area("הערות (לא חובה)", placeholder="מרקם, טמפרטורה, תיבול, עקביות...")

override = st.checkbox("שמור גם אם קיימת בדיקה דומה ב־12 השעות האחרונות (כפילויות)", key="override_dup")

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

# =============== KPIs ===============
df = load_df()
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 מדדי ביצוע")

best_branch, best_branch_count = kpi_best_branch_by_count(df)
current_branch_count = kpi_current_branch_count(df, selected_branch)
best_avg_branch, best_avg_value, best_avg_n = kpi_best_avg_branch(df, MIN_BRANCH_LEADER_N)
top_chef, top_chef_avg, top_chef_n = kpi_top_chef(df, MIN_CHEF_TOP_M)
top_dish, top_dish_count = kpi_top_dish(df)

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown("#### הסניף המוביל בבדיקות")
    if best_branch is None:
        st.write("אין נתונים")
    else:
        st.write(f"הנוכחי: **{current_branch_count}** | המוביל: **{best_branch}** — **{best_branch_count}**")

with k2:
    st.markdown("#### ממוצע ציון — נוכחי מול המוביל")
    if best_avg_branch is None:
        st.write("אין נתונים")
    else:
        cur_avg = df[df["branch"] == selected_branch]["score"].mean() if selected_branch else None
        cur_avg_str = f"{cur_avg:.2f}" if cur_avg is not None and not pd.isna(cur_avg) else '—'
        st.write(f"הנוכחי: **{cur_avg_str}** | המוביל: **{best_avg_branch}** ({best_avg_value:.2f})")
        if best_avg_n < MIN_BRANCH_LEADER_N:
            st.caption("הערה: הסניף המוביל לפי ממוצע עומד על מדגם קטן.")

with k3:
    st.markdown("#### הטבח המצטיין ברשת")
    if top_chef is None:
        st.write("אין נתונים")
    else:
        st.write(f"**{top_chef}** — ממוצע **{top_chef_avg:.2f}** (על סמך {top_chef_n} בדיקות)")
        if top_chef_n < MIN_CHEF_TOP_M:
            st.caption("מדגם קטן — מוצג המצטיין הזמין.")

with k4:
    st.markdown("#### המנה הכי נבחנת")
    if top_dish is None:
        st.write("אין נתונים")
    else:
        st.write(f"**{top_dish}** — {top_dish_count} בדיקות")

st.markdown('</div>', unsafe_allow_html=True)

# =============== GPT ANALYSIS (secrets only) ===============
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("🤖 ניתוח עם GPT")

gpt_client, gpt_err = get_openai_client()
if gpt_err:
    st.warning(f"GPT כבוי: {gpt_err}")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    # כפתור בדיקה (Ping)
    if st.button("🔎 בדיקת חיבור ל-GPT"):
        try:
            ping = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a ping responder."},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0.0,
            )
            txt = (ping.choices[0].message.content or "").strip()
            st.success(f"GPT מחובר. תשובה: {txt[:80]}")
        except Exception as e:
            st.error(f"שגיאת GPT: {e}")

    if df.empty:
        st.info("אין נתונים לניתוח עדיין.")
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        def df_to_csv_for_llm(df_in: pd.DataFrame, max_rows: int = 400) -> str:
            d = df_in.copy()
            if len(d) > max_rows:
                d = d.head(max_rows)
            return d.to_csv(index=False)

        SYSTEM_ANALYST = (
            "אתה אנליסט דאטה דובר עברית. מוצגת לך טבלה עם העמודות: "
            "id, branch, chef_name, dish_name, score, notes, created_at. "
            "ענה בתמציתיות, בעברית, עם דגשים והמלצות קצרות."
        )

        col_q, col_btns = st.columns([3, 1])
        with col_q:
            user_q = st.text_input("שאלה על הנתונים (לא חובה)")
        with col_btns:
            ask_btn = st.button("שלח")
        run_overview = st.button("ניתוח כללי")

        if run_overview or ask_btn:
            table_csv = df_to_csv_for_llm(df)
            if run_overview:
                user_prompt = (
                    f"הנה הטבלה בפורמט CSV:\n{table_csv}\n\n"
                    f"סכם מגמות, חריגים והמלצות קצרות לניהול."
                )
            else:
                user_prompt = (
                    f"שאלה: {user_q}\n\n"
                    f"הנה הטבלה בפורמט CSV (עד 400 שורות):\n{table_csv}\n\n"
                    f"ענה בעברית, תן נימוק קצר לכל מסקנה."
                )

            with st.spinner("מנתח..."):
                try:
                    resp = gpt_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": SYSTEM_ANALYST},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                    )
                    answer = (resp.choices[0].message.content or "").strip()
                    st.write(answer)
                except Exception as e:
                    st.error(f"שגיאת GPT: {e}")

    st.markdown('</div>', unsafe_allow_html=True)

# =============== Admin Panel ===============
admin_password = st.secrets.get("ADMIN_PASSWORD", "admin123")

st.markdown("---")
st.markdown('<div class="card">', unsafe_allow_html=True)

if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False

# התנתקות משתמש (לא מנהל)
c1, c2 = st.columns([4,1])
with c1:
    st.caption("לחזרה למסך כניסה: התנתק משתמש.")
with c2:
    if st.button("התנתק משתמש"):
        st.session_state.auth = {"role": None, "branch": None}
        st.rerun()

# כניסת מנהל
if not st.session_state.admin_logged_in:
    st.subheader("🔐 כניסה למנהל")
    x1, x2, x3 = st.columns([2,1,2])
    with x2:
        pwd = st.text_input("סיסמת מנהל:", type="password", key="admin_password")
        if st.button("התחבר", use_container_width=True):
            if pwd == admin_password:
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("סיסמה שגויה")
else:
    y1, y2 = st.columns([4,1])
    with y1:
        st.success("מחובר כמנהל")
    with y2:
        if st.button("התנתק מנהל"):
            st.session_state.admin_logged_in = False
            st.rerun()

st.markdown('</div>', unsafe_allow_html=True)

# אזור מנהל — ייצוא ובדיקות
if st.session_state.get("admin_logged_in", False):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📥 ייצוא ומידע – אזור מנהל")

    df_all = load_df()
    csv_bytes = df_all.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ הורדת CSV", data=csv_bytes, file_name="food_quality_export.csv", mime="text/csv")

    # דיבוג Sheets
    debug = []
    try:
        creds, identifier, ws_name, sheet_id_only = get_google_config()
        debug.append(f"gspread זמין: {GSHEETS_AVAILABLE}")
        debug.append(f"credentials קיימים: {bool(creds)}")
        debug.append(f"Worksheet: {ws_name}")
        ident_kind = "ID" if sheet_id_only else ("URL/Title" if identifier else "—")
        debug.append(f"מזהה גיליון ({ident_kind}): {bool(identifier)}")
        if creds:
            debug.append(f"client_email: {creds.get('client_email','חסר')}")
    except Exception as e:
        debug.append(f"שגיאת קונפיג: {e}")

    cols = st.columns(2)
    with cols[0]:
        if st.button("🧪 בדיקת כתיבה ל-Sheets (PING)"):
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            ok = save_to_google_sheets("DEBUG", "PING", "PING", 0, "בדיקת מערכת", ts)
            st.success("✅ נכתב לגיליון") if ok else st.error("❌ הכתיבה נכשלה")
    with cols[1]:
        if gpt_client:
            if st.button("🧪 בדיקת GPT (PING)"):
                try:
                    ping = gpt_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": "ping"}],
                        temperature=0.0,
                    )
                    st.success("✅ GPT מחובר")
                except Exception as e:
                    st.error(f"❌ GPT שגיאה: {e}")
        else:
            st.info("GPT לא הוגדר (אין OPENAI_API_KEY).")

    with st.expander("🔍 מידע טכני"):
        for line in debug:
            st.text(line)

    st.markdown('</div>', unsafe_allow_html=True)
