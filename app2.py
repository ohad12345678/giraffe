# app2.py — 🍜 ג'ירף מטבחים · איכויות אוכל
# תלות: streamlit, pandas, gspread, google-auth, openai (v1+)
# הרצה בענן: Streamlit Cloud (הכל דרך st.secrets)

from __future__ import annotations
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

import pandas as pd
import streamlit as st

# ---------- עיצוב בסיסי ----------
st.set_page_config(page_title="🍜 ג'ירף מטבחים – איכויות אוכל", layout="wide")
st.markdown("""
<style>
.main .block-container{direction:rtl;font-family:"Rubik",-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.stTextInput input,.stTextArea textarea{text-align:right}
.card{background:#fff;border:1px solid #e9edf5;border-radius:16px;padding:18px;box-shadow:0 8px 20px rgba(16,24,40,.06);margin-bottom:16px}
.status{display:flex;justify-content:space-between;gap:8px;background:linear-gradient(135deg,#10b981,#059669);color:#fff;padding:12px 16px;border-radius:14px;margin:10px 0;font-weight:800}
.tag{background:rgba(255,255,255,.18);padding:2px 10px;border-radius:999px}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="card" style="background:linear-gradient(135deg,#0f172a,#1f2937);color:#fff">
  <div style="font-size:26px;font-weight:900">🍜 ג'ירף מטבחים – איכויות אוכל</div>
  <div style="opacity:.9">טופס הזנה, מדדים ושמירה ל-Google Sheets + ניתוח GPT</div>
</div>
""", unsafe_allow_html=True)

# ---------- קבועים ----------
BRANCHES: List[str] = ["חיפה","ראשל״צ","רמה״ח","נס ציונה","לנדמרק","פתח תקווה","הרצליה","סביון"]
DISHES:   List[str] = ["פאד תאי","מלאזית","פיליפינית","אפגנית","קארי דלעת","סצ'ואן","ביף רייס","אורז מטוגן",
                       "מאקי סלמון","מאקי טונה","ספייסי סלמון","נודלס ילדים"]
DB_PATH = "food_quality.db"
DUP_HOURS = 12
MIN_BRANCH_LEADER_N = 3
MIN_CHEF_TOP_M = 5

# ---------- DB ----------
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
    c = conn(); cur = c.cursor()
    cur.execute(SCHEMA)
    for q in INDEXES: cur.execute(q)
    c.commit(); c.close()
init_db()

@st.cache_data(ttl=15)
def load_df() -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query(
        "SELECT id, branch, chef_name, dish_name, score, notes, created_at FROM food_quality ORDER BY created_at DESC", c
    )
    c.close()
    return df

def refresh_df(): load_df.clear()

# ---------- שכבת Secrets: Sheets ----------
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

def _normalize_private_key(creds: dict) -> dict:
    """מתקן את ה-private key - מחליף \\n ב-\n אם נדרש"""
    pk = creds.get("private_key")
    if isinstance(pk, str) and "\\n" in pk:
        creds = creds.copy()
        creds["private_key"] = pk.replace("\\n", "\n")
    return creds

def _get_sheets_config():
    """מחזיר את הגדרות החיבור לגוגל שיטס"""
    try:
        sheet_url = st.secrets.get("GOOGLE_SHEET_URL")
        sheet_id = st.secrets.get("GOOGLE_SHEET_ID")
        sheet_title = st.secrets.get("GOOGLE_SHEET_TITLE")
        ws_name = st.secrets.get("GOOGLE_SHEET_WORKSHEET", "sheet1")

        # נסה לקבל את ה-service account credentials
        creds_dict = dict(st.secrets.get("google_service_account", {}))
        
        if not creds_dict:
            return None, None, ws_name
            
        # תקן את ה-private key
        creds_dict = _normalize_private_key(creds_dict)
        
        # וודא שיש את כל השדות הנדרשים
        required_fields = ["type", "project_id", "private_key", "client_email", "client_id", "token_uri"]
        missing_fields = [field for field in required_fields if field not in creds_dict]
        
        if missing_fields:
            st.error(f"חסרים שדות ב-google_service_account: {', '.join(missing_fields)}")
            return None, None, ws_name

        identifier = sheet_url or sheet_id or sheet_title
        return creds_dict, identifier, ws_name
        
    except Exception as e:
        st.error(f"שגיאה בקריאת הגדרות Sheets: {e}")
        return None, None, "sheet1"

def _open_spreadsheet(gc, identifier: str):
    """פותח את הגיליון לפי מזהה - URL, ID או כותרת"""
    if identifier.startswith("http"):
        return gc.open_by_url(identifier)
    if "/" not in identifier and " " not in identifier:
        try:
            return gc.open_by_key(identifier)
        except Exception:
            pass
    return gc.open(identifier)

