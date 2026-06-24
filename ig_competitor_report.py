import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

# 새로 설치한 패키지들
from apify_client import ApifyClient
import google.generativeai as genai

# .env 환경 변수 로드
load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN") # 기존 노션 토큰 재사용
NOTION_DB_ID = os.environ.get("NOTION_CONTENT_DB_ID") # 새로운 리포트용 DB

COMPETITORS = [
    "https://www.instagram.com/konny_kr/",
    "https://www.instagram.com/moomooz_essential/",
    "https://www.instagram.com/bonats.official/",
    "https://www.instagram.com/bebedepino/",
    "https://www.instagram.com/_bobochoses_/",
    "https://www.instagram.com/benebene_official/",
    "https://www.instagram.com/detamy_project/",
    "https://www.instagram.com/apricotstudios_/"
]

def scrape_instagram_data() -> str:
    """Apify를 이용해 경쟁사 계정의 최근 게시물 수집"""
    print("🕵️‍♂️ Apify 봇 출동! 경쟁사 인스타그램 긁어오는 중... (약 2~3분 소요)")
    client = ApifyClient(APIFY_TOKEN)
    
    # 인스타그램 스크래퍼 Actor 실행 (Apify 공식 제공 봇)
    run_input = {
        "directUrls": COMPETITORS,
        "resultsType": "posts",
        "resultsLimit": 16, # 각 프로필당 최근 2개씩 총 16개 정도 수집 제한 (비용/시간 절약)
    }
    
    run = client.actor("apify/instagram-scraper").call(run_input=run_input)
    
    scraped_text = ""
    for item in client.dataset(run.default_dataset_id).iterate_items():
        owner = item.get("ownerUsername", "Unknown")
        caption = item.get("caption", "")
        likes = item.get("likesCount", 0)
        comments = item.get("commentsCount", 0)
        
        # AI가 읽기 좋게 텍스트로 정리
        scraped_text += f"[계정: {owner}]\n"
        scraped_text += f"- 좋아요: {likes} / 댓글: {comments}\n"
        scraped_text += f"- 본문: {caption[:200]}...\n\n" # 너무 길면 잘라냄
        
    print("✅ 데이터 수집 완료!")
    return scraped_text

def analyze_with_ai(scraped_data: str) -> str:
    """Gemini AI를 이용해 마케팅 인사이트 도출 (🔥인포그래픽 스타일 프롬프트 적용)"""
    print("🧠 AI 마케터 분석 시작...")
    
    # 1. API 키 설정 (줄 맞춤 확인!)
    genai.configure(api_key=GEMINI_API_KEY)
    
    # 2. 사용할 모델 탐색 (여기 들여쓰기가 스페이스바 4칸이어야 합니다)
    available_model = None
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            available_model = m.name
            break
            
    model = genai.GenerativeModel(available_model)
    
    # AI가 구구절절 쓰지 않도록 강력한 제약을 걸어둔 프롬프트입니다.
    prompt = f"""
    너는 10년 차 유아동복 퍼포먼스/콘텐츠 마케터야. 아래 데이터를 분석해서 실무진이 1분 만에 읽을 수 있는 '인포그래픽 스타일'의 노션 리포트를 써줘.
    
    데이터:
    {scraped_data}
    
    [절대 지켜야 할 작성 규칙]
    1. 절대 길게 서술하지 마. 보고서처럼 짧고 명확한 문장으로 끊어 칠 것.
    2. 시각적으로 눈에 띄게 이모지(🔥, 💡, 🚨, 🎯 등)를 듬뿍 사용할 것.
    3. 큰 카테고리 제목은 반드시 '## ' 로 시작할 것 (예: ## 🎯 1. 이번 주 트렌드 요약)
    4. 각 섹션의 핵심 한 줄 요약은 반드시 '> ' 로 시작할 것 (예: > 💡 여름 시즌 프로모션과 참여형 이벤트가 핵심!)
    
    [출력 양식]
    ## 🎯 1. 이번 주 시장 트렌드 키워드
    > (여기에 핵심 한 줄 요약 작성)
    - ☀️ 주요 무드: (짧게 1~2줄)
    - 🏷️ 핵심 해시태그: (짧게)
    - 훅(Hook) 포인트: (짧게)

    ## 🔥 2. 반응 터진 벤치마킹 포인트
    > (여기에 벤치마킹의 핵심 한 줄 요약 작성)
    - 🥇 사례 1: (브랜드명 / 성과 / 성공 이유 1줄 요약)
    - 🥈 사례 2: (브랜드명 / 성과 / 성공 이유 1줄 요약)

    ## 🚀 3. Concrete Bread 당장 실행 액션
    > (우리가 이번 주에 해야 할 핵심 목표 1줄 요약)
    - [Action 1] (아이디어 이름) : (어떻게 할지 1~2줄 요약)
    - [Action 2] (아이디어 이름) : (어떻게 할지 1~2줄 요약)
    - [Action 3] (아이디어 이름) : (어떻게 할지 1~2줄 요약)
    """
    
    response = model.generate_content(prompt)
    print("✅ AI 분석 완료!")
    return response.text

