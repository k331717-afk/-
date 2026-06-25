import os
import time
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
# 깃허브 시크릿에 설정된 메타 광고용 DB ID를 가져옵니다.
NOTION_META_AD_DB_ID = os.environ.get("NOTION_META_AD_DB_ID")

COMPETITORS = [
    "konny_kr",
    "moomooz_essential",
    "bonats.official",
    "bebedepino",
]

def scrape_meta_ads_data() -> str:
    print("🕵️ Meta 광고 라이브러리 긁어오는 중...")

    # 🚨 캡처본 기반 메타 광고 라이브러리 엔드포인트 세팅 (리스트 조회용)
    url = "https://facebook-ads-library-scraper-api.p.rapidapi.com/company/ads"
    headers = {
        "x-rapidapi-host": "facebook-ads-library-scraper-api.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    scraped_text = ""

    for company in COMPETITORS:
        print(f"📢 [{company}] 광고 수집 중...")
        # GET 방식이므로 params로 검색어를 넘깁니다. (API 파라미터명에 따라 'query'나 'company_name'으로 수정 필요할 수 있음)
        # ❌ 기존 문제의 코드:
        # querystring = {"query": company}

        #   아래 코드로 수정해 주세요!
        querystring = {
            "companyName": company,   # 'query'를 'companyName'으로 변경했습니다.
            "status": "ACTIVE",       # 현재 광고 중인 것만 수집
            "country": "KR",          # 코니(konny_kr) 광고를 보시려면 한국("KR")으로 설정하는 것이 좋습니다. (전체는 "ALL")
            "sort_by": "total_impressions"  # 노출수 높은 순 정렬
        }
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()

            ads = data.get("data", []) or []

            scraped_text += f"\n[브랜드: {company}]\n"
            if not ads:
                scraped_text += "- 현재 진행 중인 메타 광고 없음\n\n"
                continue

            for ad in ads[:5]: # 최대 5개까지만 수집
                ad_text = ad.get("ad_delivery_text", "")
                ad_status = ad.get("status", "Active")
                scraped_text += f"- 상태: {ad_status}\n"
                scraped_text += f"  광고 문구: {ad_text[:200]}\n"

            scraped_text += "\n"
            time.sleep(3) # API Rate Limit 방지

        except Exception as e:
            print(f"❌ {company} 메타 광고 수집 실패: {e}")
            scraped_text += f"[브랜드: {company}] 수집 실패\n\n"

    print("✅ 메타 광고 수집 완료!")
    return scraped_text

def analyze_with_ai(scraped_data: str) -> str:
    print("🧠 AI 퍼포먼스 마케터 분석 시작...")
    
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
너는 10년 차 퍼포먼스 마케터야. 경쟁사들이 현재 돌리고 있는 메타(페이스북/인스타그램) 광고 텍스트 데이터를 분석해서 실무진이 바로 써먹을 수 있는 노션 리포트를 써줘.

데이터:
{scraped_data}

[출력 양식]
## 🎯 1. 경쟁사 광고 소구점(USP) 트렌드
> (이번 주 경쟁사들의 주력 소구점 1줄 요약)
- 주요 카피라이팅 특징: (짧게)
- 타겟팅 앵글: (짧게)

## 🔥 2. 주목해야 할 벤치마킹 광고 카피
> (가장 눈에 띄는 광고 문구 요약)
- 🥇 베스트 카피 1: (브랜드명 / 카피 / 좋은 이유)
- 🥈 베스트 카피 2: (브랜드명 / 카피 / 좋은 이유)

## 🚀 3. 우리 브랜드 메타 광고 적용 액션
> (다음 캠페인에 적용할 핵심 아이디어)
- [Action 1] (아이디어) : (1~2줄)
- [Action 2] (아이디어) : (1~2줄)
"""
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    print("✅ 메타 광고 분석 완료!")
    return response.text

def upload_report_to_notion(analysis_text):
    if not NOTION_META_AD_DB_ID:
        print("❌ 노션 DB ID(NOTION_META_AD_DB_ID)가 설정되지 않았습니다.")
        return

    print("📝 메타 광고 리포트 노션 업로드 중...")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # 페이지 생성 시도
    page_data = {
        "parent": {"database_id": NOTION_META_AD_DB_ID},
        "properties": {
            "이름": {"title": [{"text": {"content": "📈 메타 경쟁사 광고 카피 분석 리포트"}}]} # 타이틀 속성이 '이름'인지 '제목'인지 확인 필요
        }
    }
    
    # 본문 블록 변환 (간소화)
    lines = analysis_text.strip().split("\n")
    children_blocks = []
    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith("## "):
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": line.replace("## ", "").replace("**", "").strip()}}]}})
        elif line.startswith("> "):
            children_blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": [{"type": "text", "text": {"content": line.replace("> ", "").strip()}}]}})
        else:
            children_blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": line.lstrip("*- ").strip()}}]}})

    create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data)
    if create_res.status_code != 200:
        print(f"❌ 페이지 생성 실패: {create_res.text}")
        return

    page_id = create_res.json()["id"]

    for i in range(0, len(children_blocks), 100):
        requests.patch(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers, json={"children": children_blocks[i:i+100]})

    print("✅ 노션 리포트 업로드 완료!")

def main():
    print("=" * 50)
    print("🚀 메타 경쟁사 광고 분석 시스템 시작")
    print("=" * 50)

    scraped_data = scrape_meta_ads_data()
    if not scraped_data.strip():
        print("수집된 데이터가 없습니다.")
        return

    analysis = analyze_with_ai(scraped_data)
    upload_report_to_notion(analysis)

if __name__ == "__main__":
    main()
