## Pitch Coach Infra

로컬 & 배포 서버 작동을 담당하는 infra 폴더와 작동 방법을 설명하는 README.md 파일입니다.

## 디렉터리 구성

```
infra/
├── Caddyfile                  # 리버스 프록시(Caddy) 설정
├── docker-compose.yml         # 공통 서비스 정의 (기본)
├── docker-compose.dev.yml     # 개발(develop) 환경용 오버라이드
├── docker-compose.release.yml # 운영(release) 환경용 오버라이드
├── docker-compose.prod.yml    # 지금 배포에서 쓰지 않음
├── release.caddy              # 테스트 사이트 caddy가 운영 사이트로 넘기는 설정
├── s3_local_login.md          # 로컬 S3 로그인 안내
└── README.md                  # 배포 안내 문서
```

## 각 파일의 역할

* Caddyfile : 리버스 프록시를 어떤 방식으로 진행할지 명시하는 파일입니다.
특정 URL, 혹은 URL 형식에 대해 어떤 주소와 port로 요청을 보낼지가 작성되어 있습니다.
    * `/api/*` 요청 본문 크기 상한도 여기서 겁니다. 발표자료 업로드는 41MiB, 나머지 API 는 1MiB 이고, 넘으면 BE 에 닿기 전에 413 입니다. Caddy 2.7 전에는 413 대신 502 가 나서 이미지를 2.11.7 로 올렸습니다.

* docker-compose.yml : FE, BE, AI, DB(PostgreSQL), Caddy, Dozzle(로그 뷰어)을 각각의 컨테이너로 띄운 후, 해당 컨테이너들의 관리 방침과 자동 재시작 정책, 종속성 관리 등을 명시하는 파일입니다.
    * 테스트 사이트(docker-compose.dev.yml)는 caddy 80/443 포트를 공개하고, `release.caddy`로 운영 사이트 요청까지 받아 `edge` 네트워크로 넘깁니다.
    * 운영 사이트(docker-compose.release.yml)는 포트를 공개하지 않고 `edge` 네트워크에서 요청을 받습니다. 그래서 사이트 URL이 http입니다.

## 환경 구성 표

| | 테스트 사이트 | 운영 사이트 |
|---|---|---|
| 워크플로 | `develop-deploy.yml` | `release-deploy.yml` |
| 배포 브랜치 | `develop-next` | `main` |
| compose 조합 | `docker-compose.yml` + `docker-compose.dev.yml` | `docker-compose.yml` + `docker-compose.release.yml` |
| EC2 폴더 | `~/pitch-coach` | `~/pitch-coach-release` |
| caddy | 80/443 공개, `release.caddy`로 운영 사이트 요청을 `edge` 네트워크로 넘김 | 포트 없음, `edge` 네트워크에서 요청을 받음 |
| SITE_URL | `https://pitch-coach-dev.duckdns.org` | `http://pitch-coach-release.duckdns.org` |
| GitHub Secret | `BACKEND_ENV` · `FRONTEND_ENV` · `AI_ENV` | 같은 이름 앞에 `RELEASE_` |
| fe `VITE_LAUNCH_GATE_ENABLED` | `false` | `true` |

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
-> 또한, ../backend/.env, ../frontend/.env, ../ai/.env 파일이 띄우기 전 만들어져있어야 합니다.

2. 컨테이너가 정상적으로 띄워졌는지 확인하기 위해, docker ps -a로 현재 올라온 컨테이너들을 살펴봅니다. 
이 때 status가 Exited인 경우, 에러가 발생했을 확률이 높으므로 컨테이너의 로그를 확인하셔야 합니다. 
반복적으로 status가 Restarting 상태로 지워지지 않고 남아있는 경우에도 역시 `docker logs`로 확인이 필요합니다.
```
docker ps -a
```

## 배포 진행 방법

아래 배포 과정은 Github Actions .yml 파일(`.github/workflows/develop-deploy.yml`, `.github/workflows/release-deploy.yml`) workflow에서 진행하는 과정입니다.
develop/main branch에 push를 할 때 해당 과정이 진행됩니다.

1. OIDC 발급받기
```
permissions:
  id-token: write

- name: Configure AWS credentials (OIDC)
  uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
    aws-region: ${{ env.AWS_REGION }}
```
* Github Actions -> AWS로 배포할 때 필요한 인증을 진행합니다.
* Github Actions -> AWS 배포의 역할이 AWS 계정의 ROLE에 있는지 확인한 후, ROLE 식별과 역할에 필요한 ARN 값을 Github Actions Secret에 기록합니다.
* AWS 리전은 워크플로 파일의 `env`에 `AWS_REGION`으로 기록합니다. EC2가 있는 리전과 다르면 수정해야 합니다.

2. AWS 컨테이너 보관소인 ECR 생성, 인증하기
```
- uses: aws-actions/amazon-ecr-login@v2
  id: ecr

- name: Resolve image repo
  run: |
    echo "IMAGE_REPO=${{ steps.ecr.outputs.registry }}/pitch-coach" >> "$GITHUB_ENV"

- name: Ensure ECR repository
  run: |
    aws ecr describe-repositories --repository-names pitch-coach --region "$AWS_REGION" >/dev/null 2>&1 \
      || aws ecr create-repository --repository-name pitch-coach --region "$AWS_REGION" >/dev/null
```
* ` aws ecr create-repository`로 ECR repository를 만들어 이미지를 보관할 곳을 둡니다. 

