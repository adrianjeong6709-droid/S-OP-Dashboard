import streamlit as st
import pandas as pd
import numpy as np
import re
import os
import io
from datetime import timedelta

# 🎯 콤비네이션 차트용 Plotly (미설치 시에도 대시보드는 정상 작동)
try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False

# =============================================================
# 🔐 접근 제어 설정 읽기 (set_page_config보다 먼저 실행되어야 함)
#    Streamlit Cloud 앱 → Settings → Secrets 에 아래를 설정:
#      VIEWER_PASSWORD = "열람비밀번호"   → 이 비밀번호를 입력해야 열람 가능 (계정 불필요)
#      READ_ONLY = true                  → 관리 UI를 숨긴 조회 전용 + 사이드바 접힘 상태로 시작
#    ※ 데이터 갱신은 로컬에서 수행 후 _History_Data 폴더를 GitHub에 커밋하는 방식이므로,
#      클라우드에서는 관리 기능이 필요 없습니다. 로컬(Secrets 미설정)은 항상 전체 기능이 열립니다.
# =============================================================
def _get_secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

_VIEWER_PW = _get_secret("VIEWER_PASSWORD")
_READ_ONLY = bool(_get_secret("READ_ONLY", False))
IS_ADMIN = not _READ_ONLY   # READ_ONLY=true → 조회 전용

st.set_page_config(
    page_title="S&OP Dashboard",
    layout="wide",
    # 🎯 조회 전용(클라우드)에서는 사이드바를 접은 상태로 시작. 로컬은 기존처럼 펼침.
    initial_sidebar_state=("collapsed" if _READ_ONLY else "expanded")
)
st.title("S&OP 수요계획 대비 실적 대시보드 (고도화 뷰)")

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

# =============================================================
# 🎯 [추가됨] 월 목표 대비 진척현황(금액) 전용 저장소 및 설정
# =============================================================
GOAL_STORE = os.path.join(HIST_DIR, "sales_goal.csv")        # 연 1회 영업목표 (영구)
BILL_STORE = os.path.join(HIST_DIR, "billing_done.csv")      # 빌링완료 스냅샷
PRE_STORE = os.path.join(HIST_DIR, "preship_orders.csv")     # 빌링전 스냅샷
GOAL_META = os.path.join(HIST_DIR, "goal_meta.csv")          # 스냅샷 기준일자

GOAL_COLS = ['영업부코드', '영업부명', '영업지점코드', '영업지점명', '기준월', '목표금액']
BILL_COLS = ['기준월', '영업부코드', '영업지점코드', '박스', '금액']
PRE_COLS = ['기준월', '영업부코드', '영업지점코드', '상태', '출고예정일', '인도조건', '박스', '금액']

# 🎯 원본 파일의 컬럼 위치(엑셀 열 문자). 파일 양식이 바뀌면 여기만 수정하면 됩니다.
BILLING_COL_MAP = {'영업부코드': 'AM', '영업지점코드': 'AO', '박스': 'O', '금액': 'S'}
PRESHIP_COL_MAP = {'문서구분': 'C', '상태': 'AA', '박스': 'V', '금액': 'W',
                   '출고예정일': 'AK', '인도조건': 'AH',
                   '영업부코드': 'L', '영업지점코드': 'N'}
PRESHIP_DOCTYPE = 'YOCO'      # 빌링전 데이터에서 기본으로 선택하는 문서구분
STATUS_SHIPPED = 'C3'         # 출고 완료
STATUS_BILLED = 'C4'          # 빌링 완료(빌링전 파일에서는 집계 제외)
DELIVERY_DDP = 'DDP'          # 배송 대기중
DELIVERY_EXW = 'EXW'          # 픽업 대기중

# 월 목표 탭 표의 컬럼 넓이 (원하는 픽셀로 조절 가능)
GOAL_COL_CONFIG = {
    "영업부": st.column_config.Column(width=110),
    "영업지점": st.column_config.Column(width=150),
}

def col_letter_to_idx(letter):
    """엑셀 열 문자(A, AM 등) → 0-based 인덱스"""
    idx = 0
    for ch in str(letter).strip().upper():
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1

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
# 🎯 [성능] CSV 저장소는 파일이 바뀌지 않는 한 다시 읽지 않음 (mtime 캐시)
@st.cache_data(show_spinner=False)
def _read_store_csv(path, mtime):
    return pd.read_csv(path, dtype=str, encoding='utf-8-sig')

def load_store(path, columns, qty_col):
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    df = _read_store_csv(path, file_mtime(path)).copy()
    for c in columns:
        if c not in df.columns:
            df[c] = '' if c != qty_col else 0
    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
    # 문자 컬럼의 결측은 빈 문자열로 (정렬 시 str/float 혼합 오류 방지)
    for c in columns:
        if c != qty_col:
            df[c] = df[c].fillna('').astype(str)
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
# 🎯 [추가됨] 탭6: 월 목표 대비 진척현황 (금액)
# =============================================================
def read_any(file):
    """엑셀/CSV 자동 판별 읽기 (헤더 1행 기준)"""
    name = getattr(file, 'name', '')
    if str(name).lower().endswith('.csv'):
        try:
            return pd.read_csv(file, dtype=str, encoding='utf-8-sig')
        except Exception:
            file.seek(0)
            return pd.read_csv(file, dtype=str, encoding='cp949')
    return pd.read_excel(file, dtype=str)

def to_num(s):
    """숫자로 강제 변환. '$1,234.00', '1 234', '(1,234)'(음수), 공백/기호 혼입, 텍스트 저장 모두 처리"""
    t = pd.Series(s).astype(str)
    t = t.str.replace('\xa0', ' ', regex=False).str.strip()
    neg = t.str.match(r'^\(.*\)$')                       # 회계식 괄호 음수
    t = t.str.replace(r'[^0-9.\-]', '', regex=True)
    t = t.str.replace(r'(?<=.)-', '', regex=True)        # 중간에 낀 하이픈 제거
    t = t.replace({'': np.nan, '-': np.nan, '.': np.nan})
    v = pd.to_numeric(t, errors='coerce').fillna(0)
    return np.where(neg.fillna(False), -v.abs(), v)

