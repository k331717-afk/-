import os
import requests
from dotenv import load_dotenv
from apify_client import ApifyClient
import google.generativeai as genai

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_CONTENT_DB_ID = os.environ.get("NOTION_META_AD_DB_ID")

COMPETITOR_URLS = [
    "https://www.facebook.com/konny.by.erin",
    "https://www.facebook.com/apricotstudios.co.kr",
    "https://www.facebook.com/100076376673060",
    "https://www.facebook.com/61551768921927",
    "https://www.facebook.com/moomooz_essential",
    "https://www.facebook.com/ozkizkorea",
]

def scrape_all_competitors() -> str:
    print("📡 Apify로 Meta 광고 라이브러리 수집 시작... (약 2~3분 소요)")
    client = ApifyClient(APIFY_TOKEN)

    run_input = {
        "mode": "page-ads",
        "searchTerms": [
            "konny.by.erin",
            "apricotstudios.co.kr",
            "100076376673060",
            "61551768921927",
            "moomooz_essential",
            "ozkizkorea",
        ],
        "maxItems": 30,
        "country": "KR",
        "adStatus": "ALL",
    }

    run = client.actor("unseenuser/meta-ads").call(run_input=run_input)

    all_text = ""
    current_page = ""

    for item in client.dataset(run.default_dataset_id).iterate_items():
        page_name = item.get("pageName") or item.get("advertiserName") or item.get("page_name", "Unknown")
        body      = item.get("body") or item.get("adCreativeBody") or item.get("text") or ""
        title     = item.get("title") or item.get("adCreativeLinkTitle") or ""
        start     = item.get("startDate") or item.get("adDeliveryStartTime") or "날짜 미상"
        snap      = item.get("snapshotUrl") or item.get("adSnapshotUrl") or ""

        if page_name != current_page:
            current_page = page_name
            all_text += f"\n=== {page_name} ===\n"

        all_text += f"- 집행 시작: {start}\n"
        if title:
            all_text += f"  제목: {title}\n"
        all_text += f"  본문: {body[:200]}\n"
        if snap:
            all_text += f"  미리보기: {snap}\n"
        all_text += "\n"

    print("✅ 전체 수집 완료!")
    return all_text


def analyze_with_ai(scraped_data: str) -> str:
    print("🧠 AI 마케터 분석 중...")
    genai.configure(api_key=GEMINI_API_KEY)

    available_model = None
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            available_model = m.name
            break

    model = genai.GenerativeModel(available_model)

    prompt = f"""
너는 10년 차 유아동복 퍼포먼스/콘텐츠 마케터야.
아래는 경쟁사들이 Meta(인스타그램/페이스북)에서 현재 집행 중인 광고 데이터야.
이걸 분석해서 실무진이 1분 만에 읽을 수 있는 '인포그래픽 스타일' 노션 리포트를 써줘.

데이터:
{scraped_data}

[절대 지켜야 할 작성 규칙]
1. 짧고 명확한 문장으로 끊어 칠 것. 서술 금지.
2. 이모지(🔥, 💡, 🚨, 🎯 등)를 적극 사용.
3. 큰 카테고리 제목은 반드시 '## ' 로 시작.
4. 각 섹션 핵심 한 줄 요약은 반드시 '> ' 로 시작.

[출력 양식]
## 🎯 1. 경쟁사 광고 트렌드 요약
> (핵심 한 줄 요약)
- 📢 주요 메시지 무드: (짧게)
- 🏷️ 자주 쓰는 키워드/카피 패턴: (짧게)
- 🎨 크리에이티브 포맷: (이미지/영상/캐러셀 등)

## 🔥 2. 경쟁사별 광고 분석
> (핵심 한 줄 요약)
- 🥇 코니 Konny: (광고 내용 / 왜 잘했는지 1줄)
- 🥈 아프리콧스튜디오: (광고 내용 / 왜 잘했는지 1줄)
- 🥉 드타미마켓: (광고 내용 / 왜 잘했는지 1줄)
- 4️⃣ 보나츠: (광고 내용 / 왜 잘했는지 1줄)
- 5️⃣ 므므브: (광고 내용 / 왜 잘했는지 1줄)
- 6️⃣ 오즈키즈 OZKIZ: (광고 내용 / 왜 잘했는지 1줄)

## 🚀 3. Concrete Bread 즉시 실행 액션
> (이번 주 핵심 목표 1줄)
- [Action 1] (아이디어) : (어떻게 할지 1~2줄)
- [Action 2] (아이디어) : (어떻게 할지 1~2줄)
- [Action 3] (아이디어) : (어떻게 할지 1~2줄)

## 🚨 4. 우리가 하면 안 되는 것
> (경쟁사 실수 or 포화된 패턴 요약)
- ❌ (하지 말아야 할 것 1)
- ❌ (하지 말아야 할 것 2)
"""

    response = model.generate_content(prompt)
    print("✅ AI 분석 완료!")
    return response.text


def upload_to_notion(analysis_text: str):
    print("📝 노션 업로드 중...")

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    db_res = requests.get(f"https://api.notion.com/v1/databases/{NOTION_CONTENT_DB_ID}", headers=headers)
    db_props = db_res.json().get("properties", {})
    title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "제목")
    print(f"  📌 타이틀 속성 이름: '{title_key}'")

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
            is_bullet = line.startswith("* ") or line.startswith("- ")
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
            title_key: {"title": [{"text": {"content": "📊 Meta 광고 경쟁사 분석 리포트"}}]}
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
            print(f"❌ 블록 추가 실패 (chunk {i}): {append_res.text}")
            return

    print("✅ 노션 리포트 업로드 완료!")


def main():
    print("=" * 50)
    print("🚀 Meta 광고 경쟁사 자동 분석 시스템 시작")
    print("=" * 50)

    scraped_data = scrape_all_competitors()

    if not scraped_data.strip():
        print("수집된 광고 데이터가 없습니다.")
        return

    analysis = analyze_with_ai(scraped_data)
    upload_to_notion(analysis)


if __name__ == "__main__":
    main()