3. alembic revision 순서 파일 생성
```
- name: alembic revision
  working-directory: backend
  run: |
    uv run alembic-git-revisions alembic/versions
```
* 이미지에는 git 기록이 없어 alembic이 마이그레이션 순서를 찾지 못하므로, 빌드 전에 git 기록으로 `revision_chain.json`을 만들어 이미지에 함께 넣습니다.

4. Docker Image Build후 ECR에 push

```
- name: Stub env files
  run: touch backend/.env frontend/.env ai/.env

- name: Build & push
  working-directory: infra
  run: |
    docker compose $FILES build be fe ai
    docker compose $FILES push be fe ai
```
* `$FILES`는 테스트 사이트와 운영 사이트에서 엮는 compose 파일이 다릅니다. fe 빌드 인자(`VITE_LAUNCH_GATE_ENABLED`)가 파일마다 달라서, 빌드도 이 조합으로 진행합니다.
    * 테스트 사이트(`develop-deploy.yml`): `-f docker-compose.yml -f docker-compose.dev.yml`
    * 운영 사이트(`release-deploy.yml`): `-f docker-compose.yml -f docker-compose.release.yml`
* 빌드 후 ECR 저장소에 push로 새로 반영된 이미지들을 올려줍니다.

> DB, Caddy, Dozzle은 각각 `postgres:17-alpine`, `caddy:2.11.7-alpine`, `amir20/dozzle:v11.3.0` 이미지를 사용하므로 코드에 따라 갱신되지 않기에, push하지 않습니다.

5. infra 폴더 밑 compose file & Caddyfile들과 환경변수 파일들 EC2로 복사해 전송하기
```
- name: Copy compose files to EC2
  run: |
    tar -czf - -C infra . \
      | ssh "$SSH_HOST" "mkdir -p ~/$REMOTE_DIR/infra && tar -xzf - -C ~/$REMOTE_DIR/infra"

- name: Write env files on EC2
  env:
    BACKEND_ENV: ${{ secrets.RELEASE_BACKEND_ENV }}
    FRONTEND_ENV: ${{ secrets.RELEASE_FRONTEND_ENV }}
    AI_ENV: ${{ secrets.RELEASE_AI_ENV }}
  run: |
    ssh "$SSH_HOST" "mkdir -p ~/$REMOTE_DIR/backend ~/$REMOTE_DIR/frontend ~/$REMOTE_DIR/ai"
    printf '%s' "$BACKEND_ENV"  | ssh "$SSH_HOST" "cat > ~/$REMOTE_DIR/backend/.env"
    printf '%s' "$FRONTEND_ENV" | ssh "$SSH_HOST" "cat > ~/$REMOTE_DIR/frontend/.env"
    printf '%s' "$AI_ENV"       | ssh "$SSH_HOST" "cat > ~/$REMOTE_DIR/ai/.env"
    printf 'IMAGE_REPO=%s\nIMAGE_TAG=%s\n' "$IMAGE_REPO" "$IMAGE_TAG" \
      | ssh "$SSH_HOST" "cat > ~/$REMOTE_DIR/infra/.env"
```
* 이후 단계 작업에서 이미지를 갱신할 컨테이너들을 만드는 데 사용하는 compose.yml 파일들과 Reverse Proxy에 사용되는 Caddyfile을 전송합니다.
* EC2로 전송할 환경변수는 Github Actions Secret에 FE/BE/AI, 운영용과 개발용을 구분해 기록합니다.

6. docker pull로 ECR에서 갱신된 이미지 다운받기, 컨테이너에 적용
```
aws ecr get-login-password --region "$AWS_REGION" \
  | ssh "$SSH_HOST" "docker login --username AWS --password-stdin '${IMAGE_REPO%%/*}'"

docker compose $FILES pull
docker compose $FILES run --rm -T be alembic upgrade head < /dev/null
docker compose $FILES up -d --no-build --remove-orphans
```
* ECR 저장소에 로그인하여 갱신된 이미지를 pull로 다운로드받습니다.
* 받은 be 이미지로 DB 마이그레이션(`alembic upgrade head`)을 먼저 적용합니다. 3번에서 만든 `revision_chain.json`으로 순서를 찾습니다.
* 다운받은 이미지를 EC2에 있는 컨테이너에 적용하여 최종 반영합니다.


7. 이전 이미지 삭제
```
docker image prune -af
```
* 이전에 남아있던 이미지는 용량 절약을 위해 삭제합니다.

## 주의사항
* 배포 전 main branch와 develop branch에 사용할 환경변수들을 다시 확인해주세요. 공유해도 되는 환경변수는 두 경우 모두 같게 하되, 그렇지 않은 경우에는 분리를 필수적으로 진행하여야 합니다.

```
develop-main branch 사이 분리해야 할 환경변수 목록

# Google OAuth
GOOGLE_REDIRECT_URI
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET

# AWS S3 Presigned URL용 S3 버킷 이름
S3_BUCKET_NAME

# STT API로 사용할 DEEPGRAM_API_KEY
DEEPGRAM_API_KEY

# 관리자 비밀번호
ADMIN_PASSWORD
```
