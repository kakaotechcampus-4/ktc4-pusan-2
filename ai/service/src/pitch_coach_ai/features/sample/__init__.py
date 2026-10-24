"""규약 확인용 대역 판정 모듈. 실제 판정은 하지 않는다.

판정 모듈 모양(`schemas.py` · `core.py` · `config.py` · `version.py` · `examples/`,
`docs/ai/architecture.md` 7 · 8절)을 그대로 따른다. `AI_ENABLE_SAMPLE=true` 일 때만
`POST /sample/judge` 로 열리고, 첫 실제 기능 엔드포인트가 붙으면 이 폴더와 그 테스트를 지운다.
"""
