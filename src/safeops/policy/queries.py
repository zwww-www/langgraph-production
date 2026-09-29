from typing import Any, Literal

from sqlalchemy import select

from safeops.persistence.database import Database, row_dict
from safeops.persistence.models import (
    CalibrationRow,
    CandidateRow,
    DatasetRow,
    EventRow,
    ReleaseRow,
)

Collection = Literal["candidates", "frontier", "calibrations", "revisions", "datasets", "events"]


async def collection(
    db: Database, kind: Collection, calibration_id: str | None = None
) -> list[dict[str, Any]]:
    async with db.sessions() as session:
        if kind in ("candidates", "frontier"):
            query = (
                select(CandidateRow)
                .order_by(CandidateRow.created_at.desc(), CandidateRow.id)
                .limit(500)
            )
            if calibration_id:
                query = query.where(CandidateRow.calibration_id == calibration_id)
            if kind == "frontier":
                query = query.where(CandidateRow.frontier.is_(True))
            return [row_dict(row) for row in await session.scalars(query)]
        if kind == "calibrations":
            return [
                row_dict(row)
                for row in await session.scalars(
                    select(CalibrationRow).order_by(CalibrationRow.created_at.desc()).limit(100)
                )
            ]
        if kind == "revisions":
            return [
                row_dict(row)
                for row in await session.scalars(
                    select(ReleaseRow).order_by(ReleaseRow.created_at.desc()).limit(100)
                )
            ]
        if kind == "datasets":
            return [
                row_dict(row)
                for row in await session.scalars(
                    select(DatasetRow).order_by(DatasetRow.created_at.desc()).limit(100)
                )
            ]
        return [
            row_dict(row)
            for row in await session.scalars(
                select(EventRow)
                .where(EventRow.run_id.is_(None))
                .order_by(EventRow.id.desc())
                .limit(200)
            )
        ]
