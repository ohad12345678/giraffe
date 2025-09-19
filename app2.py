# app2.py — ג'ירף מטבחים · איכויות אוכל (מינימלי + KPI גרפיים + GPT)
# דרישות: streamlit, pandas, python-dotenv, matplotlib
# אופציונלי: gspread, google-auth
# הרצה: streamlit run app2.py

from __future__ import annotations
import os, json, sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
import matplotlib.pyplot as plt

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

# צבעים לגרפים (כחול בהיר וירוק בהיר)
COLOR_NET = "#93C5FD"    # light blue
COLOR_BRANCH = "#9AE6B4"  # light green

# =========================
# ---------- STYLE --------
# =========================
st.markdown("""
<style>
:root{
  --bg:#f7f8fa; --surface:#ffffff; --text:#0f172a; --muted:#6b7280;
  --border:#e6e8ef; --primary:#0ea5a4;
  --mint-50:#ecfdf5; --mint-100:#d1fae5; --mint-700:#0d6b62;
}
html,body,.main{background:var(--bg);}
html, body, .main, .block-container, .sidebar .sidebar-content{direction:rtl;}
.main .block-container{font-family:"Rubik",-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}

/* Header ירקרק עדין */
.header-min{
  background:linear-gradient(135deg, var(--mint-50) 0%, #ffffff 70%);
  border:1px solid var(--mint-100); border-radius:18px; padding:18px;
  box-shadow:0 6px 22px rgba(13,107,98,.08); margin-bottom:14px;
}
.header-min .title{font-size:26px; font-weight:900; color:var(--mint-700); margin:0;}
.header-min .sub{display:none;} /* לא מציגים טקסט משנה */

/* כרטיס סטנדרטי */
.card{background:var(--surface); border:1px solid var(--border); border-radius:16px;
  padding:16px; box-shadow:0 4px 18px rgba(10,20,40,.04); margin-bottom:12px;}

/* Status bar מינימלי */
.status-min{display:flex; align-items:center; gap:10px; background:#fff;
  border:1px solid var(--border); border-radius:14px; padding:10px 12px;}
.chip{padding:4px 10px; border:1px solid var(--mint-100); border-radius:999px;
  font-weight:800; font-size:12px; color:var(--mint-700); background:var(--mint-50)}

/* קלטים */
.stTextInput input, .stTextArea textarea{background:#fff !important; color:var(--text) !important;
  border-radius:12px !important; border:1px solid var(--border) !important;}
.stSelectbox div[data-baseweb="select"]{background:#fff !important; color:var(--text) !important;
  border-radius:12px !important; border:1px solid var(--border) !important;}
.stTextInput label, .stTextArea label, .stSelectbox label{color:var(--text) !important; font-weight:800 !important;}
.stTextInput input:focus, .stTextArea textarea:focus, .stSelectbox [data-baseweb="select"]:focus-within{
  outline:none !important; box-shadow:0 0 0 2px rgba(14,165,164,.18) !important; border-color:var(--primary) !important;}

/* כפתור ראשי */
.stButton>button{
  background:var(--primary) !important; color:#fff !important; border:0 !important;
  border-radius:12px !important; padding:10px 14px !important; font-weight:900 !important;
  box-shadow:0 4px 16px rgba(14,165,164,.25) !important;}
.stButton>button:hover{filter:saturate(1.05) brightness(1.02);}

/* הסתרת “Press Enter to submit/apply” */
div[data-testid="stWidgetInstructions"]{display:none !important;}

/* KPI לטבח מצטיין — מספר יחיד */
.kpi-title{font-weight:900; color:var(--text); font-size:15px; margin:0 0 8px;}
.kpi-min{background:#fff; border:1px solid var(--border); border-radius:14px; padding:14px;
  box-shadow:0 4px 16px rgba(10,20,40,.05);}
.kpi-single-num{font-size:42px; font-weight:900; color:var(--text); text-align:center;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="header-min">
  <p class="title">ג'ירף מטבחים – איכויות אוכל</p>
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
    """שומר ל-SQLite ול-Google Sheets (אם קיים)."""
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
        credentials = Credentials.from_service_account_info(creds)
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

def top_chef_network_with_branch(df: pd.DataFrame, min_n: int = MIN_CHEF_TOP_M) -> Tuple[Optional[str], Optional[str], Optional[float], int]:
    """הטבח המצטיין + הסניף הדומיננטי עבורו, ממוצע ונפח."""
    if df.empty:
        return None, None, None, 0
    g = df.groupby("chef_name").agg(n=("id","count"), avg=("score","mean")).reset_index()
    g = g.sort_values(["n","avg"], ascending=[False, False])
    qual = g[g["n"] >= min_n]
    pick = qual.iloc[0] if not qual.empty else g.iloc[0]
    chef = str(pick["chef_name"])
    avg = float(pick["avg"])
    n = int(pick["n"])
    mode_branch = df[df["chef_name"] == chef]["branch"].value_counts().idxmax()
    return chef, mode_branch, avg, n

# =========================
# ------ LOGIN & CONTEXT --
# =========================
def require_auth() -> dict:
    """מסך כניסה: 'סניף' (בחירת סניף) או 'מטה' (ללא סיסמה)."""
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

# Status bar — רק שם הסניף או "מטה"
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

    with colB:
        chef = st.text_input("שם הטבח *")  # ללא placeholder

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

def bar_compare(title: str, labels: list[str], values: list[float], colors: list[str]):
    """גרף עמודות קטן ונקי להשוואה בין שני ערכים."""
    fig, ax = plt.subplots(figsize=(4.8, 3.0), dpi=160)
    bars = ax.bar(labels, values, color=colors, width=0.55)
    for b in bars:
        height = b.get_height()
        ax.text(b.get_x() + b.get_width()/2, height + 0.05, f"{height:.2f}",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, max(values) * 1.25 if values and max(values) > 0 else 1)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle=':', alpha=0.35)
    ax.set_ylabel("") ; ax.set_xlabel("")
    st.markdown(f"**{title}**")
    st.pyplot(fig, clear_figure=True)

if df.empty:
    st.info("אין נתונים להצגה עדיין.")
else:
    # ממוצעי רשת/סניף
    net_avg = network_avg(df)
    br_avg = branch_avg(df, selected_branch) if selected_branch else None

    # ממוצעי מנה
    net_dish_avg = dish_avg_network(df, dish) if dish else None
    br_dish_avg = dish_avg_branch(df, selected_branch, dish) if (selected_branch and dish) else None

    # 1) גרף השוואה — ממוצע ציון רשת מול הסניף
    if net_avg is not None and br_avg is not None:
        bar_compare(
            title=f"ממוצע ציון — השוואה רשת מול {selected_branch}",
            labels=["רשת", selected_branch],
            values=[net_avg, br_avg],
            colors=[COLOR_NET, COLOR_BRANCH],
        )
    else:
        st.info("אין מספיק נתונים להצגת ממוצע ציון רשת/סניף.")

    st.markdown("<hr style='border:none;border-top:1px solid #e6e8ef;margin:14px 0'/>", unsafe_allow_html=True)

    # 2) גרף השוואה — ממוצע ציון למנה (רשת מול הסניף)
    if net_dish_avg is not None and br_dish_avg is not None:
        bar_compare(
            title=f"ממוצע ציון למנה \"{dish}\" — רשת מול {selected_branch}",
            labels=["רשת · מנה", f"{selected_branch} · מנה"],
            values=[net_dish_avg, br_dish_avg],
            colors=[COLOR_NET, COLOR_BRANCH],
        )
    else:
        st.info("אין מספיק נתונים למנה הנבחרת להצגת השוואה.")

    st.markdown("<hr style='border:none;border-top:1px solid #e6e8ef;margin:14px 0'/>", unsafe_allow_html=True)

    # 3) הטבח המצטיין — שם טבח + שם מסעדה (סניף) + ממוצע, ללא השוואה
    chef_name, chef_branch, chef_avg, chef_n = top_chef_network_with_branch(df, MIN_CHEF_TOP_M)
    title = "הטבח המצטיין ברשת"
    if chef_name:
        title += f" — {chef_name} · {chef_branch or ''}".strip()
    st.markdown(f'<div class="kpi-title">{title}</div>', unsafe_allow_html=True)
    st.markdown('<div class="kpi-min"><div class="kpi-single-num">{}</div></div>'.format(
        "—" if chef_avg is None else f"{chef_avg:.2f}"
    ), unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# ----- GPT ANALYSIS ------
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown("**ניתוח GPT**")

if df.empty:
    st.info("אין נתונים לניתוח עדיין.")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    SYSTEM_ANALYST = (
        "אתה אנליסט דאטה דובר עברית. מוצגת לך טבלה עם העמודות: "
        "id, branch, chef_name, dish_name, score, notes, created_at. "
        "ענה בתמציתיות, בעברית, עם דגשים והמלצות קצרות."
    )

    def df_to_csv_for_llm(df_in: pd.DataFrame, max_rows: int = 400) -> str:
        d = df_in.copy()
        if len(d) > max_rows:
            d = d.head(max_rows)
        return d.to_csv(index=False)

    def call_openai(system_prompt: str, user_prompt: str) -> str:
        try:
            from openai import OpenAI
            api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
            org_id = st.secrets.get("OPENAI_ORG", os.getenv("OPENAI_ORG", ""))
            project_id = st.secrets.get("OPENAI_PROJECT", os.getenv("OPENAI_PROJECT", ""))
            if not api_key:
                return "חסר מפתח OPENAI_API_KEY (ב-Secrets או בקובץ ‎.env)."

            client_kwargs = {"api_key": api_key}
            if org_id:
                client_kwargs["organization"] = org_id
            if project_id:
                client_kwargs["project"] = project_id

            client = OpenAI(**client_kwargs)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return f"שגיאה בקריאה ל-OpenAI: {e}"

    col_q, col_btn = st.columns([3, 1])
    with col_q:
        user_q = st.text_input("שאלה על הנתונים (לא חובה)")
    with col_btn:
        ask_btn = st.button("שלח")
    run_overview = st.button("ניתוח כללי")

    if run_overview or ask_btn:
        table_csv = df_to_csv_for_llm(df)
        if run_overview:
            user_prompt = f"הנה הטבלה בפורמט CSV:\n{table_csv}\n\nסכם מגמות, חריגים והמלצות קצרות לניהול."
        else:
            user_prompt = (
                f"שאלה: {user_q}\n\n"
                f"הנה הטבלה בפורמט CSV (עד 400 שורות):\n{table_csv}\n\n"
                f"ענה בעברית, תן נימוק קצר לכל מסקנה."
            )
        with st.spinner("מנתח..."):
            answer = call_openai(SYSTEM_ANALYST, user_prompt)
        st.write(answer)

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
