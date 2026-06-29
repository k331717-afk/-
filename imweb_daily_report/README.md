# 아임웹 전일 판매 TOP 10 카카오톡 리포트

Windows PC에서 매일 실행할 Python 3.11+ 자동화 프로그램입니다.

## 기능

- Asia/Seoul 기준 전일 00:00:00 ~ 23:59:59 주문 조회
- 취소, 환불, 입금대기 주문 제외
- 상품명 기준 판매량 TOP 10 계산
- TOP 10 상품의 옵션별 판매량 순위와 구매 금액 집계
- 콘솔 출력 및 카카오톡 나에게 보내기 발송
- 노션 데이터베이스에 기준일별 리포트 저장
- `logs/report.log` 실행 로그 저장
- 디버그 옵션으로 `logs/sample_imweb_response.json` 저장
- 긴 메시지 자동 분할 발송

## 설치

권장 실행 위치는 다음과 같습니다.

```powershell
C:\imweb_daily_report
```

처음 한 번만 `.env.example`을 `.env`로 복사한 뒤 값을 채웁니다.

```powershell
cd C:\imweb_daily_report
copy .env.example .env
notepad .env
```

## 첫 실행

`.env`에서 `SEND_KAKAO=false`, `DRY_RUN=true` 상태로 먼저 실행하세요. 이 경우 카카오톡 관련 코드는 실행하지 않고 콘솔 출력과 로그만 확인합니다.

아임웹 주문 조회만 먼저 확인하려면 다음 명령을 실행합니다.

```powershell
python test_imweb_orders.py
```

이 스크립트는 전일 주문 수, 상태별 건수, 총 결제금액 합계, 주문/주문상품 원본 일부를 출력하고 전체 응답을 `logs/sample_imweb_orders.json`에 저장합니다.

페이지가 넘어가지 않거나 주문상품 배열을 찾지 못하면 진단 스크립트를 실행합니다.

```powershell
python probe_imweb_api.py
```

이 스크립트는 페이지 파라미터 후보와 주문 상세 엔드포인트 후보를 테스트하고 `logs/probe_imweb_api.json`에 저장합니다.

```powershell
run_report.bat
```

정상 출력과 `logs/sample_imweb_response.json`의 응답 구조를 확인한 뒤, 필요하면 `.env`의 아임웹 엔드포인트, 파라미터명, 제외 상태값을 실제 응답에 맞게 조정합니다.

카카오톡 발송까지 켜려면 심사 완료 후 다음처럼 변경합니다.

```dotenv
SEND_KAKAO=true
DRY_RUN=false
```

노션 저장을 켜려면 노션 Integration을 데이터베이스에 초대한 뒤 `.env`에 아래 값을 설정합니다.

```dotenv
SEND_NOTION=true
NOTION_TOKEN=secret_xxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

노션 DB에 `기준일` date 속성과 `총 구매 건수`, `총 판매 수량`, `총 상품 구매 금액` number 속성이 있으면 자동으로 채웁니다. 컬럼명이 다르면 `NOTION_PROP_DATE`, `NOTION_PROP_TOTAL_ORDERS`, `NOTION_PROP_TOTAL_QUANTITY`, `NOTION_PROP_TOTAL_AMOUNT`로 지정할 수 있습니다.

## 매일 실행 예약

Windows 작업 스케줄러에서 새 작업을 만들고 아래처럼 설정합니다.

- 프로그램/스크립트: `C:\imweb_daily_report\run_report.bat`
- 시작 위치: `C:\imweb_daily_report`
- 트리거: 매일 원하는 시간

컴퓨터가 꺼져 있어도 실행하려면 GitHub Actions 같은 클라우드 실행 환경을 사용합니다. 이 프로젝트를 GitHub 저장소 루트에 올리면 `.github/workflows/daily-report.yml`이 매일 한국시간 09:07에 `main.py`를 실행합니다.

GitHub 저장소의 `Settings > Secrets and variables > Actions > Secrets`에 아래 값을 등록합니다.

- `IMWEB_API_KEY`
- `IMWEB_SECRET_KEY`
- `IMWEB_SHOP_CODE`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

노션 DB 컬럼명이 기본값과 다르면 `Variables`에 아래 값을 등록할 수 있습니다.

- `NOTION_PROP_TITLE`
- `NOTION_PROP_DATE`
- `NOTION_PROP_TOTAL_ORDERS`
- `NOTION_PROP_TOTAL_QUANTITY`
- `NOTION_PROP_TOTAL_AMOUNT`

워크플로우는 수동 실행도 가능합니다. GitHub 저장소의 `Actions > Daily Imweb Sales Report > Run workflow`를 누르면 즉시 테스트할 수 있습니다.

## 환경변수

주요 값은 `.env.example`에 모두 포함되어 있습니다.

- `IMWEB_API_KEY`: 아임웹 API 키
- `IMWEB_SECRET_KEY`: 아임웹 API 시크릿
- `IMWEB_AUTH_ENDPOINT`: 아임웹 인증 엔드포인트
- `IMWEB_ORDERS_ENDPOINT`: 아임웹 주문 조회 엔드포인트
- `IMWEB_PARAM_PAGE`: 아임웹 페이지 파라미터명. 진단 결과 기본값은 `offset`
- `IMWEB_ACCESS_TOKEN_HEADER`: 주문 조회 때 사용할 access token 헤더명. 기본값은 `access-token`
- `IMWEB_ACCESS_TOKEN_SCHEME`: `Authorization` 헤더를 쓸 때만 필요한 스킴. 예: `Bearer`
- `REQUEST_SLEEP_SECONDS`: 페이지 조회 사이 대기 시간. 아임웹 제한을 피하려면 `1.5` 이상 권장
- `IMWEB_TOO_MANY_REQUEST_RETRIES`: `TOO MANY REQUEST` 응답 시 재시도 횟수
- `IMWEB_TOO_MANY_REQUEST_SLEEP_SECONDS`: `TOO MANY REQUEST` 응답 시 재시도 전 대기 시간
- `EXCLUDED_ORDER_STATUSES`: 제외할 주문/상품 상태값 목록
- `KAKAO_REST_API_KEY`: 카카오 REST API 키
- `KAKAO_REFRESH_TOKEN`: 카카오 refresh token
- `SEND_KAKAO`: `false`이면 카카오 토큰 갱신과 발송 코드를 실행하지 않음
- `DRY_RUN`: `true`이면 카카오톡 발송 생략
- `SAVE_SAMPLE_IMWEB_RESPONSE`: `true`이면 첫 페이지 응답 JSON 저장

## 참고

아임웹 API 응답 필드명은 계정, 버전, 권한에 따라 다를 수 있습니다. 이 프로그램은 여러 흔한 필드명을 방어적으로 읽도록 만들었지만, 실제 응답이 다르면 `logs/sample_imweb_response.json`을 보고 `main.py`의 필드 후보나 `.env`의 설정값을 조정하세요.
