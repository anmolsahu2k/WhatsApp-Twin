# grade-review

Grade ABA Quiz 2 student Jupyter notebooks using an iterative Claude-grades / Codex-reviews loop.

## Invocation

- `/grade-review` → grade all submissions in batch
- `/grade-review <path_to_notebook>` → grade one specific notebook

## Fixed Paths

| Purpose | Path |
|---------|------|
| Project dir | `/Users/anmolsahu2k/Stuff/CMU/Acad/Sem2/ABA TA/Quiz2` |
| Submissions dir | `submissions/` |
| Rubric ref | `grading_scripts/rubric.md` |
| Scripts dir | `grading_scripts/` |
| Output CSV | `Quiz2_Grades_AI.csv` |
| Master log | `grading_log.md` |
| Temp dir | `/tmp/grade-review/` |

All relative paths are relative to the project dir above.

## Workflow Overview

For each student notebook, run up to **5 rounds**:

1. **Extract** notebook text with `grading_scripts/extract_notebook.py`
2. **Grade** (you, Claude) — read rubric + notebook text, write draft JSON
3. **Log** your draft to `grading_log.md`
4. **Review** — call `grading_scripts/run_grading_loop.py` to invoke Codex
5. **Decision**:
   - `VERDICT: APPROVED` → append row to CSV, log final grades, move to next student
   - `VERDICT: REVISE` → read the ISSUE lines, update draft JSON, log the revision, go to round N+1
   - Round 5 reached → write current grades regardless

---

## Step-by-Step Instructions

### Step 1 — Resolve targets

If a specific notebook path was provided as `$ARGUMENTS`, use only that.
Otherwise collect all `.ipynb` files from the submissions directory.

Check `Quiz2_Grades_AI.csv` (if it exists) and skip any `student_id` already present — this makes batch runs safely resumable.

### Step 2 — For each notebook, run the grading loop

#### 2a. Extract notebook text

```bash
mkdir -p /tmp/grade-review
python3 "grading_scripts/extract_notebook.py" \
  "<notebook_path>" \
  "/tmp/grade-review/<student_id>_notebook.txt"
```

`student_id` is the numeric ID parsed from the filename (the 5–6 digit number after the name, e.g. `129309` from `brown_olivia129309_...`).

#### 2b. Grade the submission (your job, Claude)

Read:
- `grading_scripts/rubric.md` — exact rubric criteria
- `/tmp/grade-review/<student_id>_notebook.txt` — all student cells in order

Do **not** execute any code. Analyze the source text only.

Write your grades to `/tmp/grade-review/<student_id>_draft.json`:

```json
{
  "student_id": "129309",
  "student_name": "Olivia Brown",
  "filename": "brown_olivia129309_question_1203736_14040825_QUIZ2.ipynb",
  "P1": 1,
  "P2": 3,
  "P3": 3,
  "P4": 1,
  "P5": 2,
  "total": 10,
  "P1_rationale": "Student wrote 'Weibull is appropriate since people may respond at any time'",
  "P2_rationale": "Correct formula (1-exp(-lamb*weeks**c)), correct F(t)-F(t-1) and 1-F(t) split, bounds=(0,100) for both params",
  "P3_rationale": "Actual hazard = churns/at_risk loop, predicted hazard from prob_churn/prob_not_churn, plt.plot with both series",
  "P4_rationale": "States c=1.025≈1, concludes 'hazard is approximately constant / equivalent to exponential'",
  "P5_rationale": "new_lambda = np.exp(beta*data.Gender)*lamb inside F(t); bounds: lamb (0,100), c (0,100), beta (-100,100)"
}
```

Scores: P1 in {0,1} | P2 in {0,1,2,3} | P3 in {0,1,2,3} | P4 in {0,1} | P5 in {0,1,2}

#### 2c. Log your draft to `grading_log.md`

Append a section to `grading_log.md` for this student. Format:

