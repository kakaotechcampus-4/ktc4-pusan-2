
from sqlalchemy.orm import Session
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion

db = Session()

def create_pitch(db: Session, pitch_entity: Pitch) -> Pitch:
    db.add(pitch_entity)
    db.commit()
    db.refresh(pitch_entity)
    return pitch_entity

def get_by_id(db: Session, pitch_id: str) -> Pitch | None:
    return db.query(Pitch).filter(Pitch.id == pitch_id).first()

def save_presentation(db: Session, presentation_version: PresentationVersion) -> PresentationVersion:
    db.add(presentation_version)
    db.commit()
    db.refresh(presentation_version)
    return presentation_version

def save_script(db: Session, script_version: ScriptVersion) -> ScriptVersion:
    db.add(script_version)
    db.commit()
    db.refresh(script_version)
    return script_version