def norm_code(s):
    """코드 정규화: 숫자/텍스트 혼재, '541.0', 공백, 콤마, 선행 0 차이를 흡수 (양쪽에 동일 적용)"""
    t = pd.Series(s).astype(str).fillna('')
    t = (t.str.replace('\xa0', ' ', regex=False).str.strip()
          .str.replace(',', '', regex=False)
          .str.replace(r'\.0+$', '', regex=True))
    t = t.replace({'nan': '', 'NaN': '', 'None': '', 'NaT': '', '<NA>': ''}).fillna('')
    # 숫자로만 이뤄진 코드는 선행 0을 제거해 '0541'과 '541'을 동일하게 취급
    digits = t.str.fullmatch(r'\d+').fillna(False)
    t = t.where(~digits, t.str.lstrip('0').replace('', '0'))
    return t.fillna('')

def to_date(s):
    """날짜 변환: 문자열/날짜형은 물론 서식이 섞여 있거나 엑셀 일련번호(46239 등)여도 처리"""
    t = pd.Series(s).astype(str).str.replace('\xa0', ' ', regex=False).str.strip()
    try:
        dt = pd.to_datetime(t, errors='coerce', format='mixed')   # 행마다 서식이 달라도 각각 해석
    except Exception:
        dt = pd.to_datetime(t, errors='coerce')
    serial = t.str.fullmatch(r'\d{5}(\.\d+)?').fillna(False)      # 엑셀 날짜 일련번호 형태
    if serial.any():
        conv = pd.to_datetime(pd.to_numeric(t.where(serial), errors='coerce'),
                              unit='D', origin='1899-12-30', errors='coerce')
        dt = dt.where(~serial, conv)
    return dt

def parse_goal_month_col(c):
    """'01/2026', '2026-01', 날짜형 등을 'YYYY-MM'으로"""
    s = str(c).strip()
    m = re.fullmatch(r'(\d{1,2})[/\-.](\d{4})', s)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r'(\d{4})[/\-.](\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    try:
        return pd.to_datetime(s).strftime('%Y-%m')
    except Exception:
        return None

def process_goal_upload(f):
    """영업목표 파일 → 롱포맷(지점 × 월 × 목표금액)"""
    df = read_any(f)
    df.columns = df.columns.astype(str).str.replace('\xa0', ' ').str.strip()
    need = ['영업부코드', '영업부명', '영업지점코드', '영업지점명']
    if not all(c in df.columns for c in need):
        return None
    for c in ['영업부코드', '영업지점코드']:
        df[c] = norm_code(df[c]).replace('', np.nan)
    df[['영업부코드', '영업부명']] = df[['영업부코드', '영업부명']].ffill()   # 병합셀 대비
    month_map = {c: parse_goal_month_col(c) for c in df.columns}
    month_cols = {c: m for c, m in month_map.items() if m}
    if not month_cols:
        return None
    out = df.melt(id_vars=need, value_vars=list(month_cols.keys()),
                  var_name='_col', value_name='목표금액')
    out['기준월'] = out['_col'].map(month_cols)
    out['목표금액'] = to_num(out['목표금액'])
    out = out[out['영업지점코드'].notna()]
    return out.groupby(['영업부코드', '영업부명', '영업지점코드', '영업지점명', '기준월'],
                       as_index=False)['목표금액'].sum()

def process_billing_upload(f, month):
    """빌링완료 파일 → 지점별 박스/금액 (필터 없음). month는 히스토리 키로 사용"""
    df = read_any(f)
    idx = {k: col_letter_to_idx(v) for k, v in BILLING_COL_MAP.items()}
    if df.shape[1] <= max(idx.values()):
        return None
    out = pd.DataFrame({
        '영업부코드': norm_code(df.iloc[:, idx['영업부코드']]),
        '영업지점코드': norm_code(df.iloc[:, idx['영업지점코드']]),
        '박스': to_num(df.iloc[:, idx['박스']]),
        '금액': to_num(df.iloc[:, idx['금액']]),
    })
    out = out.groupby(['영업부코드', '영업지점코드'], as_index=False)[['박스', '금액']].sum()
    out['기준월'] = month
    return out[BILL_COLS]

def process_preship_upload(f, month):
    """빌링전 파일 → YOCO만 남기고 (상태 × 출고예정일 × 인도조건)별 집계. month는 히스토리 키"""
    df = read_any(f)
    idx = {k: col_letter_to_idx(v) for k, v in PRESHIP_COL_MAP.items()}
    if df.shape[1] <= max(idx.values()):
        return None
    doc = df.iloc[:, idx['문서구분']].astype(str).str.strip().str.upper()
    df = df[doc == PRESHIP_DOCTYPE.upper()]
    if df.empty:
        return None
    dt = to_date(df.iloc[:, idx['출고예정일']])
    out = pd.DataFrame({
        '영업부코드': norm_code(df.iloc[:, idx['영업부코드']]),
        '영업지점코드': norm_code(df.iloc[:, idx['영업지점코드']]),
        '상태': df.iloc[:, idx['상태']].astype(str).str.replace('\xa0', ' ', regex=False).str.strip().str.upper(),
        '출고예정일': dt.dt.strftime('%Y-%m-%d').fillna(''),
        '인도조건': df.iloc[:, idx['인도조건']].astype(str).str.replace('\xa0', ' ', regex=False).str.strip().str.upper(),
        '박스': to_num(df.iloc[:, idx['박스']]),
        '금액': to_num(df.iloc[:, idx['금액']]),
    })
    out = out.groupby(['영업부코드', '영업지점코드', '상태', '출고예정일', '인도조건'],
                      as_index=False)[['박스', '금액']].sum()
    out['기준월'] = month
    return out[PRE_COLS]

