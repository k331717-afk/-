import os
import time
import base64
import tempfile
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv(dotenv_path=r"C:\Users\kk331\Desktop\meta_report\.env")

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN")
NOTION_CONTENT_DB_ID = os.environ.get("NOTION_CONTENT_DB_ID")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-2"

# ✨ 대표님이 찾아오신 인스타그램 비즈니스 ID를 직접 박아넣습니다!
MY_IG_ID = "17841408849647327"

COMPETITORS = [
    "konny_kr", "moomooz_essential", "bonats.official",
    "bebedepino", "_bobochoses_", "benebene_official",
    "detamy_project", "apricotstudios_"
]

def scrape_instagram_data_official(ig_user_id) -> str:
    print("🕵️ Meta 공식 API 출동! 429 에러 없이 당당하게 긁어오는 중...")

    url = f"https://graph.facebook.com/v19.0/{ig_user_id}"
    scraped_text = ""
    valid_data_count = 0

    for username in COMPETITORS:
        print(f"📸 [{username}] 게시물 가져오는 중...")
        
        # Business Discovery를 이용한 합법적이고 빠른 데이터 요청
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
    # 503 과부하 대비 재시도 로직 (최대 3회)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            print("✅ AI 분석 완료!")
            return response.text
        except Exception as e:
            is_overload = "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e)
            if is_overload and attempt < max_retries - 1:
                wait_sec = (attempt + 1) * 30  # 30초 → 60초
                print(f"⚠️ Gemini 서버 과부하. {wait_sec}초 후 재시도... ({attempt + 1}/{max_retries})")
                time.sleep(wait_sec)
            else:
                print(f"❌ Gemini 분석 실패 (재시도 {max_retries}회 소진): {e}")
                return None

def generate_infographic_html(analysis_text: str, scraped_data: str) -> str:
    print("🎨 AI 인포그래픽 HTML 생성 시작...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
너는 유아동복 브랜드의 콘텐츠 인사이트를 한 장짜리 카드뉴스로 정리하는 시각 디자이너야.
아래 인스타그램 경쟁사 분석 결과를 바탕으로 노션 상단에 넣을 완전한 HTML 인포그래픽을 만들어줘.

[분석 텍스트]
{analysis_text}

[원본 데이터 요약]
{scraped_data[:4000]}

[디자인 요구사항]
- 크기: 너비 1200px × 높이 630px
- 폰트: Noto Sans KR (Google Fonts CDN 사용)
- 배경: 밝고 세련된 톤, 유아동복 브랜드에 어울리는 부드러운 컬러
- 섹션: 시장 트렌드 / 벤치마킹 포인트 / 실행 액션 3개 카드
- 각 카드에 아이콘 또는 이모지를 크게 배치
- 하단에 "Concrete Bread 주간 인스타그램 경쟁사 인사이트" 워터마크 표시
- 외부 이미지 없이 순수 HTML + CSS만 사용
- 반드시 완전한 HTML 파일 (<!DOCTYPE html>부터 </html>까지)로 출력

[출력 규칙]
- HTML 코드만 출력하고 설명, 마크다운 코드블록(```), 주석은 포함하지 마.
"""
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        html_text = response.text.strip() if response.text else ""
        if html_text.startswith("```"):
            lines = html_text.split("\n")
            html_text = "\n".join(lines[1:-1]).strip()
        if not html_text:
            print("⚠️ 인포그래픽 HTML 응답이 비어 있습니다.")
            return None
        print("✅ AI 인포그래픽 HTML 생성 완료!")
        return html_text
    except Exception as e:
        print(f"❌ 인포그래픽 HTML 생성 실패: {e}")
        return None


def html_to_imgbb(html_content: str, imgbb_api_key: str) -> str:
    print("📸 HTML → 이미지 변환 중...")
    import subprocess, sys

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("⚠️ playwright 패키지가 없어 자동 설치를 시도합니다.")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "playwright"],
                check=True
            )
            from playwright.sync_api import sync_playwright
        except Exception as e:
            print(f"❌ playwright 자동 설치 실패: {type(e).__name__}: {e}")
            return None

    try:
        install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        if os.name != "nt":
            install_cmd.append("--with-deps")
        subprocess.run(
            install_cmd,
            check=True
        )
    except Exception as e:
        print(f"⚠️ Chromium 설치 확인 중 경고 (무시): {e}")

    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html_content)
            html_path = f.name
        png_path = html_path.replace(".html", ".png")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(f"file://{html_path}")
            page.wait_for_timeout(2000)
            page.screenshot(path=png_path, full_page=False)
            browser.close()

        print(f"✅ 스크린샷 완료! PNG 크기: {os.path.getsize(png_path):,} bytes")
        return upload_to_imgbb(png_path, imgbb_api_key)
    except Exception as e:
        print(f"❌ HTML→이미지 변환 오류: {type(e).__name__}: {e}")
        return None


