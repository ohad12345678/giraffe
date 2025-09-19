# app2.py — 🍜 ג'ירף מטבחים – איכויות אוכל
# דרישות חובה: streamlit, pandas, python-dotenv
# אופציונלי: gspread, google-auth
# הרצה: streamlit run app2.py

from __future__ import annotations
import os, json, sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# נסה לייבא Google Sheets (לא חובה)
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
load_dotenv()

# סניפים (נוספה "סביון")
BRANCHES: List[str] = ["חיפה", "ראשל״צ", "רמה״ח", "נס ציונה", "לנדמרק", "פתח תקווה", "הרצליה", "סביון"]

# מנות
DISHES: List[str] = [
    "פאד תאי", "מלאזית", "פיליפינית", "אפגנית",
    "קארי דלעת", "סצ'ואן", "ביף רייס",
    "אורז מטוגן", "מאקי סלמון", "מאקי טונה",
    "ספייסי סלמון", "נודלס ילדים"
]

DB_PATH = "food_quality.db"
MIN_CHEF_TOP_M = 5
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
/* רקע כללי - סגול עמוק */
html, body, .main { background:#2f1c46; }
html, body, .main, .block-container, .sidebar .sidebar-content { direction: rtl; }
.main .block-container{ font-family:"Rubik", -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }

/* Header זכוכיתי עם גרדיינט */
.header-wrap{
  position:relative; overflow:hidden;
  background:linear-gradient(135deg,#3b2460 0%, #4a2b77 60%, #36205a 100%);
  color:#fff; padding:26px 22px; border-radius:22px;
  border:1px solid rgba(255,255,255,.10); box-shadow:0 25px 70px rgba(0,0,0,.35); margin-bottom:18px;
}
.header-title{ font-size:30px; font-weight:900; margin:0 0 6px; }
.header-sub{ color:#e5e7eb; font-size:14px; margin:0; opacity:.9 }

/* כרטיסים לבנים מרחפים */
.card{
  background:#ffffff; color:#0f172a;
  border:1px solid #e7e8f2; border-radius:20px;
  padding:18px; box-shadow:0 24px 60px rgba(12,16,39,.22); margin-bottom:16px;
}

/* פס סטטוס */
.status-bar{
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  background:linear-gradient(135deg,#fff,#f7f8fb); color:#0f172a;
  border:1px solid #e7e8f2; border-radius:16px; padding:12px 16px;
  box-shadow:0 16px 40px rgba(12,16,39,.12);
}
.status-bar .tag{ padding:6px 12px; border-radius:999px; background:#efe9ff; color:#2f1c46; font-weight:800; }

/* שדות טופס — שחור על אפור עדין, ללא placeholder ב'שם הטבח' */
.stTextInput input, .stTextArea textarea{
  color:#0f172a !important; background:#f3f5f9 !important; border-radius:14px !important;
}
.stSelectbox div[data-baseweb="select"]{
  color:#0f172a !important; background:#f3f5f9 !important; border-radius:14px !important;
}
.stTextInput label, .stTextArea label, .stSelectbox label{
  color:#0b1220 !important; font-weight:800 !important;
}

/* כפתור ראשי */
.stButton>button{
  background:linear-gradient(135deg,#ff8a00,#ffbf47) !important; color:#0b1020 !important;
  border:0 !important; border-radius:14px !important; padding:10px 16px !important;
  font-weight:900 !important; box-shadow:0 14px 40px rgba(255,165,0,.35) !important;
  transition: transform .08s ease-in-out;
}
.stButton>button:hover{ transform: translateY(-1px) scale(1.01); }

/* KPI — מספרים בלבד */
.kpi{
  display:flex; align-items:center; justify-content:center;
  height:110px; border-radius:16px; border:1px solid #eceef6;
  background:#ffffff; box-shadow:0 16px 40px rgba(12,16,39,.12);
  font-size:34px; font-weight:900; color:#0f172a;
  font-variant-numeric: tabular-nums;
}
.kpi-title{ font-weight:900; color:#0f172a; margin-bottom:6px; }
.kpi-sub{ color:#6b7280; font-size:12px; margin-top:-6px; margin-bottom:10px; }

/* מפריד אנכי קטן בין שני ערכים בקוביה */
.sep{
  display:inline-block; width:1px; height:22px; background:#e6e8ee; margin:0 12px; vertical-align:middle;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="header-wrap">
  <div class="header-title">🍜 ג'ירף מטבחים – איכויות אוכל</div>
  <div class="header-sub">טופס הזנת בדיקות איכות + KPI מספריים</div>
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
    """שומר ל-SQLite ול-Google Sheets (אם קיים). אין בדיקת כפילויות."""
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
        st.warning(f"נשמר מקומית, אך לא לגיליון: {e}")

def save_to_google_sheets(branch: str, chef: str, dish: str, score: int, notes: str, timestamp: str):
    """שמירה ב-Google Sheets (אם הוגדר)."""
    if not GSHEETS_AVAILABLE:
        return
    sheet_url = st.secrets.get("GOOGLE_SHEET_URL", "") or os.getenv("GOOGLE_SHEET_URL", "")
    creds = st.secrets.get("google_service_account", {})
    if not creds:
        env_json = os.getenv("GOOGLE_SERVICE_ACCOUNT", "")
        if env_json:
            try:
                creds = json.loads(env_json)
            except Exception:
                pass
    if not (sheet_url and creds):
        return
    try:
        credentials = Credentials.from_service_account_info(creds).with_scopes(
            ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        gc = gspread.authorize(credentials)
        sheet = gc.open_by_url(sheet_url).sheet1
        sheet.append_row([timestamp, branch, chef, dish, score, notes or ""])
    except Exception as e:
        st.warning(f"Google Sheets: {e}")

def refresh_df():
    load_df.clear()

def score_hint(x: int) -> str:
    return "😟 חלש" if x <= 3 else ("🙂 סביר" if x <= 6 else ("😀 טוב" if x <= 8 else "🤩 מצוין"))

# KPI חישובים
def network_avg(df: pd.DataFrame) -> Optional[float]:
    return float(df["score"].mean()) if not df.empty else None

def branch_avg(df: pd.DataFrame, branch: str) -> Optional[float]:
    d = df[df["branch"] == branch]
    return float(d["score"].mean()) if not d.empty else None

def dish_avg_network(df: pd.DataFrame, dish: str) -> Optional[float]:
    d = df[df["dish_name"] == dish]
    return float(d["score"].mean()) if not d.empty else None

def dish_avg_branch(df: pd.DataFrame, branch: str, dish: str) -> Optional[float]:
    d = df[(df["branch"] == branch) & (df["dish_name"] == dish)]
    return float(d["score"].mean()) if not d.empty else None

def top_chef_network(df: pd.DataFrame, min_n: int = MIN_CHEF_TOP_M) -> Tuple[Optional[str], Optional[float], int]:
    if df.empty:
        return None, None, 0
    g = df.groupby("chef_name").agg(n=("id","count"), avg=("score","mean")).reset_index()
    g = g.sort_values(["n","avg"], ascending=[False, False])
    qual = g[g["n"] >= min_n]
    pick = qual.iloc[0] if not qual.empty else g.iloc[0]
    return str(pick["chef_name"]), float(pick["avg"]), int(pick["n"])

# =========================
# ------ LOGIN & CONTEXT --
# =========================
def require_auth() -> dict:
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

# פס סטטוס
role_txt = 'מטה' if auth['role']=='meta' else 'סניף'
branch_html = "" if auth["role"] == "meta" else f'— <span class="tag">{auth["branch"]}</span>'
st.markdown(
    f"""
<div class="status-bar">
  <div>אתה עובד כעת במצב <span class="tag">{role_txt}</span> {branch_html}</div>
  <div><span class="tag">ניתן להתנתק ולבחור סניף אחר</span></div>
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

# בסביבת מטה בוחרים סניף, בסניף פשוט מוצג ערך
if auth["role"] == "meta":
    with colA:
        selected_branch = st.selectbox("סניף *", options=BRANCHES, index=0)
else:
    selected_branch = auth["branch"]
    with colA:
        st.text_input("סניף", value=selected_branch, disabled=True)

# שם הטבח — בלי placeholder
with colB:
    chef = st.text_input("שם הטבח *", placeholder="")

with colC:
    dish = st.selectbox("שם המנה *", options=DISHES, index=0)

colD, colE = st.columns([1,1])
with colD:
    score = st.selectbox("ציון איכות *", options=list(range(1, 11)), index=7,
                         format_func=lambda x: f"{x} - {score_hint(x)}")
with colE:
    notes = st.text_area("הערות (לא חובה)", placeholder="")  # אפשר גם בלי placeholder; השארתי ריק

save_col1, _ = st.columns([1,3])
with save_col1:
    save = st.button("💾 שמור בדיקה", type="primary")

if save:
    if not selected_branch or not chef.strip() or not dish:
        st.error("חובה לבחור/להציג סניף, להזין שם טבח ולבחור מנה.")
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
st.subheader("📊 מדדי KPI (מספרים בלבד)")

if df.empty:
    st.info("אין נתונים להצגה עדיין.")
else:
    net_avg = network_avg(df)
    br_avg = branch_avg(df, selected_branch) if selected_branch else None
    net_dish_avg = dish_avg_network(df, dish) if dish else None
    br_dish_avg = dish_avg_branch(df, selected_branch, dish) if (selected_branch and dish) else None
    chef_name, chef_avg, chef_n = top_chef_network(df, MIN_CHEF_TOP_M)

    # KPI 1 — ממוצע רשת | סניף
    k1c1, k1c2, k1c3 = st.columns([1,2,1])
    with k1c2:
        st.markdown('<div class="kpi-title">ממוצע ציון — רשת | סניף</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="kpi">{(f"{net_avg:.2f}" if net_avg is not None else "—")}<span class="sep"></span>{(f"{br_avg:.2f}" if br_avg is not None else "—")}</div>',
            unsafe_allow_html=True
        )

    # KPI 2 — ממוצע ציון למנה: רשת | סניף
    if dish:
        k2c1, k2c2, k2c3 = st.columns([1,2,1])
        with k2c2:
            st.markdown(f'<div class="kpi-title">ממוצע ציון למנה "{dish}" — רשת | סניף</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="kpi">{(f"{net_dish_avg:.2f}" if net_dish_avg is not None else "—")}<span class="sep"></span>{(f"{br_dish_avg:.2f}" if br_dish_avg is not None else "—")}</div>',
                unsafe_allow_html=True
            )

    # KPI 3 — הטבח המצטיין ברשת (שם למעלה; בקוביה: ממוצע | N)
    k3c1, k3c2, k3c3 = st.columns([1,2,1])
    with k3c2:
        title = "הטבח המצטיין ברשת" + (f" — {chef_name}" if chef_name else "")
        st.markdown(f'<div class="kpi-title">{title}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="kpi">{(f"{chef_avg:.2f}" if chef_avg is not None else "—")}<span class="sep"></span>{(chef_n if chef_n else "—")}</div>',
            unsafe_allow_html=True
        )

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# ----- ADMIN PANEL -------
# =========================
admin_password = st.secrets.get("ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "admin123"))

st.markdown("---")
st.markdown('<div class="card">', unsafe_allow_html=True)

if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False

# התנתקות משתמש
c1, c2 = st.columns([4,1])
with c1:
    st.caption("התנתקות תאפשר לבחור מצב/סניף מחדש.")
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
        st.success("🔐 מחובר כמנהל")
    with y2:
        if st.button("התנתק מנהל"):
            st.session_state.admin_logged_in = False
            st.rerun()

st.markdown('</div>', unsafe_allow_html=True)

# אזור מנהל — ייצוא ובדיקות
if st.session_state.get("admin_logged_in", False):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📥 ייצוא ומידע - אזור מנהל")

    df_all = load_df()
    csv_bytes = df_all.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ הורדת קובץ CSV", data=csv_bytes, file_name="food_quality_export.csv", mime="text/csv")

    debug_info = []
    try:
        sheet_url = st.secrets.get("GOOGLE_SHEET_URL", "") or os.getenv("GOOGLE_SHEET_URL", "")
        creds_present = bool(st.secrets.get("google_service_account", {})) or bool(os.getenv("GOOGLE_SERVICE_ACCOUNT", ""))
        debug_info.append(f"gspread זמין: {GSHEETS_AVAILABLE}")
        debug_info.append(f"google_service_account קיים: {creds_present}")
        debug_info.append(f"GOOGLE_SHEET_URL קיים: {bool(sheet_url)}")
        if creds_present:
            try:
                creds = st.secrets.get("google_service_account", {})
                if not creds:
                    creds = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT"))
                debug_info.append(f"client_email: {creds.get('client_email','חסר')}")
            except Exception as e:
                debug_info.append(f"שגיאה בקריאת JSON: {e}")
        sheets_ok = bool(GSHEETS_AVAILABLE and creds_present and sheet_url)
    except Exception as e:
        debug_info.append(f"שגיאת קונפיג: {e}")
        sheets_ok = False

    if sheets_ok:
        st.success("📊 Google Sheets מחובר")
        st.markdown(f'<a href="{sheet_url}" target="_blank">🔗 פתח Google Sheet</a>', unsafe_allow_html=True)
    else:
        st.error("📊 Google Sheets לא מוגדר")

    with st.expander("🔍 מידע טכני"):
        for info in debug_info:
            st.text(info)
        with st.expander("הוראות הגדרה"):
            st.markdown("""
            1) צור/פתח Google Sheet.  
            2) צור Service Account ב-Google Cloud והורד JSON.  
            3) ב-Secrets או .env:  
               - GOOGLE_SHEET_URL=...  
               - GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'  
            4) שתף את הגיליון עם ה-client_email בהרשאת Editor.
            """)

    st.markdown('</div>', unsafe_allow_html=True)