def load_simple_store(path, columns, num_cols):
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    df = _read_store_csv(path, file_mtime(path)).copy()
    for c in columns:
        if c not in df.columns:
            df[c] = ''
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    # 문자 컬럼의 결측은 빈 문자열로 (정렬 시 str/float 혼합 오류 방지)
    for c in columns:
        if c not in num_cols:
            df[c] = df[c].fillna('').astype(str)
    for c in ['영업부코드', '영업지점코드']:
        if c in df.columns:
            df[c] = norm_code(df[c])
    return df[columns]

# 🎯 빌링완료/빌링전도 월 단위 히스토리로 관리: 올린 달만 교체, 나머지 달은 그대로 보존
def upsert_month_store(new_df, month, path, columns, num_cols):
    store = load_simple_store(path, columns, num_cols)
    keep = store[store['기준월'] != month] if not store.empty else store
    merged = pd.concat([keep, new_df[columns]], ignore_index=True)
    save_store(merged, path)

def save_meta(key, value):
    meta = {}
    if os.path.exists(GOAL_META):
        m = pd.read_csv(GOAL_META, dtype=str, encoding='utf-8-sig')
        meta = dict(zip(m['키'], m['값']))
    meta[key] = str(value)
    pd.DataFrame({'키': list(meta.keys()), '값': list(meta.values())}).to_csv(
        GOAL_META, index=False, encoding='utf-8-sig')

def load_meta(key):
    if not os.path.exists(GOAL_META):
        return ''
    m = pd.read_csv(GOAL_META, dtype=str, encoding='utf-8-sig')
    d = dict(zip(m['키'], m['값']))
    return d.get(key, '')

def build_week_ranges(month):
    """대상월 전체를 월요일 시작 주차로 분할. 월 경계에서 잘라내며, 오늘 날짜와 무관하게 항상 동일한 결과.
       (오더는 출고/빌링 시 출고예정일이 비워지므로, 지난 주차를 보여줘도 중복이 발생하지 않음)"""
    ms = pd.Timestamp(month + '-01')
    me = ms + pd.offsets.MonthEnd(0)
    first_mon = ms - timedelta(days=int(ms.weekday()))
    weeks, cur = [], first_mon
    while cur <= me:
        s, e = max(cur, ms), min(cur + timedelta(days=6), me)
        weeks.append({'label': f"{s.month}/{s.day}일주", 'start': s, 'end': e})
        cur = cur + timedelta(days=7)
    return weeks, ms, me

def _fmt_money(x):
    if pd.isna(x) or x == 0:
        return '-'
    return f"${int(round(x)):,}"

def _fmt_box(x):
    if pd.isna(x) or x == 0:
        return '-'
    return f"{int(round(x)):,}"

def _fmt_pct(x):
    if pd.isna(x):
        return '-'
    return f"{x*100:.0f}%"

def _fmt_diff(x):
    if pd.isna(x) or x == 0:
        return '-'
    return f"${int(round(x)):+,}"


# 🎯 월 목표 진척현황 표: 3단 헤더 HTML 생성 (마크다운 코드블록 오인 방지를 위해 들여쓰기 없이 출력)
GOAL_TABLE_CSS = """<style>
.goaltbl-wrap { overflow-x: auto; width: 100%; }
.goaltbl { border-collapse: collapse; font-size: 12px; white-space: nowrap; }
.goaltbl th, .goaltbl td { border: 1px solid #808080; padding: 4px 8px; text-align: right; }
.goaltbl th { text-align: center; font-weight: 700; line-height: 1.25; }
.goaltbl .lbl { text-align: left; }
.goaltbl .h-base { background: #dbe5f1; color: #000; }
.goaltbl .h-y { background: #fdf2cf; color: #000; }
.goaltbl .h-y2 { background: #f8d97b; color: #000; }
.goaltbl .h-g { background: #d9d9d9; color: #000; }
.goaltbl .h-g2 { background: #a6a6a6; color: #000; }
.goaltbl .h-e { background: #e2efda; color: #000; }
.goaltbl .h-b { background: #1f4e79; color: #fff; }
.goaltbl .gs { border-left: 3px solid #808080; }
.goaltbl tr.sub td { background: #dce6f5; font-weight: 700; }
.goaltbl tr.total td { background: #1f4e79; color: #fff; font-weight: 700; }
</style>"""

