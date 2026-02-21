"""
ralph_loop.py
-------------
Autonomous agentic loop that drives Claude Code to completion on complex tasks.

The orchestrator imports and calls process_task(filepath) directly via importlib.
This script can also be run from the command line.

Usage:
    python ralph_loop.py --task-file Needs_Action/my_task.md
    python ralph_loop.py --prompt "Process all files in Needs_Action/"
    python ralph_loop.py --task-file Needs_Action/my_task.md --max-iterations 15
    python ralph_loop.py --task-file Needs_Action/my_task.md --dry-run

Orchestrator interface (importlib.util):
    import ralph_loop
    result = ralph_loop.process_task(filepath)
    # result keys: status, iterations, summary, duration_s
    # status values: "complete" | "timeout" | "error" | "dry_run"

Environment Variables (.env):
    RALPH_MAX_ITERATIONS    Max Claude invocations before giving up (default: 10)
    RALPH_PAUSE_BETWEEN     Seconds to pause between iterations (default: 5)
    RALPH_CLAUDE_CMD        Claude CLI command name / path (default: claude)
    VAULT_ROOT              Vault root directory (already set in .env)
    DRY_RUN                 Skip subprocess calls for testing (default: false)
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

# VAULT_ROOT: use the env var (absolute path already set in .env), fall back to script dir.
_vault_env = os.getenv("VAULT_ROOT", "")
VAULT_ROOT = Path(_vault_env) if _vault_env else SCRIPT_DIR

MAX_ITERATIONS = int(os.getenv("RALPH_MAX_ITERATIONS", "10"))
PAUSE_BETWEEN  = float(os.getenv("RALPH_PAUSE_BETWEEN", "5"))
CLAUDE_CMD     = os.getenv("RALPH_CLAUDE_CMD", "claude")
DRY_RUN_ENV    = os.getenv("DRY_RUN", "false").strip().lower() == "true"

DONE_DIR             = Path(os.getenv("DONE_DIR",             str(VAULT_ROOT / "Done")))
PLANS_DIR            = Path(os.getenv("PLANS_DIR",            str(VAULT_ROOT / "Plans")))
LOGS_DIR             = Path(os.getenv("LOGS_DIR",             str(VAULT_ROOT / "Logs")))
NEEDS_ACTION_DIR     = Path(os.getenv("NEEDS_ACTION_DIR",     str(VAULT_ROOT / "Needs_Action")))
PENDING_APPROVAL_DIR = Path(os.getenv("PENDING_APPROVAL_DIR", str(VAULT_ROOT / "Pending_Approval")))

RALPH_STATES_DIR = PLANS_DIR / "ralph_states"
AUDIT_LOG_FILE   = LOGS_DIR / "ralph_audit.json"

# Ensure required directories exist (safe to call at import time)
for _d in (DONE_DIR, PLANS_DIR, LOGS_DIR, NEEDS_ACTION_DIR,
           PENDING_APPROVAL_DIR, RALPH_STATES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Hard timeout (seconds) for a single Claude CLI call
CLAUDE_TIMEOUT = 300

# How many characters of the previous output to inject into the continuation prompt
CONTEXT_TAIL = 800

# ---------------------------------------------------------------------------
# Logging — file + console
# ---------------------------------------------------------------------------

log = logging.getLogger("ralph_loop")
log.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_fh = logging.FileHandler(LOGS_DIR / "ralph_loop.log", encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
log.addHandler(_ch)

# ---------------------------------------------------------------------------
# System prompt — the five rules Claude must follow inside the loop
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTIONS = """\
You are the Ralph Loop — an autonomous AI agent operating inside an AI Employee Vault.

RULES — follow all five without exception:

1. WORK THROUGH EVERY STEP WITHOUT STOPPING. Do not pause to ask for permission or
   confirmation. Do not write "I would need to …" — perform the action now.

2. When an action requires human approval, create a draft file in Pending_Approval/
   with the proposed content, then CONTINUE to the next step immediately. Do not
   halt or wait for human input at any point during the loop.

3. When ALL steps in the task are complete, move the task file from Needs_Action/
   to Done/ and append this footer:
       ---
       **RALPH LOOP NOTE:** Processed autonomously. All steps complete.
       Completed: {timestamp}

