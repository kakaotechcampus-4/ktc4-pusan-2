## Pitch Coach Infra

로컬 & 배포 서버 작동을 담당하는 infra 폴더와 작동 방법을 설명하는 README.md 파일입니다.

## 디렉터리 구성

```
infra/
├── Caddyfile                  # 리버스 프록시(Caddy) 설정
├── docker-compose.yml         # 공통 서비스 정의 (기본)
├── docker-compose.dev.yml     # 개발(develop) 환경용 오버라이드
├── docker-compose.prod.yml    # 운영(prod) 환경용 오버라이드
└── README.md                  # 배포 안내 문서
```

## 각 파일의 역할

* Caddyfile : 리버스 프록시를 어떤 방식으로 진행할지 명시하는 파일입니다.
특정 URL, 혹은 URL 형식에 대해 어떤 주소와 port로 요청을 보낼지가 작성되어 있습니다.
    * `/api/*` 요청 본문 크기 상한도 여기서 겁니다. 발표자료 업로드는 41MiB, 나머지 API 는 1MiB 이고, 넘으면 BE 에 닿기 전에 413 입니다. Caddy 2.7 전에는 413 대신 502 가 나서 이미지를 2.11.7 로 올렸습니다.

* docker-compose.yml : FE, BE, AI, DB(PostgreSQL, Redis), Caddy를 각각의 컨테이너로 띄운 후, 해당 컨테이너들의 관리 방침과 자동 재시작 정책, 종속성 관리 등을 명시하는 파일입니다.
    * 테스트 사이트와 운영 사이트의 caddy 포트 공개와 사이트 URL 지정을 위해 테스트 사이트의 경우 docker-compose.dev.yml, 운영 사이트의 경우 docker-compose.prod.yml에 각각 필요한 정보를 명시하였습니다.

## 컨테이너 별 로그 확인

로그 확인은 `docker logs` 명령어로 진행합니다.
```
docker logs <컨테이너 이름>
```

실시간 확인을 원할 경우, `-f`를 옵션으로 추가합니다.
```
docker logs -f <컨테이너 이름>
```

끝의 n줄만 확인하고 싶다면, `--tail n`을 옵션으로 추가합니다.
```
docker logs --tail n <컨테이너 이름>
```

서버에 접속하지 않고 브라우저에서 볼 수도 있습니다. `<사이트 주소>/admin/logs/` 에 관리자 콘솔 계정으로 들어가면 모든 컨테이너 로그를 실시간으로 검색 · 필터할 수 있습니다 (Dozzle).
계정은 `backend/.env` 의 `ADMIN_USERNAME` · `ADMIN_PASSWORD` 입니다. 컨테이너 로그는 서비스마다 10MB × 3개까지만 남습니다.

## 로컬 서버 작동 방법

1. docker-compose.yml을 실행해 해당 파일에 작성된 컨테이너들을 모두 띄웁니다. 
```
docker compose up -d
```
-> 다만 이 과정에서 docker-compose.yml에 작성된 환경변수의 경우 `--env-file infra/.env`를 옵션으로 넣어 실행하거나 `환경변수 이름=환경변수 값`으로 먼저 환경변수를 지정하는 작업이 필요합니다.
-> 또한, ../backend/.env와 ../frontend/.env 파일이 띄우기 전 만들어져있어야 합니다.

2. 컨테이너가 정상적으로 띄워졌는지 확인하기 위해, docker ps -a로 현재 올라온 컨테이너들을 살펴봅니다. 
이 때 status가 Exited인 경우, 에러가 발생했을 확률이 높으므로 컨테이너의 로그를 확인하셔야 합니다. 
반복적으로 status가 Restarting 상태로 지워지지 않고 남아있는 경우에도 역시 `docker logs`로 확인이 필요합니다.
```
docker ps -a
```

## 배포 진행 방법

아래 배포 과정은 Github Actions .yml 파일(`.github/workflows/develop-deploy.yml`, `.github/workflows/main-deploy.yml`) workflow에서 진행하는 과정입니다.
develop/main branch에 push를 할 때 해당 과정이 진행됩니다.

* develop branch(테스트 사이트)의 경우
    1. 각각의 컨테이너 정보가 담겨있는 docker-compose.yml과 테스트 사이트의 정보가 담겨있는 docker-compose.dev.yml을 동시에 엮어 실행합니다.
    ```
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
    ```
* main branch(운영 사이트)의 경우
    1. 각각의 컨테이너 정보가 담겨있는 docker-compose.yml과 운영 사이트의 정보가 담겨있는 docker-compose.prod.yml을 동시에 엮어 실행합니다.
    ```
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    ```

## 주의사항
* 배포 전 main branch와 develop branch에 사용할 환경변수들을 다시 확인해주세요. 공유해도 되는 환경변수는 두 경우 모두 같게 하되, 그렇지 않은 경우에는 분리를 필수적으로 진행하여야 합니다.