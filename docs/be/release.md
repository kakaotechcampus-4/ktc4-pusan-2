* 이 내용은 배포와 hotfix를 어떻게 수행할지에 대한 방법을 나타내는 문서입니다.

## 배포
* develop에서 검증이 완료된 커밋을 선택합니다.
* `release` branch를 그 커밋까지 fast-forward하여, develop의 진척사항을 배포 버전에 반영합니다.
* 큰 수정사항의 경우 `release` branch에 반영한 후 이를 `develop`에 PR을 생성해, 승인 후 merge 시켜 develop에 반영하도록 합니다.
* 생성된 PR은 1명 이상의 리뷰어의 승인이 필요합니다.

## hotfix
* 급하게 고쳐야 할 수정사항이 필요한 경우 release branch에서 `git checkout -b`로 수정사항을 반영한 branch를 만든 후 수정합니다.
* 수정이 끝나면 수정사항을 반영한 branch에 push하면, 해당 branch에서 `release` branch로의 PR이 생성됩니다.(자동 PR을 하지 못한다면, 수동으로 PUSH한 후 PR을 반드시 생성해주셔야 합니다)
* 생성된 PR은 1명 이상의 리뷰어의 승인이 필요합니다.
* 승인을 완료했다면, 수정사항이 `release` branch에 반영됩니다.