import requests

NOTION_TOKEN = "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" 
DB_ID = "3889f355db8580f9a4cdd1aa29372ad9"

url = f"https://api.notion.com/v1/databases/{DB_ID}"
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# 파이썬이 노션에 자동으로 만들어줄 열(속성) 설정
data = {
    "properties": {
        "주차(시작일)": {"date": {}},
        "플랫폼": {"select": {}},
        "계정": {"rich_text": {}},
        "캠페인": {"rich_text": {}},
        "비용": {"number": {"format": "number"}},
        "노출": {"number": {"format": "number"}}
    }
}

print("노션 표 자동 세팅 중...")
response = requests.patch(url, headers=headers, json=data)

if response.status_code == 200:
    print("✅ 노션 표에 모든 열이 마법처럼 생성되었습니다! 노션을 확인해 보세요.")
else:
    print(f"❌ 실패: {response.text}")