```markdown
---
## [N/total] Student Name (student_id)
**File:** `filename.ipynb`

### Round 1 — Claude's Draft
| Problem | Score | Rationale |
|---------|-------|-----------|
| P1 | x/1 | rationale |
| P2 | x/3 | rationale |
| P3 | x/3 | rationale |
| P4 | x/1 | rationale |
| P5 | x/2 | rationale |
| **Total** | **x/10** | |
```

If the log file doesn't exist yet, create it with a header line: `# Quiz 2 Grading Log`

#### 2d. Call the grading loop script

```bash
python3 "grading_scripts/run_grading_loop.py" \
  "<notebook_path>" \
  --round 1 \
  --draft "/tmp/grade-review/<student_id>_draft.json" \
  --log "grading_log.md"
```

The script will:
- Build the Codex prompt combining rubric + notebook text + draft JSON
- Run Codex in read-only sandbox mode
- Parse the verdict
- Append the Codex verdict section to `grading_log.md`
- Print a JSON result to stdout: `{"action": "COMPLETE"|"REVISE", ...}`

#### 2e. Handle the result

**If `action == "COMPLETE"`** (APPROVED or max rounds reached):
- The script already appended the row to the CSV and logged the final grades
- Print: `[student_name]: P1=x P2=x P3=x P4=x P5=x total=x (round N)`
- Move to the next student

**If `action == "REVISE"`**:
- Read the `issues` array from the JSON result
- Re-read the student notebook text and re-examine the specific problems flagged
- Update the relevant score and rationale in the draft JSON
- Write the updated draft JSON back to `/tmp/grade-review/<student_id>_draft.json`
- Append a "Round N — Updated Draft" section to `grading_log.md` showing only the changed scores
- Call the script again with incremented round and the session ID:

```bash
python3 "grading_scripts/run_grading_loop.py" \
  "<notebook_path>" \
  --round <N+1> \
  --draft "/tmp/grade-review/<student_id>_draft.json" \
  --log "grading_log.md" \
  --session "<session_uuid>"
```

### Step 3 — Completion report

After all students are processed, append a summary to `grading_log.md`:
```markdown
---
## Summary
Graded N students. Average: X.X/10. Output: Quiz2_Grades_AI.csv
```

Print the summary to the user as well.

---

## Rubric Quick Reference

Full rubric: `grading_scripts/rubric.md`

| Problem | Pts | What to check (static analysis) |
|---------|-----|----------------------------------|
| P1 | 1 | Chose Weibull AND stated continuous-time / "any time" reasoning |
| P2(1) | 1 | Formula: `1 - exp(-lambda * t**c)` — shape param exponent must be present |
| P2(2) | 1 | `F(t) - F(t-1)` for churn; `1 - F(t)` for non-churn; both in same function |
| P2(3) | 1 | `bounds` with positive lower bound on **both** lambda and c |
| P3(1) | 1 | Actual hazard = churns / at-risk count, per week |
| P3(2) | 1 | Predicted hazard derived from fitted params (ratio or formula) |
| P3(3) | 1 | `plt.plot(` (or equivalent) with **both** actual and predicted series |
| P4 | 1 | c approx 1 implies constant hazard / Weibull approx Exponential |
| P5(1) | 1 | `lambda_new = lambda * exp(beta * Gender)` inside F(t) |
| P5(2) | 1 | lambda > 0 AND c > 0 bounded; beta bounds allow **both signs** |

### Common mistakes to watch for

- P2(1): BG/Weibull-Gamma formula like `(alpha/(alpha+t**c))**r` → **0 pts**
- P2(3): Only one param bounded, or no bounds at all → **0 pts**
- P4: Says "no, exponential is not appropriate" without checking c approx 1 → **0 pts**
- P5(1): lambda_new formula only outside F(t) as a post-hoc scale → check carefully
- P5(2): `beta` bounded to `(0, ...)` or `(..., 0)` only → **0 pts** for this sub-point
