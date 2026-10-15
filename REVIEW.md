# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.


# REVIEW.md

## 1. Late-minute calculation — `compute_late_minutes()`

- **Problem:** The code truncates elapsed seconds to whole minutes before checking the grace period. At 09:40:01 for a 09:30 shift, it returns 0 instead of 10.
- **Fix:** Compare exact elapsed seconds against 600 seconds, then floor the elapsed minutes. Construct the shift start in IST and account for overnight shifts.

## 2. Work-hour rounding — `compute_work_hours()`

- **Problem:** Python's built-in `round()` does not guarantee the required half-up rounding.
- **Fix:** Use `Decimal` with `ROUND_HALF_UP` and return two-decimal hours.

## 3. Overtime calculation — `compute_overtime()`

- **Problem:** The original helper always uses the same calendar date for shift end and grants overtime for any positive duration.
- **Fix:** Calculate the correct IST shift-end instant, move it to the next date for overnight shifts, floor the elapsed minutes, and return overtime only when the result is at least 30.

## 4. Employee validation — `EmployeeIn`

- **Problem:** The original model does not enforce all contract formats or reject identical shift start and end times.
- **Fix:** Validate employee code, email, department/name lengths, date and shift-time formats, and distinct shift times.

## 5. Duplicate employee creation — `create_employee()`

- **Problem:** Checking for an existing employee before inserting is vulnerable to concurrent requests.
- **Fix:** Enforce a unique index on `emp_code` and convert `DuplicateKeyError` into HTTP 409.

## 6. Employee creation timestamp — `create_employee()`

- **Problem:** `datetime.now()` uses a naive local datetime, and the API requires epoch milliseconds.
- **Fix:** Store a UTC datetime and serialize it as epoch milliseconds.

## 7. Employee pagination — `list_employees()`

- **Problem:** `skip = page * page_size` skips the first page.
- **Fix:** Use `(page - 1) * page_size`.

## 8. Employee count — `list_employees()`

- **Problem:** `total` counts the entire collection instead of the department-filtered results.
- **Fix:** Use `count_documents()` with the same filter as the query.

## 9. Employee ordering — `list_employees()`

- **Problem:** Results are not explicitly sorted by `emp_code`.
- **Fix:** Sort ascending and add a supporting compound index for department and employee code.

## 10. Health check — `health()`

- **Problem:** The endpoint returns 200 without checking MongoDB availability.
- **Fix:** Execute a MongoDB ping and return HTTP 503 when the database is unavailable.

## 11. Unknown employee — `punch_in()`

- **Problem:** The code does not check whether the employee exists before accessing shift fields; this can cause HTTP 500.
- **Fix:** Return HTTP 404 when no employee matches the supplied code.

## 12. Timestamp and attendance date — `punch_in()`

- **Problem:** The original conversion depends on the machine's local timezone and does not consistently apply the IST attendance-date rule.
- **Fix:** Convert epoch milliseconds to UTC, truncate fractional seconds, and derive the attendance date using IST and the employee's shift.

## 13. Punch-in validation — `PunchInIn` and `punch_in()`

- **Problem:** The original model does not restrict status values or validate epoch-millisecond timestamps; a falsy check also treats timestamp 0 as omitted.
- **Fix:** Validate the status and timestamp, and distinguish `None` from an explicitly supplied value.

## 14. Concurrent punch-ins — `punch_in()`

- **Problem:** A check-then-insert sequence can allow duplicate attendance records under concurrent requests.
- **Fix:** Enforce a unique compound index on `(emp_code, date)` and return HTTP 409 on duplicate-key errors.

## 15. Internal IDs — `punch_in()` and `list_attendance()`

- **Problem:** The original responses expose MongoDB `_id` or a generated `id`, contrary to the contract.
- **Fix:** Address attendance by `(emp_code, date)` and omit internal identifiers from responses.

## 16. Attendance listing — `list_attendance()`

- **Problem:** The endpoint loads every matching document into Python, sorts in memory, and paginates afterward. Its count also ignores the database's filtered-count operation.
- **Fix:** Filter, count, sort, skip, and limit in MongoDB. Validate date-range ordering and pagination bounds, use the required deterministic sort, and serialize timestamps as epoch milliseconds.

## Items checked

The existing default shift times and default punch-in status are consistent with the contract. The corrected implementation preserves those defaults while adding validation and the missing database safeguards.