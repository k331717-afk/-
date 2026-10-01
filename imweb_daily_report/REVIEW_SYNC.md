# 아임웹 상품 구매평 → 노션

대상: https://app.notion.com/p/e7caefbd53c1428abf90c8ef399d77d2

매주 월요일 오전 3시(한국 시간)에 상품 구매평을 동기화합니다. 리뷰 글은 각 페이지의 `리뷰 내용` 본문에, HTML에 포함된 사진은 이미지 블록과 `사진` 속성에 저장합니다. 평점 3점 이하는 제목 앞의 🔴 표시를 유지합니다.

`내용`, `리뷰 번호`, `비밀글`, `숨김` 속성은 사용하지 않습니다. 중복 방지에 필요한 아임웹 리뷰 번호와 노션 페이지 ID는 `review-sync-state` 브랜치의 `imweb_daily_report/review_sync_state.json`에 압축 저장합니다. 이 브랜치를 삭제하면 동기화가 중복 방지를 위해 중단됩니다. 생성 요청 직전에 내부 기록을 저장하므로, 응답 지연으로 같은 구매평을 다시 만들지 않습니다.

GitHub Secrets: `REVIEW_NOTION_TOKEN`, `IMWEB_API_KEY`, `IMWEB_SECRET_KEY`, 필요 시 `IMWEB_SHOP_CODE`. 내부 기록 저장에는 해당 워크플로의 `GITHUB_TOKEN`과 `contents: write` 권한을 사용합니다.

`Move Review Content Into Pages` 작업은 기존 속성을 먼저 백업하고 각 페이지 본문을 이전·검증합니다. 전부 확인하고 원본이 바뀌지 않았을 때만 네 속성을 삭제합니다. 실패 시 원본 속성을 유지하며, 재실행하면 완료된 페이지를 이어서 처리합니다. 이전과 주간 동기화는 같은 실행 잠금을 사용합니다.

판매 리포트와 별도이며 판매 리포트의 코드, 토큰, 데이터베이스는 사용하지 않습니다.

검증: `python imweb_daily_report/test_review_body.py`

