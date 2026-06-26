import os
import base64
import requests
import tempfile
from google import genai

# ─────────────────────────────────────────────
# 1. 메인 진입점
# ─────────────────────────────────────────────
def main():
    RAPIDAPI_KEY        = os.environ.get("RAPIDAPI_KEY")
    GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY")
    NOTION_TOKEN        = os.environ.get("NOTION_API_TOKEN")
    NOTION_META_AD_DB_ID = os.environ.get("NOTION_META_AD_DB_ID")
    IMGBB_API_KEY       = os.environ.get("IMGBB_API_KEY")     # ← 추가 필요

    if not RAPIDAPI_KEY or not GEMINI_API_KEY:
        print("❌ 에러: 필수 API 키 환경변수가 설정되지 않았습니다.")
        return

    # ── 광고 데이터 수집 ──────────────────────────────
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
        querystring = {
            "query": keyword, "status": "ACTIVE", "country": "KR",
            "media_type": "ALL", "sort_by": "total_impressions", "trim": "false"
        }
        try:
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                all_collected_ads.extend(data)
            elif isinstance(data, dict) and "ads" in data:
                all_collected_ads.extend(data["ads"])
            else:
                all_collected_ads.append(data)
            print(f"✅ '{keyword}' 수집 완료!")
        except Exception as e:
            print(f"❌ '{keyword}' 수집 중 오류: {e}")
            continue

    if not all_collected_ads:
        print("⚠️ 수집된 메타 광고 데이터가 없습니다.")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)

    # ── AI 텍스트 분석 ──────────────────────────────
    report_text = generate_text_report(client, all_collected_ads)
    if not report_text:
        return

    # ── AI 인포그래픽 HTML 생성 ──────────────────────
    infographic_html = generate_infographic_html(client, all_collected_ads, report_text)

    # ── HTML → 이미지 → Imgur 업로드 ─────────────────
    image_url = None
    if infographic_html and IMGBB_API_KEY:
        image_url = html_to_imgbb(infographic_html, IMGBB_API_KEY)
    elif not IMGBB_API_KEY:
        print("⚠️ IMGBB_API_KEY 미설정 → 이미지 없이 텍스트만 업로드합니다.")

    # ── 노션 업로드 (상단 이미지 + 하단 텍스트) ───────
    upload_to_notion(report_text, NOTION_TOKEN, NOTION_META_AD_DB_ID, image_url)


# ─────────────────────────────────────────────
# 2. Gemini 텍스트 분석 리포트 생성 (기존)
# ─────────────────────────────────────────────
def generate_text_report(client, ads_data):
    print("🤖 Gemini AI 카테고리 트렌드 분석 시작...")
    prompt = f"""
당신은 아동 의류 업계 전문 마케터입니다. 아래 메타 광고 데이터를 분석하여 노션에 업로드할 리포트를 작성해 주세요.

[수집 데이터]
{ads_data[:30]}

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
    try:
        ai_response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        try:
            report_text = ai_response.text
        except ValueError:
            report_text = None

        if not report_text:
            print("⚠️ Gemini 텍스트 응답이 비어있습니다.")
            return None

        print("✨ AI 텍스트 분석 완료!")
        return report_text
    except Exception as e:
        print(f"❌ Gemini 텍스트 분석 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 3. Gemini 인포그래픽 HTML 생성 (신규)
# ─────────────────────────────────────────────
def generate_infographic_html(client, ads_data, report_text):
    print("🎨 Gemini AI 인포그래픽 HTML 생성 시작...")
    prompt = f"""
당신은 시각 디자인 전문가입니다.
아래 아동복 Meta 광고 분석 결과를 바탕으로 **완전한 단일 HTML 파일**로 인포그래픽을 만들어 주세요.

[분석 텍스트 요약]
{report_text}

