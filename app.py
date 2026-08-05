import streamlit as st
import pandas as pd
import numpy as np
import re
import os
import io

# 🎯 콤비네이션 차트용 Plotly (미설치 시에도 대시보드는 정상 작동)
try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False

st.set_page_config(page_title="S&OP Dashboard", layout="wide")
st.title("S&OP 대시보드 (고도화 뷰)")

# 🎯 공통 컬럼 넓이 설정 (원하는 픽셀 크기로 조절 가능)
COMMON_COL_CONFIG = {
    "제품명": st.column_config.Column(width=280),
    "영업지점명": st.column_config.Column(width=150),
    "영업사원명": st.column_config.Column(width=130)
}

# 🎯 [추가됨] 당월 진척도 탭 전용 컬럼 넓이 (계획/실적/진척도/GAP, 원하는 픽셀로 조절 가능)
PROGRESS_COL_CONFIG = {
    "계획": st.column_config.Column(width=110),
    "실적": st.column_config.Column(width=110),
    "진척도": st.column_config.Column(width=95),
    "GAP": st.column_config.Column(width=110)
}
# 진척도 탭 표에 실제 적용되는 통합 설정 (공통 + 전용)
PROG_TABLE_CONFIG = {**COMMON_COL_CONFIG, **PROGRESS_COL_CONFIG}

# =============================================================
# 폴더 및 저장소 경로
# =============================================================
DATA_DIR = "_Master_Data"      # 마스터 3종 (기존과 동일)
HIST_DIR = "_History_Data"     # 🎯 [추가됨] 월별 계획/실적 히스토리 + 당월 진척도 영구 저장소
for _d in [DATA_DIR, HIST_DIR]:
    if not os.path.exists(_d):
        os.makedirs(_d)

PLAN_STORE = os.path.join(HIST_DIR, "plan_history.csv")
ACT_STORE = os.path.join(HIST_DIR, "actual_history.csv")
PROG_STORE = os.path.join(HIST_DIR, "progress_orders.csv")

GROUP_COLS = ['거래처 코드', '제품코드', '영업부명', '영업지점명', '영업사원명']
PLAN_COLS = ['기준월'] + GROUP_COLS + ['계획수량', '소스']
ACT_COLS = ['기준월'] + GROUP_COLS + ['실적수량']
PROG_COLS = ['기준월'] + GROUP_COLS + ['마감여부', '실적수량']

# 🎯 오류가 수정된 사발면류 완벽 맵핑 리스트 (모듈 공통 사용)
PRODUCT_MAPPING = {
    '101002098': '101070162', '101002237': '101070163', '101002158': '101070175',
    '101002099': '101070165', '101002510': '101070164', '101002238': '101070166',
    '101002159': '101070173', '101002101': '101070168', '101002718': '101070167',
    '101002162': '101070179', '101002100': '101070156', '101001836': '101070155',
    '101002160': '101070176', '101002186': '101070154', '101002187': '101070177',
    '101002102': '101070158', '101003128': '101070157', '101002243': '101070153',
    '101002719': '101070152', '101002244': '101070178', '101001835': '101070161'
}

# =============================================================
# 🎯 [기간 한정 규칙] 분석 화면에만 적용. 저장 원본(CSV 히스토리)은 절대 건드리지 않음.
#    → 아래 항목을 삭제하거나 주석 처리하면 즉시 원상복구됨.
# =============================================================
# [실적] (대상월, 원래코드): 통합코드 — 해당 월에 한해 원래코드의 '실적'을 통합코드로 합산
MONTHLY_ACTUAL_MERGE = {
    ('2026-07', '101070105'): '101004224',   # 신라면 KDH 실적 → 신라면(기존). 7월 KDH 포장재 결품 대응
}
# [계획] (대상월, 원래코드): (통합코드, 모드) — 해당 월에 한해 원래코드의 '계획' 처리
#   'dedupe' : 같은 거래처에 통합코드 계획이 이미 있으면 원래코드 계획 삭제(이중 입력 제거),
#              통합코드 계획이 없는 거래처는 원래코드 계획을 통합코드로 이관(성실 입력 사원 보호)
#   'drop'   : 해당 월 원래코드 계획을 전부 삭제
MONTHLY_PLAN_MERGE = {
    ('2026-07', '101070105'): ('101004224', 'dedupe'),
}
# [제외] 코드: 시작월 — 해당 월부터(이후 계속) 분석에서 제외. 시작월 이전 과거 데이터/정확도는 그대로 유지
MONTHLY_EXCLUSIONS = {
    '101070076': '2026-07',
}
# [조직 통합] 영업부명/영업지점명에 나타나는 명칭을 통합 명칭으로 변경 (전 기간 · 전체 탭 적용)
ORG_NAME_MERGE = {
    'E-Commerce Project A': 'E-Commerce',
}
# [평가 제외 조직] 탭3(상세 분석)·탭4(전월 대비 개선)에서 제외할 조직명 (영업부명 또는 영업지점명 일치 시)
#  → 탭1·2의 전체 실적 집계에는 그대로 포함됨
EVAL_EXCLUDE_ORGS = ['NSA HQ']

def apply_period_rules(df, kind):
    """기간 한정 규칙 적용 (표시용, 원본 불변). kind: 'plan' 또는 'actual'"""
    if df is None or df.empty or '기준월' not in df.columns or '제품코드' not in df.columns:
        return df
    df = df.copy()

    if kind == 'actual':
        for (m, src), dst in MONTHLY_ACTUAL_MERGE.items():
            mask = (df['기준월'] == m) & (df['제품코드'] == src)
            if mask.any():
                df.loc[mask, '제품코드'] = dst

    if kind == 'plan':
        for (m, src), (dst, mode) in MONTHLY_PLAN_MERGE.items():
            src_mask = (df['기준월'] == m) & (df['제품코드'] == src)
            if not src_mask.any():
                continue
            if mode == 'drop':
                df = df[~src_mask]
            else:  # 'dedupe'
                dst_customers = set(df[(df['기준월'] == m) & (df['제품코드'] == dst)]['거래처 코드'])
                dup_mask = src_mask & df['거래처 코드'].isin(dst_customers)
                df = df[~dup_mask]                          # 이중 입력 거래처: 원래코드 계획 삭제
                move_mask = (df['기준월'] == m) & (df['제품코드'] == src)
                df.loc[move_mask, '제품코드'] = dst          # 단독 입력 거래처: 통합코드로 이관

    for code, start in MONTHLY_EXCLUSIONS.items():
        df = df[~((df['제품코드'] == code) & (df['기준월'] >= start))]

    # 조직 통합 (전 기간): 영업부명/영업지점명 양쪽에서 명칭 치환
    if ORG_NAME_MERGE:
        for src, dst in ORG_NAME_MERGE.items():
            for col in ('영업부명', '영업지점명'):
                if col in df.columns:
                    df.loc[df[col].astype(str).str.strip() == src, col] = dst
    return df

# 규칙 변경 시 캐시가 자동 무효화되도록 캐시 키로 쓰는 문자열
PERIOD_RULES_KEY = (str(sorted(MONTHLY_ACTUAL_MERGE.items())) + '|'
                    + str(sorted(MONTHLY_PLAN_MERGE.items())) + '|'
                    + str(sorted(MONTHLY_EXCLUSIONS.items())) + '|'
                    + str(sorted(ORG_NAME_MERGE.items())) + '|'
                    + str(sorted(EVAL_EXCLUDE_ORGS)))

def save_uploaded_file(uploaded_file, filename):
    if uploaded_file is not None:
        with open(os.path.join(DATA_DIR, filename), "wb") as f:
            f.write(uploaded_file.getbuffer())
        return True
    return False

def normalize_cols(df):
    df.columns = df.columns.astype(str).str.replace('\xa0', ' ').str.lower().str.strip().str.replace(r'\s+', ' ', regex=True)
    return df

def clean_code(series):
    return series.astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

def file_mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else 0.0


# =============================================================
# 공통 계산 함수
# =============================================================
# 🎯 계획, 실적이 모두 0인 달은 정확도 계산에서 투명인간(결측치) 처리
def compute_accuracy(plan, actual):
    if (pd.isna(plan) or plan == 0) and (pd.isna(actual) or actual == 0):
        return np.nan
    if pd.isna(plan) or plan == 0:
        return 0.0
    if actual <= plan:
        return actual / plan
    else:
        return max(0.0, 1.0 - ((actual - plan) / plan))

# 🎯 [추가됨] 진척도: 단순 실적/계획 (과판매도 100% 초과로 그대로 표시)
def compute_progress(plan, actual):
    p = 0 if pd.isna(plan) else plan
    a = 0 if pd.isna(actual) else actual
    if p == 0 and a == 0:
        return np.nan
    if p == 0:
        return np.inf  # 계획 없이 실적만 존재 → '∞' 표시
    return a / p

def parse_month(val):
    if pd.isna(val): return np.nan
    val_str = str(val).strip()
    if val_str.endswith('.0'): val_str = val_str[:-2]

    if len(val_str) == 6 and val_str.isdigit():
        return f"{val_str[:4]}-{val_str[4:]}"
    if len(val_str) == 8 and val_str.isdigit():
        return f"{val_str[:4]}-{val_str[4:6]}"
    try:
        return pd.to_datetime(val_str).strftime('%Y-%m')
    except:
        return val_str


# =============================================================
# 마스터 데이터 로더 (파일 변경 시각 기준 캐시)
# =============================================================
master_path = os.path.join(DATA_DIR, "master.xlsx")
item_master_path = os.path.join(DATA_DIR, "item_master.xlsx")
exclusion_path = os.path.join(DATA_DIR, "exclusion.xlsx")

