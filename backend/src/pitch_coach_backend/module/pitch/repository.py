
from sqlmodel import Session
from pitch.entity import Pitch

db = Session()

def create_pitch(db: Session, pitch_entity: Pitch) -> Pitch:
    db.add(pitch_entity)
    db.commit()
    db.refresh(pitch_entity)
    return pitch_entity

def get_by_id(db: Session, pitch_id: str) -> Pitch | None:
    return db.query(Pitch).filter(Pitch.id == pitch_id).first()
