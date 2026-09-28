import os
import base64
import requests
import tempfile
from datetime import datetime, timedelta, timezone
from google import genai


def find_first_value(data, field_names):
    if isinstance(data, dict):
        for name in field_names:
            value = data.get(name)
            if value not in (None, "", [], {}):
                return value
        for value in data.values():
            found = find_first_value(value, field_names)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(data, list):
        for value in data:
            found = find_first_value(value, field_names)
            if found not in (None, "", [], {}):
                return found
    return None


def compact_value(value, limit=240):
    if isinstance(value, list):
        value = " / ".join(str(item) for item in value if item)
    elif isinstance(value, dict):
        value = " / ".join(f"{key}: {item}" for key, item in value.items())
    return str(value or "확인 불가").replace("\n", " ").strip()[:limit]


def active_days_from(started_at):
    if not started_at or started_at == "확인 불가":
        return None
    try:
        start_date = datetime.fromisoformat(str(started_at)[:10]).date()
        today_kst = datetime.now(timezone(timedelta(hours=9))).date()
        return max((today_kst - start_date).days + 1, 1)
    except (TypeError, ValueError):
        return None


def build_ad_evidence(ads_data):
    rows = []
    seen = set()
    for ad in ads_data:
        if not isinstance(ad, dict):
            continue
        ad_id = compact_value(find_first_value(ad, ["ad_archive_id", "ad_id", "id"]), 100)
        advertiser = compact_value(find_first_value(ad, ["page_name", "advertiser_name", "page_title"]), 120)
        body = compact_value(find_first_value(
            ad, ["ad_creative_bodies", "body", "ad_text", "primary_text", "text"]
        ))
        started_at = compact_value(find_first_value(
            ad, ["ad_delivery_start_time", "start_date", "ad_creation_time", "created_at"]
        ), 80)
        impressions = compact_value(find_first_value(
            ad, ["impressions", "total_impressions", "estimated_impressions"]
        ), 120)
        reach = compact_value(find_first_value(
            ad, ["reach", "estimated_reach", "total_reach"]
        ), 120)
        spend = compact_value(find_first_value(
            ad, ["spend", "estimated_spend", "spend_range"]
        ), 120)
        audience = compact_value(find_first_value(
            ad, ["estimated_audience_size", "audience_size", "potential_reach"]
        ), 120)
        platforms = compact_value(find_first_value(
            ad, ["publisher_platforms", "platforms", "placements"]
        ), 120)
        url = find_first_value(ad, ["ad_snapshot_url", "ad_library_url", "snapshot_url", "url"])
        url = str(url) if isinstance(url, str) and url.startswith("http") else ""
        if not url and ad_id != "확인 불가":
            url = f"https://www.facebook.com/ads/library/?id={ad_id}"
        dedupe_key = ad_id if ad_id != "확인 불가" else (advertiser, body)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append({
            "source_id": f"A{len(rows) + 1}",
            "advertiser": advertiser,
            "ad_id": ad_id,
            "started_at": started_at,
            "active_days": active_days_from(started_at),
            "impressions": impressions,
            "reach": reach,
            "spend": spend,
            "audience": audience,
            "platforms": platforms,
            "body": body,
            "url": url,
        })
    return rows[:30]


def format_ad_evidence(evidence_rows):
    lines = []
    for row in evidence_rows:
        lines.append(
            f"[{row['source_id']}] 광고주={row['advertiser']} | 광고ID={row['ad_id']} | "
            f"시작일={row['started_at']} | 활성일수={row['active_days'] or '확인 불가'} | "
            f"노출={row['impressions']} | 도달={row['reach']} | 비용={row['spend']} | "
            f"예상타깃={row['audience']} | 게재위치={row['platforms']} | 문구={row['body']} | "
            f"원문={row['url'] or '확인 불가'}"
        )
    return "\n".join(lines)