def save_to_google_sheets(branch: str, chef: str, dish: str, score: int, notes: str, ts: str) -> bool:
    """שומר רשומה לגוגל שיטס"""
    if not GSHEETS_AVAILABLE:
        st.warning("gspread/google-auth לא מותקנות — לא ניתן לכתוב לגיליון.")
        return False
        
    creds_dict, identifier, ws_name = _get_sheets_config()
    
    if not creds_dict:
        st.warning("חסרות הגדרות google_service_account ב-secrets.toml")
        return False
        
    if not identifier:
        st.warning("חסר מזהה גיליון (GOOGLE_SHEET_URL/ID/TITLE) ב-secrets.toml")
        return False
        
    try:
        # יצירת credentials ואימות
        credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(credentials)
        
        # פתיחת הגיליון
        sh = _open_spreadsheet(gc, identifier)
        
        # פתיחת/יצירת worksheet
        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            ws = sh.add_worksheet(title=ws_name, rows=1000, cols=12)
            # הוספת כותרות אם זה worksheet חדש
            ws.append_row(["תאריך/שעה", "סניף", "שם טבח", "מנה", "ציון", "הערות"])
        
        # הוספת השורה
        ws.append_row([ts, branch, chef, dish, score, notes or ""], value_input_option="USER_ENTERED")
        return True
        
    except Exception as e:
        st.error(f"שגיאת Google Sheets: {e}")
        return False

# ---------- שכבת Secrets: GPT ----------
def get_openai_client():
    """מחזיר OpenAI client או שגיאה"""
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    if not api_key or api_key == "sk-PASTE_YOUR_KEY_HERE":
        return None, "חסר OPENAI_API_KEY תקין ב-secrets.toml"
        
    org = st.secrets.get("OPENAI_ORG", "")
    proj = st.secrets.get("OPENAI_PROJECT", "")
    
    try:
        from openai import OpenAI
        kw = {"api_key": api_key}
        if org: kw["organization"] = org
        if proj: kw["project"] = proj
        return OpenAI(**kw), None
    except Exception as e:
        return None, f"שגיאת OpenAI: {e}"

# ---------- לוגיקה ----------
def score_hint(x:int)->str: 
    return "😟 חלש" if x<=3 else ("🙂 סביר" if x<=6 else ("😀 טוב" if x<=8 else "🤩 מצוין"))

def has_recent_duplicate(branch:str, chef:str, dish:str, hours:int=DUP_HOURS)->bool:
    if hours<=0: return False
    cutoff = (datetime.utcnow()-timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT 1 FROM food_quality WHERE branch=? AND chef_name=? AND dish_name=? AND created_at >= ? LIMIT 1""",
                (branch.strip(), chef.strip(), dish.strip(), cutoff))
    ok = cur.fetchone() is not None
    c.close(); return ok

def insert_record(branch:str, chef:str, dish:str, score:int, notes:str, submitted_by:Optional[str]=None):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    # SQLite
    c = conn(); cur = c.cursor()
    cur.execute("""INSERT INTO food_quality (branch, chef_name, dish_name, score, notes, created_at, submitted_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (branch.strip(), chef.strip(), dish.strip(), int(score), (notes or "").strip(), ts, submitted_by))
    c.commit(); c.close()
    # Sheets
    ok = save_to_google_sheets(branch, chef, dish, score, notes, ts)
    st.toast("נשמר גם ל-Google Sheets ✅" if ok else "נשמר מקומית בלבד ℹ️", icon="✅" if ok else "ℹ️")

def kpi_best_branch_by_count(df:pd.DataFrame)->Tuple[Optional[str],int]:
    if df.empty: return None,0
    s = df.groupby("branch")["id"].count().sort_values(ascending=False)
    return s.index[0], int(s.iloc[0])

def kpi_best_avg_branch(df:pd.DataFrame, min_n:int)->Tuple[Optional[str],Optional[float],int]:
    if df.empty: return None,None,0
    g = df.groupby("branch").agg(n=("id","count"), avg=("score","mean")).reset_index().sort_values(["avg","n"],ascending=[False,False])
    leader = g[g["n"]>=min_n]
    row = (leader if not leader.empty else g).iloc[0]
    return str(row["branch"]), float(row["avg"]), int(row["n"])

def kpi_top_chef(df:pd.DataFrame, min_m:int)->Tuple[Optional[str],Optional[float],int]:
    if df.empty: return None,None,0
    g = df.groupby("chef_name").agg(n=("id","count"), avg=("score","mean")).reset_index().sort_values(["n","avg"],ascending=[False,False])
    qual = g[g["n"]>=min_m]
    pick = qual.iloc[0] if not qual.empty else g.iloc[0]
    return str(pick["chef_name"]), float(pick["avg"]), int(pick["n"])

def kpi_top_dish(df:pd.DataFrame)->Tuple[Optional[str],int]:
    if df.empty: return None,0
    s = df.groupby("dish_name")["id"].count().sort_values(ascending=False)
    return s.index[0], int(s.iloc[0])

# ---------- התחברות (בחירת מצב) ----------
def require_auth()->dict:
    if "auth" not in st.session_state:
        st.session_state.auth = {"role": None, "branch": None}
    auth = st.session_state.auth
    if not auth["role"]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.subheader("👋 מסך כניסה")
        role = st.radio("בחר סוג משתמש", options=["סניף","מטה"], horizontal=True, index=0)
        if role=="סניף":
            b = st.selectbox("בחר סניף", options=["— בחר —"]+BRANCHES, index=0)
            if st.button("המשך"):
                if b=="— בחר —": st.error("בחר סניף כדי להמשיך.")
                else:
                    st.session_state.auth = {"role":"branch","branch":b}; st.rerun()
        else:
            if st.button("המשך כ'מטה'"):
                st.session_state.auth = {"role":"meta","branch":None}; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True); st.stop()
    return auth

