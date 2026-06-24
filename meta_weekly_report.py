#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================
  Meta Ads 주간 자동화 리포팅 스크립트
  Concrete Bread (콘크리트브레드) | 광고 계정: 417307475814443
  매주 월요일 오전 9:00 자동 실행
  수신: k331717@concretebread.com
=============================================================

[설치 필요 패키지]
  pip install facebook-business requests pandas jinja2

[환경 변수 설정 (반드시 .env 파일 또는 시스템 환경변수로 관리)]
  META_ACCESS_TOKEN=your_long_lived_access_token
  META_AD_ACCOUNT_ID=act_417307475814443
  SMTP_USER=your_gmail@gmail.com
  SMTP_PASSWORD=your_app_password
  REPORT_RECIPIENT=k331717@concretebread.com
"""

import os
import json
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import requests
import pandas as pd

# ──────────────────────────────────────────────
# 1. 환경 설정
# ──────────────────────────────────────────────

# .env 파일 로드 (python-dotenv 설치 시 사용 가능)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=r"C:\Users\kk331\Desktop\meta_report\.env")
except ImportError:
    pass  # dotenv 없이도 시스템 환경변수로 동작

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("meta_report.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 설정값
CONFIG = {
    "access_token":   os.environ.get("META_ACCESS_TOKEN", ""),
    "ad_account_id":  os.environ.get("META_AD_ACCOUNT_ID", "act_417307475814443"),
    "api_version":    "v25.0",
    "lookback_days":  7,           # 지난 N일 데이터 조회
    "smtp_host":      "smtp.gmail.com",
    "smtp_port":      587,
    "smtp_user":      os.environ.get("SMTP_USER", ""),
    "smtp_password":  os.environ.get("SMTP_PASSWORD", ""),
    "recipient":      os.environ.get("REPORT_RECIPIENT", "k331717@concretebread.com"),
    "brand_name":     "Concrete Bread",
    "notion_token":   os.environ.get("NOTION_API_TOKEN", ""),
    "notion_db_id":   os.environ.get("NOTION_DATABASE_ID", "")
}

# ──────────────────────────────────────────────
# 2. Meta Graph API 데이터 추출
# ──────────────────────────────────────────────

def get_date_range(lookback_days: int) -> tuple[str, str]:
    """지난 N일 날짜 범위 반환 (YYYY-MM-DD 형식)"""
    end_date   = datetime.now().date() - timedelta(days=1)  # 어제까지
    start_date = end_date - timedelta(days=lookback_days - 1)
    return str(start_date), str(end_date)


def fetch_campaign_insights(config: dict) -> list[dict]:
    """
    Meta Graph API를 통해 캠페인별 인사이트 데이터 추출
    
    반환 지표:
      spend, impressions, clicks, ctr, cpc, cpm,
      actions(purchase), action_values(purchase) → CPA, ROAS 계산용
    """
    start_date, end_date = get_date_range(config["lookback_days"])
    logger.info(f"📅 조회 기간: {start_date} ~ {end_date}")

    base_url = (
        f"https://graph.facebook.com/{config['api_version']}"
        f"/{config['ad_account_id']}/insights"
    )

    params = {
        "access_token": config["access_token"],
        "level":        "campaign",
        "time_range":   json.dumps({"since": start_date, "until": end_date}),
        "fields": ",".join([
            "campaign_name",
            "campaign_id",
            "spend",
            "impressions",
            "reach",
            "frequency",
            "clicks",
            "ctr",
            "cpc",
            "cpm",
            "actions",            # 전환 액션 목록
            "action_values",      # 전환 가치 (ROAS 계산용)
        ]),
        "filtering": json.dumps([
            {"field": "campaign.effective_status", "operator": "IN", "value": ["ACTIVE"]}
        ]),
        "limit": 100,
    }

    response = requests.get(base_url, params=params, timeout=30)
    try:
        data = response.json()
    except ValueError:
        data = {}

    if not response.ok:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        message = error.get("message") or response.text[:500]
        raise RuntimeError(f"Meta API 오류 ({response.status_code}): {message}")

    if "error" in data:
        raise ValueError(f"Meta API 오류: {data['error']['message']}")

    campaigns = data.get("data", [])
    logger.info(f"✅ {len(campaigns)}개 활성 캠페인 데이터 수집 완료")
    return campaigns


def parse_action_value(actions_list: list, action_type: str) -> float:
    """actions / action_values 리스트에서 특정 액션 유형의 값 추출"""
    if not actions_list:
        return 0.0
    for item in actions_list:
        if item.get("action_type") == action_type:
            return float(item.get("value", 0))
    return 0.0


def build_dataframe(raw_campaigns: list[dict]) -> pd.DataFrame:
    """원시 API 데이터를 분석용 DataFrame으로 변환 + 파생 지표 계산"""
    rows = []
    for c in raw_campaigns:
        spend       = float(c.get("spend", 0))
        impressions = int(c.get("impressions", 0))
        clicks      = int(c.get("clicks", 0))
        ctr         = float(c.get("ctr", 0))      # % 단위 (API 반환값)
        cpc         = float(c.get("cpc", 0))

        # 구매 전환 수 & 전환 가치
        purchases       = parse_action_value(c.get("actions", []), "purchase")
        purchase_value  = parse_action_value(c.get("action_values", []), "purchase")

        # 파생 지표 계산
        cpa  = (spend / purchases)       if purchases > 0       else None
        roas = (purchase_value / spend)  if spend > 0           else None

        rows.append({
            "캠페인명":     c.get("campaign_name", ""),
            "캠페인ID":     c.get("campaign_id", ""),
            "지출(원)":     round(spend),
            "노출수":       impressions,
            "클릭수":       clicks,
            "CTR(%)":      round(ctr, 2),
            "CPC(원)":     round(cpc),
            "구매전환수":   int(purchases),
            "전환가치(원)": round(purchase_value),
            "CPA(원)":     round(cpa)  if cpa  else None,
            "ROAS":        round(roas, 2) if roas else None,
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("지출(원)", ascending=False).reset_index(drop=True)
    return df


# ──────────────────────────────────────────────
# 3. 성과 분석 & 인사이트 생성
# ──────────────────────────────────────────────

def analyze_campaigns(df: pd.DataFrame) -> dict:
    """
    캠페인 성과 자동 분석:
    - 전체 요약 지표
    - Top 2 / Bottom 2 캠페인
    - 예산 최적화 액션 아이템
    """
    total_spend    = df["지출(원)"].sum()
    total_clicks   = df["클릭수"].sum()
    total_purchases = df["구매전환수"].sum()
    total_value    = df["전환가치(원)"].sum()

    avg_ctr  = round(df["CTR(%)"].mean(), 2)
    avg_cpc  = round(df["CPC(원)"].mean())
    avg_cpa  = round(total_spend / total_purchases) if total_purchases > 0 else None
    total_roas = round(total_value / total_spend, 2) if total_spend > 0 else None

    summary = {
        "total_spend":     total_spend,
        "total_clicks":    total_clicks,
        "total_purchases": total_purchases,
        "total_value":     total_value,
        "avg_ctr":         avg_ctr,
        "avg_cpc":         avg_cpc,
        "avg_cpa":         avg_cpa,
        "total_roas":      total_roas,
    }

    # ROAS가 있는 캠페인만 순위 평가
    df_with_roas = df[df["ROAS"].notna()].copy()
    top2    = df_with_roas.nlargest(2, "ROAS")
    bottom2 = df_with_roas.nsmallest(2, "ROAS")

    # 액션 아이템 자동 생성
    action_items = []
    ROAS_THRESHOLD = 2.0  # 손익분기 ROAS (업종/마진에 맞게 조정)
    CPA_THRESHOLD  = 30000  # 목표 CPA 기준값 (원)

    for _, row in df.iterrows():
        name  = row["캠페인명"]
        roas  = row["ROAS"]
        cpa   = row["CPA(원)"]
        spend = row["지출(원)"]

        if roas is not None:
            if roas >= ROAS_THRESHOLD * 1.5:
                action_items.append(f"📈 **[예산 증액]** '{name}' — ROAS {roas}x로 우수. 예산 20~30% 증액 권장")
            elif roas < ROAS_THRESHOLD:
                action_items.append(f"📉 **[예산 감액/OFF]** '{name}' — ROAS {roas}x로 손익분기 미달. 즉시 예산 축소 또는 일시 정지 검토")
        if cpa is not None and cpa > CPA_THRESHOLD:
            action_items.append(f"⚠️ **[CPA 개선 필요]** '{name}' — CPA {cpa:,}원으로 목표 초과. 타겟 오디언스 또는 소재 교체 권장")

    return {
        "summary":      summary,
        "top2":         top2,
        "bottom2":      bottom2,
        "action_items": action_items,
    }


# ──────────────────────────────────────────────
# 4. HTML 리포트 생성
# ──────────────────────────────────────────────

def build_html_report(df: pd.DataFrame, analysis: dict, config: dict) -> str:
    """분석 결과를 가독성 높은 HTML 이메일 리포트로 변환"""
    start_date, end_date = get_date_range(config["lookback_days"])
    s = analysis["summary"]

    def is_missing(v):
        return v is None or pd.isna(v)

    def fmt_krw(v):
        return "N/A" if is_missing(v) else f"₩{int(v):,}"

    def fmt_pct(v):
        return "N/A" if is_missing(v) else f"{v}%"

    def fmt_roas(v):
        return "N/A" if is_missing(v) else f"{v}x"

    # 캠페인 테이블 행 생성
    table_rows = ""
    for _, row in df.iterrows():
        roas = row["ROAS"]
        roas_style = ""
        if not is_missing(roas):
            roas_style = "color:#16a34a;font-weight:bold;" if roas >= 2 else "color:#dc2626;font-weight:bold;"
        table_rows += f"""
        <tr>
          <td>{row['캠페인명']}</td>
          <td style="text-align:right">{fmt_krw(row['지출(원)'])}</td>
          <td style="text-align:right">{int(row['노출수']):,}</td>
          <td style="text-align:right">{fmt_pct(row['CTR(%)'])}</td>
          <td style="text-align:right">{fmt_krw(row['CPC(원)'])}</td>
          <td style="text-align:right">{fmt_krw(row['CPA(원)'])}</td>
          <td style="text-align:right;{roas_style}">{fmt_roas(row['ROAS'])}</td>
        </tr>"""

    # 액션 아이템 리스트
    action_html = "".join(f"<li style='margin-bottom:8px'>{item}</li>" for item in analysis["action_items"])
    if not action_html:
        action_html = "<li>이번 주 특이 사항 없음</li>"

    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Apple SD Gothic Neo', Arial, sans-serif; color: #1f2937; background: #f9fafb; margin:0; padding:20px; }}
  .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
  .header {{ background: linear-gradient(135deg, #1e293b, #334155); color: white; padding: 32px 40px; }}
  .header h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: -0.5px; }}
  .header p {{ margin: 0; opacity: 0.7; font-size: 13px; }}
  .kpi-grid {{ display: flex; gap: 16px; padding: 28px 40px; background: #f8fafc; flex-wrap: wrap; }}
  .kpi-card {{ flex: 1; min-width: 130px; background: white; border-radius: 10px; padding: 18px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
  .kpi-label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
  .kpi-value {{ font-size: 22px; font-weight: 700; color: #111827; }}
  .section {{ padding: 28px 40px; }}
  .section h2 {{ font-size: 16px; font-weight: 700; color: #111827; margin: 0 0 16px; border-left: 4px solid #3b82f6; padding-left: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1e293b; color: white; padding: 10px 12px; text-align: left; font-weight: 600; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  .action-box {{ background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 20px 24px; }}
  ul {{ margin: 0; padding-left: 20px; }}
  .footer {{ background: #f1f5f9; padding: 16px 40px; text-align: center; font-size: 11px; color: #9ca3af; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 {config['brand_name']} — Meta 광고 주간 성과 리포트</h1>
    <p>조회 기간: {start_date} ~ {end_date} &nbsp;|&nbsp; 생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">총 지출</div><div class="kpi-value">{fmt_krw(s['total_spend'])}</div></div>
    <div class="kpi-card"><div class="kpi-label">총 클릭수</div><div class="kpi-value">{int(s['total_clicks']):,}</div></div>
    <div class="kpi-card"><div class="kpi-label">평균 CTR</div><div class="kpi-value">{fmt_pct(s['avg_ctr'])}</div></div>
    <div class="kpi-card"><div class="kpi-label">평균 CPC</div><div class="kpi-value">{fmt_krw(s['avg_cpc'])}</div></div>
    <div class="kpi-card"><div class="kpi-label">총 구매전환</div><div class="kpi-value">{int(s['total_purchases']):,}건</div></div>
    <div class="kpi-card"><div class="kpi-label">평균 CPA</div><div class="kpi-value">{fmt_krw(s['avg_cpa'])}</div></div>
    <div class="kpi-card"><div class="kpi-label">전체 ROAS</div><div class="kpi-value">{fmt_roas(s['total_roas'])}</div></div>
  </div>

  <div class="section">
    <h2>캠페인별 성과 상세</h2>
    <table>
      <thead>
        <tr>
          <th>캠페인명</th><th>지출</th><th>노출수</th><th>CTR</th><th>CPC</th><th>CPA</th><th>ROAS</th>
        </tr>
      </thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <h2>💡 이번 주 액션 아이템</h2>
    <div class="action-box">
      <ul>{action_html}</ul>
    </div>
  </div>

  <div class="footer">
    이 리포트는 Meta Graph API 기반 자동화 시스템이 생성했습니다. &copy; {config['brand_name']}
  </div>
</div>
</body>
</html>
"""
    return html


