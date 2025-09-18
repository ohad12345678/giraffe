# app2.py — 🍜 ג'ירף מטבחים – איכויות אוכל
# דרישות: streamlit, pandas, python-dotenv, openai (גרסאות v1+ נתמכות)
# הרצה: streamlit run app2.py

from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# =========================
# ------- SETTINGS --------
# =========================
st.set_page_config(page_title="🍜 ג'ירף מטבחים – איכויות אוכל", layout="wide")
load_dotenv()  # ינסה לטעון .env אם קיים

# סניפים סגורים בלבד (ללא הקלדה חופשית)
BRANCHES: List[str] = ["חיפה", "ראשל״צ", "רמה״ח", "נס ציונה", "לנדמרק", "פתח תקווה", "הרצליה"]

# רשימת מנות התחלתית (ניתן לערוך בקוד)
DISHES: List[str] = [
    "פאד תאי", "מלאזית", "פיליפינית", "אפגנית", "קארי דלעת", "סצ'ואן",
    "ביף רייס", "אורז מטוגן", "מאקי סלמון", "מאקי טונה", "ספייסי סלמון", "נודלס ילדים"
]

# רשימת טבחים - לא בשימוש (הטבח הוא שדה טקסט חופשי)
# CHEFS: List[str] = [
#     "לי צ'אנג", "ניו פנג", "ואן לי", "סון ויי", "חן דונג", "ז'אנג יאן"
# ]

DB_PATH = "food_quality.db"
DUP_HOURS = 12         # חלון כפילויות – שעות
MIN_BRANCH_LEADER_N = 3  # מינימום תצפיות לענף מוביל לפי ממוצע
MIN_CHEF_TOP_M = 5       # מינימום תצפיות לטבח מצטיין