auth = require_auth()
st.markdown(f'<div class="status"> <div>מצב עבודה: <span class="tag">{ "מטה" if auth["role"]=="meta" else "סניף" }</span>'
            f'{"" if auth["role"]=="meta" else " · "+auth["branch"]}</div>'
            f'<div><span class="tag">אפשר להתנתק ולבחור סניף אחר</span></div></div>', unsafe_allow_html=True)

# ---------- טופס ----------
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("✍️ הזנת בדיקת איכות חדשה")

colA,colB,colC = st.columns(3)
if auth["role"]=="meta":
    with colA: selected_branch = st.selectbox("סניף *", options=BRANCHES, index=0)
else:
    selected_branch = auth["branch"]
    with colA: st.text_input("סניף", value=selected_branch, disabled=True)
with colB: chef = st.text_input("שם הטבח *")
with colC: dish = st.selectbox("שם המנה *", options=DISHES, index=0)

colD,colE = st.columns(2)
with colD:
    score = st.selectbox("ציון איכות *", options=list(range(1,11)), index=7,
                         format_func=lambda x: f"{x} - {score_hint(x)}")
with colE:
    notes = st.text_area("הערות (לא חובה)")

override = st.checkbox("שמור גם אם קיימת בדיקה דומה ב-12 השעות האחרונות (כפילויות)")
if st.button("💾 שמור בדיקה", type="primary"):
    if not selected_branch or not chef.strip() or not dish:
        st.error("חובה לבחור/להציג סניף, להזין שם טבח ולבחור מנה.")
    else:
        if (not override) and has_recent_duplicate(selected_branch, chef, dish, DUP_HOURS):
            st.warning("נמצאה בדיקה קודמת לאותו סניף/טבח/מנה ב-12 השעות האחרונות. סמן 'שמור גם אם…' כדי לאשר.")
        else:
            insert_record(selected_branch, chef, dish, score, notes, submitted_by=auth["role"])
            st.success(f"✅ נשמר: **{selected_branch} · {chef} · {dish}** • ציון **{score}**")
            refresh_df()
st.markdown('</div>', unsafe_allow_html=True)

# ---------- KPI ----------
df = load_df()
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 מדדים")
if df.empty:
    st.info("אין נתונים להצגה עדיין.")
else:
    best_branch, best_branch_count = kpi_best_branch_by_count(df)
    best_avg_branch, best_avg_value, best_avg_n = kpi_best_avg_branch(df, MIN_BRANCH_LEADER_N)
    top_chef, top_chef_avg, top_chef_n = kpi_top_chef(df, MIN_CHEF_TOP_M)
    top_dish, top_dish_count = kpi_top_dish(df)

    c1,c2,c3,c4 = st.columns(4)
    with c1:
        st.markdown("#### הסניף המוביל בבדיקות")
        st.write("אין נתונים" if not best_branch else f"**{best_branch}** — **{best_branch_count}**")
    with c2:
        st.markdown("#### ממוצע ציון — המוביל")
        st.write("אין נתונים" if not best_avg_branch else f"**{best_avg_branch}** — {best_avg_value:.2f}"
                 + (" (מדגם קטן)" if best_avg_n<MIN_BRANCH_LEADER_N else ""))
    with c3:
        st.markdown("#### הטבח המצטיין")
        st.write("אין נתונים" if not top_chef else f"**{top_chef}** — {top_chef_avg:.2f} ({top_chef_n} בדיקות)")
    with c4:
        st.markdown("#### המנה הכי נבחנת")
        st.write("אין נתונים" if not top_dish else f"**{top_dish}** — {top_dish_count}")
