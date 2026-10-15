## 1. Indexes

I created a unique index on `employees.emp_code` to enforce employee identity, and a unique compound index on `attendance_logs (emp_code, date)` to prevent duplicate attendance records. Additional indexes support department-filtered employee listings and attendance filtering and sorting. I will verify their effectiveness using execution statistics and adjust them if the final query plans need it.

## 2. Punch-in race condition

An application-level existence check cannot prevent two simultaneous requests from inserting the same record. The unique compound index is the final safeguard. One insert succeeds; duplicate inserts raise `DuplicateKeyError`, which the API converts to HTTP 409.

## 3. Leaderboard ties

I will use MongoDB's competition ranking so equal late-minute totals receive the same rank and the next rank is skipped. I will apply the limit after ranking and include all employees tied at the cutoff, ordering tied results by `emp_code`.

## 4. Department headcount

The department summary will start with employees eligible by the reporting period's end, then join attendance records. This includes eligible employees with no logs and excludes employees who joined later. Averages will be calculated across attendance records, not by averaging employee averages.

## 5. Scaling

For 100 times the data, I would inspect execution statistics, benchmark aggregation pipelines, and tune indexes. If analytics became too expensive, I would evaluate precomputed summaries while preserving correctness for new and regularized records.