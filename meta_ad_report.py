import os
import requests
from google import genai

def main():
    RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
    NOTION_META_AD_DB_ID = os.environ.get("NOTION_META_AD_DB_ID")
    
    if not RAPIDAPI_KEY or not GEMINI_API_KEY:
        print("❌ 에러: 필수 API 키 환경변수가 설정되지 않았습니다.")
        return

    search_keywords = ["아동복", "유아복"]
    print(f"🚀 카테고리 키워드 {search_keywords} 메타 광고 데이터 수집 시작")

    url = "https://facebook-ads-library-scraper-api.p.rapidapi.com/search/ads"
    all_collected_ads = []
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": "facebook-ads-library-scraper-api.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    for keyword in search_keywords:
        print(f"🔍 '{keyword}' 관련 광고 수집 중...")
        querystring = {"query": keyword, "status": "ACTIVE", "country": "KR", "media_type": "ALL", "sort_by": "total_impressions", "trim": "false"}
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list): all_collected_ads.extend(data)
            elif isinstance(data, dict) and "ads" in data: all_collected_ads.extend(data["ads"])
            else: all_collected_ads.append(data)
            print(f"✅ '{keyword}' 수집 완료!")
        except Exception as e:
            print(f"❌ '{keyword}' 수집 중 오류: {e}")
            continue

    if not all_collected_ads:
        print("⚠️ 수집된 메타 광고 데이터가 없습니다. (API 한도 초과 등)")
        return

    print("🤖 제미나이 AI 카테고리 트렌드 분석 시작...")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""
당신은 아동 의류 업계 전문 마케터입니다. 아래 메타 광고 데이터를 분석하여 노션에 업로드할 리포트를 작성해 주세요.

[수집 데이터]
{all_collected_ads[:30]}

[절대 지켜야 할 작성 규칙 (노션 파싱용)]
1. 큰 제목은 반드시 '## ' 로 시작할 것.
2. 각 문단의 핵심 요약은 반드시 '> ' 로 시작할 것.
3. 세부 항목이나 리스트는 반드시 '- ' 로 시작할 것.

[출력 양식]
## 🎯 1. 핵심 소구점 분석
> (현재 부모들의 지갑을 여는 주요 셀링포인트 요약 1줄)
- (분석 내용)
- (분석 내용)

## 🔥 2. 히트 광고 카피 패턴 분석
> (노출수가 높은 광고들의 훅 문구/패턴 요약 1줄)
- (패턴 특징 1)
- (패턴 특징 2)

## 💡 3. 우리 브랜드 추천 벤치마킹 카피 5선
> (당장 사용할 수 있는 피드용 카피 1줄)
- 1. (카피 예시)
- 2. (카피 예시)
"""
        ai_response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        report_text = ai_response.text
        print("✨ AI 분석 완료!")
        
        # 📝 노션 업로드 로직 실행
        upload_meta_report_to_notion(report_text, NOTION_TOKEN, NOTION_META_AD_DB_ID)

    except Exception as e:
        print(f"❌ 제미나이 분석 중 오류: {e}")

def upload_meta_report_to_notion(analysis_text, token, db_id):
    if not db_id:
        print("❌ NOTION_META_AD_DB_ID가 설정되지 않았습니다.")
        return

    print("📝 노션 메타 광고 리포트 업로드 중...")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}

    db_res = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=headers)
    db_props = db_res.json().get("properties", {})
    title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "이름")

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
            block_type = "bulleted_list_item" if line.startswith("- ") else "paragraph"
            clean_text = line.lstrip("*- ").strip()
            parts = clean_text.split("**")
            rich_text_list = [{"type": "text", "text": {"content": part}, "annotations": {"bold": i % 2 == 1}} for i, part in enumerate(parts) if part]
            if not rich_text_list: rich_text_list = [{"type": "text", "text": {"content": clean_text}}]
            children_blocks.append({"object": "block", "type": block_type, block_type: {"rich_text": rich_text_list}})

    page_data = {
        "parent": {"database_id": db_id},
        "properties": {title_key: {"title": [{"text": {"content": "📈 주간 메타(Meta) 아동복 카테고리 광고 리포트"}}]}}
    }
    create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data)
    if create_res.status_code != 200:
        print(f"❌ 페이지 생성 실패: {create_res.text}")
        return

    page_id = create_res.json()["id"]
    for i in range(0, len(children_blocks), 100):
        chunk = children_blocks[i:i+100]
        requests.patch(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers, json={"children": chunk})
    print("✅ 노션 리포트 업로드 완료!")

if __name__ == "__main__":
    main()
