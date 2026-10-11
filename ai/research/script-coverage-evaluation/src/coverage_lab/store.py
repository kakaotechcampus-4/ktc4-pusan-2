"""로컬 SQLite 저장소: LLM 응답 캐시 표, 평가 기준 · 평가 결과 표, 사용자 확인 표.

노트북의 DB(`outputs/rubrics.sqlite`)와 표 이름 · 열이 같다. 실행을 반복해도 지우지 않으므로 캐시가 남는다.
처음부터 다시 하려면 파일을 지운다.

- `evaluation_rubrics` — 키 `(script_name, slide_number)`. STT 평가가 읽어 가는 평가 기준
- `slide_evaluations` — 연습(take)의 슬라이드별 평가 결과
- `similar_items` — 비슷한 말을 한 행씩 (위치 포함)
- `similar_confirmations` · `confirmed_evaluations` — 사용자 확인과, 그 답으로 다시 계산한 평가
- 캐시 표(`semantic_cache` · `semantic_samples` · `final_cache` · `unit_cache` · `stt_llm_cache`)는 `cache.py` 가 쓴다
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from script_coverage.shared.rubric import EvaluationRubric
from script_coverage.stt_evaluation.confirm import CONFIRM_TEXT, rescore_evaluation
from script_coverage.stt_evaluation.schemas import ConfirmAnswer, SlideEvaluation, Take
from script_coverage.stt_evaluation.scoring import take_scores

from .paths import DB_PATH

# 대본 분석(1차 분석 · 최종 결론 · 전달 단위 캐시, 평가 기준) 표
SCRIPT_ANALYSIS_DDL = """
        -- LLM 분기 결과 캐시. 정규화 대본과 LLM 설정이 같으면 다시 호출하지 않는다.
        CREATE TABLE IF NOT EXISTS semantic_cache (
            content_hash    TEXT NOT NULL,
            llm_config_hash TEXT NOT NULL,
            output_json     TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            PRIMARY KEY (content_hash, llm_config_hash)
        );
        -- 일관성 측정용: 같은 입력으로 LLM 을 여러 번 부른 응답. 0번은 semantic_cache 의 응답을 쓴다
        CREATE TABLE IF NOT EXISTS semantic_samples (
            content_hash    TEXT    NOT NULL,
            llm_config_hash TEXT    NOT NULL,
            sample_index    INTEGER NOT NULL,
            output_json     TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            PRIMARY KEY (content_hash, llm_config_hash, sample_index)
        );
        -- 최종 결론 캐시. 입력 메시지(대본 + 1차 분석 + 규칙 분석 + 경고)와 설정이 같으면 다시 호출하지 않는다
        CREATE TABLE IF NOT EXISTS final_cache (
            input_hash        TEXT NOT NULL,
            final_config_hash TEXT NOT NULL,
            output_json       TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            PRIMARY KEY (input_hash, final_config_hash)
        );
        -- 전달 단위 캐시. 입력 메시지(문장 + 최종 역할 + 사실)와 설정이 같으면 다시 호출하지 않는다
        CREATE TABLE IF NOT EXISTS unit_cache (
            input_hash       TEXT NOT NULL,
            unit_config_hash TEXT NOT NULL,
            output_json      TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            PRIMARY KEY (input_hash, unit_config_hash)
        );
        -- STT 와 비교할 때 읽어 갈 평가 기준
        CREATE TABLE IF NOT EXISTS evaluation_rubrics (
            script_name     TEXT    NOT NULL,
            slide_number    INTEGER NOT NULL,
            content_hash    TEXT    NOT NULL,
            rubric_json     TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            PRIMARY KEY (script_name, slide_number)
        );
    """

# STT 평가(LLM 응답 캐시, 평가 결과, 비슷한 말, 사용자 확인) 표
STT_EVALUATION_DDL = """
    -- STT 평가용 LLM 응답 캐시. kind = semantic / verifier (반복 채점의 k 번째 응답은 semantic#k …)
    CREATE TABLE IF NOT EXISTS stt_llm_cache (
        kind        TEXT NOT NULL,
        input_hash  TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        output_json TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (kind, input_hash, config_hash)
    );
    -- 연습(take) 한 번의 슬라이드별 평가 결과
    CREATE TABLE IF NOT EXISTS slide_evaluations (
        take_id         TEXT    NOT NULL,
        slide_number    INTEGER NOT NULL,
        script_name     TEXT    NOT NULL,
        rubric_id       TEXT    NOT NULL,
        evaluation_json TEXT    NOT NULL,
        created_at      TEXT    NOT NULL,
        PRIMARY KEY (take_id, slide_number)
    );
    -- 비슷한 말: 리뷰 agent 가 위치로 찾아 사용자에게 확인할 수 있게 한 행씩 저장한다
    CREATE TABLE IF NOT EXISTS similar_items (
        take_id               TEXT    NOT NULL,
        slide_number          INTEGER NOT NULL,
        item_id               TEXT    NOT NULL,
        kind                  TEXT    NOT NULL,   -- word / number
        script_text           TEXT    NOT NULL,
        stt_text              TEXT    NOT NULL,
        script_sentence_index INTEGER NOT NULL,
        script_start          INTEGER NOT NULL,   -- 정규화 대본 텍스트 기준
        script_end            INTEGER NOT NULL,
        stt_sentence_index    INTEGER NOT NULL,   -- 정규화 STT 문장 번호 (T번호)
        stt_start             INTEGER NOT NULL,   -- 정규화 STT 텍스트 기준
        stt_end               INTEGER NOT NULL,
        stt_raw_start         INTEGER,            -- 원본 STT 텍스트 기준 (Deepgram 단어 타임스탬프와 맞출 때)
        stt_raw_end           INTEGER,
        fact_id               TEXT,
        key_point_ids         TEXT    NOT NULL,   -- JSON 배열
        rule_guess            TEXT    NOT NULL,   -- 규칙 추정 (참고)
        signals               TEXT    NOT NULL,   -- JSON 배열
        created_at            TEXT    NOT NULL,
        PRIMARY KEY (take_id, slide_number, item_id)
    );
    -- 리뷰 agent 가 사용자에게 받은 비슷한 말 확인. 항목의 표현도 남겨, 다시 채점해 번호가 바뀌어도 다른 항목에 적용되지 않게 한다
    CREATE TABLE IF NOT EXISTS similar_confirmations (
        take_id      TEXT    NOT NULL,
        slide_number INTEGER NOT NULL,
        item_id      TEXT    NOT NULL,
        script_text  TEXT    NOT NULL,
        stt_text     TEXT    NOT NULL,
        answer       TEXT    NOT NULL,   -- as_script: 대본대로 말함 / as_stt: STT 대로 말함
        confirmed_at TEXT    NOT NULL,
        PRIMARY KEY (take_id, slide_number, item_id)
    );
    -- 사용자 확인을 반영해 다시 계산한 평가 결과 (처음 채점 결과는 slide_evaluations 에 그대로 둔다)
    CREATE TABLE IF NOT EXISTS confirmed_evaluations (
        take_id         TEXT    NOT NULL,
        slide_number    INTEGER NOT NULL,
        evaluation_json TEXT    NOT NULL,
        created_at      TEXT    NOT NULL,
        PRIMARY KEY (take_id, slide_number)
    );
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """로컬 SQLite DB. 실행을 반복해도 지우지 않으므로 LLM 캐시가 남는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCRIPT_ANALYSIS_DDL)
    conn.executescript(STT_EVALUATION_DDL)
    return conn


def save_rubric(conn: sqlite3.Connection, rubric: EvaluationRubric) -> None:
    m = rubric.meta
    conn.execute(
        "INSERT OR REPLACE INTO evaluation_rubrics VALUES (?, ?, ?, ?, ?)",
        (m.script_name, m.slide_number, m.content_hash, rubric.model_dump_json(), m.created_at),
    )
    conn.commit()


def delete_rubric(conn: sqlite3.Connection, script_name: str, slide_number: int) -> None:
    """저장된 평가 기준 한 장을 지운다 (분석이 실패한 슬라이드의 예전 기준이 남아 쓰이지 않게)."""
    with conn:
        conn.execute(
            "DELETE FROM evaluation_rubrics WHERE script_name = ? AND slide_number = ?",
            (script_name, slide_number),
        )


def load_rubric(
    conn: sqlite3.Connection, script_name: str, slide_number: int
) -> EvaluationRubric | None:
    row = conn.execute(
        "SELECT rubric_json FROM evaluation_rubrics WHERE script_name = ? AND slide_number = ?",
        (script_name, slide_number),
    ).fetchone()
    return EvaluationRubric.model_validate_json(row[0]) if row else None


def load_rubrics(conn: sqlite3.Connection, script_name: str) -> dict[int, EvaluationRubric]:
    """대본 하나의 평가 기준 전부 {슬라이드 번호: 평가 기준}. `evaluate_take` 에 그대로 넘긴다."""
    rows = conn.execute(
        "SELECT slide_number, rubric_json FROM evaluation_rubrics WHERE script_name = ? ORDER BY slide_number",
        (script_name,),
    ).fetchall()
    return {n: EvaluationRubric.model_validate_json(js) for n, js in rows}


def save_evaluation(conn: sqlite3.Connection, evaluation: SlideEvaluation) -> None:
    """평가 결과 · 비슷한 말 · 확인 결과 삭제를 한 트랜잭션으로 저장한다. 중간에 실패하면 모두 되돌린다."""
    with conn:  # 성공하면 commit, 예외가 나면 rollback
        conn.execute(
            "INSERT OR REPLACE INTO slide_evaluations VALUES (?, ?, ?, ?, ?, ?)",
            (
                evaluation.take_id,
                evaluation.slide_number,
                evaluation.script_name,
                evaluation.rubric_id,
                evaluation.model_dump_json(),
                evaluation.created_at,
            ),
        )
        conn.execute(
            "DELETE FROM similar_items WHERE take_id = ? AND slide_number = ?",
            (evaluation.take_id, evaluation.slide_number),
        )
        # 다시 채점했으므로 예전 확인 결과는 지운다. 저장된 사용자 답은 남겨 두고 confirmed_evaluation 을 부를 때 다시 적용한다
        conn.execute(
            "DELETE FROM confirmed_evaluations WHERE take_id = ? AND slide_number = ?",
            (evaluation.take_id, evaluation.slide_number),
        )
        conn.executemany(
            "INSERT INTO similar_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    evaluation.take_id,
                    evaluation.slide_number,
                    it.item_id,
                    it.kind,
                    it.script_text,
                    it.stt_text,
                    it.script_sentence_index,
                    *it.script_span,
                    it.stt_sentence_index,
                    *it.stt_span,
                    *(it.stt_raw_span or (None, None)),
                    it.fact_id,
                    json.dumps(it.key_point_ids),
                    it.rule_guess,
                    json.dumps(it.signals, ensure_ascii=False),
                    evaluation.created_at,
                )
                for it in evaluation.similar_items
            ],
        )


def load_similar_items(
    conn: sqlite3.Connection, take_id: str, slide_number: int | None = None
) -> pd.DataFrame:
    """리뷰 agent 용: 연습 한 번(또는 슬라이드 한 장)의 비슷한 말을 위치와 함께 읽는다."""
    query, params = "SELECT * FROM similar_items WHERE take_id = ?", [take_id]
    if slide_number is not None:
        query, params = query + " AND slide_number = ?", params + [slide_number]
    return pd.read_sql_query(query + " ORDER BY slide_number, stt_start", conn, params=params)


def load_evaluation(
    conn: sqlite3.Connection, take_id: str, slide_number: int
) -> SlideEvaluation | None:
    """처음 채점 결과 (사용자 확인 전)."""
    row = conn.execute(
        "SELECT evaluation_json FROM slide_evaluations WHERE take_id = ? AND slide_number = ?",
        (take_id, slide_number),
    ).fetchone()
    return SlideEvaluation.model_validate_json(row[0]) if row else None


def load_take_evaluations(conn: sqlite3.Connection, take: Take) -> list[SlideEvaluation]:
    """연습 한 번의 처음 채점 결과를 DB 에서 읽는다 (슬라이드 순서는 연습의 슬라이드 순서). 아직 채점하지 않은 슬라이드는 빠진다."""
    evaluations = (load_evaluation(conn, take.take_id, s.slide_number) for s in take.slides)
    return [e for e in evaluations if e is not None]


def load_confirmations(conn: sqlite3.Connection, evaluation: SlideEvaluation) -> dict[str, str]:
    """저장된 사용자 답 중, 지금 평가에 같은 표현으로 남아 있는 항목의 답만 (다시 채점해 항목이 바뀌었으면 적용하지 않는다)."""
    rows = conn.execute(
        "SELECT item_id, script_text, stt_text, answer FROM similar_confirmations WHERE take_id = ? AND slide_number = ?",
        (evaluation.take_id, evaluation.slide_number),
    ).fetchall()
    items = {it.item_id: it for it in evaluation.similar_items}
    return {
        i: a
        for i, script, stt, a in rows
        if i in items and (items[i].script_text, items[i].stt_text) == (script, stt)
    }


def _confirmed_evaluation(
    conn: sqlite3.Connection, take_id: str, slide_number: int
) -> SlideEvaluation | None:
    """`confirmed_evaluation` 의 본문. commit 은 부르는 쪽이 한다."""
    original = load_evaluation(conn, take_id, slide_number)
    if original is None:
        return None
    answers = load_confirmations(conn, original)
    if not answers:
        return original
    evaluation = rescore_evaluation(
        original, answers, load_rubric(conn, original.script_name, slide_number)
    )
    conn.execute(
        "INSERT OR REPLACE INTO confirmed_evaluations VALUES (?, ?, ?, ?)",
        (take_id, slide_number, evaluation.model_dump_json(), evaluation.created_at),
    )
    return evaluation


def confirmed_evaluation(
    conn: sqlite3.Connection, take_id: str, slide_number: int
) -> SlideEvaluation | None:
    """사용자 확인을 반영한 최종 평가. 확인한 답이 없으면 처음 채점 결과 그대로다. 다시 계산한 결과는 confirmed_evaluations 에 저장한다."""
    with conn:
        return _confirmed_evaluation(conn, take_id, slide_number)


def confirm_similar_item(
    conn: sqlite3.Connection, take_id: str, slide_number: int, item_id: str, answer: ConfirmAnswer
) -> SlideEvaluation:
    """리뷰 agent 용: 사용자가 비슷한 말을 확인한 답을 저장하고, 그 슬라이드를 다시 계산한 평가를 돌려준다."""
    if answer not in CONFIRM_TEXT:
        raise ValueError(f"answer 는 {list(CONFIRM_TEXT)} 중 하나: {answer}")
    original = load_evaluation(conn, take_id, slide_number)
    item = next(
        (it for it in (original.similar_items if original else []) if it.item_id == item_id), None
    )
    if item is None:
        raise ValueError(f"{take_id} 슬라이드 {slide_number} 에 비슷한 말 {item_id} 가 없다")
    with conn:  # 답 저장과 다시 계산한 결과 저장을 함께 commit (다시 계산이 실패하면 답도 되돌린다)
        conn.execute(
            "INSERT OR REPLACE INTO similar_confirmations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                take_id,
                slide_number,
                item_id,
                item.script_text,
                item.stt_text,
                answer,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        return _confirmed_evaluation(conn, take_id, slide_number)


def confirmed_take_scores(conn: sqlite3.Connection, take_id: str) -> dict:
    """연습 한 번의 최종 점수 (사용자 확인 반영). 슬라이드 점수를 처음 채점과 같은 방식으로 모은다."""
    slides = [
        n
        for (n,) in conn.execute(
            "SELECT slide_number FROM slide_evaluations WHERE take_id = ? ORDER BY slide_number",
            (take_id,),
        )
    ]
    return take_scores([confirmed_evaluation(conn, take_id, n) for n in slides])