def build_goal_html(body, spec, week_specs, mm):
    """body: (종류, 영업부, 영업지점, 값dict) 목록 / spec: (키, 종류, 색) 목록
       굵은 구분선은 아래 5곳에만: 영업지점|영업목표, 영업목표|Billing 완료,
       Billing+출고%|주차, 월 총 합계|출고 미확정, 출고 미확정|총 합계"""
    def cell(v, kind):
        if kind == 'money':
            return _fmt_money(v)
        if kind == 'box':
            return _fmt_box(v)
        return _fmt_pct(v)

    # 굵은 세로선이 들어갈 컬럼(그 컬럼의 왼쪽 경계)
    first_week_key = week_specs[0][0] if week_specs else '월합계 박스'
    thick_keys = {'Billing 박스', first_week_key, '배송대기 박스', '총합계 박스'}
    gs = {i for i, (k, _, _) in enumerate(spec) if k in thick_keys}
    def th_cls(base, thick=False):
        return f'{base} gs' if thick else base

    h = [GOAL_TABLE_CSS, '<div class="goaltbl-wrap"><table class="goaltbl"><thead>']

    # 1행: 상위 그룹
    r1 = ['<tr>',
          '<th class="h-base" rowspan="3">영업부</th>',
          '<th class="h-base" rowspan="3">영업지점</th>',
          '<th class="h-base gs" rowspan="3">영업목표</th>',
          '<th class="h-y gs" colspan="3" rowspan="2">Billing 완료</th>',
          '<th class="h-y" colspan="2" rowspan="2">출고 완료</th>',
          '<th class="h-y2" rowspan="3">Billing +<br>출고 %</th>']
    for wi, (_, _, lb) in enumerate(week_specs):
        r1.append(f'<th class="{th_cls("h-g", wi == 0)}" colspan="2" rowspan="2">{lb}<br>출고 확정</th>')
    r1 += [f'<th class="{th_cls("h-g", not week_specs)}" colspan="3" rowspan="2">{mm}월 출고 확정<br>합계</th>',
           f'<th class="h-g" colspan="3" rowspan="2">{mm}월 총 합계<br>(Billing+출고 완료,확정)</th>',
           f'<th class="h-e gs" colspan="4">{mm}월 출고 미확정</th>',
           '<th class="h-b gs" colspan="2" rowspan="2">총 합계<br>(미확정 포함)</th>',
           '<th class="h-b" rowspan="3">차이</th>',
           '<th class="h-b" rowspan="3">%</th>', '</tr>']
    h.append(''.join(r1))

    # 2행: 출고 미확정 하위 그룹
    h.append('<tr><th class="h-e gs" colspan="2">배송 대기</th>'
             '<th class="h-e" colspan="2">픽업 대기</th></tr>')

    # 3행: 박스 / 금액 / %
    r3 = ['<tr>']
    for i, (k, kind, color) in enumerate(spec):
        if k in ('Billing+출고 %', '차이', '달성률'):
            continue                      # rowspan=3 컬럼은 이 행에 셀이 없음
        nm = '박스' if kind == 'box' else ('금액' if kind == 'money' else '%')
        r3.append(f'<th class="{th_cls("h-" + color, i in gs)}">{nm}</th>')
    r3.append('</tr>')
    h.append(''.join(r3))
    h.append('</thead><tbody>')

    for kind_row, dept, branch, vals in body:
        cls = {'row': '', 'sub': ' class="sub"', 'total': ' class="total"'}[kind_row]
        pin = '📍 ' if kind_row == 'sub' else ''
        tds = [f'<tr{cls}>',
               f'<td class="lbl">{dept}</td>',
               f'<td class="lbl">{pin}{branch}</td>',
               f'<td class="gs">{_fmt_money(vals.get("영업목표"))}</td>']
        for i, (k, kd, color) in enumerate(spec):
            tds.append(f'<td class="gs">{cell(vals.get(k), kd)}</td>' if i in gs
                       else f'<td>{cell(vals.get(k), kd)}</td>')
        tds.append('</tr>')
        h.append(''.join(tds))
    h.append('</tbody></table></div>')
    return ''.join(h)