[디자인 요구사항]
- 크기: 너비 1200px × 높이 630px (SNS 썸네일 비율)
- 배경: 흰색 또는 파스텔 톤 (#FFF8F0 등 따뜻한 계열)
- 폰트: Noto Sans KR (Google Fonts CDN 사용)
- 섹션을 카드 형식으로 나누어 표시 (소구점 / 카피 패턴 / 추천 카피)
- 각 카드마다 아이콘 이모지 활용
- 하단에 "Concrete Bread 주간 광고 인사이트" 브랜드 워터마크 표시
- CSS는 <style> 태그 내에 인라인으로 포함
- 외부 이미지 없이 순수 HTML + CSS만 사용
- 반드시 완전한 HTML 파일 (<!DOCTYPE html> 부터 </html> 까지) 로 출력

[출력 규칙]
- HTML 코드만 출력할 것. 설명 텍스트, 마크다운 코드블록(```) 절대 포함 금지.
"""
    try:
        ai_response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        try:
            html_text = ai_response.text
        except ValueError:
            html_text = None

        if not html_text:
            print("⚠️ Gemini 인포그래픽 응답이 비어있습니다.")
            return None

        # 혹시 마크다운 코드블록이 포함된 경우 제거
        html_text = html_text.strip()
        if html_text.startswith("```"):
            lines = html_text.split("\n")
            html_text = "\n".join(lines[1:-1])

        print("✨ AI 인포그래픽 HTML 생성 완료!")
        return html_text
    except Exception as e:
        print(f"❌ Gemini 인포그래픽 생성 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 4. HTML → PNG 스크린샷 → imgbb 업로드
# ─────────────────────────────────────────────
def html_to_imgbb(html_content, imgbb_api_key):
    """playwright 로 HTML을 PNG로 렌더링 후 imgbb에 업로드"""
    print("📸 HTML → 이미지 변환 중...")
    try:
        from playwright.sync_api import sync_playwright

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html_content)
            html_path = f.name

        png_path = html_path.replace(".html", ".png")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(f"file://{html_path}")
            page.wait_for_timeout(1500)  # 폰트 로드 대기
            page.screenshot(path=png_path, full_page=False)
            browser.close()

        print("✅ 스크린샷 완료!")
        return upload_to_imgbb(png_path, imgbb_api_key)

    except ImportError:
        print("❌ playwright 미설치. 터미널에서 실행: pip install playwright && playwright install chromium")
        return None
    except Exception as e:
        print(f"❌ HTML→이미지 변환 오류: {e}")
        return None


def upload_to_imgbb(image_path, api_key):
    """로컬 이미지 파일을 imgbb에 업로드하고 공개 URL 반환"""
    print("☁️  imgbb 업로드 중...")
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        res = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": b64}
        )
        res.raise_for_status()
        data = res.json()
        image_url = data["data"]["url"]
        print(f"✅ imgbb 업로드 완료: {image_url}")
        return image_url
    except Exception as e:
        print(f"❌ imgbb 업로드 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 5. 노션 업로드 (상단 이미지 + 하단 텍스트)
# ─────────────────────────────────────────────
def upload_to_notion(analysis_text, token, db_id, image_url=None):
    if not analysis_text:
        print("❌ 유효한 AI 분석 결과가 없어 노션 업로드를 취소합니다.")
        return
    if not db_id:
        print("❌ NOTION_META_AD_DB_ID가 설정되지 않았습니다.")
        return

    print("📝 노션 업로드 중...")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # DB 제목 컬럼 키 확인
    db_res = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=headers)
    db_props = db_res.json().get("properties", {})
    title_key = next((k for k, v in db_props.items() if v.get("type") == "title"), "이름")

    # ── 블록 구성 ──────────────────────────────────
    children_blocks = []

    # 1) 상단 인포그래픽 이미지 블록
    if image_url:
        children_blocks.append({
            "object": "block",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": image_url}
            }
        })
        # 이미지 아래 여백용 구분선
        children_blocks.append({
            "object": "block",
            "type": "divider",
            "divider": {}
        })

    # 2) 하단 텍스트 분석 블록
    lines = analysis_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("## "):
            children_blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {
                    "content": line.replace("## ", "").replace("**", "").strip()
                }}]}
            })
        elif line.startswith("> "):
            children_blocks.append({
                "object": "block", "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {
                    "content": line.replace("> ", "").strip()
                }}]}
            })
        else:
            block_type = "bulleted_list_item" if line.startswith("- ") else "paragraph"
            clean_text = line.lstrip("*- ").strip()
            parts = clean_text.split("**")
            rich_text_list = [
                {"type": "text", "text": {"content": part},
                 "annotations": {"bold": i % 2 == 1}}
                for i, part in enumerate(parts) if part
            ]
            if not rich_text_list:
                rich_text_list = [{"type": "text", "text": {"content": clean_text}}]
            children_blocks.append({
                "object": "block", "type": block_type,
                block_type: {"rich_text": rich_text_list}
            })

    # ── 페이지 생성 ─────────────────────────────────
    page_data = {
        "parent": {"database_id": db_id},
        "properties": {
            title_key: {"title": [{"text": {"content": "📈 주간 메타(Meta) 아동복 카테고리 광고 리포트"}}]}
        }
    }
    create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=page_data)
    if create_res.status_code != 200:
        print(f"❌ 페이지 생성 실패: {create_res.text}")
        return

    page_id = create_res.json()["id"]

    # ── 블록 100개씩 청크 업로드 ─────────────────────
    for i in range(0, len(children_blocks), 100):
        chunk = children_blocks[i:i + 100]
        requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers,
            json={"children": chunk}
        )

    print("✅ 노션 리포트 업로드 완료! (상단 인포그래픽 + 하단 텍스트)")


if __name__ == "__main__":
    main()