4. After completing the task, update Dashboard.md with a one-line entry
   summarising what was done and when.

5. End your FINAL response — and ONLY the final response — with exactly this token
   on its own line (no extra text after it):
       <TASK_COMPLETE>

Your working directory is the vault root. Use relative paths from there.\
"""

# ---------------------------------------------------------------------------
# State & audit helpers
# ---------------------------------------------------------------------------


def _write_state(task_stem: str, status: str, iteration: int, summary: str) -> None:
    """Write current loop state to Plans/ralph_states/{task_stem}_ralph_state.json."""
    state = {
        "task":       task_stem,
        "status":     status,
        "iteration":  iteration,
        "summary":    summary,
        "updated_at": datetime.now().isoformat(),
    }
    state_file = RALPH_STATES_DIR / f"{task_stem}_ralph_state.json"
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        log.warning("Could not write state file: %s", exc)


def _append_audit(
    task: str,
    status: str,
    iterations: int,
    duration_s: float,
    summary: str,
) -> None:
    """
    Append one record to Logs/ralph_audit.json.
    Trims the log to the most recent 200 records.
    """
    record = {
        "timestamp":  datetime.now().isoformat(),
        "task":       task,
        "status":     status,
        "iterations": iterations,
        "duration_s": round(duration_s, 2),
        "summary":    summary,
    }
    records: list = []
    if AUDIT_LOG_FILE.exists():
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (json.JSONDecodeError, OSError):
            records = []

    records.append(record)

    if len(records) > 200:
        records = records[-200:]

    try:
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log.warning("Could not write audit log: %s", exc)


# ---------------------------------------------------------------------------
# Completion detection
# ---------------------------------------------------------------------------


def _check_promise(output: str) -> bool:
    """True if <TASK_COMPLETE> appears anywhere in Claude's output."""
    return "<TASK_COMPLETE>" in output


def _check_file_moved(task_stem: str) -> bool:
    """True if any file whose name starts with *task_stem* exists in Done/."""
    return bool(list(DONE_DIR.glob(f"{task_stem}*")))


def _is_complete(output: str, task_stem: str, strategy: str = "both") -> bool:
    """
    Evaluate completion using the requested strategy.

    strategy="both"    — either <TASK_COMPLETE> OR file in Done/ (inclusive-or, default)
    strategy="promise" — only the <TASK_COMPLETE> token check
    strategy="file"    — only the Done/ file-movement check
    """
    if strategy == "promise":
        return _check_promise(output)
    if strategy == "file":
        return _check_file_moved(task_stem)
    # "both" (default): either condition suffices
    return _check_promise(output) or _check_file_moved(task_stem)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_initial_prompt(task_content: str, task_path: str) -> str:
    return (
        f"{_SYSTEM_INSTRUCTIONS}\n\n"
        f"---\n\n"
        f"## Task file: {task_path}\n\n"
        f"{task_content}\n\n"
        f"---\n\n"
        f"Begin working through all steps now. "
        f"End with <TASK_COMPLETE> only when every step is fully done."
    )


def _build_continuation_prompt(
    task_content: str,
    task_path: str,
    iteration: int,
    prev_output_tail: str,
) -> str:
    return (
        f"{_SYSTEM_INSTRUCTIONS}\n\n"
        f"---\n\n"
        f"## Task file: {task_path}\n\n"
        f"{task_content}\n\n"
        f"---\n\n"
        f"## Context from previous iteration (iteration {iteration - 1})\n\n"
        f"You have already started working on this task. Here are the last "
        f"{CONTEXT_TAIL} characters of your previous output:\n\n"
        f"```\n{prev_output_tail}\n```\n\n"
        f"Continue from where you left off. Complete any remaining steps, "
        f"then end with <TASK_COMPLETE>."
    )


# ---------------------------------------------------------------------------
# Claude subprocess invocation
# ---------------------------------------------------------------------------

# Sentinel strings returned by _run_claude on specific failures
_ERR_NOT_FOUND = "__claude_not_found__"
_ERR_TIMEOUT   = "__timeout__"