def render_goal_tab():
    goal = load_simple_store(GOAL_STORE, GOAL_COLS, ['목표금액'])
    if goal.empty:
        return st.info("좌측 🎯 영역에서 영업목표 파일을 먼저 등록해주세요. (영업부코드/영업부명/영업지점코드/영업지점명 + 월별 목표 컬럼)")

    bill = load_simple_store(BILL_STORE, BILL_COLS, ['박스', '금액'])
    pre = load_simple_store(PRE_STORE, PRE_COLS, ['박스', '금액'])
    snap = load_meta('기준일자')

    months = sorted([m for m in goal['기준월'].unique() if isinstance(m, str) and m.strip()])
    data_months = sorted({m for m in (set(bill['기준월']) | set(pre['기준월'])) if isinstance(m, str) and m.strip()})
    if data_months:
        cands = [m for m in months if m in set(data_months)]
        default_idx = months.index(cands[-1]) if cands else len(months) - 1
    else:
        cur_m = pd.Timestamp.today().strftime('%Y-%m')
        default_idx = months.index(cur_m) if cur_m in months else len(months) - 1
    c1, c2 = st.columns([1, 3])
    with c1:
        month = st.selectbox("📅 대상월", months, index=default_idx, key="goal_month")
    with c2:
        st.caption(f"📌 보유 빌링 데이터: **{', '.join(data_months) if data_months else '없음'}** / 기준일자: **{snap or '미등록'}** — "
                   "빌링완료·빌링전 두 파일은 반드시 같은 시점 자료를 함께 올려주세요. 금액 단위 USD. "
                   "주차는 월요일 시작이며 월 경계(1일·말일)에서 잘립니다. 라벨은 각 구간의 시작일 기준이라 "
                   "월초·월말의 짧은 조각 주는 그 조각의 첫날로 표기됩니다.")

    # 🎯 선택한 달의 데이터만 사용 (월별 히스토리에서 추출)
    bill = bill[bill['기준월'] == month]
    pre = pre[pre['기준월'] == month].reset_index(drop=True)
    if bill.empty and pre.empty:
        st.warning(f"⚠️ {month} 빌링 데이터가 없습니다. 좌측 🎯 영역에서 해당 월 자료를 등록해주세요. (목표만 표시됩니다)")

    weeks, ms, me = build_week_ranges(month)

    g = goal[goal['기준월'] == month].copy()
    if g.empty:
        return st.warning(f"{month} 목표 데이터가 없습니다.")

    # --- 지점 단위 집계 준비 ---
    base = g[['영업부코드', '영업부명', '영업지점코드', '영업지점명', '목표금액']].copy()
    base = base.groupby(['영업부코드', '영업부명', '영업지점코드', '영업지점명'], as_index=False)['목표금액'].sum()

    # 목표에 없는 지점(예: NSA HQ)도 실적이 있으면 하단에 표시
    known = set(base['영업지점코드'])
    extra_codes = set(bill['영업지점코드']) | set(pre['영업지점코드'])
    extra_codes = {c for c in extra_codes if c and c not in known and c != 'nan'}
    if extra_codes:
        ex = pd.DataFrame({'영업지점코드': sorted(extra_codes)})
        ex['영업부코드'] = ''
        ex['영업부명'] = '목표 미등록'
        ex['영업지점명'] = ex['영업지점코드']
        ex['목표금액'] = 0.0
        base = pd.concat([base, ex[base.columns]], ignore_index=True)

    def agg_by_branch(df, mask=None):
        d = df if mask is None else df[mask]
        if d.empty:
            return pd.DataFrame({'영업지점코드': pd.Series(dtype='object'),
                                 '박스': pd.Series(dtype='float64'),
                                 '금액': pd.Series(dtype='float64')})
        return d.groupby('영업지점코드', as_index=False)[['박스', '금액']].sum()

    def join(col_box, col_amt, agg):
        # 숫자형으로 명시 변환해 결측 채우기 (dtype 다운캐스팅 경고 방지)
        m = base[['영업지점코드']].merge(agg, on='영업지점코드', how='left')
        base[col_box] = pd.to_numeric(m['박스'], errors='coerce').fillna(0.0).to_numpy()
        base[col_amt] = pd.to_numeric(m['금액'], errors='coerce').fillna(0.0).to_numpy()

    # ① Billing 완료
    join('Billing 박스', 'Billing 금액', agg_by_branch(bill))

    # ② 출고 완료 (상태 C3)
    join('출고 박스', '출고 금액', agg_by_branch(pre, pre['상태'] == STATUS_SHIPPED))

    # ③ 주차별 출고 일정 확정 (출고예정일이 해당 주 범위)
    pdt = pd.to_datetime(pre['출고예정일'], errors='coerce')
    week_cols = []
    for w in weeks:
        mask = pdt.notna() & (pdt >= w['start']) & (pdt <= w['end'])
        bx, am = f"{w['label']} 박스", f"{w['label']} 금액"
        join(bx, am, agg_by_branch(pre, mask))
        week_cols += [bx, am]

    # 대상월 범위를 벗어난 출고예정 건(다음 달 등)은 이 달 집계에서 제외 — 금액만 안내
    out_mask = pdt.notna() & ((pdt < ms) | (pdt > me))
    if out_mask.any():
        out_amt = float(pre.loc[out_mask, '금액'].sum())
        st.caption(f"ℹ️ 출고예정일이 {month} 범위를 벗어난 건 {int(out_mask.sum())}행 / ${out_amt:,.0f} 은 "
                   "해당 월 실적이 아니므로 이 표에서 제외했습니다.")

    # ④ 월 합계 (주차별 합)
    base['월합계 박스'] = base[[c for c in week_cols if c.endswith('박스')]].sum(axis=1)
    base['월합계 금액'] = base[[c for c in week_cols if c.endswith('금액')]].sum(axis=1)

    # ⑤ 출고 확정 합계 = Billing + 출고 + 월합계
    base['확정합계 박스'] = base['Billing 박스'] + base['출고 박스'] + base['월합계 박스']
    base['확정합계 금액'] = base['Billing 금액'] + base['출고 금액'] + base['월합계 금액']

    # ⑥ 출고 미확정 (C3·C4 제외 + 출고예정일 없음) → DDP / EXW
    undecided = (~pre['상태'].isin([STATUS_SHIPPED, STATUS_BILLED])) & (pdt.isna())
    join('배송대기 박스', '배송대기 금액', agg_by_branch(pre, undecided & (pre['인도조건'] == DELIVERY_DDP)))
    join('픽업대기 박스', '픽업대기 금액', agg_by_branch(pre, undecided & (pre['인도조건'] == DELIVERY_EXW)))

    # ⑦ 총 합계 / 차이 / 달성률
    base['총합계 박스'] = base['확정합계 박스'] + base['배송대기 박스'] + base['픽업대기 박스']
    base['총합계 금액'] = base['확정합계 금액'] + base['배송대기 금액'] + base['픽업대기 금액']

    def ratio(num, den):
        return np.where(den > 0, num / den.replace(0, np.nan), np.nan)

    base['Billing %'] = ratio(base['Billing 금액'], base['목표금액'])
    base['Billing+출고 %'] = ratio(base['Billing 금액'] + base['출고 금액'], base['목표금액'])
    base['월합계 %'] = ratio(base['월합계 금액'], base['목표금액'])
    base['확정합계 %'] = ratio(base['확정합계 금액'], base['목표금액'])
    base['차이'] = base['총합계 금액'] - base['목표금액']
    base['달성률'] = ratio(base['총합계 금액'], base['목표금액'])

    value_cols = (['영업목표', 'Billing 박스', 'Billing 금액', 'Billing %', '출고 박스', '출고 금액', 'Billing+출고 %']
                  + week_cols + ['월합계 박스', '월합계 금액', '월합계 %',
                                 '확정합계 박스', '확정합계 금액', '확정합계 %',
                                 '배송대기 박스', '배송대기 금액', '픽업대기 박스', '픽업대기 금액',
                                 '총합계 박스', '총합계 금액', '차이', '달성률'])
    base = base.rename(columns={'목표금액': '영업목표'})

    # 🎯 [1] 값이 전혀 없는 주차 컬럼은 숨김 (이미 Billing/출고 완료에 반영된 지난 주차 등)
    kept_weeks = []
    for w in weeks:
        bx, am = f"{w['label']} 박스", f"{w['label']} 금액"
        if float(base[bx].sum()) != 0 or float(base[am].sum()) != 0:
            kept_weeks.append(w)
        else:
            base = base.drop(columns=[bx, am])
    hidden_n = len(weeks) - len(kept_weeks)
    if hidden_n:
        st.caption(f"ℹ️ 출고 확정 물량이 남아있지 않은 주차 {hidden_n}개는 숨겼습니다 "
                   "(해당 주차 오더는 이미 Billing 완료·출고 완료에 반영됨).")

    # --- 행 구성: 영업부별 지점 → 영업부 합계 → 총 합계 ---
    money_cols = [c for c in base.columns if ('금액' in c) or (c in ('영업목표', '차이'))]
    box_cols = [c for c in base.columns if '박스' in c]

    def summarize(block):
        s = {c: float(block[c].sum()) for c in money_cols + box_cols}
        tgt = s.get('영업목표', 0)
        s['Billing %'] = (s['Billing 금액'] / tgt) if tgt else np.nan
        s['Billing+출고 %'] = ((s['Billing 금액'] + s['출고 금액']) / tgt) if tgt else np.nan
        s['월합계 %'] = (s['월합계 금액'] / tgt) if tgt else np.nan
        s['확정합계 %'] = (s['확정합계 금액'] / tgt) if tgt else np.nan
        s['차이'] = s['총합계 금액'] - tgt
        s['달성률'] = (s['총합계 금액'] / tgt) if tgt else np.nan
        return s

    # 🎯 정렬: 영업부는 목표 합계 내림차순 → 부서 내 지점도 목표 내림차순 (목표 미등록 그룹은 맨 아래)
    dept_total = base.groupby('영업부명')['영업목표'].sum().sort_values(ascending=False)
    dept_order = [d for d in dept_total.index if d != '목표 미등록']
    if '목표 미등록' in dept_total.index:
        dept_order.append('목표 미등록')

    body = []   # (종류, 영업부, 영업지점, 값dict)
    for d in dept_order:
        blk = base[base['영업부명'] == d].sort_values('영업목표', ascending=False)
        for _, r in blk.iterrows():
            body.append(('row', d, r['영업지점명'], r.to_dict()))
        if len(blk) > 1:
            body.append(('sub', d, f"{d} 합계", summarize(blk)))
    body.append(('total', '총 합계', '', summarize(base)))

    # --- 3단 헤더 HTML 테이블 (엑셀 보고서 형태) ---
    mm = int(month[5:7])
    week_specs = [(f"{w['label']} 박스", f"{w['label']} 금액", w['label']) for w in kept_weeks]

    # (키, 종류, 그룹색)
    spec = ([('Billing 박스', 'box', 'y'), ('Billing 금액', 'money', 'y'), ('Billing %', 'pct', 'y'),
             ('출고 박스', 'box', 'y'), ('출고 금액', 'money', 'y'),
             ('Billing+출고 %', 'pct', 'y2')]
            + [x for (bx, am, _) in week_specs for x in [(bx, 'box', 'g'), (am, 'money', 'g')]]
            + [('월합계 박스', 'box', 'g'), ('월합계 금액', 'money', 'g'), ('월합계 %', 'pct', 'g'),
               ('확정합계 박스', 'box', 'g'), ('확정합계 금액', 'money', 'g'), ('확정합계 %', 'pct', 'g2'),
               ('배송대기 박스', 'box', 'e'), ('배송대기 금액', 'money', 'e'),
               ('픽업대기 박스', 'box', 'e'), ('픽업대기 금액', 'money', 'e'),
               ('총합계 박스', 'box', 'b'), ('총합계 금액', 'money', 'b'),
               ('차이', 'money', 'b'), ('달성률', 'pct', 'b')])

    st.markdown(build_goal_html(body, spec, week_specs, mm), unsafe_allow_html=True)

    st.caption("💡 Billing % = Billing 금액 ÷ 영업목표 / Billing+출고 % = (Billing + 출고 완료) ÷ 영업목표 / "
               f"{mm}월 출고 확정 합계 = 주차별 확정의 합 / {mm}월 총 합계 = Billing + 출고 완료 + 출고 확정 / "
               "총 합계(미확정 포함) = 여기에 배송·픽업 대기를 더한 값 / 차이 = 총 합계 - 영업목표 / % = 총 합계 ÷ 영업목표. "
               "각 오더는 상태에 따라 한 곳에만 집계되어 중복이 없습니다.")


