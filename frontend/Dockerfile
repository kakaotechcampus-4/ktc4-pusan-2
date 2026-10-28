FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
# 의존성 설치. 하지만 package.json에 쓰기 권한은 없다.
RUN npm ci

COPY . .
# 시선 엔진 자산(public/models/*)은 커밋하지 않습니다(수십 MB). 이미지를 만들 때 넣습니다 — 없으면
# 배포에서 시선 분석이 켜지지 않습니다. wasm 은 node_modules 에서 복사하고, 모델은 공식 주소에서 받아
# 크기 · sha256 을 확인합니다. 다르면 여기서 빌드가 멈춥니다 (scripts/models.mjs)
RUN npm run models -- --download
RUN npm run build

FROM caddy:2.6.4-alpine
COPY --from=build /app/dist /srv

# /assets/* 는 파일 이름에 해시가 붙어 바뀌면 주소도 바뀐다. 나머지(index.html · SPA 경로)는
# no-cache 로 쓰기 전에 매번 서버에 확인하게 해, 배포 뒤 이전 번들 · 빈 화면이 뜨지 않게 한다 (#435)
RUN printf ':5173 {\n\
    root * /srv\n\
    @notAssets not path /assets/*\n\
    header @notAssets Cache-Control "no-cache"\n\
    try_files {path} /index.html\n\
    file_server\n\
}' > /etc/caddy/Caddyfile

EXPOSE 5173

