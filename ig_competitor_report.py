import os
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

# 🚨 변경: RapidAPI 대신 메타 공식 토큰을 사용합니다.
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_CONTENT_DB_ID = os.environ.get("NOTION_CONTENT_DB_ID")

COMPETITORS = [
    "konny_kr", "moomooz_essential", "bonats.official",
    "bebedepino", "_bobochoses_", "benebene_official",
    "detamy_project", "apricotstudios_"
]

def get_my_ig_user_id():
    """토큰을 이용해 내 인스타그램 비즈니스 계정 ID를 자동 추적합니다."""
    print("🔍 내 인스타그램 비즈니스 계정 ID 찾는 중...")
    url = "https://graph.facebook.com/v19.0/me/accounts"
    params = {
        "fields": "instagram_business_account",
        "access_token": META_ACCESS_TOKEN
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json().get("data", [])
        
        for page in data:
            if "instagram_business_account" in page:
                ig_id = page["instagram_business_account"]["id"]
                print(f"✅ 내 인스타그램 ID 찾기 성공! (ID: {ig_id})")
                return ig_id
                
        print("❌ 연결된 인스타그램 비즈니스 계정을 찾을 수 없습니다. 페이스북 페이지와 인스타그램이 잘 연결되어 있는지 확인해주세요.")
        return None
        
    except Exception as e:
        print(f"❌ 내 계정 정보 불러오기 실패: {e}")
        return None

def scrape_instagram_data_official(ig_user_id) -> str:
    print("🕵️ Meta 공식 API 출동! 429 에러 없이 당당하게 긁어오는 중...")

    url = f"https://graph.facebook.com/v19.0/{ig_user_id}"
    scraped_text = ""
    valid_data_count = 0

    for username in COMPETITORS:
        print(f"📸 [{username}] 게시물 가져오는 중...")
        
        # Business Discovery를 이용한 합법적이고 빠른 데이터 요청!
        params = {
            "fields": f"business_discovery.username({username}){{followers_count,media_count,media.limit(6){{comments_count,like_count,caption}}}}",
            "access_token": META_ACCESS_TOKEN
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            business_data = data.get("business_discovery", {})
            media_data = business_data.get("media", {}).get("data", [])

            scraped_text += f"\n[계정: {username}]\n"
            scraped_text += f"- 팔로워: {business_data.get('followers_count', 0):,}명\n"
            
            if not media_data:
                scraped_text += "- 최근 게시물 데이터 없음\n\n"
                continue

            for post in media_data:
                caption = post.get("caption", "내용 없음")
                likes = post.get("like_count", 0)
                comments = post.get("comments_count", 0)
                scraped_text += f"  > 좋아요: {likes} / 댓글: {comments}\n"
                scraped_text += f"    본문: {caption[:150]}...\n"

            scraped_text += "\n"
            valid_data_count += 1

        except Exception as e:
            print(f"❌ {username} 수집 실패: {e}")
            if hasattr(response, 'text'):
                print(f"상세 에러: {response.text}")
            scraped_text += f"[계정: {username}] 수집 실패\n\n"

    if valid_data_count == 0:
        print("⚠️ 유효한 경쟁사 데이터가 수집되지 않아 시스템을 중단합니다.")
        return ""
        
    print("✅ 데이터 수집 완료! 영원히 막히지 않는 수집 성공! 🎉")
    return scraped_text

def analyze_with_ai(scraped_data: str) -> str:
    print("🧠 AI 마케터 분석 시작...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
너는 10년 차 유아동복 퍼포먼스/콘텐츠 마케터야. 아래 데이터를 분석해서 실무진이 1분 만에 읽을 수 있는 인포그래픽 스타일 노션 리포트를 써줘.

데이터:
{scraped_data}

[절대 지켜야 할 작성 규칙]
1. 짧고 명확한 문장으로 끊어 칠 것. 서술 금지.
2. 이모지(🔥, 💡, 🚨, 🎯 등)를 듬뿍 사용.
3. 큰 카테고리 제목은 반드시 '## ' 로 시작.
4. 각 섹션 핵심 한 줄 요약은 반드시 '> ' 로 시작.

[출력 양식]
## 🎯 1. 이번 주 시장 트렌드 키워드
> (핵심 한 줄 요약)
- 주요 무드: (짧게)
- 핵심 해시태그: (짧게)
- 훅 포인트: (짧게)

## 🔥 2. 반응 터진 벤치마킹 포인트
> (핵심 한 줄 요약)
- 🥇 사례 1: (브랜드명 / 성과 / 성공 이유 1줄)
- 🥈 사례 2: (브랜드명 / 성과 / 성공 이유 1줄)

## 🚀 3. 당장 실행 액션
> (이번 주 핵심 목표 1줄)
- [Action 1] (아이디어) : (1~2줄)
- [Action 2] (아이디어) : (1~2줄)
"""
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    print("✅ AI 분석 완료!")
    return response.text

def upload_report_to_notion(analysis_text):
    if not NOTION_CONTENT_DB_ID:
        print("❌ NOTION_CONTENT_DB_ID가 설정되지 않았습니다.")
        return

    print("📝 노션 업로드 중...")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    db_res = requests.get(f"https://api.notion.com/v1/databases/{NOTION_CONTENT_DB_ID}", headers=headers)
    db_props = db_res.json().get("properties", {})
    title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "이름")

    lines = analysis_text.strip().split("\n")
    children_blocks = []

    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith("## "):
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": line.replace("## ", "").replace("**", "").strip()}}]}})
        elif line.startswith("### "):
            children_blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": line.replace("### ", "").replace("**", "").strip()}}]}})
        elif line.startswith("> "):
            children_blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": [{"type": "text", "text": {"content": line.replace("> ", "").strip()}}]}})
        else:
            block_type = "bulleted_list_item" if (line.startswith("- ") or line.startswith("* ")) else "paragraph"
            clean_text = line.lstrip("*- ").strip()
            parts = clean_text.split("**")
            rich_text_list = [{"type": "text", "text": {"content": part}, "annotations": {"bold": i % 2 == 1}} for i, part in enumerate(parts) if part]
            if not rich_text_list: rich_text_list = [{"type": "text", "text": {"content": clean_text}}]
            children_blocks.append({"object": "block", "type": block_type, block_type: {"rich_text": rich_text_list}})

    page_data = {
        "parent": {"database_id": NOTION_CONTENT_DB_ID},
        "properties": {title_key: {"title": [{"text": {"content": "📊 주간 인스타그램 경쟁사 트렌드 리포트"}}]}}
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

def main():
    print("=" * 50)
    print("🚀 인스타그램 경쟁사 자동 분석 시스템 시작 (META 공식 API 버전)")
    print("=" * 50)

    if not META_ACCESS_TOKEN:
        print("❌ 에러: META_ACCESS_TOKEN 환경변수가 설정되지 않았습니다.")
        return

    my_ig_id = get_my_ig_user_id()
    if not my_ig_id:
        return

    scraped_data = scrape_instagram_data_official(my_ig_id)
    if not scraped_data:
        return 

    analysis = analyze_with_ai(scraped_data)
    upload_report_to_notion(analysis)

if __name__ == "__main__":
    main()
