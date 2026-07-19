import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 로컬(비도커) 개발 기본값: 백엔드가 같은 머신에서 uvicorn --port 8123으로 떠 있다고
// 가정한다. docker-compose로 띄울 때는 프런트/백엔드가 별도 컨테이너라 127.0.0.1이 아닌
// 도커 네트워크 서비스명(backend)으로 가리켜야 하므로, docker-compose.yml이
// VITE_API_PROXY_TARGET=http://backend:8123을 환경변수로 넘긴다 — 이 값이 있으면
// 우선하고, 없으면(로컬 개발) 기존 기본값 그대로다.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8123'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiProxyTarget,
    },
    // 도커 바인드 마운트(macOS Docker Desktop 등)에서는 파일시스템 이벤트가 컨테이너까지
    // 전달되지 않아 기본 파일 감시(fsevents/inotify)가 HMR을 못 잡는 경우가 있다 —
    // docker-compose.yml이 CHOKIDAR_USEPOLLING=true를 줄 때만 폴링으로 전환하고, 로컬
    // 개발(환경변수 없음)에서는 기존처럼 네이티브 감시를 그대로 쓴다.
    watch: {
      usePolling: process.env.CHOKIDAR_USEPOLLING === 'true',
    },
  },
})