@st.cache_data
def load_master_lookup(master_mtime):
    master_df = normalize_cols(pd.read_excel(master_path))
    master_rename_dict = {
        'sold-to party': '거래처 코드',
        'sales org. name': '영업부명',
        'sales group name': '영업지점명',
        'sales person name': '영업사원명',
        'sales preson name': '영업사원명'
    }
    master_df = master_df.rename(columns=master_rename_dict)
    master_lookup = master_df[['거래처 코드', '영업부명', '영업지점명', '영업사원명']].drop_duplicates(subset=['거래처 코드'])
    master_lookup['거래처 코드'] = clean_code(master_lookup['거래처 코드'])
    return master_lookup

@st.cache_data
def load_item_info_and_dropcodes(item_mtime, exc_mtime):
    item_master_df = normalize_cols(pd.read_excel(item_master_path))
    item_master_df.rename(columns={item_master_df.columns[3]: '국가'}, inplace=True)
    item_master_df['제품코드'] = clean_code(item_master_df['제품코드'])

    exc_df = normalize_cols(pd.read_excel(exclusion_path))
    if '제품코드' in exc_df.columns:
        exc_codes = clean_code(exc_df['제품코드']).tolist()
    elif '품목코드' in exc_df.columns:
        exc_codes = clean_code(exc_df['품목코드']).tolist()
    else:
        exc_codes = clean_code(exc_df.iloc[:, 0]).tolist()

    tra_mask = item_master_df.astype(str).apply(lambda x: x.str.contains('TRA.GOODS', case=False, na=False)).any(axis=1)
    udon_codes = ['101001911', '101007351', '101002381']
    udon_mask = item_master_df['제품코드'].isin(udon_codes)
    drop_tra_codes = item_master_df[tra_mask & ~udon_mask]['제품코드'].tolist()
    final_drop_codes = set(drop_tra_codes + exc_codes)

    item_info = item_master_df[['제품코드', '제품명', '국가']].drop_duplicates(subset=['제품코드'])
    return item_info, final_drop_codes


# =============================================================
# 업로드 파일 → 표준 롱포맷 변환 (조직 매핑은 업로드 시점 마스터 기준으로 동결)
# =============================================================
def _attach_org(df, master_lookup):
    for col in ['영업부명', '영업지점명', '영업사원명']:
        if col in df.columns:
            df = df.drop(columns=[col])
    df = pd.merge(df, master_lookup, on='거래처 코드', how='left')
    for col in GROUP_COLS:
        df[col] = df[col].fillna('미상')
    return df

def process_plan_upload(plan_file, master_lookup):
    plan_df = normalize_cols(pd.read_excel(plan_file))
    if '거래처코드' in plan_df.columns:
        plan_df = plan_df.rename(columns={'거래처코드': '거래처 코드'})
    plan_df['제품코드'] = clean_code(plan_df['제품코드']).replace(PRODUCT_MAPPING)
    plan_df['거래처 코드'] = clean_code(plan_df['거래처 코드'])

    if '정상계획' in plan_df.columns and '행사계획' in plan_df.columns:
        plan_df['계획수량'] = plan_df['정상계획'].fillna(0) + plan_df['행사계획'].fillna(0)
    elif '정상계획' in plan_df.columns:
        plan_df['계획수량'] = plan_df['정상계획'].fillna(0)
    else:
        plan_df['계획수량'] = 0

    plan_df = _attach_org(plan_df, master_lookup)
    plan_month_col = '해당월' if '해당월' in plan_df.columns else '계획대상월' if '계획대상월' in plan_df.columns else '기준월'
    plan_df['기준월'] = plan_df[plan_month_col].apply(parse_month)
    return plan_df.groupby(['기준월'] + GROUP_COLS, as_index=False)['계획수량'].sum()

def process_can_upload(can_plan_file, master_lookup):
    can_df = pd.read_excel(can_plan_file)
    can_df.rename(columns={can_df.columns[0]: '제품코드'}, inplace=True)
    can_df['제품코드'] = clean_code(can_df['제품코드']).replace(PRODUCT_MAPPING)

    month_pattern = re.compile(r'[A-Za-z]{3,}\.?\s*\d{4}')
    date_cols = [c for c in can_df.columns if month_pattern.search(str(c))]
    if not date_cols:
        return None

    can_melt = can_df.melt(id_vars=['제품코드'], value_vars=date_cols, var_name='기준월', value_name='계획수량')
    can_melt['기준월'] = can_melt['기준월'].apply(parse_month)
    can_melt['계획수량'] = pd.to_numeric(can_melt['계획수량'], errors='coerce').fillna(0)

    can_mask = master_lookup.astype(str).apply(lambda x: x.str.contains('캐나다|can|canada', case=False, na=False)).any(axis=1)
    if can_mask.any():
        can_info = master_lookup[can_mask].iloc[0]
        can_melt['거래처 코드'] = can_info['거래처 코드']
        can_melt['영업부명'] = can_info['영업부명']
        can_melt['영업지점명'] = can_info['영업지점명']
        can_melt['영업사원명'] = can_info['영업사원명']
    else:
        # 🎯 [수정됨] 마스터에서 캐나다 거래처를 못 찾아도 행이 '미상' 필터에 걸러지지 않도록 '캐나다' 라벨 부여
        can_melt['거래처 코드'] = 'CAN_TEMP'
        can_melt['영업부명'] = '캐나다'
        can_melt['영업지점명'] = '캐나다'
        can_melt['영업사원명'] = '캐나다'

    return can_melt.groupby(['기준월'] + GROUP_COLS, as_index=False)['계획수량'].sum()

def process_actual_upload(actual_file, master_lookup):
    actual_df = normalize_cols(pd.read_excel(actual_file))
    actual_df = actual_df.rename(columns={'sold-to party': '거래처 코드', 'material': '제품코드', 'quantity': '실적수량'})
    actual_df['제품코드'] = clean_code(actual_df['제품코드']).replace(PRODUCT_MAPPING)
    actual_df['거래처 코드'] = clean_code(actual_df['거래처 코드'])
    actual_df = _attach_org(actual_df, master_lookup)
    actual_month_col = '출고월' if '출고월' in actual_df.columns else '기준월'
    actual_df['기준월'] = actual_df[actual_month_col].apply(parse_month)
    return actual_df.groupby(['기준월'] + GROUP_COLS, as_index=False)['실적수량'].sum()

# 🎯 [추가됨] 당월 진척도용 오더 데이터: '마감 여부' 컬럼 포함
def process_progress_upload(prog_file, master_lookup, target_month):
    df = normalize_cols(pd.read_excel(prog_file))
    df = df.rename(columns={'sold-to party': '거래처 코드', 'material': '제품코드', 'quantity': '실적수량'})
    status_col = None
    for cand in ['마감 여부', '마감여부']:
        if cand in df.columns:
            status_col = cand
            break
    if status_col is None:
        return None
    df = df.rename(columns={status_col: '마감여부'})
    df['마감여부'] = df['마감여부'].astype(str).str.strip().replace({'nan': '미기재', '': '미기재'})
    df['제품코드'] = clean_code(df['제품코드']).replace(PRODUCT_MAPPING)
    df['거래처 코드'] = clean_code(df['거래처 코드'])
    df = _attach_org(df, master_lookup)
    out = df.groupby(GROUP_COLS + ['마감여부'], as_index=False)['실적수량'].sum()
    out['기준월'] = target_month
    return out[PROG_COLS]


# =============================================================
# 히스토리 저장소 입출력 및 업서트
# =============================================================
def load_store(path, columns, qty_col):
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path, dtype=str, encoding='utf-8-sig')
    for c in columns:
        if c not in df.columns:
            df[c] = '' if c != qty_col else 0
    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
    return df[columns]

def save_store(df, path):
    df.to_csv(path, index=False, encoding='utf-8-sig')

# 🎯 [핵심] 롤링 업서트: 선택한 월만 삭제 후 교체, 나머지 월은 동결 보존
def upsert_plan(new_df, months, source):
    store = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
    keep = ~((store['소스'] == source) & (store['기준월'].isin(months)))
    new_df = new_df[new_df['기준월'].isin(months)].copy()
    new_df['소스'] = source
    merged = pd.concat([store[keep], new_df[PLAN_COLS]], ignore_index=True)
    save_store(merged, PLAN_STORE)
    return []

def upsert_actual(new_df, months):
    store = load_store(ACT_STORE, ACT_COLS, '실적수량')
    keep = ~store['기준월'].isin(months)
    new_df = new_df[new_df['기준월'].isin(months)].copy()
    merged = pd.concat([store[keep], new_df[ACT_COLS]], ignore_index=True)
    save_store(merged, ACT_STORE)
    # 🎯 [자동 세대교체] 월 마감 실적이 등록된 월의 진척도 데이터는 자동 삭제
    prog = load_store(PROG_STORE, PROG_COLS, '실적수량')
    if not prog.empty:
        removed = prog['기준월'].isin(months)
        if removed.any():
            save_store(prog[~removed], PROG_STORE)
            return sorted(prog[removed]['기준월'].unique())
    return []

def delete_month(path, columns, qty_col, month):
    store = load_store(path, columns, qty_col)
    save_store(store[store['기준월'] != month], path)

def apply_adjustment(adj_file):
    adj_df = normalize_cols(pd.read_excel(adj_file))
    adj_month_col = '해당월' if '해당월' in adj_df.columns else '기준월'
    adj_qty_col = '조정계획수량' if '조정계획수량' in adj_df.columns else '계획수량' if '계획수량' in adj_df.columns else '수량'
    if '제품코드' not in adj_df.columns or adj_qty_col not in adj_df.columns:
        return None
    adj_df['제품코드'] = clean_code(adj_df['제품코드']).replace(PRODUCT_MAPPING)
    adj_df['기준월'] = adj_df[adj_month_col].apply(parse_month)
    adj_df = adj_df.rename(columns={adj_qty_col: '조정수량'})[['기준월', '제품코드', '조정수량']]

    store = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
    if store.empty:
        return None
    store = pd.merge(store, adj_df, on=['기준월', '제품코드'], how='left')
    store['계획수량'] = np.where(store['조정수량'].notna(), pd.to_numeric(store['조정수량'], errors='coerce'), store['계획수량'])
    affected = sorted(store[store['조정수량'].notna()]['기준월'].unique())
    store = store.drop(columns=['조정수량'])
    save_store(store[PLAN_COLS], PLAN_STORE)
    return affected