def summarize_ad_metrics(evidence_rows):
    advertisers = {row["advertiser"] for row in evidence_rows if row["advertiser"] != "확인 불가"}
    active_days = [row["active_days"] for row in evidence_rows if row.get("active_days")]
    return {
        "ad_count": len(evidence_rows),
        "advertiser_count": len(advertisers),
        "impression_count": sum(row["impressions"] != "확인 불가" for row in evidence_rows),
        "reach_count": sum(row["reach"] != "확인 불가" for row in evidence_rows),
        "spend_count": sum(row["spend"] != "확인 불가" for row in evidence_rows),
        "average_active_days": sum(active_days) / len(active_days) if active_days else None,
        "longest_active_days": max(active_days) if active_days else None,
    }


def format_ad_metrics(metrics):
    average_days = (
        f"{metrics['average_active_days']:.1f}일" if metrics["average_active_days"] is not None else "확인 불가"
    )
    longest_days = (
        f"{metrics['longest_active_days']}일" if metrics["longest_active_days"] is not None else "확인 불가"
    )
    return (
        f"분석 광고 {metrics['ad_count']}건 / 광고주 {metrics['advertiser_count']}개 / "
        f"노출 수 확인 가능 {metrics['impression_count']}건 / 도달 수 확인 가능 {metrics['reach_count']}건 / "
        f"비용 확인 가능 {metrics['spend_count']}건 / 평균 활성일수 {average_days} / 최장 활성일수 {longest_days}"
    )

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
    evidence_rows = build_ad_evidence(all_collected_ads)
    report_text = generate_text_report(client, evidence_rows)
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
    upload_to_notion(report_text, NOTION_TOKEN, NOTION_META_AD_DB_ID, image_url, evidence_rows)