# =============================================================
# 사이드바: 월별 히스토리 등록 (롤링 업서트)
# =============================================================
# 열람 비밀번호 게이트: 인증 성공 시 세션에 기록해 입력칸을 화면에서 제거.
# 창을 닫았다 새로 열면 다시 요구됨.
if _VIEWER_PW and not st.session_state.get("viewer_ok", False):
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
    st.sidebar.caption("파일 업로드 → 반영할 월 확인 → 버튼 클릭 시 해당 월만 덮어쓰기(업서트)됩니다. 나머지 월은 동결 보존됩니다.")

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
    st.sidebar.caption("'마감 여부' 컬럼이 포함된 오더 데이터. 업로드 시 전체 교체(스냅샷)되며, 해당월 마감 실적이 히스토리에 등록되면 자동 삭제됩니다.")

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

# --- 월 목표 대비 진척현황 데이터 ---
if IS_ADMIN:
    st.sidebar.divider()
    st.sidebar.header("🎯 월 목표 진척현황 데이터")
    st.sidebar.caption("영업목표는 연 1회 등록(영구 보관). 빌링완료·빌링전 두 파일은 항상 같은 시점 자료를 함께 올려주세요.")

    goal_file = st.sidebar.file_uploader("6. 영업목표 (연 1회)", type=['xlsx', 'csv'], key="up_goal")
    if goal_file is not None and st.sidebar.button("✅ 영업목표 등록/교체", key="goal_btn"):
        try:
            parsed_goal = process_goal_upload(goal_file)
        except Exception as e:
            parsed_goal = None
            st.sidebar.error(f"영업목표 해석 실패: {e}")
        if parsed_goal is None or parsed_goal.empty:
            st.sidebar.error("영업목표 형식을 확인해주세요. (영업부코드/영업부명/영업지점코드/영업지점명 + 월별 목표 컬럼 필요)")
        else:
            save_store(parsed_goal[GOAL_COLS], GOAL_STORE)
            st.sidebar.success(f"영업목표 등록 완료 ({parsed_goal['기준월'].nunique()}개월 × {parsed_goal['영업지점코드'].nunique()}개 지점)")
            st.rerun()

    sc1, sc2 = st.sidebar.columns(2)
    with sc1:
        snap_date = st.date_input("기준일자", value=pd.Timestamp.today().date(), key="goal_snap_date")
    with sc2:
        snap_month_in = st.text_input("대상월", value=pd.Timestamp.today().strftime('%Y-%m'), key="goal_snap_month")
    st.sidebar.caption("※ 대상월 = 이 데이터가 담고 있는 월(YYYY-MM). 월별로 보관되며 같은 달을 다시 올리면 그 달만 교체됩니다.")
    bill_file = st.sidebar.file_uploader("7. 빌링완료 데이터", type=['xlsx', 'csv'], key="up_bill")
    pre_file = st.sidebar.file_uploader("8. 빌링전 데이터", type=['xlsx', 'csv'], key="up_pre")
    if st.sidebar.button("✅ 빌링완료 + 빌링전 함께 반영", key="billpre_btn"):
        tgt_m = parse_month(snap_month_in)
        if bill_file is None or pre_file is None:
            st.sidebar.warning("두 파일(빌링완료·빌링전)을 모두 올린 뒤 눌러주세요. 기준 시점이 어긋나면 중복 집계가 발생합니다.")
        elif not (isinstance(tgt_m, str) and re.fullmatch(r'\d{4}-\d{2}', tgt_m)):
            st.sidebar.error("대상월 형식이 올바르지 않습니다. 예: 2026-07")
        else:
            try:
                b = process_billing_upload(bill_file, tgt_m)
                p = process_preship_upload(pre_file, tgt_m)
            except Exception as e:
                b = p = None
                st.sidebar.error(f"파일 해석 실패: {e}")
            if b is None or b.empty:
                st.sidebar.error("빌링완료 파일에서 데이터를 읽지 못했습니다. 컬럼 위치(AM/AO/O/S)를 확인해주세요.")
            elif p is None or p.empty:
                st.sidebar.error(f"빌링전 파일에서 '{PRESHIP_DOCTYPE}' 데이터를 찾지 못했습니다. 컬럼 위치를 확인해주세요.")
            else:
                upsert_month_store(b, tgt_m, BILL_STORE, BILL_COLS, ['박스', '금액'])
                upsert_month_store(p, tgt_m, PRE_STORE, PRE_COLS, ['박스', '금액'])
                save_meta('기준일자', snap_date)
                save_meta('대상월', tgt_m)
                st.sidebar.success(f"{tgt_m} 반영 완료 (기준일자 {snap_date}) — 다른 달 데이터는 그대로 보존됩니다.")
                st.rerun()