# =============================================================
# 히스토리 → 분석용 데이터프레임 (기존 comparison_df와 동일 구조)
# =============================================================
@st.cache_data
def build_history_df(plan_mtime, act_mtime, item_mtime, exc_mtime, rules_key):
    plan = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
    act = load_store(ACT_STORE, ACT_COLS, '실적수량')
    if plan.empty and act.empty:
        return None

    # 🎯 기간 한정 규칙 적용 (표시용 — 저장 원본은 불변)
    plan = apply_period_rules(plan, 'plan')
    act = apply_period_rules(act, 'actual')

    plan_g = plan.groupby(['기준월'] + GROUP_COLS, as_index=False)['계획수량'].sum() if not plan.empty \
        else pd.DataFrame(columns=['기준월'] + GROUP_COLS + ['계획수량'])
    act_g = act.groupby(['기준월'] + GROUP_COLS, as_index=False)['실적수량'].sum() if not act.empty \
        else pd.DataFrame(columns=['기준월'] + GROUP_COLS + ['실적수량'])

    comparison_df = pd.merge(plan_g, act_g, on=['기준월'] + GROUP_COLS, how='outer').fillna(0)

    item_info, final_drop_codes = load_item_info_and_dropcodes(file_mtime(item_master_path), file_mtime(exclusion_path))
    comparison_df = pd.merge(comparison_df, item_info, on='제품코드', how='left')
    comparison_df['제품명'] = comparison_df['제품명'].fillna('품목마스터 누락')
    comparison_df['국가'] = comparison_df['국가'].fillna('미분류')

    comparison_df = comparison_df[~comparison_df['제품코드'].isin(final_drop_codes)]
    for col in GROUP_COLS + ['제품명']:
        comparison_df = comparison_df[comparison_df[col] != '미상']
    return comparison_df


# =============================================================
# 사이드바: 월별 히스토리 등록 (롤링 업서트)
# =============================================================
# =============================================================
# 🔐 접근 제어 (배포 환경용 — 로컬에서 Secrets 미설정 시 아무 제한 없음)
#    Streamlit Cloud 앱 → Settings → Secrets 에 아래를 필요에 따라 설정:
#      ADMIN_PASSWORD  = "관리자비밀번호"   → 업로드/삭제 등 관리 기능 잠금 (미입력자는 읽기 전용)
#      VIEWER_PASSWORD = "열람비밀번호"     → 이 비밀번호를 입력해야 대시보드 열람 가능 (계정 불필요)
# =============================================================
def _get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""

_ADMIN_PW = _get_secret("ADMIN_PASSWORD")
_VIEWER_PW = _get_secret("VIEWER_PASSWORD")

if _ADMIN_PW:
    with st.sidebar.expander("🔐 관리자 로그인"):
        _pw_in = st.text_input("비밀번호", type="password", key="admin_pw_input")
    IS_ADMIN = (_pw_in == _ADMIN_PW)
    if not IS_ADMIN:
        st.sidebar.info("👀 조회 전용 모드. (업로드/관리는 관리자 전용)")
else:
    IS_ADMIN = True

# 열람 비밀번호 게이트: 관리자 인증자는 통과, 그 외에는 비밀번호 입력 전까지 화면 차단
# 🎯 인증에 성공하면 세션에 기록해 입력칸을 화면에서 제거. 창을 닫았다 새로 열면 다시 요구됨.
if _VIEWER_PW and not IS_ADMIN and not st.session_state.get("viewer_ok", False):
    _vpw_in = st.text_input("🔑 열람 비밀번호를 입력하세요", type="password", key="viewer_pw_input")
    if _vpw_in == _VIEWER_PW:
        st.session_state["viewer_ok"] = True
        st.rerun()
    if _vpw_in:
        st.error("비밀번호가 올바르지 않습니다.")
    else:
        st.info("비밀번호 입력 후 대시보드가 표시됩니다.")
    st.stop()

if IS_ADMIN:
    st.sidebar.header("📚 월별 히스토리 데이터 (영구 저장)")
    st.sidebar.caption("파일 업로드 → 반영할 월 확인 → 버튼 클릭 시 해당 월만 덮어쓰기(업서트). 나머지 월은 동결 보존")

    masters_ready = all(os.path.exists(p) for p in [master_path, item_master_path, exclusion_path])

    # 🎯 [성능] 업로드 파일 파싱 결과 캐시: 같은 파일이면 최초 1회만 엑셀을 해석.
    #    월 체크박스를 조작할 때마다 파일을 다시 읽던 문제(화면 비활성화 지연) 해결.
    @st.cache_data(show_spinner=False)
    def _cached_parse(kind, file_bytes, file_name, master_mtime):
        bio = io.BytesIO(file_bytes)
        master_lookup = load_master_lookup(master_mtime)
        if kind == 'plan':
            return process_plan_upload(bio, master_lookup)
        if kind == 'can':
            return process_can_upload(bio, master_lookup)
        if kind == 'act':
            return process_actual_upload(bio, master_lookup)
        return None

    def _month_commit_widget(uploaded, kind, commit_fn, key_prefix, label):
        """업로드 파일에서 월을 파싱해 보여주고, 선택한 월만 커밋 (파싱은 캐시됨)"""
        if uploaded is None:
            return
        if not masters_ready:
            st.sidebar.error("먼저 하단 ⚙️에 마스터 데이터 3종을 등록해주세요.")
            return
        try:
            with st.spinner(f"{label} 파일 해석 중... (같은 파일은 최초 1회만)"):
                parsed = _cached_parse(kind, uploaded.getvalue(), uploaded.name, file_mtime(master_path))
        except Exception as e:
            st.sidebar.error(f"{label} 파일 해석 실패: {e}")
            return
        if parsed is None or parsed.empty:
            st.sidebar.warning(f"{label}: 인식된 데이터가 없습니다.")
            return
        months_found = sorted(parsed['기준월'].dropna().unique())
        sel = st.sidebar.multiselect(f"↳ {label} 반영할 월", months_found, default=months_found, key=f"{key_prefix}_months")
        if st.sidebar.button(f"✅ {label} 히스토리 반영", key=f"{key_prefix}_btn"):
            if not sel:
                st.sidebar.warning("반영할 월을 선택해주세요.")
            else:
                extra = commit_fn(parsed, sel)
                st.sidebar.success(f"{label} 반영 완료: {', '.join(sel)}")
                if extra:
                    st.sidebar.info(f"⏱️ 월 마감 등록으로 진척도 데이터 자동 정리: {', '.join(extra)}")
                st.rerun()

    plan_file = st.sidebar.file_uploader("1. 수요계획 (USA,MEX)", type=['xlsx', 'csv'], key="up_plan")
    _month_commit_widget(plan_file, 'plan', lambda d, m: upsert_plan(d, m, 'USMX'), "plan", "수요계획(USA,MEX)")

    can_plan_file = st.sidebar.file_uploader("2. 수요계획 (CAN)", type=['xlsx', 'csv'], key="up_can")
    _month_commit_widget(can_plan_file, 'can', lambda d, m: upsert_plan(d, m, 'CAN'), "can", "수요계획(CAN)")

    actual_file = st.sidebar.file_uploader("3. 출고 실적 (월 마감)", type=['xlsx', 'csv'], key="up_act")
    _month_commit_widget(actual_file, 'act', lambda d, m: upsert_actual(d, m), "act", "출고실적")

    adj_plan_file = st.sidebar.file_uploader("4. 영업기획 조정계획 (선택)", type=['xlsx', 'csv'], key="up_adj")
    if adj_plan_file is not None:
        if st.sidebar.button("✅ 조정계획을 히스토리 계획에 반영", key="adj_btn"):
            affected = apply_adjustment(adj_plan_file)
            if affected:
                st.sidebar.success(f"조정 반영 완료: {', '.join(affected)}")
                st.rerun()
            else:
                st.sidebar.warning("조정계획 반영 실패: 형식 또는 저장된 계획을 확인해주세요.")

# --- 당월 진척도 데이터 ---
if IS_ADMIN:
    st.sidebar.divider()
    st.sidebar.header("⏱️ 당월 진척도 데이터")
    st.sidebar.caption("'마감 여부' 컬럼이 포함된 오더 데이터. 업로드 시 전체 교체(스냅샷)되며, 해당월 마감 실적이 히스토리에 등록되면 자동 삭제")

    prog_target = st.sidebar.text_input("진척도 대상월 (YYYY-MM)", value=pd.Timestamp.today().strftime('%Y-%m'), key="prog_month")
    prog_file = st.sidebar.file_uploader("5. 당월 오더/출고 데이터", type=['xlsx', 'csv'], key="up_prog")
    if prog_file is not None:
        if st.sidebar.button("✅ 진척도 데이터 반영 (전체 교체)", key="prog_btn"):
            tm = parse_month(prog_target)
            if not (isinstance(tm, str) and re.fullmatch(r'\d{4}-\d{2}', tm)):
                st.sidebar.error("대상월 형식이 올바르지 않습니다. 예: 2026-07")
            elif not masters_ready:
                st.sidebar.error("먼저 마스터 데이터 3종을 등록해주세요.")
            else:
                master_lookup = load_master_lookup(file_mtime(master_path))
                parsed = process_progress_upload(prog_file, master_lookup, tm)
                if parsed is None:
                    st.sidebar.error("'마감 여부' 컬럼을 찾을 수 없습니다.")
                else:
                    act_months_now = set(load_store(ACT_STORE, ACT_COLS, '실적수량')['기준월'].unique())
                    if tm in act_months_now:
                        st.sidebar.warning(f"{tm}은 이미 월 마감 실적이 등록된 월입니다. 진척도 대상월을 확인해주세요.")
                    else:
                        save_store(parsed, PROG_STORE)
                        st.sidebar.success(f"진척도 데이터 반영 완료 (대상월: {tm})")
                        st.rerun()

