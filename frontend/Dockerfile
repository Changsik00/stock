# 프런트엔드 상시 개발 컨테이너 (PLAN.md: docker compose 상시 서비스 전환).
# vite dev 서버를 --host 0.0.0.0으로 띄우고, docker-compose.yml이 ./frontend:/app +
# 익명 볼륨(/app/node_modules)을 마운트해 핫리로드를 지원한다 — 익명 볼륨은 호스트의
# node_modules(있다면 다른 OS/아키텍처용일 수 있음)가 컨테이너의 npm ci 설치물을
# 덮어쓰지 않도록 막는 표준 트릭이다.
FROM node:22-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
