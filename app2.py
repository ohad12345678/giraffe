# app2.py — ג'ירף מטבחים · איכויות אוכל (גרסה מינימלית)
# דרישות: streamlit, pandas, python-dotenv
# אופציונלי: gspread, google-auth
# הרצה: streamlit run app2.py

from __future__ import annotations
import os, json, sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ===== Optional Google Sheets =====
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

# =========================
# ------- SETTINGS --------
# =========================
st.set_page_config(page_title="ג'ירף מטבחים – איכויות אוכל", layout="wide")
load_dotenv()

# סניפים (כולל סביון)
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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# =========================
# ---------- STYLE --------
# =========================
st.markdown("""
<style>
:root{
  --bg:#f7f8fa; --surface:#ffffff; --text:#0f172a; --muted:#6b7280;
  --border:#e6e8ef; --primary:#0ea5a4; --primary-weak:#d1fae5;
}
html,body,.main{background:var(--bg);}
html, body, .main, .block-container, .sidebar .sidebar-content{direction:rtl;}
.main .block-container{font-family:"Rubik",-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}

/* Header מינימלי */
.header-min{background:var(--surface); border:1px solid var(--border);
  border-radius:18px; padding:18px; box-shadow:0 4px 18px rgba(10,20,40,.04); margin-bottom:14px;}
.header-min .title{font-size:26px; font-weight:900; color:var(--text); margin:0;}
.header-min .sub{display:none;} /* הוסר הטקסט המשני */

/* כרטיס סטנדרטי */
.card{background:var(--surface); border:1px solid var(--border); border-radius:16px;
  padding:16px; box-shadow:0 4px 18px rgba(10,20,40,.04); margin-bottom:12px;}

/* Status bar מינימלי */
.status-min{display:flex; align-items:center; gap:10px; background:var(--surface);
  border:1px solid var(--border); border-radius:14px; padding:10px 12px;}
.chip{padding:4px 10px; border:1px solid var(--border); border-radius:999px; font-weight:800; font-size:12px; color:var(--text); background:#fbfbfd}

/* קלטים */
.stTextInput input, .stTextArea textarea{background:#fff !important; color:var(--text) !important;
  border-radius:12px !important; border:1px solid var(--border) !important;}
.stSelectbox div[data-baseweb="select"]{background:#fff !important; color:var(--text) !important;
  border-radius:12px !important; border:1px solid var(--border) !important;}
.stTextInput label, .stTextArea label, .stSelectbox label{color:var(--text) !important; font-weight:800 !important;}
/* פוקוס דק ועדין */
.stTextInput input:focus, .stTextArea textarea:focus, .stSelectbox [data-baseweb="select"]:focus-within{
  outline:none !important; box-shadow:0 0 0 2px rgba(14,165,164,.18) !important; border-color:var(--primary) !important;}

/* כפתור ראשי נקי */
.stButton>button{
  background:var(--primary) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
  padding:10px 14px !important; font-weight:900 !important; box-shadow:0 4px 16px rgba(14,165,164,.25) !important;}
.stButton>button:hover{filter:saturate(1.05) brightness(1.02);}

/* הסתרת הודעת “Press Enter to submit/apply” */
div[data-testid="stWidgetInstructions"]{display:none !important;}

/* KPI מינימלי – מספרים בלבד בתוך הקוביה */
.kpi-title{font-weight:900; color:var(--text); font-size:15px; margin:0 0 8px;}
.kpi-min{background:#fff; border:1px solid var(--border); border-radius:14px; padding:14px;
  box-shadow:0 4px 16px rgba(10,20,40,.05);}
.kpi-body{display:flex; align-items:baseline; justify-content:center; gap:14px;}
.kpi-num{font-size:38px; font-weight:900; color:var(--text); font-variant-numeric:tabular-nums;}
.kpi-sep{width:1px; height:22px; background:var(--border);}

/* מובייל */
@media (max-width:480px){
  .kpi-num{font-size:42px}
  .main .block-container{padding-left:12px; padding-right:12px;}
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="header-min">
  <p class="title">ג'ירף מטבחים – איכויות אוכל</p>
  <p class="sub">טופס הזנת בדיקות איכות + KPI מספריים</p>
</div>
""", unsafe_allow_html=True)

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
    """שמירה ב-Google Sheets (אם הגדרות קיימות)."""
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
        credentials = Credentials.from_service_account_info(creds).with_scopes(SCOPES)
        gc = gspread.authorize(credentials)
        sheet = gc.open_by_url(sheet_url).sheet1
        sheet.append_row([timestamp, branch, chef, dish, score, notes or ""])
    except Exception as e:
        st.warning(f"Google Sheets: {e}")

def refresh_df():
    load_df.clear()

def score_hint(x: int) -> str:
    return "חלש" if x <= 3 else ("סביר" if x <= 6 else ("טוב" if x <= 8 else "מצוין"))

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