# =========================
# --------- STYLE ---------
# =========================
st.markdown(
    """
<style>
html, body, [class*="css"] { direction: rtl; font-family: "Rubik", -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
.header-wrap {
  background: linear-gradient(135deg, #0f172a 0%, #1f2937 50%, #0b1324 100%);
  color: #fff; padding: 26px 22px; border-radius: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.25);
  border: 1px solid rgba(255,255,255,.06); margin-bottom: 22px;
}
.header-title { font-size: 28px; font-weight: 800; margin: 0 0 6px 0; }
.header-sub { opacity: .9; font-size: 14px; }
.card { background:#fff; border:1px solid #e9edf5; border-radius:16px; padding:18px; box-shadow:0 8px 20px rgba(16,24,40,.06); margin-bottom:16px; }
.kpi { padding:16px; border-radius:14px; border:1px solid #eef2f7; box-shadow:0 4px 14px rgba(16,24,40,.06); }
.kpi h4 { margin:0 0 8px 0; font-size:16px; }
.kpi .big { font-size:26px; font-weight:900; }
.kpi .num { font-size:20px; font-weight:800; }
.hint { color:#6b7280; font-size:12px; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; background:#f3f4f6; font-size:12px; margin-right:6px; }
.btn-primary > button { background: linear-gradient(135deg, #f59e0b, #ff9800); color:white; border:0; border-radius:12px; padding:10px 16px; font-weight:700; width:100%; }
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
    c = conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO food_quality (branch, chef_name, dish_name, score, notes, created_at, submitted_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (branch.strip(), chef.strip(), dish.strip(), int(score), notes.strip(), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), submitted_by),
    )
    c.commit()
    c.close()

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
        # אם אין סניף שעובר את הסף – ניקח הכי טוב שקיים
        leader = g.iloc[:1]
    row = leader.iloc[0]
    return str(row["branch"]), float(row["avg"]), int(row["n"])

def kpi_top_chef(df: pd.DataFrame, min_m: int = MIN_CHEF_TOP_M) -> Tuple[Optional[str], Optional[float], int]:
    if df.empty: return None, None, 0
    g = df.groupby("chef_name").agg(n=("id","count"), avg=("score","mean")).reset_index()
    # קודם לפי n (ככל שיש יותר אמינות), ואז avg
    g = g.sort_values(["n","avg"], ascending=[False, False])
    # מי שעובר סף
    qual = g[g["n"] >= min_m]
    pick = qual.iloc[0] if not qual.empty else g.iloc[0]
    return str(pick["chef_name"]), float(pick["avg"]), int(pick["n"])

def kpi_top_dish(df: pd.DataFrame) -> Tuple[Optional[str], int]:
    if df.empty: return None, 0
    s = df.groupby("dish_name")["id"].count().sort_values(ascending=False)
    return s.index[0], int(s.iloc[0])

def score_hint(Score: int) -> str:
    return "😟 חלש" if Score <= 3 else ("🙂 סביר" if Score <= 6 else ("😀 טוב" if Score <= 8 else "🤩 מצוין"))

# invalidate cache helper
def refresh_df():
    load_df.clear()

# =========================
# ---------- UI -----------
# =========================

st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("✍️ הזנת בדיקת איכות חדשה")

# טופס מרכזי
colA, colB, colC = st.columns([1,1,1])
with colA:
    branch = st.selectbox("סניף *", options=BRANCHES, index=None, placeholder="בחר סניף")
with colB:
    chef = st.text_input("שם הטבח *", placeholder="הקלד שם טבח...")
with colC:
    dish = st.selectbox("שם המנה *", options=DISHES, index=None, placeholder="בחר מנה")

colD, colE = st.columns([1,1])
with colD:
    score = st.selectbox("ציון איכות *", options=list(range(1, 11)), index=7, format_func=lambda x: f"{x} - {score_hint(x)}")
with colE:
    notes = st.text_area("הערות (לא חובה)", placeholder="מרקם, טמפרטורה, תיבול, עקביות...")

override = st.checkbox("שמור גם אם קיימת בדיקה דומה ב־12 השעות האחרונות (כפילויות)")

save_col1, save_col2 = st.columns([1,3])
with save_col1:
    save = st.button("💾 שמור בדיקה", type="primary")

if save:
    if not branch or not chef.strip() or not dish:
        st.error("חובה לבחור סניף, להזין שם טבח ולבחור מנה.")
    else:
        if (not override) and has_recent_duplicate(branch, chef, dish, DUP_HOURS):
            st.warning("נמצאה בדיקה קודמת לאותו סניף/טבח/מנה ב־12 השעות האחרונות. סמן 'שמור גם אם…' כדי לאשר בכל זאת.")
        else:
            insert_record(branch, chef, dish, score, notes)
            st.success(f"✅ נשמר: **{branch} · {chef} · {dish}** • ציון **{score}**")
            refresh_df()

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# --------- KPIs ----------
# =========================
df = load_df()

st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 מדדי ביצוע (מתעדכן מיד)")

best_branch, best_branch_count = kpi_best_branch_by_count(df)
current_branch_count = kpi_current_branch_count(df, branch)
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
        # הסניף הנוכחי מצד ימין, המוביל מצד שמאל
        current_html = f'<span class="big">{current_branch_count}</span>' if branch else '<span class="num">—</span>'
        st.write(f"הנוכחי: {current_html} | **{best_branch}** — **{best_branch_count}** בדיקות", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with k2:
    st.markdown('<div class="kpi">', unsafe_allow_html=True)
    st.markdown("#### ממוצע ציון — נוכחי מול המוביל")
    if best_avg_branch is None:
        st.write("אין נתונים")
    else:
        cur_avg = df[df["branch"] == branch]["score"].mean() if branch else None
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
# ------ DATA INSIGHTS -----
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 תובנות נתונים")

if not df.empty:
    st.write("### סיכום כללי:")
    
    # תובנות בסיסיות
    total_checks = len(df)
    avg_score = df['score'].mean()
    branches_active = df['branch'].nunique()
    chefs_active = df['chef_name'].nunique()
    dishes_tested = df['dish_name'].nunique()
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("סה\"כ בדיקות", total_checks)
        st.metric("ממוצע ציון כללי", f"{avg_score:.2f}")
    with col2:
        st.metric("סניפים פעילים", branches_active)
        st.metric("טבחים פעילים", chefs_active)
    with col3:
        st.metric("מנות נבדקות", dishes_tested)
    
    # תובנות מתקדמות
    st.write("### פילוח לפי סניפים:")
    branch_stats = df.groupby('branch').agg({
        'score': ['count', 'mean'],
        'chef_name': 'nunique'
    }).round(2)
    branch_stats.columns = ['בדיקות', 'ממוצע ציון', 'טבחים']
    st.dataframe(branch_stats, use_container_width=True)
    
else:
    st.info("אין נתונים עדיין - התחל למלא בדיקות!")

st.markdown('</div>', unsafe_allow_html=True)

# =========================
# ----- EXPORT / META -----
# =========================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📥 ייצוא ומידע")
colx, coly = st.columns([1,3])
with colx:
    if st.button("⬇️ ייצוא CSV"):
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("הורדת קובץ CSV", data=csv, file_name="food_quality_export.csv", mime="text/csv")
with coly:
    st.write(f"סה\"כ רשומות: **{len(df)}**")
st.markdown('</div>', unsafe_allow_html=True)

