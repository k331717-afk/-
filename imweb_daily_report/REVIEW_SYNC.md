# 아임웹 상품 구매평 → 노션

노션 데이터베이스: https://app.notion.com/p/e7caefbd53c1428abf90c8ef399d77d2

`review_sync.py`는 아임웹 상품 구매평 전체를 읽어 리뷰 번호로 중복을 확인한 뒤, 새 리뷰를 만들고 내용이 바뀐 리뷰를 수정합니다. 비밀글과 숨김 상태도 별도 열에 표시합니다. 판매 리포트 파일을 불러오지 않으며 상품평 전용 노션 토큰만 사용합니다.

## 연결 준비

1. 이 데이터베이스에 상품평 전용 Notion 통합을 연결합니다. 기존 판매 리포트 통합은 연결하지 않습니다.
2. GitHub Actions secret `REVIEW_NOTION_TOKEN`에 상품평 전용 통합 토큰을 등록합니다. 아임웹 인증에는 이미 등록된 `IMWEB_API_KEY`, `IMWEB_SECRET_KEY`를 사용합니다. 사이트 코드가 필요한 설정이라면 `IMWEB_SHOP_CODE`도 사용합니다.
3. `imweb_daily_report/review_sync.py`와 `.github/workflows/review-sync.yml`을 해당 저장소에 올립니다. `.github` 폴더는 저장소 최상위에 두어야 합니다.
4. GitHub Actions에서 **Sync Imweb Product Reviews → Run workflow**를 실행해 전체 구매평을 처음 가져옵니다. 이후 매일 오전 3시(한국 시간)에 자동 동기화합니다. 실행 제한은 360분이며, 중단 후 다시 실행해도 리뷰 번호로 중복을 확인합니다.

로컬 실행은 `IMWEB_API_KEY`, `IMWEB_SECRET_KEY`, `REVIEW_NOTION_TOKEN`을 설정하고 `python imweb_daily_report/review_sync.py`를 실행합니다. 리뷰 데이터베이스 ID는 프로그램에 설정돼 있습니다.

아임웹 구매평 조회는 기존 주문 보고서와 같은 v2 인증을 사용합니다. 공식 문서의 `GET /v2/shop/reviews`와 공통 `offset`/`limit` 페이지 방식을 따릅니다. 실제 사이트에서 첫 실행 결과를 확인해야 완료로 판단할 수 있습니다.