def upload_to_imgbb(image_path: str, api_key: str) -> str:
    print("☁️ imgbb 업로드 중...")
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        res = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": b64}
        )
        res.raise_for_status()
        image_url = res.json()["data"]["url"]
        print(f"✅ imgbb 업로드 완료: {image_url}")
        return image_url
    except Exception as e:
        print(f"❌ imgbb 업로드 오류: {type(e).__name__}: {e}")
        if "res" in locals():
            print(f"   imgbb 응답: {res.status_code} / {res.text[:300]}")
        return None


def generate_openai_infographic_to_imgbb(analysis_text: str, scraped_data: str) -> str:
    if not OPENAI_API_KEY:
        print("⚠️ OPENAI_API_KEY가 없어 OpenAI 인포그래픽 생성을 건너뜁니다.")
        return None
    if not IMGBB_API_KEY:
        print("⚠️ IMGBB_API_KEY가 없어 생성 이미지를 노션에 넣을 수 없습니다.")
        return None

    print("🎨 OpenAI 이미지 생성기로 인포그래픽 생성 중...")
    prompt = f"""
Create a polished Korean infographic image for a Notion weekly report.

Topic: Instagram competitor trend report for a Korean kidswear brand, Concrete Bread.
Canvas: landscape 1536x1024.
Style: premium but warm kidswear brand mood, soft bright palette, clean editorial layout, modern Korean marketing report.

Use 3 large visual sections:
1. 이번 주 시장 트렌드
2. 벤치마킹 포인트
3. 실행 액션

Important text guidance:
- Include only short Korean labels and very short phrases.
- Avoid tiny body text.
- Use the analysis below as content direction, but do not try to fit every sentence.
- Make the image look like a high-end dashboard/cardnews cover for Notion.
- Add a small footer text: "Concrete Bread 주간 인스타그램 경쟁사 인사이트"

Analysis:
{analysis_text[:2500]}

Source data excerpt:
{scraped_data[:1500]}
"""

    try:
        res = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENAI_IMAGE_MODEL,
                "prompt": prompt,
                "size": "1536x1024",
                "quality": "medium",
                "output_format": "png"
            },
            timeout=180
        )
        res.raise_for_status()
        image_base64 = res.json()["data"][0]["b64_json"]

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(base64.b64decode(image_base64))
            png_path = f.name

        print(f"✅ OpenAI 인포그래픽 생성 완료: {os.path.getsize(png_path):,} bytes")
        return upload_to_imgbb(png_path, IMGBB_API_KEY)
    except Exception as e:
        print(f"❌ OpenAI 인포그래픽 생성 실패: {type(e).__name__}: {e}")
        if "res" in locals():
            print(f"   OpenAI 응답: {res.status_code} / {res.text[:500]}")
        return None


def upload_report_to_notion(analysis_text, image_url=None):
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

    if image_url:
        children_blocks.append({
            "object": "block",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": image_url}
            }
        })
        children_blocks.append({"object": "block", "type": "divider", "divider": {}})

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

    print(f"✅ 내 인스타그램 ID({MY_IG_ID})로 바로 탐색을 시작합니다!")
    
    # 🚨 에러가 났던 계정 찾기 함수를 빼버리고, 직접 ID를 넣어서 실행합니다.
    scraped_data = scrape_instagram_data_official(MY_IG_ID)
    if not scraped_data:
        return 

    analysis = analyze_with_ai(scraped_data)
    if not analysis:
        print("❌ AI 분석 결과가 없어 노션 업로드를 건너뜁니다.")
        return

    image_url = generate_openai_infographic_to_imgbb(analysis, scraped_data)
    if not image_url and IMGBB_API_KEY:
        print("⚠️ OpenAI 이미지 생성이 실패해 Gemini HTML 캡처 이미지로 대체합니다.")
        infographic_html = generate_infographic_html(analysis, scraped_data)
        if infographic_html:
            image_url = html_to_imgbb(infographic_html, IMGBB_API_KEY)

    upload_report_to_notion(analysis, image_url)

if __name__ == "__main__":
    main()
