FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
# 의존성 설치. 하지만 package.json에 쓰기 권한은 없다.
RUN npm ci

COPY . .
RUN npm run build

FROM caddy:2.6.4-alpine
COPY --from=build /app/dist /srv

RUN printf ':3000 {\n\
    root * /srv\n\
    try_files {path} /index.html\n\
    file_server\n\
}' > /etc/caddy/Caddyfile

EXPOSE 3000

