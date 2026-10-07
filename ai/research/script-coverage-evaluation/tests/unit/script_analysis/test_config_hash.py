from script_coverage.script_analysis.final import final_config_hash
from script_coverage.script_analysis.semantic import semantic_config_hash
from script_coverage.script_analysis.units import unit_config_hash

# 값은 원본 노트북(MODEL="test-model")의 llm_config_hash / final_config_hash / unit_config_hash 출력이다


def test_config_hashes_match_notebook():
    assert semantic_config_hash("test-model") == "082c56a55231"
    assert final_config_hash("test-model") == "af5bd3e4aba2"
    assert unit_config_hash("test-model") == "150d0924a7b6"


def test_config_hash_depends_on_model():
    assert semantic_config_hash("a") != semantic_config_hash("b")