# ──────────────────────────────────────────────
# 5. 이메일 발송
# ──────────────────────────────────────────────

def send_email(html_content: str, config: dict, df: pd.DataFrame):
    """Gmail SMTP를 통해 HTML 리포트 + CSV 첨부 발송"""
    start_date, end_date = get_date_range(config["lookback_days"])
    subject = f"[{config['brand_name']}] Meta 광고 주간 리포트 | {start_date} ~ {end_date}"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = config["smtp_user"]
    msg["To"]      = config["recipient"]

    # HTML 본문
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    # CSV 첨부 파일
    csv_path = os.path.join(
         os.path.dirname(os.path.abspath(__file__)),
         f"meta_report_{end_date}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(csv_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="meta_report_{end_date}.csv"')
    msg.attach(part)

    # SMTP 발송
    with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
        server.ehlo()
        server.starttls()
        server.login(config["smtp_user"], config["smtp_password"])
        server.sendmail(config["smtp_user"], config["recipient"], msg.as_string())

    logger.info(f"✉️ 리포트 발송 완료 → {config['recipient']}")

# ──────────────────────────────────────────────
# 5-1. 노션(Notion) 데이터베이스 전송
# ──────────────────────────────────────────────
def send_to_notion(df: pd.DataFrame, config: dict):
    if not config["notion_token"] or not config["notion_db_id"]:
        logger.warning("노션 API 토큰 또는 DB ID가 없어 노션 전송을 건너뜁니다.")
        return

    start_date, _ = get_date_range(config["lookback_days"])
    parent_title = f"[{start_date}]"
    
    headers = {
        "Authorization": f"Bearer {config['notion_token']}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # -------------------------------------------------------------
    # Step 1: '주간 메타 광고 동향' 표(DB)에 날짜 행 생성
    # -------------------------------------------------------------
    logger.info(f"📁 메인 표에 주차별 행 생성 중: {parent_title}")
    create_page_url = "https://api.notion.com/v1/pages"
    
    page_payload = {
        "parent": {"database_id": config["notion_db_id"]},
        "properties": {
            "이름": {"title": [{"text": {"content": parent_title}}]}
        }
    }
    
    page_res = requests.post(create_page_url, headers=headers, json=page_payload)
    if page_res.status_code != 200:
        logger.error(f"❌ 주차 행 생성 실패: {page_res.text}")
        return
        
    parent_page_id = page_res.json()["id"]

    # -------------------------------------------------------------
    # Step 2: 생성된 행 내부에 하위 표 만들기 (🔥 요청하신 서식 완벽 적용)
    # -------------------------------------------------------------
    logger.info("🗄️ 생성된 행 내부에 전용 데이터베이스(표) 생성 중...")
    create_db_url = "https://api.notion.com/v1/databases"
    
    db_payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": f"상세 캠페인 리스트"}}],
        "properties": {
            "캠페인명": {"title": {}},
            "비용": {"number": {"format": "won"}},                   # ₩ 서식
            "노출": {"number": {"format": "number_with_commas"}},    # 천 단위 콤마
            "클릭수": {"number": {"format": "number_with_commas"}},  # 천 단위 콤마
            "CTR(%)": {"number": {"format": "percent"}},             # % 서식
            "CPC(원)": {"number": {"format": "won"}},                # ₩ 서식
            "구매전환수": {"number": {"format": "number_with_commas"}}, # 천 단위 콤마
            "전환가치(원)": {"number": {"format": "won"}},             # ₩ 서식
            "CPA(원)": {"number": {"format": "won"}},                # ₩ 서식
            "ROAS": {"number": {"format": "percent"}}                # % 서식 (추가됨!)
        }
    }
    
    db_res = requests.post(create_db_url, headers=headers, json=db_payload)
    if db_res.status_code != 200:
        logger.error(f"❌ 내부 데이터베이스 생성 실패: {db_res.text}")
        return
        
    child_database_id = db_res.json()["id"]

    # -------------------------------------------------------------
    # Step 3: 데이터 채우기
    # -------------------------------------------------------------
    logger.info("📝 생성된 내부 표에 캠페인 데이터 채우는 중...")
    success_count = 0
    for _, row in df.iterrows():
        data = {
            "parent": {"database_id": child_database_id},
            "properties": {
                "캠페인명": {"title": [{"text": {"content": row['캠페인명']}}]},
                "비용": {"number": int(row['지출(원)'])} if not pd.isna(row['지출(원)']) else {"number": 0},
                "노출": {"number": int(row['노출수'])} if not pd.isna(row['노출수']) else {"number": 0},
                "클릭수": {"number": int(row['클릭수'])} if not pd.isna(row['클릭수']) else {"number": 0},
                # CTR은 4.74 -> 0.0474로 넣어야 노션에서 4.74%로 정상 표기됨
                "CTR(%)": {"number": float(row['CTR(%)']) / 100.0} if not pd.isna(row['CTR(%)']) else {"number": 0.0},
                "CPC(원)": {"number": int(row['CPC(원)'])} if not pd.isna(row['CPC(원)']) else {"number": 0},
                "구매전환수": {"number": int(row['구매전환수'])} if not pd.isna(row['구매전환수']) else {"number": 0},
                "전환가치(원)": {"number": int(row['전환가치(원)'])} if not pd.isna(row['전환가치(원)']) else {"number": 0},
                "CPA(원)": {"number": int(row['CPA(원)'])} if (row['CPA(원)'] is not None and not pd.isna(row['CPA(원)'])) else {"number": 0},
                # ROAS는 4.74를 그대로 넣으면 노션에서 474%로 예쁘게 표기됨
                "ROAS": {"number": float(row['ROAS'])} if (row['ROAS'] is not None and not pd.isna(row['ROAS'])) else {"number": 0.0}
            }
        }
        
        response = requests.post(create_page_url, headers=headers, json=data)
        if response.status_code == 200:
            success_count += 1
        else:
            logger.error(f"내부 데이터 업로드 실패 ({row['캠페인명']}): {response.text}")

    logger.info(f"✅ 완료! 메인 표에 [{start_date}] 행이 생성되었고, 그 안에 {success_count}개 캠페인이 나열되었습니다.")
# ──────────────────────────────────────────────
# 6. 메인 실행
# ──────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("🚀 Meta 주간 리포트 자동화 시작")
    logger.info("=" * 60)

    # 토큰 검증
    if not CONFIG["access_token"]:
        raise EnvironmentError("META_ACCESS_TOKEN 환경변수가 설정되지 않았습니다.")

    # Step 1: 데이터 추출
    logger.info("Step 1/4: Meta API에서 캠페인 데이터 추출 중...")
    raw_data = fetch_campaign_insights(CONFIG)

    if not raw_data:
        logger.warning("활성 캠페인 데이터가 없습니다. 종료합니다.")
        return

    # Step 2: DataFrame 변환
    logger.info("Step 2/4: 데이터 가공 및 지표 계산 중...")
    df = build_dataframe(raw_data)
    print("\n📊 캠페인 성과 요약:")
    print(df[["캠페인명", "지출(원)", "CTR(%)", "CPA(원)", "ROAS"]].to_string(index=False))

    # Step 3: 분석
    logger.info("Step 3/4: 성과 분석 및 인사이트 생성 중...")
    analysis = analyze_campaigns(df)
    logger.info(f"🏆 Top ROAS 캠페인: {analysis['top2']['캠페인명'].tolist()}")
    logger.info(f"⚠️ Bottom ROAS 캠페인: {analysis['bottom2']['캠페인명'].tolist()}")

    # Step 4: 이메일 발송
    logger.info("Step 4/4: HTML 리포트 생성 및 이메일 발송 중...")
    html = build_html_report(df, analysis, CONFIG)
    send_email(html, CONFIG, df)
    send_to_notion(df, CONFIG)

    logger.info("✅ 모든 작업 완료!")


if __name__ == "__main__":
    main()