# --- 저장 현황 및 관리 ---
st.sidebar.divider()
st.sidebar.header("🗂️ 저장 데이터 현황" + ("/관리" if IS_ADMIN else ""))
if not IS_ADMIN:
    st.sidebar.caption("👀 조회 전용 화면입니다. 데이터 갱신은 관리자가 수행합니다.")
_plan_store = load_store(PLAN_STORE, PLAN_COLS, '계획수량')
_act_store = load_store(ACT_STORE, ACT_COLS, '실적수량')
_prog_store = load_store(PROG_STORE, PROG_COLS, '실적수량')
plan_months = sorted([m for m in _plan_store['기준월'].unique() if isinstance(m, str) and m.strip()])
usmx_months = sorted([m for m in _plan_store[_plan_store['소스'] == 'USMX']['기준월'].unique() if isinstance(m, str) and m.strip()])
can_months = sorted([m for m in _plan_store[_plan_store['소스'] == 'CAN']['기준월'].unique() if isinstance(m, str) and m.strip()])
act_months = sorted([m for m in _act_store['기준월'].unique() if isinstance(m, str) and m.strip()])
prog_months = sorted([m for m in _prog_store['기준월'].unique() if isinstance(m, str) and m.strip()])
st.sidebar.caption(f"계획(USA,MEX): {', '.join(usmx_months) if usmx_months else '없음'}")
st.sidebar.caption(f"계획(CAN): {', '.join(can_months) if can_months else '없음'}")
st.sidebar.caption(f"실적 보유: {', '.join(act_months) if act_months else '없음'}")
st.sidebar.caption(f"진척도 보유: {', '.join(prog_months) if prog_months else '없음'}")
_goal_store = load_simple_store(GOAL_STORE, GOAL_COLS, ['목표금액'])
if not _goal_store.empty:
    _gm = sorted([m for m in _goal_store['기준월'].unique() if isinstance(m, str) and m.strip()])
    st.sidebar.caption(f"영업목표: {_gm[0]} ~ {_gm[-1]}")
_bill_months = sorted({m for m in (set(load_simple_store(BILL_STORE, BILL_COLS, ['박스', '금액'])['기준월'])
                                   | set(load_simple_store(PRE_STORE, PRE_COLS, ['박스', '금액'])['기준월']))
                       if isinstance(m, str) and m.strip()})
st.sidebar.caption(f"빌링 보유: {', '.join(_bill_months) if _bill_months else '없음'} (최근 기준일자 {load_meta('기준일자') or '없음'})")

if IS_ADMIN:
    with st.sidebar.expander("🧹 특정 월 삭제 / 전체 초기화"):
        del_target = st.selectbox("대상 저장소", ["계획", "실적", "진척도", "빌링(목표진척)"], key="del_store")
        _opts = {'계획': plan_months, '실적': act_months, '진척도': prog_months,
                 '빌링(목표진척)': _bill_months}[del_target]
        if _opts:
            del_month_sel = st.selectbox("삭제할 월", _opts, key="del_month")
            if st.button("해당 월 삭제", key="del_btn"):
                if del_target == '빌링(목표진척)':
                    # 빌링완료 + 빌링전 두 저장소에서 해당 월 제거
                    for _p, _c, _n in [(BILL_STORE, BILL_COLS, ['박스', '금액']),
                                       (PRE_STORE, PRE_COLS, ['박스', '금액'])]:
                        _s = load_simple_store(_p, _c, _n)
                        save_store(_s[_s['기준월'] != del_month_sel], _p)
                else:
                    _map = {'계획': (PLAN_STORE, PLAN_COLS, '계획수량'),
                            '실적': (ACT_STORE, ACT_COLS, '실적수량'),
                            '진척도': (PROG_STORE, PROG_COLS, '실적수량')}
                    delete_month(*_map[del_target], del_month_sel)
                st.rerun()
        else:
            st.caption("저장된 월이 없습니다.")
        confirm_reset = st.checkbox("전체 초기화에 동의합니다 (복구 불가)", key="reset_ok")
        if st.button("🚨 히스토리 전체 초기화", key="reset_btn") and confirm_reset:
            for p in [PLAN_STORE, ACT_STORE, PROG_STORE, BILL_STORE, PRE_STORE, GOAL_META]:
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
    if IS_ADMIN:
        st.sidebar.info("✅ 마스터 데이터 3종이 내장되어 정상 작동 중입니다.")
