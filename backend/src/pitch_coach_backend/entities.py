"""모든 도메인 entity 를 한 곳에서 import 한다.

Alembic(env.py)과 main.py 가 이 모듈을 import 하면 Base.metadata 에
모든 테이블이 등록된다. 새 도메인을 추가하면 아래에 한 줄 추가한다.
빠뜨리면 마이그레이션에 테이블이 잡히지 않는다.

예)
    import pitch_coach_backend.module.user.entity  # noqa: F401
"""

import pitch_coach_backend.module.user.entity  # noqa: F401
