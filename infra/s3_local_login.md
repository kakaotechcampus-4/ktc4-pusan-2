* 이 문서는 S3를 이용한 테스트를 로컬에서 진행할 때, 인증 과정을 작성한 문서입니다.

## 배경
* 본 프로젝트의 S3는 Public으로 개방하지 못하게 보안 규칙이 설정되어 있습니다.
* S3의 Private 상태를 유지하면서 데이터 조작을 가능하게 하는 인증 방법은 2가지가 존재합니다.
1. 임시 URL을 전달하는 Presigned URL
2. SSO 프로필을 발급받아 인증하는 aws sso login
이 중 Presigned URL은 FE와의 직접적인 연동이 필요하여 배포 버전에만 등록해두었고, 로컬의 경우에는 sso 프로필을 이용한 로그인이 가능한 환경이기에 aws login sso로 S3 연결 테스트같은 테스트를 진행하였습니다.
* 하지만, 멘토님이나 다른 팀원들이 이러한 인증 방법을 모르는 환경에서 테스트를 원활히 진행하기엔 무리가 있다고 판단해 `aws login sso`를 이용한 인증 방법을 이 문서에 설명드리려고 합니다.

## 방법
1. SSO 프로필 등록(최초 1번)
* 우선 인증에 사용할 SSO 프로필을 등록합니다. 명령어는 다음과 같습니다.
```
aws configure sso
```

* 이 과정에서 들어가야 할 내용은 다음과 같습니다.
```
SSO session name (Recommended): session 이름(필수)
SSO start URL [None]: 사용자가 인증을 시작할 때 접속하는 AWS SSO URL(필수)
SSO region [None]: 기본 리전(필수)
SSO registration scopes [sso:account:access]: 클라이언트가 SSO에서 접근할 수 있는 범위(필수 X)
```

2. 추가 정보 입력
* 등록 후 브라우저가 열리며 승인을 받으면, 아래에 추가 정보를 입력합니다.
```
계정 선택
There are ~ AWS accounts available to you.
> 

역할 선택
Using the account ID ~
There are 2 roles available to you.
> ReadOnly
  FullAccess

CLI default client Region [None]: 기본 리전(필수)
CLI default output format [None]: 출력 형식(필수)
CLI profile name [123456789011_ReadOnly]: 프로필 이름
``` 

3. 로그인
* 토큰을 아직 발급받지 않았거나, 토큰이 만료될 때마다 해당 명령어를 입력하면 로그인이 완료됩니다.
```
aws sso login --profile <프로필 이름>
```

=> 이 과정을 모두 거치면, 로컬에서 S3를 이용한 테스트를 진행할 수 있습니다.