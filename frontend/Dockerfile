FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
# 의존성 설치. 하지만 package.json에 쓰기 권한은 없다.
RUN npm ci

COPY . .
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