# פורמט מספרים
def _fmt(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "—"
    try:
        if abs(v - int(v)) < 1e-9:
            return f"{int(v)}"
        return f"{float(v):.{decimals}f}"
    except Exception:
        return "—"

# רנדר KPI מינימלי
def render_kpi_min(title: str, left_value: Optional[float], right_value: Optional[float], decimals: int = 2):
    st.markdown(f'<div class="kpi-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="kpi-min">
          <div class="kpi-body">
            <div class="kpi-num">{_fmt(left_value, decimals)}</div>
            <div class="kpi-sep"></div>
            <div class="kpi-num">{_fmt(right_value, decimals)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# =========================
# ------ LOGIN & CONTEXT --
# =========================
def require_auth() -> dict:
    """מסך כניסה: 'סניף' (בחירת שם סניף) או 'מטה' (ללא סיסמה)."""
    if "auth" not in st.session_state:
        st.session_state.auth = {"role": None, "branch": None}
    auth = st.session_state.auth

    if not auth["role"]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.write("בחרו מצב עבודה:")
        role = st.radio("", options=["סניף", "מטה"], horizontal=True, index=0, label_visibility="collapsed")

        if role == "סניף":
            branch_choice = st.selectbox("שם סניף", options=["— בחר —"] + BRANCHES, index=0)
            if st.button("המשך"):
                if branch_choice == "— בחר —":
                    st.error("בחרו סניף כדי להמשיך.")
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

# Status bar מינימלי — רק שם הסניף או "מטה"
if auth["role"] == "branch":
    st.markdown(f'<div class="status-min"><span class="chip">{auth["branch"]}</span></div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="status-min"><span class="chip">מטה</span></div>', unsafe_allow_html=True)

# =========================
# ---------- FORM ---------
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
with st.form("quality_form", clear_on_submit=False):
    colA, colB, colC = st.columns([1,1,1])

    if auth["role"] == "meta":
        with colA:
            selected_branch = st.selectbox("שם סניף *", options=BRANCHES, index=0)
    else:
        selected_branch = auth["branch"]
        with colA:
            st.text_input("שם סניף", value=selected_branch, disabled=True)

    # שם הטבח — ללא placeholder וללא הודעת "Press Enter…"
    with colB:
        chef = st.text_input("שם הטבח *")

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
        notes = st.text_area("הערות (לא חובה)")

    submitted = st.form_submit_button("שמור בדיקה")

if submitted:
    if not selected_branch or not chef.strip() or not dish:
        st.error("חובה לבחור/להציג סניף, להזין שם טבח ולבחור מנה.")
    else:
        insert_record(selected_branch, chef, dish, score, notes, submitted_by=auth["role"])
        st.success(f"נשמר: {selected_branch} · {chef} · {dish} • ציון {score}")
        refresh_df()
        st.balloons()
st.markdown('</div>', unsafe_allow_html=True)

# =========================
# --------- KPI'S ---------
# =========================
df = load_df()
st.markdown('<div class="card">', unsafe_allow_html=True)

if df.empty:
    st.info("אין נתונים להצגה עדיין.")
else:
    # ממוצעים רשת/סניף
    net_avg = network_avg(df)
    br_avg = branch_avg(df, selected_branch) if selected_branch else None

    # ממוצעי מנה
    net_dish_avg = dish_avg_network(df, dish) if dish else None
    br_dish_avg = dish_avg_branch(df, selected_branch, dish) if (selected_branch and dish) else None

    # טבח מצטיין
    chef_name, chef_avg, chef_n = top_chef_network(df, MIN_CHEF_TOP_M)

    # KPI 1 — ממוצע ציון: רשת | {branch}
    render_kpi_min(
        title=f"ממוצע ציון — רשת | {selected_branch}",
        left_value=net_avg,
        right_value=br_avg,
        decimals=2
    )
    st.markdown("<br/>", unsafe_allow_html=True)

    # KPI 2 — ממוצע ציון למנה: רשת | {branch}
    render_kpi_min(
        title=f"ממוצע ציון למנה \"{dish}\" — רשת | {selected_branch}",
        left_value=net_dish_avg,
        right_value=br_dish_avg,
        decimals=2
    )
    st.markdown("<br/>", unsafe_allow_html=True)

    # KPI 3 — הטבח המצטיין (שם בכותרת; בקוביה: ממוצע | N)
    chef_title = "הטבח המצטיין ברשת" + (f" — {chef_name}" if chef_name else "")
    render_kpi_min(
        title=chef_title,
        left_value=chef_avg,
        right_value=int(chef_n) if chef_n else None,
        decimals=2
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

# התנתקות משתמש (כדי לבחור סניף/מצב מחדש)
c1, c2 = st.columns([4,1])
with c1:
    st.caption("לחזרה למסך כניסה: התנתק משתמש.")
with c2:
    if st.button("התנתק משתמש"):
        st.session_state.auth = {"role": None, "branch": None}
        st.rerun()

# כניסת מנהל
if not st.session_state.admin_logged_in:
    st.write("כניסה למנהל")
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
    st.write("ייצוא ומידע")

    df_all = load_df()
    csv_bytes = df_all.to_csv(index=False).encode("utf-8")
    st.download_button("הורדת CSV", data=csv_bytes, file_name="food_quality_export.csv", mime="text/csv")

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
        st.success("Google Sheets מחובר")
        st.markdown(f'<a href="{sheet_url}" target="_blank">פתח Google Sheet</a>', unsafe_allow_html=True)
    else:
        st.error("Google Sheets לא מוגדר")

    with st.expander("מידע טכני"):
        for info in debug_info:
            st.text(info)
        with st.expander("הוראות הגדרה"):
            st.markdown("""
            1) צור/פתח Google Sheet  
            2) צור Service Account ב-Google Cloud והורד JSON  
            3) הוסף ל-Secrets/.env:  
               - GOOGLE_SHEET_URL=...  
               - GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'  
            4) שתף את הגיליון עם ה-client_email בהרשאת Editor
            """)

    st.markdown('</div>', unsafe_allow_html=True)