st.markdown('</div>', unsafe_allow_html=True)

# ---------- GPT ----------
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("🤖 ניתוח GPT")
gpt_client, gpt_err = get_openai_client()
if gpt_err:
    st.warning(gpt_err)
else:
    # בדיקת חיבור
    if st.button("🔎 בדיקת חיבור ל-GPT"):
        try:
            ping = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":"You are a ping responder."},
                          {"role":"user","content":"ping"}],
                temperature=0.0,
            )
            msg = (ping.choices[0].message.content or "").strip()
            st.success(f"GPT מחובר. תשובה: {msg[:100]}")
        except Exception as e:
            st.error(f"שגיאת GPT: {e}")

    if df.empty:
        st.info("אין נתונים לניתוח.")
    else:
        def df_to_csv_for_llm(dfin:pd.DataFrame, max_rows:int=400)->str:
            d = dfin.copy()
            if len(d)>max_rows: d = d.head(max_rows)
            return d.to_csv(index=False)

        q_col, btn_col = st.columns([3,1])
        with q_col: user_q = st.text_input("שאלה על הנתונים (אופציונלי)")
        with btn_col: ask_btn = st.button("שלח")
        overview_btn = st.button("ניתוח כללי")

        if overview_btn or ask_btn:
            csv_text = df_to_csv_for_llm(df)
            if overview_btn:
                user_prompt = f"הנה הטבלה בפורמט CSV:\n{csv_text}\n\nסכם מגמות, חריגים והמלצות קצרות."
            else:
                user_prompt = f"שאלה: {user_q}\n\nהנה הטבלה (CSV, עד 400 שורות):\n{csv_text}\n\nענה בעברית, עם נימוק קצר."

            with st.spinner("מנתח..."):
                try:
                    resp = gpt_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role":"system","content":
                                   "אתה אנליסט דאטה דובר עברית. העמודות: id, branch, chef_name, dish_name, score, notes, created_at."},
                                  {"role":"user","content": user_prompt}],
                        temperature=0.2,
                    )
                    ans = (resp.choices[0].message.content or "").strip()
                    st.write(ans)
                except Exception as e:
                    st.error(f"שגיאת GPT: {e}")
st.markdown('</div>', unsafe_allow_html=True)

# ---------- Admin ----------
admin_password = st.secrets.get("ADMIN_PASSWORD", "admin123")
st.markdown("---")
st.markdown('<div class="card">', unsafe_allow_html=True)
if "admin_logged_in" not in st.session_state: st.session_state.admin_logged_in = False

c1,c2 = st.columns([4,1])
with c1: st.caption("לחזרה למסך הכניסה: התנתק משתמש.")
with c2:
    if st.button("התנתק משתמש"):
        st.session_state.auth = {"role":None,"branch":None}; st.rerun()

if not st.session_state.admin_logged_in:
    st.subheader("🔐 כניסה למנהל")
    mid = st.columns([2,1,2])[1]
    with mid:
        pwd = st.text_input("סיסמת מנהל:", type="password")
        if st.button("התחבר", use_container_width=True):
            if pwd == admin_password:
                st.session_state.admin_logged_in = True; st.rerun()
            else:
                st.error("סיסמה שגויה")
else:
    st.success("מחובר כמנהל")
    cc1,cc2 = st.columns(2)
    with cc2:
        if st.button("התנתק מנהל"): st.session_state.admin_logged_in = False; st.rerun()

st.markdown('</div>', unsafe_allow_html=True)

if st.session_state.get("admin_logged_in", False):
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📥 ייצוא ובדיקות")
    data = load_df().to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ הורדת CSV", data=data, file_name="food_quality_export.csv", mime="text/csv")

    # PING ל-Sheets ו-GPT
    colx, coly = st.columns(2)
    with colx:
        if st.button("🧪 בדיקת כתיבה ל-Sheets"):
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            ok = save_to_google_sheets("DEBUG","PING","PING",0,"בדיקת מערכת",ts)
            st.success("✅ נכתב לגיליון") if ok else st.error("❌ הכתיבה נכשלה")
    with coly:
        gc, ge = get_openai_client()
        if ge: st.info("GPT לא הוגדר")
        else:
            if st.button("🧪 בדיקת GPT"):
                try:
                    gc.chat.completions.create(model="gpt-4o-mini",
                                               messages=[{"role":"user","content":"ping"}],
                                               temperature=0.0)
                    st.success("✅ GPT מחובר")
                except Exception as e:
                    st.error(f"❌ GPT שגיאה: {e}")
    st.markdown('</div>', unsafe_allow_html=True)
