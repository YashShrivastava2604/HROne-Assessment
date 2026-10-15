import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError


load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=3000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
AttendanceStatus = Literal[
    "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
]

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


# ---------- Time and serialization helpers ----------

def truncate_to_seconds(value: datetime) -> datetime:
    """Remove fractional seconds without changing the instant."""
    return value.replace(microsecond=0)


def epoch_ms_to_datetime(value: int) -> datetime:
    """Convert validated epoch milliseconds into an aware UTC datetime."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Timestamp must be an integer in epoch milliseconds")

    # Values resembling seconds, and unreasonable future values, are invalid.
    if value < 100_000_000_000 or value > 253_402_300_799_999:
        raise ValueError("Timestamp is outside the accepted range")

    try:
        return datetime.fromtimestamp(value // 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Invalid timestamp") from exc


def datetime_to_epoch_ms(value: datetime) -> int:
    """Serialize a stored instant as epoch milliseconds."""
    # PyMongo normally returns naive datetimes representing UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    value = truncate_to_seconds(value)
    return int(value.timestamp() * 1000)


def serialize_document(value):
    """Convert nested datetimes and remove MongoDB's internal _id."""
    if isinstance(value, datetime):
        return datetime_to_epoch_ms(value)

    if isinstance(value, list):
        return [serialize_document(item) for item in value]

    if isinstance(value, dict):
        return {
            key: serialize_document(item)
            for key, item in value.items()
            if key != "_id"
        }

    return value


def attendance_date_for_punch(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    """Return the IST date on which this shift started."""
    local_time = punch_in.astimezone(IST)

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    is_overnight = (end_hour, end_minute) <= (
        start_hour,
        start_minute,
    )

    shift_date = local_time.date()

    # For overnight shifts, a punch-in before shift_end belongs
    # to the shift that began on the previous calendar day.
    if is_overnight and (
        local_time.hour,
        local_time.minute,
    ) < (end_hour, end_minute):
        shift_date -= timedelta(days=1)

    return shift_date.isoformat()


# ---------- Business-rule helpers ----------

def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> int:
    """Apply R2: strict 10-minute grace period, then floor minutes."""
    local_punch_in = punch_in.astimezone(IST)

    attendance_date = attendance_date_for_punch(
        local_punch_in, shift_start, shift_end
    )
    shift_date = date.fromisoformat(attendance_date)

    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_start_at = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        start_hour,
        start_minute,
        tzinfo=IST,
    )

    elapsed_seconds = (
        local_punch_in - shift_start_at
    ).total_seconds()

    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)


def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    """Apply R4: elapsed hours, rounded to 2 decimals, half-up."""
    elapsed_seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )
    hours = elapsed_seconds / Decimal("3600")

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    """Apply R3: floor overtime minutes, only when at least 30."""
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_date = date.fromisoformat(date_str)

    is_overnight = (end_hour, end_minute) <= (
        start_hour,
        start_minute,
    )

    end_date = shift_date
    if is_overnight:
        end_date += timedelta(days=1)

    shift_end_at = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        end_hour,
        end_minute,
        tzinfo=IST,
    )

    local_punch_out = punch_out.astimezone(IST)
    elapsed_seconds = (
        local_punch_out - shift_end_at
    ).total_seconds()

    whole_minutes = int(elapsed_seconds // 60)

    return whole_minutes if whole_minutes >= 30 else 0


def compute_half_day(work_hours: float) -> bool:
    """Apply R5 using the already-rounded work_hours value."""
    return work_hours < 4.50


# ---------- Request models ----------

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("joined_on must be YYYY-MM-DD") from exc

        if parsed.isoformat() != value:
            raise ValueError("joined_on must be YYYY-MM-DD")

        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, strict=True)
    status: PresenceStatus = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, value):
        if value is not None:
            epoch_ms_to_datetime(value)
        return value


# ---------- Indexes ----------

@app.on_event("startup")
def create_indexes():
    """Create indexes automatically; repeated startup is safe."""
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="employees_emp_code_unique",
    )
    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="employees_department_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="attendance_emp_code_date_unique",
    )
    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="attendance_date_emp_code",
    )
    db.attendance_logs.create_index(
        [("status", 1), ("date", -1), ("emp_code", 1)],
        name="attendance_status_date_emp_code",
    )


# ---------- Existing endpoints ----------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        ) from exc

    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        ) from exc

    return serialize_document(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    # Use is not None so an explicitly supplied empty string is
    # still treated as an exact filter value.
    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": serialize_document(items),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is None:
        punched_at = datetime.now(UTC).replace(microsecond=0)
    else:
        punched_at = epoch_ms_to_datetime(body.punched_at)

    attendance_date = attendance_date_for_punch(
        punched_at,
        emp["shift_start"],
        emp["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punched_at,
            emp["shift_start"],
            emp["shift_end"],
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        # The unique compound index, not a preliminary find(),
        # is what makes simultaneous duplicate requests safe.
        raise HTTPException(
            status_code=409,
            detail="already punched in for this attendance date",
        ) from exc

    return serialize_document(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[AttendanceStatus] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if date_from is not None or date_to is not None:
        query["date"] = {}

        if date_from is not None:
            query["date"]["$gte"] = date_from.isoformat()

        if date_to is not None:
            query["date"]["$lte"] = date_to.isoformat()

    if status is not None:
        query["status"] = status

    skip = (page - 1) * page_size

    total = db.attendance_logs.count_documents(query)

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for doc in docs:
        # Backward compatibility for legacy records.
        doc.setdefault("history", [])
        doc.setdefault("half_day", False)
        doc.setdefault("late_minutes", 0)
        doc.setdefault("overtime_minutes", 0)

    return {
        "items": serialize_document(docs),
        "total": total,
        "page": page,
        "page_size": page_size,
    }