# --- 저장 현황 및 관리 ---
st.sidebar.divider()
st.sidebar.header("🗂️ 저장 데이터 현황/관리")
_plan_store = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
_act_store = load_store(ACT_STORE, ACT_COLS, '실적수량')
_prog_store = load_store(PROG_STORE, PROG_COLS, '실적수량')
plan_months = sorted(_plan_store['기준월'].unique())
usmx_months = sorted(_plan_store[_plan_store['소스'] == 'USMX']['기준월'].unique())
can_months = sorted(_plan_store[_plan_store['소스'] == 'CAN']['기준월'].unique())
act_months = sorted(_act_store['기준월'].unique())
prog_months = sorted(_prog_store['기준월'].unique())
st.sidebar.caption(f"계획(USA,MEX): {', '.join(usmx_months) if usmx_months else '없음'}")
st.sidebar.caption(f"계획(CAN): {', '.join(can_months) if can_months else '없음'}")
st.sidebar.caption(f"실적 보유: {', '.join(act_months) if act_months else '없음'}")
st.sidebar.caption(f"진척도 보유: {', '.join(prog_months) if prog_months else '없음'}")

if IS_ADMIN:
    with st.sidebar.expander("🧹 특정 월 삭제 / 전체 초기화"):
        del_target = st.selectbox("대상 저장소", ["계획", "실적", "진척도"], key="del_store")
        _opts = {'계획': plan_months, '실적': act_months, '진척도': prog_months}[del_target]
        if _opts:
            del_month_sel = st.selectbox("삭제할 월", _opts, key="del_month")
            if st.button("해당 월 삭제", key="del_btn"):
                _map = {'계획': (PLAN_STORE, PLAN_COLS, '계획수량'),
                        '실적': (ACT_STORE, ACT_COLS, '실적수량'),
                        '진척도': (PROG_STORE, PROG_COLS, '실적수량')}
                delete_month(*_map[del_target], del_month_sel)
                st.rerun()
        else:
            st.caption("저장된 월이 없습니다.")
        confirm_reset = st.checkbox("전체 초기화에 동의합니다 (복구 불가)", key="reset_ok")
        if st.button("🚨 히스토리 전체 초기화", key="reset_btn") and confirm_reset:
            for p in [PLAN_STORE, ACT_STORE, PROG_STORE]:
                if os.path.exists(p):
                    os.remove(p)
            st.rerun()

# --- 마스터 데이터 관리 (기존과 동일) ---
st.sidebar.divider()
if IS_ADMIN:
    st.sidebar.header("⚙️ 마스터 데이터 관리")
    st.sidebar.caption("최초 1회 업로드 시 시스템에 저장됨. 내용 갱신 필요 시 재업로드.")

    master_upload = st.sidebar.file_uploader("🔄 영업마스터 갱신 (선택)", type=['xlsx', 'csv'])
    item_master_upload = st.sidebar.file_uploader("🔄 품목마스터 갱신 (선택)", type=['xlsx', 'csv'])
    exclusion_upload = st.sidebar.file_uploader("🔄 제외 품목 리스트 갱신 (선택)", type=['xlsx', 'csv'])

    if master_upload:
        save_uploaded_file(master_upload, "master.xlsx")
        st.sidebar.success("영업마스터가 시스템에 저장(갱신)되었습니다!")
    if item_master_upload:
        save_uploaded_file(item_master_upload, "item_master.xlsx")
        st.sidebar.success("품목마스터가 시스템에 저장(갱신)되었습니다!")
    if exclusion_upload:
        save_uploaded_file(exclusion_upload, "exclusion.xlsx")
        st.sidebar.success("제외 품목 리스트가 시스템에 저장(갱신)되었습니다!")

master_ready = os.path.exists(master_path)
item_master_ready = os.path.exists(item_master_path)
exclusion_ready = os.path.exists(exclusion_path)

if master_ready and item_master_ready and exclusion_ready:
    st.sidebar.info("✅ 마스터 데이터 3종이 내장되어 정상 작동 중입니다.")
else:
    st.sidebar.warning("⚠️ 저장된 마스터 데이터가 없습니다. 위 ⚙️ 영역에 최초 1회 업로드 해주세요.")


# =============================================================
# 분석/표시 함수
# =============================================================
# 🎯 품목 단위 정확도 산출 후 그룹 평균 테이블 생성 (탭2 정확도 로직)
def build_item_avg_accuracy(df, index_cols):
    item_cols = list(dict.fromkeys(index_cols + ['제품코드']))
    item_level = df.groupby(item_cols + ['기준월'])[['계획수량', '실적수량']].sum().reset_index()
    item_level['_정확도'] = [compute_accuracy(p, a) for p, a in zip(item_level['계획수량'], item_level['실적수량'])]
    acc_pivot = item_level.pivot_table(index=index_cols, columns='기준월', values='_정확도', aggfunc='mean')
    return acc_pivot


# 🎯 탭1 콤비네이션 차트: 지정 색상 + 축 상한 고정(수량 8M / 정확도 80%, 초과 시 자동 확장)
def create_combo_chart(df, months_list, chart_key):
    if df.empty or not months_list:
        return
    if not PLOTLY_OK:
        st.info("📈 차트를 보려면 plotly 설치가 필요합니다. 터미널에서 `pip install plotly` 실행 후 새로고침 해주세요.")
        return

    monthly = df.groupby('기준월')[['계획수량', '실적수량']].sum().reindex(months_list).fillna(0)

    # 월별 정확도 = 품목별 정확도의 평균
    item_level = df.groupby(['제품코드', '기준월'])[['계획수량', '실적수량']].sum().reset_index()
    item_level['_정확도'] = [compute_accuracy(p, a) for p, a in zip(item_level['계획수량'], item_level['실적수량'])]
    acc_monthly = item_level.groupby('기준월')['_정확도'].mean().reindex(months_list)

    # 축 상한: 기본 8,000,000 / 80%. 데이터가 넘으면 잘리지 않게 자동 확장
    qty_peak = float(monthly[['계획수량', '실적수량']].max().max()) if not monthly.empty else 0.0
    y_max = max(8_000_000, qty_peak * 1.05)
    acc_valid = acc_monthly.dropna()
    acc_peak = float(acc_valid.max()) if len(acc_valid) else 0.0
    y2_max = 0.8 if acc_peak <= 0.8 else min(1.05, acc_peak + 0.05)

    # x축 라벨: 'Apr', 'May' 형태의 월 약어 (연도가 2개 이상 섞이면 'Apr '26'로 구분)
    def month_label(m, multi_year):
        try:
            dt = pd.to_datetime(str(m) + '-01')
            return dt.strftime("%b '%y") if multi_year else dt.strftime('%b')
        except Exception:
            return str(m)

    years = {str(m)[:4] for m in months_list}
    multi_year = len(years) > 1
    tick_labels = [month_label(m, multi_year) for m in months_list]

    # 정확도 마커 위 xx.x% 데이터 라벨
    acc_texts = ['' if pd.isna(v) else f"{v*100:.1f}%" for v in acc_monthly.values]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=months_list, y=monthly['계획수량'], name='계획',
        marker_color='#1E4D9A', opacity=0.95,
        hovertemplate='%{x}<br>계획: %{y:,.0f}<extra></extra>'
    ))
    fig.add_trace(go.Bar(
        x=months_list, y=monthly['실적수량'], name='실적',
        marker_color='#76A7E1', opacity=0.95,
        hovertemplate='%{x}<br>실적: %{y:,.0f}<extra></extra>'
    ))
    fig.add_trace(go.Scatter(
        x=months_list, y=acc_monthly.values, name='정확도', yaxis='y2',
        mode='lines+markers+text',
        text=acc_texts, textposition='top center',
        textfont=dict(size=12, color='#0A1A2F', family='Arial Black, sans-serif'),
        line=dict(shape='spline', smoothing=1.3, width=3, color='#0A1A2F'),
        marker=dict(size=14, color='#D7DFE9', line=dict(width=3.5, color='#0A1A2F')),
        hovertemplate='%{x}<br>정확도: %{y:.1%}<extra></extra>'
    ))
    fig.update_layout(
        barmode='group',
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
        # type='category': 날짜 자동 인식으로 중간 눈금(Mar 22 등)이 생기는 현상 차단
        xaxis=dict(type='category', tickmode='array',
                   tickvals=months_list, ticktext=tick_labels),
        yaxis=dict(title='수량', separatethousands=True, range=[0, y_max]),
        yaxis2=dict(title='정확도', overlaying='y', side='right',
                    range=[0, y2_max], tickformat='.0%', showgrid=False),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
        hovermode='x unified'
    )
    st.plotly_chart(fig, width='stretch', key=chart_key)


# 표시 형식 공통: 정확도/진척도는 %, 수량/GAP은 천단위 콤마, 결측/0은 '-'
def build_format_dict(cols):
    format_dict = {}
    for c in cols:
        if '진척도' in c:
            format_dict[c] = lambda x: '-' if pd.isna(x) else ('∞' if np.isinf(x) else f"{x*100:.1f}%")
        elif '정확도' in c:
            format_dict[c] = lambda x: '-' if pd.isna(x) else f"{x*100:.1f}%"
        elif ('계획' in c) or ('실적' in c) or ('GAP' in c):
            format_dict[c] = lambda x: '-' if pd.isna(x) or x == 0 else f"{int(x):,}"
    return format_dict