elif IS_ADMIN:
    st.sidebar.warning("⚠️ 저장된 마스터 데이터가 없습니다. 위 ⚙️ 영역에 최초 1회 업로드 해주세요.")
else:
    st.sidebar.warning("⚠️ 데이터가 준비되지 않았습니다. 관리자에게 문의해주세요.")


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
# 🎯 탭4: 전월 대비 정확도/GAP 개선 (영업사원별)
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
               "🟢 옅은 녹색 = 전월 대비 정확도 상승, 🔴 옅은 붉은색 = 하락. "
               "하단 전체 평균은 사원 기준(사원 1명=1표)과 지점 기준(지점 소계의 평균, 지점 1개=1표)을 함께 표시합니다.")

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
    # ① 사원 기준 = 표의 사원 행 전체 평균 (사원 1명 = 1표)
    # ② 지점 기준 = 표에 표시된 지점 소계들의 평균 (지점 1개 = 1표, 탭2 전체 평균과 동일·손검산 일치)
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
# 🎯 탭5: 당월 진척도 렌더링
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
    # 기간 한정 규칙 적용 (KDH 한시 통합, 시작월부터 제외 — 저장 원본은 불변)
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
    st.caption("💡 기본값(해당월 출고 확정)은 확정 오더 기준 실적입니다. '출고 미확정' 등을 추가하면 해당 오더가 전량 당월 출고된다고 가정한 예상 수량이 됩니다.")
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

    # 국가 필터 (USA/MEX/CAN 등 복수 선택·해제)
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
    # 계획 없이 실적 발생(진척도 ∞) 품목 행은 옅은 붉은색 처리
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
    # 진척도 무한대(계획 없이 실적 발생) 품목은 항상 리스트 맨 아래에 붉은색으로 포함
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
               "②에 리스트업된 제품 중 진척도가 무한대(∞, 계획 없이 실적 발생)인 제품은 GAP 왜곡 방지를 위해 집계에서 제외했습니다.")
    gap_df = merged[merged['제품코드'].isin(low_codes)]  # ∞ 품목 제외 (유한 진척도 품목만)
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

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊 1. 제품별 실적 뷰",
        "🏢 2. 영업조직별 실적 뷰",
        "🛠️ 3. 상세 분석 (조직/사원별 딥다이브)",
        "📈 4. 전월 대비 개선 (사원별)",
        "⏱️ 5. 당월 진척도",
        "💰 6. 월 목표 대비 진척현황"
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
                "📅 조회할 월(Month) 범위를 지정하세요",
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
            exclude_codes = st.multiselect("❌ 제외할 제품코드/품목 (화면 임시 제외)", all_codes, default=[])

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
            st.caption("💡 정확도 = 각 지점이 담당한 품목별 정확도의 평균 (GAP은 총 계획량 - 총 실적량 기준)")
            create_styled_pivot(filtered_df, ['영업부명', '영업지점명'], selected_months, acc_mode='item_avg')

        with tab3:
            st.markdown("##### 영업부/지점/사원을 좁혀가며, 문제 품목과 담당자를 딥다이브 하세요.")
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
                    "정확도 하위 필터 (기간 평균 %가 이 값 미만인 품목만, 100=전체)",
                    min_value=0, max_value=100, value=100, step=5
                )

            summary_base = apply_product_filters(branch_df, kw_input, acc_threshold, selected_months)
            t3_filtered = apply_product_filters(t3_df, kw_input, acc_threshold, selected_months)

            st.markdown("---")
            st.markdown("##### 👥 지점별 · 영업사원별 정확도/GAP 요약")
            st.caption("💡 지점 소계 정확도는 탭2(지점 품목별 정확도 평균)와 동일 기준이며, 사원 드롭다운과 무관하게 선택 지점 내 전체 사원을 비교합니다. 품목 필터를 걸면 '그 품목들에 대해 누가 문제인지' 바로 보입니다. 정렬: 정확도 낮은 순. 하단 전체 평균 = 사원 행들의 평균(사원 1명=1표).")
            render_person_summary(summary_base, selected_months)

            st.markdown("---")
            st.markdown("##### 📋 상세 테이블 (제품 소계 + 정확도 오름차순)")
            dynamic_rows = st.multiselect(
                "📌 행(Row)으로 볼 항목을 배치하세요. (제품코드/제품명 포함 시 제품 소계가 자동 표시됩니다)",
                ['제품코드', '제품명', '영업부명', '영업지점명', '영업사원명', '국가'],
                default=['제품코드', '제품명', '영업사원명']
            )
            st.caption("💡 정확도 = 해당 행의 품목별 정확도 평균 / 📍 제품 소계 = 해당 제품 전체 합계와 총량 기준 정확도(탭1과 동일 수치). 정렬은 기간 평균 정확도 오름차순(문제 품목·행이 맨 위)입니다. 하단 전체 평균 = 표시된 세부 행들의 평균(세부 행 1개=1표)이라, 행 구성에 따라 위 요약표의 사원 기준 평균과 다를 수 있습니다.")

            if dynamic_rows:
                render_detail_table(t3_filtered, dynamic_rows, selected_months)

        with tab4:
            st.markdown("##### 전월 대비 정확도/GAP 개선 (영업사원별)")
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

    with tab6:
        st.markdown("##### 월 영업목표 대비 실적 현황 (금액 기준)")
        render_goal_tab()

else:
    st.info("하단 ⚙️ 마스터 데이터 3종을 먼저 등록해주세요. 등록 후 좌측 📚 영역에서 계획/실적을 히스토리에 반영하면, 재업로드 없이 항상 대시보드가 표시됩니다.")