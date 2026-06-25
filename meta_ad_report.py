import os
import requests
from google import genai

def main():
    # 1. 환경 변수에서 API 키들 가져오기
    RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    
    if not RAPIDAPI_KEY or not GEMINI_API_KEY:
        print("❌ 에러: RAPIDAPI_KEY 또는 GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    # ✨ [변경] 특정 브랜드 대신 조사하고 싶은 카테고리 키워드를 설정합니다.
    search_keywords = ["아동복", "유아복"]
    print(f"🚀 카테고리 키워드 {search_keywords} 기반 메타 광고 데이터 수집을 시작합니다.")

    # ✨ [변경] 엔드포인트를 company/ads에서 search/ads로 변경했습니다.
    url = "https://facebook-ads-library-scraper-api.p.rapidapi.com/search/ads"
    
    all_collected_ads = []

    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": "facebook-ads-library-scraper-api.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY
    }

    # 설정한 키워드들을 돌며 광고 수집
    for keyword in search_keywords:
        print(f"🔍 '{keyword}' 관련 광고 수집 중...")
        
        # ✨ [변경] 카테고리 검색에 맞는 파라미터 구조로 수정했습니다.
        # (주의: API 문서에 따라 'query' 대신 'search_terms'일 수 있습니다. 에러 발생 시 확인 필요)
        querystring = {
            "query": keyword,               # 검색할 카테고리 키워드
            "status": "ACTIVE",             # 현재 집행 중인 활성 광고만
            "country": "KR",                # 대한민국 타겟 광고
            "media_type": "ALL",
            "sort_by": "total_impressions", # 노출수 높은 인기 광고 위주로
            "trim": "false"
        }

        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            
            data = response.json()
            # 수집된 광고 데이터가 리스트 형태라면 합쳐줍니다.
            if isinstance(data, list):
                all_collected_ads.extend(data)
            elif isinstance(data, dict) and "ads" in data:  # 결과가 dict 구조일 경우 대응
                all_collected_ads.extend(data["ads"])
            else:
                all_collected_ads.append(data)
                
            print(f"✅ '{keyword}' 관련 광고 수집 완료!")

        except Exception as e:
            print(f"❌ '{keyword}' 수집 중 오류 발생: {e}")
            continue

    # 3. 제미나이(Gemini) AI를 통한 데이터 분석 리포트 생성
    if not all_collected_ads:
        print("⚠️ 수집된 광고 데이터가 없어 분석을 진행할 수 없습니다.")
        return

    print("🤖 제미나이 AI 카테고리 트렌드 및 카피 분석 시작...")
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # AI에게 카테고리 분석에 특화된 프롬프트 전달
        prompt = f"""
        당신은 아동 의류 업계에 정통한 전문 퍼포먼스 마케터입니다.
        아래 데이터는 현재 메타(페이스북/인스타그램)에서 집행 중인 '아동복' 및 '유아복' 관련 광고 분석 데이터(JSON)입니다.
        이 데이터를 바탕으로 최신 아동/유아복 광고 트렌드 및 카피라이팅 리포트를 작성해 주세요.
        
        [수집 데이터]
        {all_collected_ads[:30]} # 데이터가 너무 크면 AI가 읽지 못하므로 상위 30개로 제한합니다.
        
        [리포트 포함 필수 항목]
        1. 핵심 소구점 분석: 부모들(타겟층)의 지갑을 열게 만드는 요즘 아동복 광고의 주요 셀링 포인트 (예: 소재의 안전성, 디자인, 가성비, 상하복 세트 구성 등)
        2. 히트 광고 카피 패턴 분석: 노출수가 높은 광고들이 주로 사용하는 후킹 문구, 제목 패턴, 이모지 활용법 요약
        3. 벤치마킹 추천 카피 라이팅 예시: 수집된 트렌드를 반영하여 우리 브랜드에서 즉시 활용할 수 있는 아동복/유아복 광고 카피 예시 5가지 (인스타그램 피드용)
        4. 향후 우리 브랜드가 시도해야 할 소재(이미지/영상 콘텐츠) 방향성 제언
        """
        
        ai_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        report_text = ai_response.text
        
        print("\n=================== ✨ [AI 아동/유아복 광고 트렌드 리포트] ===================\n")
        print(report_text)
        print("\n============================================================================\n")
        
        # 파일 저장
        output_filename = "children_wear_ad_trend_report.txt"
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"💾 분석 리포트가 '{output_filename}' 파일로 저장되었습니다.")

    except Exception as e:
        print(f"❌ 제미나이 분석 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
