# 아임웹 상품 구매평 → 노션

노션 데이터베이스: https://app.notion.com/p/3ea9f355db8580aab978ea7455afc2e3

`review_sync.py`는 아임웹 상품 구매평 전체를 읽어 리뷰 번호로 중복을 확인한 뒤, 새 리뷰를 만들고 내용이 바뀐 리뷰를 수정합니다. 비밀글과 숨김 상태도 별도 열에 표시합니다. 매일 09:52(한국시간)에 실행하도록 `review-sync.yml`을 준비했습니다.

## 연결 준비

1. 이 데이터베이스의 **연결** 메뉴에서 기존 판매 리포트에 쓰는 Notion 통합을 추가합니다.
2. 기존 GitHub 저장소의 Actions secrets에 `IMWEB_API_KEY`, `IMWEB_SECRET_KEY`, `IMWEB_NOTION_TOKEN`이 이미 등록돼 있다면 그대로 사용합니다. 사이트 코드가 필요한 설정이라면 `IMWEB_SHOP_CODE`도 사용합니다.
3. `imweb_daily_report/review_sync.py`와 `.github/workflows/review-sync.yml`을 해당 저장소에 올립니다. `.github` 폴더는 저장소 최상위에 두어야 합니다.
4. GitHub Actions에서 **Sync Imweb Product Reviews → Run workflow**를 한 번 실행합니다. 첫 실행에서는 전체 구매평을 가져오므로 리뷰 수에 따라 시간이 걸립니다.

로컬 실행은 기존 `.env`에 `IMWEB_API_KEY`, `IMWEB_SECRET_KEY`, `NOTION_TOKEN`을 설정하고 `python imweb_daily_report/review_sync.py`를 실행합니다. 리뷰 데이터베이스 ID는 프로그램에 설정돼 있습니다.

아임웹 구매평 조회는 기존 주문 보고서와 같은 v2 인증을 사용합니다. 공식 문서의 `GET /v2/shop/reviews`와 공통 `offset`/`limit` 페이지 방식을 따릅니다. 실제 사이트에서 첫 실행 결과를 확인해야 완료로 판단할 수 있습니다.