# 탭1/탭2용: acc_mode 'sum'=집계 총량 기준 정확도 / 'item_avg'=품목별 정확도의 평균
def create_styled_pivot(df, index_cols, months_list, acc_mode='sum'):
    if df.empty:
        return st.warning("해당 조건에 맞는 데이터가 없습니다.")

    pivot = df.pivot_table(
        index=index_cols,
        columns='기준월',
        values=['계획수량', '실적수량'],
        aggfunc='sum',
        fill_value=0
    )

    acc_pivot = build_item_avg_accuracy(df, index_cols) if acc_mode == 'item_avg' else None

    final_cols = []
    for m in months_list:
        if '계획수량' in pivot.columns and m in pivot['계획수량'].columns:
            p = pivot[('계획수량', m)]
            a = pivot[('실적수량', m)]
            pivot[('GAP', m)] = p - a  # GAP은 항상 총량 기준
            if acc_pivot is not None and m in acc_pivot.columns:
                pivot[('정확도', m)] = acc_pivot.reindex(pivot.index)[m].values
            else:
                acc_list = [compute_accuracy(p_val, a_val) for p_val, a_val in zip(p, a)]
                pivot[('정확도', m)] = acc_list
            final_cols.extend([('계획수량', m), ('실적수량', m), ('정확도', m), ('GAP', m)])

    if not final_cols:
        return st.warning("선택하신 월에 해당하는 실적/계획 데이터가 없습니다.")

    pivot = pivot[final_cols]

    # 화면 표시용 컬럼명 축약 (내부 데이터의 '계획수량'/'실적수량'은 그대로 유지됨)
    display_name = {'계획수량': '계획', '실적수량': '실적', '정확도': '정확도', 'GAP': 'GAP'}
    pivot.columns = [f"{m} {display_name.get(col, col)}" for col, m in pivot.columns]

    qty_cols = [c for c in pivot.columns if c.endswith('계획') or c.endswith('실적')]
    pivot = pivot[pivot[qty_cols].sum(axis=1) != 0]

    if pivot.empty:
        return st.info("선택된 기간에 유효한 데이터(계획 또는 실적)가 없습니다.")

    totals = []
    for c in pivot.columns:
        if '정확도' in c:
            totals.append(pivot[c].mean())
        else:
            totals.append(pivot[c].sum())

    total_index_name = tuple(['전체 합계/평균'] + [''] * (len(index_cols) - 1)) if len(index_cols) > 1 else '전체 합계/평균'

    format_dict = build_format_dict(pivot.columns)

    styled_main = pivot.style.format(format_dict)
    st.dataframe(styled_main, width='content', height=500, column_config=COMMON_COL_CONFIG)

    total_df = pd.DataFrame([totals], columns=pivot.columns, index=pd.Index([total_index_name], name=pivot.index.name))
    styled_total = total_df.style.format(format_dict).apply(
        lambda x: ['background-color: #e6e6e6; font-weight: bold; color: #000000'] * len(x), axis=1
    )
    st.dataframe(styled_total, width='content', column_config=COMMON_COL_CONFIG)


# 🎯 그룹별 월별 계획/실적/정확도/GAP 플랫 테이블 (정확도 = 품목별 정확도의 평균)
def build_flat_month_table(df, index_cols, months_list, include_qty=True):
    d = df[df['기준월'].isin(months_list)]
    if d.empty:
        return None, []
    g = d.groupby(index_cols + ['기준월'], as_index=False)[['계획수량', '실적수량']].sum()
    item_cols = list(dict.fromkeys(index_cols + ['제품코드']))
    il = d.groupby(item_cols + ['기준월'], as_index=False)[['계획수량', '실적수량']].sum()
    il['_acc'] = [compute_accuracy(p, a) for p, a in zip(il['계획수량'], il['실적수량'])]
    acc = il.groupby(index_cols + ['기준월'], as_index=False)['_acc'].mean()
    g = g.merge(acc, on=index_cols + ['기준월'], how='left')
    g['GAP'] = g['계획수량'] - g['실적수량']

    months_present = [m for m in months_list if m in set(g['기준월'])]
    if not months_present:
        return None, []

    out = None
    for m in months_present:
        sub = g[g['기준월'] == m].set_index(index_cols)
        if include_qty:
            sub = sub[['계획수량', '실적수량', '_acc', 'GAP']]
            sub.columns = [f"{m} 계획", f"{m} 실적", f"{m} 정확도", f"{m} GAP"]
        else:
            sub = sub[['_acc', 'GAP']]
            sub.columns = [f"{m} 정확도", f"{m} GAP"]
        out = sub if out is None else out.join(sub, how='outer')
    return out.reset_index(), months_present


def render_total_row(label_cols, value_cols, totals, table_key=None, col_config=None):
    total_df = pd.DataFrame([[('전체 합계/평균' if i == 0 else '') for i in range(len(label_cols))] + totals],
                            columns=label_cols + value_cols)
    styled = total_df.style.format(build_format_dict(value_cols)).apply(
        lambda x: ['background-color: #e6e6e6; font-weight: bold; color: #000000'] * len(x), axis=1
    )
    st.dataframe(styled, width='content', hide_index=True,
                 column_config=col_config if col_config is not None else COMMON_COL_CONFIG)


# 🎯 탭3 상단: 지점별 → 영업사원별 정확도/GAP 요약표 (지점 소계 + 전체 합계/평균)
def render_person_summary(df, months_list):
    if df.empty:
        return st.info("해당 조건에 맞는 데이터가 없습니다.")

    flat, mp = build_flat_month_table(df, ['영업지점명', '영업사원명'], months_list, include_qty=False)
    if flat is None:
        return st.info("선택하신 기간에 데이터가 없습니다.")
    br, _ = build_flat_month_table(df, ['영업지점명'], months_list, include_qty=False)

    value_cols = []
    for m in mp:
        value_cols.extend([f"{m} 정확도", f"{m} GAP"])
    acc_cols = [c for c in value_cols if '정확도' in c]
    gap_cols = [c for c in value_cols if 'GAP' in c]

    # 정확도 결측 & GAP 전무(0)인 유령 행 제거
    ghost = flat[acc_cols].isna().all(axis=1) & (flat[gap_cols].fillna(0).abs().sum(axis=1) == 0)
    flat = flat[~ghost]
    if flat.empty:
        return st.info("선택하신 기간에 유효한 데이터가 없습니다.")

    flat['_평균정확도'] = flat[acc_cols].mean(axis=1)
    br['_평균정확도'] = br[acc_cols].mean(axis=1)

    rows, subtotal_pos = [], []
    br_sorted = br.sort_values('_평균정확도', na_position='last')  # 문제 지점이 위로
    for _, brow in br_sorted.iterrows():
        b = brow['영업지점명']
        ppl = flat[flat['영업지점명'] == b].sort_values('_평균정확도', na_position='last')
        if ppl.empty:
            continue
        for _, prow in ppl.iterrows():
            rows.append([b, prow['영업사원명']] + [prow[c] for c in value_cols])
        rows.append([b, '📍 지점 소계'] + [brow[c] for c in value_cols])
        subtotal_pos.append(len(rows) - 1)

    if not rows:
        return st.info("선택하신 기간에 유효한 데이터가 없습니다.")

    label_cols = ['영업지점명', '영업사원명']
    result = pd.DataFrame(rows, columns=label_cols + value_cols)

    def highlight(row):
        if row.name in subtotal_pos:
            return ['background-color: #dce6f5; font-weight: bold; color: #000000'] * len(row)
        return [''] * len(row)

    styled = result.style.format(build_format_dict(value_cols)).apply(highlight, axis=1)
    h = min(520, 37 * (len(result) + 1) + 12)
    st.dataframe(styled, width='content', hide_index=True, height=h, column_config=COMMON_COL_CONFIG)

    # 전체 합계/평균: GAP = 사원 행 합계, 정확도 = 사원 행 정확도의 평균
    totals = [flat[c].mean() if '정확도' in c else flat[c].sum() for c in value_cols]
    render_total_row(label_cols, value_cols, totals)


# 🎯 탭3 메인: 제품 소계 행 + 정확도 오름차순 정렬 상세 테이블
def render_detail_table(df, index_cols, months_list):
    if df.empty:
        return st.warning("해당 조건에 맞는 데이터가 없습니다.")

    prod_cols = [c for c in ['제품코드', '제품명'] if c in index_cols]
    other_cols = [c for c in index_cols if c not in prod_cols]
    ordered = prod_cols + other_cols  # 제품 컬럼을 앞으로

    flat, mp = build_flat_month_table(df, ordered, months_list, include_qty=True)
    if flat is None:
        return st.warning("선택하신 월에 해당하는 실적/계획 데이터가 없습니다.")

    value_cols = []
    for m in mp:
        value_cols.extend([f"{m} 계획", f"{m} 실적", f"{m} 정확도", f"{m} GAP"])
    acc_cols = [c for c in value_cols if '정확도' in c]
    qty_cols = [c for c in value_cols if c.endswith('계획') or c.endswith('실적')]

    flat = flat[flat[qty_cols].fillna(0).sum(axis=1) != 0]
    if flat.empty:
        return st.info("선택된 기간에 유효한 데이터(계획 또는 실적)가 없습니다.")
    flat['_평균정확도'] = flat[acc_cols].mean(axis=1)

    rows, subtotal_pos = [], []

    if prod_cols and other_cols:
        # 제품 단위 소계 (정확도는 제품 총량 기준 → 탭1과 동일한 수치)
        sub, _ = build_flat_month_table(df, prod_cols, months_list, include_qty=True)
        sub = sub[sub[qty_cols].fillna(0).sum(axis=1) != 0]
        sub['_평균정확도'] = sub[acc_cols].mean(axis=1)
        sub = sub.sort_values('_평균정확도', na_position='last')  # 정확도 낮은 제품이 위로

        for _, srow in sub.iterrows():
            key_mask = np.logical_and.reduce([(flat[c] == srow[c]).values for c in prod_cols])
            block = flat[key_mask].sort_values('_평균정확도', na_position='last')
            if block.empty:
                continue
            for _, r in block.iterrows():
                rows.append([r[c] for c in ordered] + [r[c] for c in value_cols])
            rows.append([srow[c] for c in prod_cols] + ['📍 제품 소계'] + [''] * (len(other_cols) - 1)
                        + [srow[c] for c in value_cols])
            subtotal_pos.append(len(rows) - 1)
        result = pd.DataFrame(rows, columns=ordered + value_cols)
    else:
        result = flat.sort_values('_평균정확도', na_position='last')[ordered + value_cols].reset_index(drop=True)

    if result.empty:
        return st.info("선택된 기간에 유효한 데이터(계획 또는 실적)가 없습니다.")

    def highlight(row):
        if row.name in subtotal_pos:
            return ['background-color: #dce6f5; font-weight: bold; color: #000000'] * len(row)
        return [''] * len(row)

    styled = result.style.format(build_format_dict(value_cols)).apply(highlight, axis=1)
    h = min(520, 37 * (len(result) + 1) + 12)
    st.dataframe(styled, width='content', hide_index=True, height=h, column_config=COMMON_COL_CONFIG)

    # 전체 합계/평균: 세부(leaf) 행 기준 (소계 행 중복 합산 방지)
    totals = [flat[c].mean() if '정확도' in c else flat[c].sum() for c in value_cols]
    render_total_row(ordered, value_cols, totals)