def upload_report_to_notion(analysis_text):
    """AI 분석 리포트를 노션 페이지 내에 인포그래픽 스타일 블록으로 생성"""
    print("📝 노션에 인포그래픽 스타일 리포트 작성 중...")
    
# OS 환경변수에서 확실하게 다시 가져오기 (이 2줄을 추가해 주세요!)
    global NOTION_TOKEN
    NOTION_CONTENT_DB_ID = "3899f355db85802abeaae6c6555b4210"

    lines = analysis_text.strip().split("\n")
    children_blocks = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 대제목 (##) 처리 - 노션 API 제목 규격에 맞게 annotations 제거
        if line.startswith("## "):
            text_content = line.replace("## ", "").replace("**", "").strip() # 볼드 기호 제거
            children_blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": text_content}}] # 스타일 없이 순수 텍스트만
                }
            })
        # 소제목 (###) 처리
        elif line.startswith("### "):
            text_content = line.replace("### ", "").replace("**", "").strip()
            children_blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": text_content}}]
                }
            })
        # 일반 글머리 기호 또는 본문 처리
        else:
            # 기본 본문 텍스트 빌드
            is_bullet = line.startswith("* ") or line.startswith("- ")
            clean_text = line.replace("* ", "").replace("- ", "").strip()
            
            block_type = "bulleted_list_item" if is_bullet else "paragraph"
            
            # 본문 내의 간단한 볼드(**) 처리
            parts = clean_text.split("**")
            rich_text_list = []
            for i, part in enumerate(parts):
                if not part:
                    continue
                rich_text_list.append({
                    "type": "text",
                    "text": {"content": part},
                    "annotations": {"bold": True if i % 2 == 1 else False}
                })
            
            if not rich_text_list:
                rich_text_list = [{"type": "text", "text": {"content": clean_text}}]
                
            children_blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": rich_text_list}
            })

    # 노션 페이지 생성 및 블록 추가
    try:
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        
        # 0. DB 속성 이름 자동 감지 (타이틀 컬럼 이름이 뭔지 모를 때 대비)
        db_res = requests.get(f"https://api.notion.com/v1/databases/{NOTION_CONTENT_DB_ID}", headers=headers)
        db_props = db_res.json().get("properties", {})
        title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "제목")
        print(f"📌 노션 DB 타이틀 속성 이름: '{title_key}'")

        # 1. 빈 페이지 먼저 생성
        page_data = {
            "parent": {"database_id": NOTION_CONTENT_DB_ID},
            "properties": {
                title_key: {"title": [{"text": {"content": "📊 인스타그램 경쟁사 트렌드 분석 리포트"}}]}
            }
        }
        create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data)
        
        if create_res.status_code == 200:
            page_id = create_res.json()["id"]
            # 2. 생성된 페이지 내부에 블록들 추가
            append_res = requests.patch(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers, json={"children": children_blocks})
            
            if append_res.status_code == 200:
                print("✅ 노션 리포트 업로드 전송 성공!")
            else:
                print(f"❌ 본문 내용 추가 실패: {append_res.text}")
        else:
            print(f"❌ 노션 페이지 생성 실패: {create_res.text}")
            
    except Exception as e:
        print(f"❌ 노션 통신 중 에러 발생: {e}")
def main():
    print("="*50)
    print("🚀 인스타그램 경쟁사 자동 분석 시스템 시작")
    print("="*50)
    
    # 1. Apify로 경쟁사 데이터 긁기
    scraped_data = scrape_instagram_data()
    
    if not scraped_data.strip():
        print("수집된 데이터가 없습니다. 경쟁사 링크나 Apify 설정을 확인해주세요.")
        return
        
    # 2. AI에게 데이터 주고 분석 리포트 쓰게 하기
    analysis = analyze_with_ai(scraped_data)
    
    # 3. 노션에 업로드
    upload_report_to_notion(analysis)

if __name__ == "__main__":
    main()