def _run_claude(prompt: str, dry_run: bool) -> tuple[bool, str]:
    """
    Invoke the Claude CLI with `--print <prompt>`.

    Returns (success: bool, output: str).

    success=True  → output contains Claude's response text.
    success=False, output=_ERR_NOT_FOUND → binary missing; abort the whole loop.
    success=False, output=_ERR_TIMEOUT   → transient; count as failed iteration, keep looping.
    success=False, other                 → non-zero exit; keep looping.
    """
    if dry_run:
        log.info(
            "[DRY-RUN] Would invoke: %s --print <prompt> (cwd=%s, prompt_len=%d)",
            CLAUDE_CMD, VAULT_ROOT, len(prompt),
        )
        return True, "[DRY-RUN] Simulated Claude response.\n<TASK_COMPLETE>"

    cmd = [CLAUDE_CMD, "--print", prompt]
    log.debug(
        "Invoking Claude (prompt=%d chars, cwd=%s, timeout=%ds) …",
        len(prompt), VAULT_ROOT, CLAUDE_TIMEOUT,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=str(VAULT_ROOT),
        )
        if proc.returncode != 0:
            log.warning(
                "[CLAUDE] Non-zero exit code %d. stderr: %s",
                proc.returncode, proc.stderr[:400],
            )
        return True, proc.stdout

    except FileNotFoundError:
        log.error(
            "[ERROR] Claude CLI not found: %r\n"
            "  Install with : npm install -g @anthropic-ai/claude-code\n"
            "  Or set       : RALPH_CLAUDE_CMD=<full path> in .env\n"
            "  Verify with  : where claude   (Windows) / which claude  (Unix)",
            CLAUDE_CMD,
        )
        return False, _ERR_NOT_FOUND

    except subprocess.TimeoutExpired:
        log.warning(
            "[TIMEOUT] Claude did not finish within %ds — "
            "counting as a failed iteration and continuing.",
            CLAUDE_TIMEOUT,
        )
        return False, _ERR_TIMEOUT


# ---------------------------------------------------------------------------
# Timeout annotation
# ---------------------------------------------------------------------------


def _annotate_timeout(filepath: Path, iterations: int) -> None:
    """Append a TIMEOUT warning block to the task file in Needs_Action/."""
    warning = (
        f"\n\n---\n"
        f"## ⚠️ RALPH LOOP TIMEOUT\n\n"
        f"The Ralph Loop exhausted **{iterations}** iteration(s) without completing "
        f"this task.\n\n"
        f"- **Action required:** Human review needed.\n"
        f"- **Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Suggestions:**\n"
        f"  - Break this task into smaller sub-tasks and resubmit.\n"
        f"  - Increase `RALPH_MAX_ITERATIONS` in `.env` (currently {iterations}).\n"
        f"  - Run manually: `python ralph_loop.py --task-file {filepath.name} "
        f"--max-iterations {iterations + 5}`\n"
    )
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(warning)
        log.warning("[TIMEOUT] Appended timeout warning to %s.", filepath.name)
    except OSError as exc:
        log.error("[TIMEOUT] Could not annotate task file: %s", exc)


# ---------------------------------------------------------------------------
# Core: process_task — imported by orchestrator.py via importlib
# ---------------------------------------------------------------------------