# 🎯 탭3 딥다이브 필터: 키워드 검색 + 정확도 하위 품목 필터
def apply_product_filters(df, kw_input, acc_threshold, months_list):
    out = df
    if kw_input and kw_input.strip():
        kws = [k.strip() for k in kw_input.split(',') if k.strip()]
        if kws:
            mask = pd.Series(False, index=out.index)
            for k in kws:
                mask |= out['제품명'].astype(str).str.contains(k, case=False, regex=False, na=False)
                mask |= out['제품코드'].astype(str).str.contains(k, case=False, regex=False, na=False)
            out = out[mask]
    if acc_threshold < 100 and not out.empty:
        d = out[out['기준월'].isin(months_list)]
        if not d.empty:
            il = d.groupby(['제품코드', '기준월'], as_index=False)[['계획수량', '실적수량']].sum()
            il['_acc'] = [compute_accuracy(p, a) for p, a in zip(il['계획수량'], il['실적수량'])]
            item_mean = il.groupby('제품코드')['_acc'].mean()
            low_codes = item_mean[item_mean < acc_threshold / 100].index
            out = out[out['제품코드'].isin(low_codes)]
    return out


# =============================================================
# 🎯 [추가됨] 탭4: 전월 대비 정확도/GAP 개선 (영업사원별)
# =============================================================
def render_improvement_tab(df, available_months):
    if df is None or df.empty or len(available_months) < 2:
        return st.info("비교하려면 히스토리에 2개월 이상의 데이터가 필요합니다.")

    def _prev(m):
        try:
            return (pd.Period(m, freq='M') - 1).strftime('%Y-%m')
        except Exception:
            return None

    month_set = set(available_months)
    cands = [m for m in available_months if _prev(m) in month_set]
    default_idx = available_months.index(cands[-1]) if cands else len(available_months) - 1
    month = st.selectbox("📅 평가 기준월 (이 달과 바로 전월을 비교)", available_months, index=default_idx, key="imp_month")
    pm = _prev(month)
    if pm not in month_set:
        return st.warning(f"전월({pm}) 데이터가 히스토리에 없어 비교할 수 없습니다.")

    st.caption(f"💡 정확도 = 사원(지점)별 품목별 정확도의 평균 / GAP = 총 계획 - 총 실적. "
               f"정확도 개선 = {month} 정확도 - {pm} 정확도 (%p), GAP 개선 = {pm} GAP - {month} GAP. "
               "정렬: 지점은 기준월 정확도 내림차순, 지점 내 사원은 정확도 개선 내림차순. "
               "🟢 옅은 녹색 = 전월 대비 정확도 상승, 🔴 옅은 붉은색 = 하락. ")

    flat, mp = build_flat_month_table(df, ['영업지점명', '영업사원명'], [pm, month], include_qty=False)
    if flat is None or len(mp) < 2:
        return st.warning("두 달 모두 데이터가 있어야 비교할 수 있습니다.")
    br, _ = build_flat_month_table(df, ['영업지점명'], [pm, month], include_qty=False)

    acc_prev, acc_cur = f"{pm} 정확도", f"{month} 정확도"
    gap_prev, gap_cur = f"{pm} GAP", f"{month} GAP"

    for t in (flat, br):
        t['정확도 개선'] = t[acc_cur] - t[acc_prev]
        t['GAP 개선'] = t[gap_prev] - t[gap_cur]

    # 두 달 모두 유효 데이터가 없는 유령 행 제거
    ghost = flat[[acc_prev, acc_cur]].isna().all(axis=1) & (flat[[gap_prev, gap_cur]].fillna(0).abs().sum(axis=1) == 0)
    flat = flat[~ghost]
    if flat.empty:
        return st.info("선택하신 기간에 유효한 데이터가 없습니다.")

    value_cols = [acc_prev, acc_cur, '정확도 개선', gap_prev, gap_cur, 'GAP 개선']
    label_cols = ['영업지점명', '영업사원명']

    rows, subtotal_pos, used_branches = [], [], []
    br_sorted = br.sort_values(acc_cur, ascending=False, na_position='last')  # 지점: 기준월 정확도 내림차순
    for _, brow in br_sorted.iterrows():
        b = brow['영업지점명']
        ppl = flat[flat['영업지점명'] == b].sort_values('정확도 개선', ascending=False, na_position='last')
        if ppl.empty:
            continue
        used_branches.append(b)
        for _, prow in ppl.iterrows():
            rows.append([b, prow['영업사원명']] + [prow[c] for c in value_cols])
        rows.append([b, '📍 지점 소계'] + [brow[c] for c in value_cols])
        subtotal_pos.append(len(rows) - 1)

    if not rows:
        return st.info("선택하신 기간에 유효한 데이터가 없습니다.")

    result = pd.DataFrame(rows, columns=label_cols + value_cols)

    fmt = build_format_dict(value_cols)
    fmt['정확도 개선'] = lambda x: '-' if pd.isna(x) else f"{x*100:+.1f}%p"
    fmt['GAP 개선'] = lambda x: '-' if pd.isna(x) else f"{int(x):+,}"

    def highlight(row):
        if row.name in subtotal_pos:
            return ['background-color: #dce6f5; font-weight: bold; color: #000000'] * len(row)
        v = row['정확도 개선']
        if pd.notna(v) and v > 0:
            return ['background-color: #e6f4ea; color: #000000'] * len(row)
        if pd.notna(v) and v < 0:
            return ['background-color: #fbe9e9; color: #000000'] * len(row)
        return [''] * len(row)

    styled = result.style.format(fmt).apply(highlight, axis=1)
    h = min(560, 37 * (len(result) + 1) + 12)
    st.dataframe(styled, width='content', hide_index=True, height=h, column_config=COMMON_COL_CONFIG)

    # 전체 평균: 두 가지 기준을 모두 표시
    # 🎯 [수정됨] ① 사원 기준 = 표의 사원 행 전체 평균 (사원 1명 = 1표)
    #            ② 지점 기준 = 표에 표시된 지점 소계들의 평균 (지점 1개 = 1표, 탭2 전체 평균과 동일·손검산 일치)
    #    GAP은 두 기준 모두 동일한 전체 합계. 개선값은 '표시된 전월·당월 값의 차이'로 계산해 검산이 항상 일치.
    br_used = br[br['영업지점명'].isin(used_branches)]
    p_acc_prev, p_acc_cur = flat[acc_prev].mean(), flat[acc_cur].mean()
    b_acc_prev, b_acc_cur = br_used[acc_prev].mean(), br_used[acc_cur].mean()
    tot_gap_prev, tot_gap_cur = br_used[gap_prev].sum(), br_used[gap_cur].sum()
    total_df = pd.DataFrame([
        ['전체 평균 (사원 기준)', '', p_acc_prev, p_acc_cur, p_acc_cur - p_acc_prev,
         tot_gap_prev, tot_gap_cur, tot_gap_prev - tot_gap_cur],
        ['전체 평균 (지점 기준)', '', b_acc_prev, b_acc_cur, b_acc_cur - b_acc_prev,
         tot_gap_prev, tot_gap_cur, tot_gap_prev - tot_gap_cur],
    ], columns=label_cols + value_cols)
    styled_total = total_df.style.format(fmt).apply(
        lambda x: ['background-color: #e6e6e6; font-weight: bold; color: #000000'] * len(x), axis=1
    )
    st.dataframe(styled_total, width='content', hide_index=True, column_config=COMMON_COL_CONFIG)


