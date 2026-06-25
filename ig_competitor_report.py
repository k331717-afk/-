import os
import time
import requests
import json
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_CONTENT_DB_ID = "3899f355db85802abeaae6c6555b4210"

COMPETITORS = [
    "konny_kr",
    "moomooz_essential",
    "bonats.official",
    "bebedepino",
    "_bobochoses_",
    "benebene_official",
    "detamy_project",
    "apricotstudios_"
]

def scrape_instagram_data() -> str:
    print("🕵️ RapidAPI 출동! 경쟁사 인스타그램 긁어오는 중...")

    # 게시물 수집 전용 엔드포인트 (팔로워 API 아님)
    url = "https://instagram-scraper-stable-api.p.rapidapi.com/ig_get_user_posts_v2.php"
    headers = {
        "x-rapidapi-host": "instagram-scraper-stable-api.p.rapidapi.com",
        "x-rapidapi-key": RAPIDAPI_KEY,
        "Content-Type": "application/x-www-form-urlencoded"
    }

    scraped_text = ""

    for username in COMPETITORS:
        print(f"📸 [{username}] 게시물 가져오는 중...")
        payload = {
            "username_or_url": f"https://www.instagram.com/{username}/",
            "amount": "6"
        }

        try:
            response = requests.post(url, data=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            posts = data.get("data", {}).get("items", []) or data.get("items", []) or []

            scraped_text += f"\n[계정: {username}]\n"
            if not posts:
                scraped_text += "- 게시물 데이터 없음\n\n"
                continue

            for post in posts[:6]:
                caption = post.get("caption", {})
                caption_text = caption.get("text", "") if isinstance(caption, dict) else str(caption)
                likes = post.get("like_count", 0)
                comments = post.get("comment_count", 0)
                scraped_text += f"- 좋아요: {likes} / 댓글: {comments}\n"
                scraped_text += f"  본문: {caption_text[:200]}\n"

            scraped_text += "\n"
            time.sleep(3)

        except Exception as e:
            print(f"❌ {username} 수집 실패: {e}")
            scraped_text += f"[계정: {username}] 수집 실패\n\n"

    print("✅ 데이터 수집 완료!")
    return scraped_text


def analyze_with_ai(scraped_data: str) -> str:
    print("🧠 AI 마케터 분석 시작...")
    genai.configure(api_key=GEMINI_API_KEY)

    available_model = None
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            available_model = m.name
            break

    model = genai.GenerativeModel(available_model)

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
- 🥉 사례 3: (브랜드명 / 성과 / 성공 이유 1줄)

## 🚀 3. Concrete Bread 당장 실행 액션
> (이번 주 핵심 목표 1줄)
- [Action 1] (아이디어) : (1~2줄)
- [Action 2] (아이디어) : (1~2줄)
- [Action 3] (아이디어) : (1~2줄)
"""

    response = model.generate_content(prompt)
    print("✅ AI 분석 완료!")
    return response.text


def upload_report_to_notion(analysis_text):
    print("📝 노션 업로드 중...")

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    db_res = requests.get(f"https://api.notion.com/v1/databases/{NOTION_CONTENT_DB_ID}", headers=headers)
    db_props = db_res.json().get("properties", {})
    title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "제목")
    print(f"📌 타이틀 속성 이름: '{title_key}'")

    lines = analysis_text.strip().split("\n")
    children_blocks = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("## "):
            text_content = line.replace("## ", "").replace("**", "").strip()
            children_blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": text_content}}]}
            })
        elif line.startswith("### "):
            text_content = line.replace("### ", "").replace("**", "").strip()
            children_blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": text_content}}]}
            })
        elif line.startswith("> "):
            text_content = line.replace("> ", "").strip()
            children_blocks.append({
                "object": "block", "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {"content": text_content}}]}
            })
        else:
            is_bullet = line.startswith("- ") or line.startswith("* ")
            clean_text = line.lstrip("*- ").strip()
            block_type = "bulleted_list_item" if is_bullet else "paragraph"

            parts = clean_text.split("**")
            rich_text_list = []
            for i, part in enumerate(parts):
                if not part:
                    continue
                rich_text_list.append({
                    "type": "text",
                    "text": {"content": part},
                    "annotations": {"bold": i % 2 == 1}
                })
            if not rich_text_list:
                rich_text_list = [{"type": "text", "text": {"content": clean_text}}]

            children_blocks.append({
                "object": "block", "type": block_type,
                block_type: {"rich_text": rich_text_list}
            })

    page_data = {
        "parent": {"database_id": NOTION_CONTENT_DB_ID},
        "properties": {
            title_key: {"title": [{"text": {"content": "📊 인스타그램 경쟁사 트렌드 분석 리포트"}}]}
        }
    }
    create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data)

    if create_res.status_code != 200:
        print(f"❌ 페이지 생성 실패: {create_res.text}")
        return

    page_id = create_res.json()["id"]

    for i in range(0, len(children_blocks), 100):
        chunk = children_blocks[i:i+100]
        append_res = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers,
            json={"children": chunk}
        )
        if append_res.status_code != 200:
            print(f"❌ 블록 추가 실패: {append_res.text}")
            return

    print("✅ 노션 리포트 업로드 완료!")


def main():
    print("=" * 50)
    print("🚀 인스타그램 경쟁사 자동 분석 시스템 시작")
    print("=" * 50)

    scraped_data = scrape_instagram_data()
    if not scraped_data.strip():
        print("수집된 데이터가 없습니다.")
        return

    analysis = analyze_with_ai(scraped_data)
    upload_report_to_notion(analysis)


if __name__ == "__main__":
    main()