def process_task(
    filepath: Path,
    *,
    max_iterations: Optional[int] = None,
    dry_run: Optional[bool] = None,
    strategy: str = "both",
) -> dict:
    """
    Run the Ralph Loop on *filepath*.

    Called by orchestrator.py as:
        result = ralph.process_task(filepath)

    Parameters
    ----------
    filepath : Path
        Path to the task .md file (typically in Needs_Action/).
    max_iterations : int, optional
        Override RALPH_MAX_ITERATIONS from .env.  None → read from env.
    dry_run : bool, optional
        Override DRY_RUN from .env.  None → read from env.
    strategy : str
        Completion detection strategy — 'both' | 'promise' | 'file'.
        Default 'both': either <TASK_COMPLETE> OR file appears in Done/.

    Returns
    -------
    dict
        {
            "status":     "complete" | "timeout" | "error" | "dry_run",
            "iterations": int,
            "summary":    str,
            "duration_s": float,
        }
    """
    start     = time.monotonic()
    _max      = max_iterations if max_iterations is not None else MAX_ITERATIONS
    _dry      = dry_run if dry_run is not None else DRY_RUN_ENV
    task_stem = filepath.stem  # filename without extension

    log.info("=" * 60)
    log.info("Ralph Loop starting.")
    log.info("  Task      : %s", filepath)
    log.info("  Max iter  : %d", _max)
    log.info("  Pause     : %gs", PAUSE_BETWEEN)
    log.info("  Strategy  : %s", strategy)
    log.info("  Dry-run   : %s", _dry)
    log.info("=" * 60)

    # ── Read task file ──────────────────────────────────────────────────────
    try:
        task_content = filepath.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("[ERROR] Cannot read task file: %s", exc)
        summary = f"Cannot read task file: {exc}"
        _write_state(task_stem, "error", 0, summary)
        _append_audit(str(filepath), "error", 0, time.monotonic() - start, summary)
        return {
            "status":     "error",
            "iterations": 0,
            "summary":    summary,
            "duration_s": round(time.monotonic() - start, 2),
        }

    # ── Dry-run short-circuit ───────────────────────────────────────────────
    if _dry:
        log.info("[DRY-RUN] Dry-run mode — one simulated iteration, no Claude calls.")
        _write_state(task_stem, "dry_run", 1, "Dry-run: simulated completion.")
        _append_audit(
            str(filepath), "dry_run", 1,
            time.monotonic() - start, "Dry-run: simulated completion.",
        )
        return {
            "status":     "dry_run",
            "iterations": 1,
            "summary":    "Dry-run mode: no real Claude invocations were made.",
            "duration_s": round(time.monotonic() - start, 2),
        }

    # ── Early binary check (fail fast before entering the loop) ─────────────
    if not shutil.which(CLAUDE_CMD):
        log.error(
            "[ERROR] Claude CLI not found: %r\n"
            "  Install with : npm install -g @anthropic-ai/claude-code\n"
            "  Or set       : RALPH_CLAUDE_CMD=<full path> in .env\n"
            "  Verify with  : where claude   (Windows) / which claude  (Unix)",
            CLAUDE_CMD,
        )
        summary = (
            f'Claude CLI "{CLAUDE_CMD}" not found. '
            "Install with: npm install -g @anthropic-ai/claude-code"
        )
        _write_state(task_stem, "error", 0, summary)
        _append_audit(str(filepath), "error", 0, time.monotonic() - start, summary)
        return {
            "status":     "error",
            "iterations": 0,
            "summary":    summary,
            "duration_s": round(time.monotonic() - start, 2),
        }

    # ── Main iteration loop ─────────────────────────────────────────────────
    prev_output = ""
    iteration   = 0

    for iteration in range(1, _max + 1):
        log.info("--- Iteration %d / %d ---", iteration, _max)

        # Build prompt: full instructions on iter 1, context tail on subsequent iters
        if iteration == 1:
            prompt = _build_initial_prompt(task_content, str(filepath))
        else:
            tail   = prev_output[-CONTEXT_TAIL:] if prev_output else ""
            prompt = _build_continuation_prompt(
                task_content, str(filepath), iteration, tail
            )

        _write_state(task_stem, "running", iteration, f"Iteration {iteration} in progress.")

        # ── Invoke Claude ──────────────────────────────────────────────────
        ok, output = _run_claude(prompt, dry_run=False)

        if not ok:
            if output == _ERR_NOT_FOUND:
                # Binary vanished mid-loop (rare but possible on Windows PATH changes)
                summary = (
                    f'Claude CLI "{CLAUDE_CMD}" disappeared after {iteration} iteration(s). '
                    "Install with: npm install -g @anthropic-ai/claude-code"
                )
                _write_state(task_stem, "error", iteration, summary)
                _append_audit(
                    str(filepath), "error", iteration,
                    time.monotonic() - start, summary,
                )
                return {
                    "status":     "error",
                    "iterations": iteration,
                    "summary":    summary,
                    "duration_s": round(time.monotonic() - start, 2),
                }
            # Transient failure (timeout / non-zero exit) — log and keep looping
            log.warning(
                "[ITER %d] Claude call failed transiently (%s) — will retry next iteration.",
                iteration, output.replace("\n", " ")[:80],
            )
            prev_output = ""
        else:
            log.info("[ITER %d] Claude output: %d chars.", iteration, len(output))
            log.debug("[ITER %d] Output tail: %s", iteration, output[-200:].replace("\n", "↵"))
            prev_output = output

        # ── Check both completion conditions ───────────────────────────────
        effective_output = output if ok else ""
        if _is_complete(effective_output, task_stem, strategy):
            duration    = time.monotonic() - start
            promise_hit = _check_promise(effective_output)
            file_hit    = _check_file_moved(task_stem)
            summary = (
                f"Completed in {iteration} iteration(s). "
                f"Promise={'yes' if promise_hit else 'no'}, "
                f"File={'yes' if file_hit else 'no'}."
            )
            log.info("[COMPLETE] %s", summary)
            _write_state(task_stem, "complete", iteration, summary)
            _append_audit(str(filepath), "complete", iteration, duration, summary)
            return {
                "status":     "complete",
                "iterations": iteration,
                "summary":    summary,
                "duration_s": round(duration, 2),
            }

        # ── Not done yet — pause then continue ─────────────────────────────
        if iteration < _max:
            log.info(
                "[ITER %d] Not yet complete — pausing %gs before iteration %d.",
                iteration, PAUSE_BETWEEN, iteration + 1,
            )
            time.sleep(PAUSE_BETWEEN)

    # ── All iterations exhausted ────────────────────────────────────────────
    log.warning("[TIMEOUT] Exhausted %d iteration(s) without completion.", _max)
    _annotate_timeout(filepath, _max)

    duration = time.monotonic() - start
    summary  = f"Timed out after {_max} iteration(s) without completing the task."
    _write_state(task_stem, "timeout", _max, summary)
    _append_audit(str(filepath), "timeout", _max, duration, summary)
    return {
        "status":     "timeout",
        "iterations": _max,
        "summary":    summary,
        "duration_s": round(duration, 2),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ralph Loop — autonomous Claude agentic loop for complex tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python ralph_loop.py --task-file Needs_Action/my_task.md\n"
            "  python ralph_loop.py --prompt 'Process all files in Needs_Action/'\n"
            "  python ralph_loop.py --task-file Needs_Action/my_task.md "
            "--max-iterations 15\n"
            "  python ralph_loop.py --task-file Needs_Action/my_task.md --dry-run\n"
        ),
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--task-file",
        metavar="PATH",
        help=(
            "Path to the task .md file. Absolute, or relative to VAULT_ROOT "
            f"({VAULT_ROOT})."
        ),
    )
    src.add_argument(
        "--prompt",
        metavar="TEXT",
        help=(
            "Ad-hoc prompt text. A temporary task file is created in Needs_Action/ "
            "and the loop runs on it."
        ),
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        metavar="N",
        help=f"Override RALPH_MAX_ITERATIONS (env default: {MAX_ITERATIONS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the loop without invoking Claude (overrides DRY_RUN in .env).",
    )

    args = parser.parse_args()

    # Merge CLI --dry-run with env DRY_RUN (either source activates it)
    dry = args.dry_run or DRY_RUN_ENV

    if args.task_file:
        filepath = Path(args.task_file)
        if not filepath.is_absolute():
            filepath = VAULT_ROOT / filepath
        if not filepath.exists():
            log.error("[CLI] Task file not found: %s", filepath)
            sys.exit(1)

        result = process_task(
            filepath,
            max_iterations=args.max_iterations,
            dry_run=dry,
        )

    else:
        # --prompt mode: materialise a temporary task file then run the loop on it
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_path = NEEDS_ACTION_DIR / f"{ts}_ralph_cli_prompt_action.md"
        tmp_path.write_text(
            f"---\ntype: cli_task\nstatus: pending\n---\n\n"
            f"# Ralph Loop CLI Task\n\n{args.prompt}\n",
            encoding="utf-8",
        )
        log.info("[CLI] Created temporary task file: %s", tmp_path)

        result = process_task(
            tmp_path,
            max_iterations=args.max_iterations,
            dry_run=dry,
        )

    # Pretty-print result
    print()
    print("=" * 52)
    print("  Ralph Loop Result")
    print("=" * 52)
    for k, v in result.items():
        print(f"  {k:<14}: {v}")
    print("=" * 52)

    # Exit 0 on success or dry_run, 1 on timeout or error
    sys.exit(0 if result["status"] in ("complete", "dry_run") else 1)


if __name__ == "__main__":
    main()