# =============================================================
# 🎯 [추가됨] 탭5: 당월 진척도 렌더링
# =============================================================
def render_progress_tab():
    prog = load_store(PROG_STORE, PROG_COLS, '실적수량')
    act = load_store(ACT_STORE, ACT_COLS, '실적수량')

    # 자동 세대교체 이중 안전장치: 마감 실적이 있는 월의 진척도는 화면에서도 제거
    closed = prog['기준월'].isin(set(act['기준월'].unique()))
    if not prog.empty and closed.any():
        save_store(prog[~closed], PROG_STORE)
        prog = prog[~closed]
        st.info("월 마감 실적이 등록된 월의 진척도 데이터가 자동 정리되었습니다.")

    if prog.empty:
        return st.info("좌측 ⏱️ 영역에서 당월 오더/출고 데이터('마감 여부' 컬럼 포함)를 업로드해주세요. 계획은 히스토리에 등록된 해당월 계획을 자동으로 사용합니다.")

    p_months = sorted(prog['기준월'].unique())
    month = st.selectbox("📅 진척도 대상월", p_months, index=len(p_months) - 1)
    # 🎯 기간 한정 규칙 적용 (KDH 한시 통합, 시작월부터 제외 — 저장 원본은 불변)
    prog_m = apply_period_rules(prog[prog['기준월'] == month], 'actual')
    if prog_m.empty:
        return st.info("기간 한정 규칙 적용 후 남은 진척도 데이터가 없습니다.")

    # 마감 여부 선택 (기본: 해당월 출고 확정만)
    statuses = sorted(prog_m['마감여부'].unique())
    try:
        mnum = str(int(month[5:7]))
    except Exception:
        mnum = ''
    default_sel = [s for s in statuses if '확정' in s and (mnum and f"{mnum}월" in s)]
    if not default_sel:
        default_sel = [s for s in statuses if '확정' in s] or statuses
    sel_status = st.multiselect("✅ 집계에 포함할 '마감 여부' 상태", statuses, default=default_sel)
    st.caption("💡 기본값은 출고 확정 오더 기준 실적. '출고 미확정' 등을 추가하면 해당 오더가 전량 당월 출고된다고 가정한 예상 수량이 됩니다.")
    if not sel_status:
        return st.warning("집계할 마감 여부 상태를 1개 이상 선택해주세요.")

    prog_sel = prog_m[prog_m['마감여부'].isin(sel_status)].groupby(GROUP_COLS, as_index=False)['실적수량'].sum()

    # 계획: 히스토리 저장소의 해당월 계획을 자동 사용 (기간 한정 규칙 동일 적용)
    plan_store = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
    plan_rows = apply_period_rules(plan_store[plan_store['기준월'] == month], 'plan')
    plan_m = plan_rows.groupby(GROUP_COLS, as_index=False)['계획수량'].sum()
    if plan_m.empty:
        st.warning(f"⚠️ 히스토리에 {month} 계획이 없습니다. 좌측 📚 영역에서 해당월 계획을 먼저 반영해주세요. (아래는 실적만 표시됩니다)")

    merged = pd.merge(plan_m, prog_sel, on=GROUP_COLS, how='outer').fillna(0)

    # 품목마스터 정보 부착 + 제외 품목/미상 정리 (히스토리 뷰와 동일 기준)
    item_info, final_drop_codes = load_item_info_and_dropcodes(file_mtime(item_master_path), file_mtime(exclusion_path))
    merged = pd.merge(merged, item_info, on='제품코드', how='left')
    merged['제품명'] = merged['제품명'].fillna('품목마스터 누락')
    merged['국가'] = merged['국가'].fillna('미분류')
    merged = merged[~merged['제품코드'].isin(final_drop_codes)]
    for col in GROUP_COLS + ['제품명']:
        merged = merged[merged[col] != '미상']

    if merged.empty:
        return st.info("집계 가능한 데이터가 없습니다.")

    # 🎯 [추가됨] 국가 필터 (USA/MEX/CAN 등 복수 선택·해제)
    all_countries = sorted(merged['국가'].unique())
    sel_countries = st.multiselect("🌍 국가 필터", all_countries, default=all_countries, key="prog_country")
    merged = merged[merged['국가'].isin(sel_countries)]
    if merged.empty:
        return st.info("선택한 국가에 해당하는 데이터가 없습니다.")

    fmt_cols = ['계획', '실적', '진척도', 'GAP']

    # --- ① 품목별 진척도 ---
    st.markdown("---")
    st.markdown(f"##### ① 품목별 진척도 ({month}, 진척도 = 실적 ÷ 계획, 오름차순)")
    prod = merged.groupby(['제품코드', '제품명'], as_index=False)[['계획수량', '실적수량']].sum()
    prod['진척도'] = [compute_progress(p, a) for p, a in zip(prod['계획수량'], prod['실적수량'])]
    prod['GAP'] = prod['계획수량'] - prod['실적수량']
    prod = prod.sort_values('진척도', na_position='last')
    prod_disp = prod.rename(columns={'계획수량': '계획', '실적수량': '실적'})[['제품코드', '제품명'] + fmt_cols].reset_index(drop=True)
    # 🎯 [추가됨] 계획 없이 실적 발생(진척도 ∞) 품목 행은 옅은 붉은색 처리
    inf_rows_prod = set(prod_disp.index[np.isinf(prod_disp['진척도'].fillna(0))])

    def highlight_prod(row):
        if row.name in inf_rows_prod:
            return ['background-color: #fbe9e9; color: #000000'] * len(row)
        return [''] * len(row)

    h1 = min(520, 37 * (len(prod_disp) + 1) + 12)
    st.dataframe(prod_disp.style.format(build_format_dict(fmt_cols)).apply(highlight_prod, axis=1),
                 width='content', hide_index=True, height=h1, column_config=PROG_TABLE_CONFIG)
    t_plan, t_act = prod['계획수량'].sum(), prod['실적수량'].sum()
    render_total_row(['제품코드', '제품명'], fmt_cols,
                     [t_plan, t_act, compute_progress(t_plan, t_act), t_plan - t_act],
                     col_config=PROG_TABLE_CONFIG)

    # --- ② 진척도 하위 품목 딥다이브 ---
    st.markdown("---")
    st.markdown("##### ② 진척도 하위 품목 상세 (제품 × 영업부 × 지점 × 사원)")
    thr = st.number_input("진척도 X% 이하 품목만 (100=전체)", min_value=0, max_value=500, value=100, step=5, key="prog_thr")
    # 🎯 [수정됨] 진척도 무한대(계획 없이 실적 발생) 품목은 항상 리스트 맨 아래에 붉은색으로 포함
    low_codes = prod[prod['진척도'].fillna(np.inf) <= thr / 100]['제품코드'].tolist()   # 유한 진척도만
    inf_codes = prod[np.isinf(prod['진척도'].fillna(0))]['제품코드'].tolist()          # 계획 0 & 실적 발생
    list_codes = low_codes + inf_codes
    low_df = merged[merged['제품코드'].isin(list_codes)]
    if low_df.empty:
        st.info("조건에 해당하는 품목이 없습니다.")
        return
    st.caption("💡 표 하단의 붉은색 행은 계획 없이 실적이 발생한 품목(진척도 ∞)입니다.")

    org_cols = ['영업부명', '영업지점명', '영업사원명']
    detail = low_df.groupby(['제품코드', '제품명'] + org_cols, as_index=False)[['계획수량', '실적수량']].sum()
    detail['진척도'] = [compute_progress(p, a) for p, a in zip(detail['계획수량'], detail['실적수량'])]
    detail['GAP'] = detail['계획수량'] - detail['실적수량']

    label_cols = ['제품코드', '제품명'] + org_cols
    rows, subtotal_pos, inf_pos = [], [], set()
    prod_low = prod[prod['제품코드'].isin(list_codes)]  # 진척도 오름차순 → ∞ 품목이 자동으로 맨 아래
    inf_code_set = set(inf_codes)
    for _, srow in prod_low.iterrows():
        is_inf = srow['제품코드'] in inf_code_set
        block = detail[detail['제품코드'] == srow['제품코드']].sort_values('진척도', na_position='last')
        if block.empty:
            continue
        for _, r in block.iterrows():
            rows.append([r[c] for c in label_cols] + [r['계획수량'], r['실적수량'], r['진척도'], r['GAP']])
            if is_inf:
                inf_pos.add(len(rows) - 1)
        rows.append([srow['제품코드'], srow['제품명'], '📍 제품 소계', '', '']
                    + [srow['계획수량'], srow['실적수량'], srow['진척도'], srow['GAP']])
        subtotal_pos.append(len(rows) - 1)
        if is_inf:
            inf_pos.add(len(rows) - 1)

    result = pd.DataFrame(rows, columns=label_cols + fmt_cols)

    def highlight(row):
        if row.name in subtotal_pos and row.name in inf_pos:
            return ['background-color: #f2c9c9; font-weight: bold; color: #000000'] * len(row)
        if row.name in subtotal_pos:
            return ['background-color: #dce6f5; font-weight: bold; color: #000000'] * len(row)
        if row.name in inf_pos:
            return ['background-color: #fbe9e9; color: #000000'] * len(row)
        return [''] * len(row)

    styled = result.style.format(build_format_dict(fmt_cols)).apply(highlight, axis=1)
    h2 = min(520, 37 * (len(result) + 1) + 12)
    st.dataframe(styled, width='content', hide_index=True, height=h2, column_config=PROG_TABLE_CONFIG)
    d_plan, d_act = detail['계획수량'].sum(), detail['실적수량'].sum()
    render_total_row(label_cols, fmt_cols,
                     [d_plan, d_act, compute_progress(d_plan, d_act), d_plan - d_act],
                     col_config=PROG_TABLE_CONFIG)

    # --- ③ 선택 품목의 지점·사원별 GAP ---
    st.markdown("---")
    st.markdown("##### ③ 선택 품목 기준 지점 · 영업사원별 GAP (GAP 큰 순)")
    st.caption("💡 위 ②에서 지정한 진척도 조건에 걸린 품목들만 집계한 GAP입니다. GAP = 계획 - 실적 (양수 = 미달). "
               "②에 리스트업된 제품 중 진척도가 무한대(∞)인 제품은 GAP 왜곡 방지를 위해 집계에서 제외.")
    gap_df = merged[merged['제품코드'].isin(low_codes)]  # 🎯 ∞ 품목 제외 (유한 진척도 품목만)
    if gap_df.empty:
        return st.info("GAP 집계 대상 품목이 없습니다. (∞ 품목 제외 기준)")
    pg = gap_df.groupby(['영업지점명', '영업사원명'], as_index=False)[['계획수량', '실적수량']].sum()
    pg['GAP'] = pg['계획수량'] - pg['실적수량']
    bg = gap_df.groupby(['영업지점명'], as_index=False)[['계획수량', '실적수량']].sum()
    bg['GAP'] = bg['계획수량'] - bg['실적수량']

    g_cols = ['계획', '실적', 'GAP']
    rows3, subtotal_pos3 = [], []
    for _, brow in bg.sort_values('GAP', ascending=False).iterrows():
        b = brow['영업지점명']
        ppl = pg[pg['영업지점명'] == b].sort_values('GAP', ascending=False)
        for _, prow in ppl.iterrows():
            rows3.append([b, prow['영업사원명'], prow['계획수량'], prow['실적수량'], prow['GAP']])
        rows3.append([b, '📍 지점 소계', brow['계획수량'], brow['실적수량'], brow['GAP']])
        subtotal_pos3.append(len(rows3) - 1)

    result3 = pd.DataFrame(rows3, columns=['영업지점명', '영업사원명'] + g_cols)

    def highlight3(row):
        if row.name in subtotal_pos3:
            return ['background-color: #dce6f5; font-weight: bold; color: #000000'] * len(row)
        return [''] * len(row)

    styled3 = result3.style.format(build_format_dict(g_cols)).apply(highlight3, axis=1)
    h3 = min(520, 37 * (len(result3) + 1) + 12)
    st.dataframe(styled3, width='content', hide_index=True, height=h3, column_config=PROG_TABLE_CONFIG)
    render_total_row(['영업지점명', '영업사원명'], g_cols,
                     [pg['계획수량'].sum(), pg['실적수량'].sum(), pg['계획수량'].sum() - pg['실적수량'].sum()],
                     col_config=PROG_TABLE_CONFIG)


