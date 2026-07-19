# 백엔드 상시 개발 컨테이너 (PLAN.md: docker compose 상시 서비스 전환).
# uvicorn --reload로 띄우고, docker-compose.yml이 ./backend:/app을 바인드 마운트해
# 핫리로드를 지원한다 — pip로 설치된 패키지는 컨테이너 이미지의 site-packages(이
# WORKDIR 바깥)에 있으므로 바인드 마운트가 /app을 덮어써도 지워지지 않는다(표준 패턴).
FROM python:3.12-slim

WORKDIR /app

# requirements.txt만 먼저 복사해 pip install 레이어를 캐시한다 — 앱 코드만 바뀌는
# 흔한 경우에는 이 레이어가 재사용돼 빌드가 빨라진다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8123

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8123", "--reload"]