# ─────────────────────────────────────────────
# 2. Gemini 텍스트 분석 리포트 생성 (기존)
# ─────────────────────────────────────────────
def generate_text_report(client, evidence_rows):
    print("🤖 Gemini AI 카테고리 트렌드 분석 시작...")
    prompt = f"""
당신은 아동 의류 업계 전문 마케터입니다. 아래 메타 광고 데이터를 분석하여 노션에 업로드할 리포트를 작성해 주세요.

[광고 수치 요약]
{format_ad_metrics(summarize_ad_metrics(evidence_rows))}

[수집 데이터]
{format_ad_evidence(evidence_rows)}

[절대 지켜야 할 작성 규칙 (노션 파싱용)]
1. 큰 제목은 반드시 '## ' 로 시작할 것.
2. 각 문단의 핵심 요약은 반드시 '> ' 로 시작할 것.
3. 세부 항목이나 리스트는 반드시 '- ' 로 시작할 것.
4. 모든 분석과 성과 판단 뒤에 반드시 근거번호 [A숫자]를 표시할 것.
5. 제공된 데이터로 확인할 수 없는 내용은 추정하지 말고 '확인 불가'라고 쓸 것.
6. 수치가 제공된 광고는 노출, 도달, 비용, 활성일수를 반드시 적을 것.
7. '성과가 좋다'고 단정하려면 비교 가능한 수치 근거가 있어야 하며, 없으면 '장기 집행 가능성이 높은 광고'처럼 관찰 가능한 범위로 표현할 것.
8. 각 핵심 분석은 '관찰 수치 → 그렇게 판단한 이유 → 우리 브랜드 적용점' 순서로 쓸 것.

[출력 양식]
## 📊 1. 광고 수치 요약
> (분석 광고 수, 광고주 수, 수치 확인 가능 건수, 평균·최장 활성일수)
- 수치가 확인되는 대표 광고: (광고주 / 노출 / 도달 / 비용 / 활성일수 / 근거번호)
- 수치 제한: (확인 불가 항목과 그에 따른 해석 한계)

## 🎯 2. 핵심 소구점과 판단 근거
> (반복되는 핵심 소구점 요약)
- 관찰 수치: (광고주 / 노출·도달·활성일수 / 근거번호)
- 판단 이유: (광고 문구의 어떤 표현과 수치를 연결해 판단했는지)
- 공통 패턴: (2개 이상의 광고를 비교한 상세 근거)

## 🔥 3. 주목할 광고 상세 분석
> (가장 설득력 있는 관찰 한 줄)
- 사례 1 관찰 수치: (광고주 / 노출 / 도달 / 비용 / 활성일수 / 근거번호)
- 사례 1 판단 이유: (문구와 수치가 의미하는 바를 자세히)
- 사례 2 관찰 수치: (광고주 / 노출 / 도달 / 비용 / 활성일수 / 근거번호)
- 사례 2 판단 이유: (문구와 수치가 의미하는 바를 자세히)

## 💡 4. 우리 브랜드 적용 제안
> (실행 방향 한 줄)
- 제안 1: (구체적 카피 / 연결된 근거번호 / 기대 이유)
- 제안 2: (구체적 카피 / 연결된 근거번호 / 기대 이유)
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
        import subprocess, sys

        # GitHub Actions 환경에서 chromium 자동 설치
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            check=True, capture_output=True
        )
        print("✅ Chromium 설치 확인 완료")
    except Exception as e:
        print(f"⚠️ Chromium 설치 중 경고 (무시): {e}")

    try:
        from playwright.sync_api import sync_playwright

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html_content)
            html_path = f.name
        print(f"📄 HTML 저장 완료: {html_path}")

        png_path = html_path.replace(".html", ".png")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(f"file://{html_path}")
            page.wait_for_timeout(2000)  # 폰트 로드 대기
            page.screenshot(path=png_path, full_page=False)
            browser.close()

        import os
        print(f"✅ 스크린샷 완료! PNG 크기: {os.path.getsize(png_path):,} bytes")
        return upload_to_imgbb(png_path, imgbb_api_key)

    except ImportError:
        print("❌ playwright 미설치: pip install playwright")
        return None
    except Exception as e:
        print(f"❌ HTML→이미지 변환 오류 (상세): {type(e).__name__}: {e}")
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
        print(f"❌ imgbb 업로드 오류 (상세): {type(e).__name__}: {e}")
        if 'res' in dir():
            print(f"   imgbb 응답: {res.status_code} / {res.text[:300]}")
        return None


# ─────────────────────────────────────────────
# 5. 노션 업로드 (상단 이미지 + 하단 텍스트)
# ─────────────────────────────────────────────
def upload_to_notion(analysis_text, token, db_id, image_url=None, evidence_rows=None):
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

    metrics = summarize_ad_metrics(evidence_rows or [])
    children_blocks.append({
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "📊"},
            "rich_text": [{"type": "text", "text": {"content": format_ad_metrics(metrics)}}],
        },
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

    children_blocks.append({"object": "block", "type": "divider", "divider": {}})
    children_blocks.append({
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔎 분석 근거 자료"}}]}
    })
    children_blocks.append({
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {
            "content": "수집 시점에 활성 상태였던 광고입니다. 본문 분석의 [A숫자]와 연결됩니다."
        }}]}
    })
    for row in evidence_rows or []:
        label = (f"[{row['source_id']}] {row['advertiser']} | 광고 ID {row['ad_id']} | "
                 f"시작 {row['started_at']} · 활성 {row['active_days'] or '확인 불가'}일 | "
                 f"노출 {row['impressions']} · 도달 {row['reach']} · 비용 {row['spend']} · "
                 f"예상 타깃 {row['audience']} · 게재위치 {row['platforms']} | {row['body']}")
        rich_text = [{"type": "text", "text": {"content": label[:1700]}}]
        if row.get("url"):
            rich_text.append({"type": "text", "text": {
                "content": "  광고 원문 보기", "link": {"url": row["url"]}
            }})
        children_blocks.append({
            "object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich_text}
        })

    # ── 페이지 생성 ─────────────────────────────────
    today_kst = datetime.now(timezone(timedelta(hours=9))).date()
    period_end = today_kst - timedelta(days=today_kst.weekday() + 1)
    period_start = period_end - timedelta(days=6)
    end_year = f"{period_end.year}년 " if period_end.year != period_start.year else ""
    period = (f"{period_start.year}년 {period_start.month}월 {period_start.day}일(월) ~ "
              f"{end_year}{period_end.month}월 {period_end.day}일(일)")
    title = f"📈 주간 메타(Meta) 아동복 카테고리 광고 리포트 ({period})"

    page_data = {
        "parent": {"database_id": db_id},
        "properties": {
            title_key: {"title": [{"text": {"content": title}}]}
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