# =============================================================
# 메인 로직 (히스토리 저장소 기반: 재업로드 없이 항상 표시)
# =============================================================
if master_ready and item_master_ready and exclusion_ready:
    raw_df = build_history_df(file_mtime(PLAN_STORE), file_mtime(ACT_STORE),
                              file_mtime(item_master_path), file_mtime(exclusion_path),
                              PERIOD_RULES_KEY)

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 1. 제품별 실적 뷰",
        "🏢 2. 영업조직별 실적 뷰",
        "🛠️ 3. 상세 분석",
        "📈 4. 전월 대비 개선",
        "⏱️ 5. 당월 진척도"
    ])

    if raw_df is None or raw_df.empty:
        with tab1:
            st.info("좌측 📚 영역에서 계획/실적 데이터를 히스토리에 반영해주세요. 한 번 반영하면 재업로드 없이 계속 표시됩니다.")
        with tab2:
            st.info("히스토리 데이터가 없습니다.")
        with tab3:
            st.info("히스토리 데이터가 없습니다.")
        with tab4:
            st.info("히스토리 데이터가 없습니다.")
    else:
        st.subheader("🔍 통합 데이터 필터")

        available_months = sorted([m for m in raw_df['기준월'].unique() if pd.notna(m) and str(m).strip() != ''])
        if len(available_months) >= 2:
            start_month, end_month = st.select_slider(
                "📅 조회할 월(Month) 범위 지정",
                options=available_months,
                value=(available_months[0], available_months[-1])
            )
        elif len(available_months) == 1:
            start_month = end_month = available_months[0]
            st.info(f"📅 단일 월({start_month}) 데이터만 존재합니다.")
        else:
            start_month, end_month = None, None

        selected_months = [m for m in available_months if start_month <= m <= end_month]

        # 🎯 국가는 포함 선택만으로 제어, 품목 상세 필터는 탭3 딥다이브 검색으로 대체
        col1, col2 = st.columns(2)
        with col1:
            all_countries = sorted(raw_df['국가'].unique())
            sel_countries = st.multiselect("🌍 국가 포함", all_countries, default=all_countries)
        with col2:
            all_codes = sorted(raw_df['제품코드'].unique())
            exclude_codes = st.multiselect("❌ 제외할 제품코드/품목 (임시 제외)", all_codes, default=[])

        filtered_df = raw_df[
            (raw_df['기준월'].isin(selected_months)) &
            (raw_df['국가'].isin(sel_countries)) &
            (~raw_df['제품코드'].isin(exclude_codes))
        ]

        with tab1:
            st.markdown("##### 📈 월별 계획·실적(Bar) + 정확도(Line) 트렌드")
            create_combo_chart(filtered_df, selected_months, chart_key='combo_tab1')
            st.markdown("##### 품목별 수요계획 대비 판매실적")
            create_styled_pivot(filtered_df, ['제품코드', '제품명', '국가'], selected_months)

        with tab2:
            st.markdown("##### 지점별 수요계획 대비 판매실적")
            st.caption("💡 정확도 = 각 지점별 품목별 정확도의 평균 (GAP은 총 계획량 - 총 실적량 기준)")
            create_styled_pivot(filtered_df, ['영업부명', '영업지점명'], selected_months, acc_mode='item_avg')

        with tab3:
            st.markdown("##### 영업부/지점/사원을 좁혀가며, 이슈 품목 딥다이브")
            # 🎯 [추가됨] 평가 제외 조직(EVAL_EXCLUDE_ORGS)은 상세 분석에서 제외
            t3_base = filtered_df[
                (~filtered_df['영업부명'].astype(str).str.strip().isin(EVAL_EXCLUDE_ORGS)) &
                (~filtered_df['영업지점명'].astype(str).str.strip().isin(EVAL_EXCLUDE_ORGS))
            ]
            if EVAL_EXCLUDE_ORGS:
                st.caption(f"※ 평가 제외 조직: {', '.join(EVAL_EXCLUDE_ORGS)} (탭1·2 전체 집계에는 포함)")
            f_col1, f_col2, f_col3 = st.columns(3)
            with f_col1:
                dept_opts = ['(전체)'] + sorted(t3_base['영업부명'].unique())
                t3_dept = st.selectbox("▶ 영업부 선택", dept_opts)
            dept_df = t3_base if t3_dept == '(전체)' else t3_base[t3_base['영업부명'] == t3_dept]
            with f_col2:
                branch_opts = ['(전체)'] + sorted(dept_df['영업지점명'].unique())
                t3_branch = st.selectbox("▶ 영업지점 선택", branch_opts)
            branch_df = dept_df if t3_branch == '(전체)' else dept_df[dept_df['영업지점명'] == t3_branch]
            with f_col3:
                person_opts = ['(전체)'] + sorted(branch_df['영업사원명'].unique())
                t3_person = st.selectbox("▶ 영업사원 선택", person_opts)
            t3_df = branch_df if t3_person == '(전체)' else branch_df[branch_df['영업사원명'] == t3_person]

            # 🎯 딥다이브 전용 필터 (상단 멀티선택 없이 간편 검색)
            st.markdown("###### 🔎 품목 딥다이브 필터")
            d_col1, d_col2 = st.columns([2, 1])
            with d_col1:
                kw_input = st.text_input(
                    "제품 검색 (제품코드/제품명 일부, 쉼표로 여러 개 입력)",
                    placeholder="예: 신라면, 101070"
                )
            with d_col2:
                acc_threshold = st.number_input(
                    "정확도 필터 (기간 평균 %가 특정값 미만인 품목만 조회)",
                    min_value=0, max_value=100, value=100, step=5
                )

            summary_base = apply_product_filters(branch_df, kw_input, acc_threshold, selected_months)
            t3_filtered = apply_product_filters(t3_df, kw_input, acc_threshold, selected_months)

            st.markdown("---")
            st.markdown("##### 👥 지점별 · 영업사원별 정확도/GAP 요약")
            st.caption("💡 지점 소계 정확도는 탭2(지점 품목별 정확도 평균)와 동일 기준이며, 사원 드롭다운과 무관하게 선택 지점 내 전체 사원 조회. 품목 필터를 걸면 '그 품목들에 대한' 조회. 정렬: 정확도 낮은 순.")
            render_person_summary(summary_base, selected_months)

            st.markdown("---")
            st.markdown("##### 📋 상세 테이블 (제품 소계 + 정확도 오름차순)")
            dynamic_rows = st.multiselect(
                "📌 행(Row)으로 볼 항목 배치. (제품코드/제품명 포함 시 제품 소계 자동 표시)",
                ['제품코드', '제품명', '영업부명', '영업지점명', '영업사원명', '국가'],
                default=['제품코드', '제품명', '영업사원명']
            )
            st.caption("💡 정확도 = 해당 행의 품목별 정확도 평균 / 📍 제품 소계 = 해당 제품 전체 합계와 총량 기준 정확도(탭1과 동일 수치). 정렬은 기간 평균 정확도 오름차순(이슈 품목·행이 맨 위). 하단 전체 평균 = 표시된 세부 행들의 평균이라, 행 구성에 따라 위 요약표의 사원 기준 평균과 다를 수 있음")

            if dynamic_rows:
                render_detail_table(t3_filtered, dynamic_rows, selected_months)

        with tab4:
            st.markdown("##### 전월 대비 정확도/GAP 개선 (사원별)")
            # 국가/제외 품목 필터는 적용하되, 월은 상단 슬라이더와 무관하게 자체 선택
            # 🎯 [추가됨] 평가 제외 조직(EVAL_EXCLUDE_ORGS)은 개선 평가에서 제외
            imp_base = raw_df[
                (raw_df['국가'].isin(sel_countries)) &
                (~raw_df['제품코드'].isin(exclude_codes)) &
                (~raw_df['영업부명'].astype(str).str.strip().isin(EVAL_EXCLUDE_ORGS)) &
                (~raw_df['영업지점명'].astype(str).str.strip().isin(EVAL_EXCLUDE_ORGS))
            ]
            if EVAL_EXCLUDE_ORGS:
                st.caption(f"※ 평가 제외 조직: {', '.join(EVAL_EXCLUDE_ORGS)}")
            render_improvement_tab(imp_base, available_months)

    with tab5:
        st.markdown("##### 당월 판매 진척도 점검 (주차별 중간 점검용)")
        render_progress_tab()

else:
    st.info("하단 ⚙️ 마스터 데이터 3종을 먼저 등록해주세요. 등록 후 좌측 📚 영역에서 계획/실적을 히스토리에 반영하면, 재업로드 없이 항상 대시보드가 표시됩니다.")