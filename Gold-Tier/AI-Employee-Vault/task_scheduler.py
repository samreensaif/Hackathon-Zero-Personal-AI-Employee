"""
Task Scheduler — AI Employee Vault (Gold Tier)

Master control script that launches, monitors, and restarts every service
in the AI Employee system.

Managed services:

    CONTINUOUS (always running):
        gmail_watcher         Watchers/gmail_watcher.py
        file_watcher          Watchers/file_watcher.py
        orchestrator          orchestrator.py
        approval_manager      approval_manager.py

    SCHEDULED (time-windowed):
        linkedin_poster       Watchers/linkedin_poster.py       Tue/Thu 9-11 AM
        social_media_poster   Watchers/social_media_poster.py   Daily 10 AM, 2 PM, 6 PM
        ceo_briefing          ceo_briefing_generator.py          Sunday 8 PM

    ON-DEMAND (triggered externally):
        ralph_loop            ralph_loop.py --auto              Via --start ralph_loop

Usage:
    python task_scheduler.py --start-all          Start every service
    python task_scheduler.py --stop-all           Gracefully stop everything
    python task_scheduler.py --status             Show live status table
    python task_scheduler.py --start <name>       Start a single service
    python task_scheduler.py --stop  <name>       Stop  a single service
    python task_scheduler.py --restart <name>     Restart a single service
    python task_scheduler.py --start-odoo         Start Odoo via docker-compose
    python task_scheduler.py --health             Run a one-shot health check

Environment Variables (from .env):
    SCHEDULER_HEALTH_CHECK_INTERVAL   Seconds between health checks (default: 60)
    LINKEDIN_POSTING_DAYS             Comma-separated day names
    LINKEDIN_OPTIMAL_HOURS            Hour range e.g. 9-11
    LOGS_DIR                          Folder for scheduler.log
    ODOO_URL                          Odoo instance URL for health check
    BRIEFING_TIME                     CEO briefing time HH:MM (default: 20:00)
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
import subprocess
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / '.env')

LOGS_DIR = Path(os.getenv('LOGS_DIR', str(SCRIPT_DIR / 'Logs')))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_CHECK_INTERVAL = int(os.getenv('SCHEDULER_HEALTH_CHECK_INTERVAL', 60))
POSTING_DAYS = [d.strip() for d in os.getenv('LINKEDIN_POSTING_DAYS', 'Tuesday,Thursday').split(',')]
OPTIMAL_HOURS = os.getenv('LINKEDIN_OPTIMAL_HOURS', '9-11')
ODOO_URL = os.getenv('ODOO_URL', '') or 'http://localhost:8069'

# Social media poster runs at these hours
SOCIAL_MEDIA_HOURS = [10, 14, 18]  # 10 AM, 2 PM, 6 PM

# CEO briefing schedule
BRIEFING_DAY = 'Sunday'
BRIEFING_HOUR = 20  # 8 PM
briefing_time_raw = os.getenv('BRIEFING_TIME', '20:00')
try:
    BRIEFING_HOUR = int(briefing_time_raw.split(':')[0])
except ValueError:
    pass

SOCIAL_MEDIA_DIR = Path(os.getenv('SOCIAL_MEDIA_DIR', str(SCRIPT_DIR / 'Social_Media')))
PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(SCRIPT_DIR / 'Pending_Approval')))
BRIEFINGS_DIR = Path(os.getenv('BRIEFINGS_DIR', str(SCRIPT_DIR / 'Briefings')))
DOCKER_COMPOSE_DIR = SCRIPT_DIR.parent  # Gold-Tier/ contains docker-compose.yml

STATE_FILE = SCRIPT_DIR / 'process_state.json'

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'scheduler.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------

SERVICES: Dict[str, Dict] = {
    # --- Continuous ---
    'gmail_watcher': {
        'script': str(SCRIPT_DIR / 'Watchers' / 'gmail_watcher.py'),
        'args': [],
        'mode': 'continuous',
        'description': 'Gmail inbox monitor',
    },
    'file_watcher': {
        'script': str(SCRIPT_DIR / 'Watchers' / 'file_watcher.py'),
        'args': [],
        'mode': 'continuous',
        'description': 'Inbox folder monitor (watchdog)',
    },
    'orchestrator': {
        'script': str(SCRIPT_DIR / 'orchestrator.py'),
        'args': [],
        'mode': 'continuous',
        'description': 'Task analyser & plan generator',
    },
    'approval_manager': {
        'script': str(SCRIPT_DIR / 'approval_manager.py'),
        'args': [],
        'mode': 'continuous',
        'description': 'Approval workflow processor',
    },
    # --- Scheduled ---
    'linkedin_poster': {
        'script': str(SCRIPT_DIR / 'Watchers' / 'linkedin_poster.py'),
        'args': ['--schedule'],
        'mode': 'scheduled',
        'schedule': 'linkedin',
        'description': f'LinkedIn posts ({", ".join(POSTING_DAYS)})',
    },
    'social_media_poster': {
        'script': str(SCRIPT_DIR / 'Watchers' / 'social_media_poster.py'),
        'args': ['--all'],
        'mode': 'scheduled',
        'schedule': 'social_media',
        'description': 'FB/IG/X posts (daily 10a/2p/6p)',
    },
    'ceo_briefing': {
        'script': str(SCRIPT_DIR / 'ceo_briefing_generator.py'),
        'args': [],
        'mode': 'scheduled',
        'schedule': 'weekly_briefing',
        'description': f'CEO briefing ({BRIEFING_DAY} {BRIEFING_HOUR}:00)',
    },
    # --- On-Demand ---
    'ralph_loop': {
        'script': str(SCRIPT_DIR / 'ralph_loop.py'),
        'args': ['--auto'],
        'mode': 'on_demand',
        'description': 'Autonomous task loop (on-demand)',
    },
}

# ---------------------------------------------------------------------------
# Process state persistence
# ---------------------------------------------------------------------------


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'services': {}}


def save_state(state: Dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f'[STATE] Failed to save: {e}')

# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

# Running subprocesses keyed by service name
_processes: Dict[str, subprocess.Popen] = {}

# Track which scheduled services already ran this hour to avoid re-triggers
_scheduled_runs: Dict[str, str] = {}  # service_name -> "YYYY-MM-DD HH" last run


def is_pid_alive(pid: int) -> bool:
    """Check if process is running (Windows-compatible)."""
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _service_log_path(name: str) -> Path:
    return LOGS_DIR / f'{name}_subprocess.log'


def start_service(name: str) -> bool:
    """Launch a single service as a subprocess."""
    if name not in SERVICES:
        logger.error(f'[START] Unknown service: {name}')
        return False

    # Already running?
    if name in _processes and _processes[name].poll() is None:
        logger.info(f'[START] {name} is already running (PID {_processes[name].pid})')
        return True

    svc = SERVICES[name]
    script = svc['script']

    if not Path(script).exists():
        logger.error(f'[START] Script not found: {script}')
        return False

    cmd = [sys.executable, script] + svc['args']
    log_path = _service_log_path(name)

    try:
        log_fh = open(log_path, 'a')
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(SCRIPT_DIR),
        )
        _processes[name] = proc

        # Persist state
        state = load_state()
        state['services'][name] = {
            'pid': proc.pid,
            'started_at': datetime.now().isoformat(),
            'script': script,
            'status': 'running',
            'mode': svc['mode'],
        }
        save_state(state)

        logger.info(f'[START] {name} started (PID {proc.pid})')
        return True

    except Exception as e:
        logger.error(f'[START] Failed to start {name}: {e}')
        return False


def stop_service(name: str) -> bool:
    """Stop a running service gracefully."""
    proc = _processes.get(name)

    if proc is None or proc.poll() is not None:
        # Maybe it's a stale PID from state file
        state = load_state()
        svc_state = state.get('services', {}).get(name, {})
        pid = svc_state.get('pid', 0)
        if is_pid_alive(pid):
            logger.info(f'[STOP] Sending SIGTERM to stale PID {pid} for {name}')
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        svc_state['status'] = 'stopped'
        svc_state['stopped_at'] = datetime.now().isoformat()
        save_state(state)
        _processes.pop(name, None)
        logger.info(f'[STOP] {name} stopped')
        return True

    logger.info(f'[STOP] Stopping {name} (PID {proc.pid})...')
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning(f'[STOP] {name} did not exit in 10s — killing')
        proc.kill()
        proc.wait(timeout=5)

    _processes.pop(name, None)

    state = load_state()
    if name in state.get('services', {}):
        state['services'][name]['status'] = 'stopped'
        state['services'][name]['stopped_at'] = datetime.now().isoformat()
    save_state(state)

    logger.info(f'[STOP] {name} stopped')
    return True


def restart_service(name: str) -> bool:
    """Stop then start a service."""
    stop_service(name)
    time.sleep(1)
    return start_service(name)


def run_oneshot(name: str) -> bool:
    """
    Run a scheduled service as a one-shot subprocess (wait for completion).
    Used for scheduled tasks that run and exit (social_media_poster, ceo_briefing).
    """
    if name not in SERVICES:
        return False

    svc = SERVICES[name]
    script = svc['script']

    if not Path(script).exists():
        logger.error(f'[ONESHOT] Script not found: {script}')
        return False

    cmd = [sys.executable, script] + svc['args']
    log_path = _service_log_path(name)

    try:
        log_fh = open(log_path, 'a')
        logger.info(f'[ONESHOT] Running {name}...')

        # Update state to show it's running
        state = load_state()
        state['services'][name] = {
            'pid': 0,
            'started_at': datetime.now().isoformat(),
            'script': script,
            'status': 'running',
            'mode': svc['mode'],
        }
        save_state(state)

        proc = subprocess.run(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(SCRIPT_DIR),
            timeout=300,  # 5 minute max
        )

        success = proc.returncode == 0
        status = 'completed' if success else 'error'

        state = load_state()
        state['services'][name] = {
            'pid': 0,
            'started_at': state.get('services', {}).get(name, {}).get('started_at', ''),
            'last_run': datetime.now().isoformat(),
            'script': script,
            'status': status,
            'mode': svc['mode'],
            'exit_code': proc.returncode,
        }
        save_state(state)

        if success:
            logger.info(f'[ONESHOT] {name} completed successfully')
        else:
            logger.error(f'[ONESHOT] {name} exited with code {proc.returncode}')
        return success

    except subprocess.TimeoutExpired:
        logger.error(f'[ONESHOT] {name} timed out after 300s')
        return False
    except Exception as e:
        logger.error(f'[ONESHOT] Failed to run {name}: {e}')
        return False


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


def start_all():
    """Start all continuous and scheduled services (not on-demand)."""
    logger.info('=' * 60)
    logger.info('AI Employee — Starting All Services (Gold Tier)')
    logger.info('=' * 60)
    for name, svc in SERVICES.items():
        if svc['mode'] == 'on_demand':
            logger.info(f'[SKIP] {name} — on-demand only (use --start {name})')
            continue
        if svc['mode'] == 'scheduled' and svc.get('schedule') != 'linkedin':
            # social_media_poster and ceo_briefing run as one-shots on schedule
            # We don't start them as long-running processes
            logger.info(f'[SKIP] {name} — will run on schedule')
            continue
        start_service(name)
    logger.info('[READY] All services launched')


def stop_all():
    logger.info('=' * 60)
    logger.info('AI Employee — Stopping All Services')
    logger.info('=' * 60)
    for name in list(_processes.keys()):
        stop_service(name)
    # Also kill anything from state that we didn't launch this session
    state = load_state()
    for name, info in state.get('services', {}).items():
        pid = info.get('pid', 0)
        if is_pid_alive(pid):
            logger.info(f'[STOP] Cleaning up stale process {name} (PID {pid})')
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        info['status'] = 'stopped'
        info['stopped_at'] = datetime.now().isoformat()
    save_state(state)
    logger.info('[DONE] All services stopped')


# ---------------------------------------------------------------------------
# Odoo management
# ---------------------------------------------------------------------------


def start_odoo() -> bool:
    """Start Odoo via docker-compose."""
    compose_file = DOCKER_COMPOSE_DIR / 'docker-compose.yml'
    if not compose_file.exists():
        logger.error(f'[ODOO] docker-compose.yml not found at {compose_file}')
        return False

    logger.info('[ODOO] Starting Odoo via docker-compose...')
    try:
        result = subprocess.run(
            ['docker-compose', 'up', '-d'],
            cwd=str(DOCKER_COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info('[ODOO] Odoo containers started')
            return True
        else:
            # Try docker compose (v2) if docker-compose fails
            result = subprocess.run(
                ['docker', 'compose', 'up', '-d'],
                cwd=str(DOCKER_COMPOSE_DIR),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info('[ODOO] Odoo containers started (docker compose v2)')
                return True
            logger.error(f'[ODOO] Failed: {result.stderr[:300]}')
            return False
    except FileNotFoundError:
        logger.error('[ODOO] docker-compose not found. Install Docker Desktop.')
        return False
    except Exception as e:
        logger.error(f'[ODOO] Failed to start: {e}')
        return False


# ---------------------------------------------------------------------------
# Gold Tier health checks
# ---------------------------------------------------------------------------


def check_odoo_health() -> Dict:
    """Ping Odoo to verify it's reachable."""
    try:
        import requests
        resp = requests.get(f'{ODOO_URL}/web/login', timeout=5)
        return {
            'name': 'Odoo',
            'status': 'healthy' if resp.status_code == 200 else f'HTTP {resp.status_code}',
            'url': ODOO_URL,
        }
    except ImportError:
        return {'name': 'Odoo', 'status': 'unknown (requests not installed)', 'url': ODOO_URL}
    except Exception as e:
        return {'name': 'Odoo', 'status': f'unreachable ({type(e).__name__})', 'url': ODOO_URL}


def check_social_media_queue() -> Dict:
    """Count pending social media drafts."""
    queue_count = 0
    if SOCIAL_MEDIA_DIR.exists():
        queue_count = len(list(SOCIAL_MEDIA_DIR.glob('*.md')))
    return {
        'name': 'Social Media Queue',
        'count': queue_count,
    }


def check_pending_approvals() -> Dict:
    """Count pending approval items."""
    count = 0
    if PENDING_APPROVAL_DIR.exists():
        count = len(list(PENDING_APPROVAL_DIR.glob('*.md')))
    return {
        'name': 'Pending Approvals',
        'count': count,
    }


def check_pending_briefings() -> Dict:
    """Count briefings generated this week."""
    count = 0
    if BRIEFINGS_DIR.exists():
        count = len(list(BRIEFINGS_DIR.glob('*.md')))
    return {
        'name': 'Briefings Generated',
        'count': count,
    }


def run_health_checks() -> List[Dict]:
    """Run all Gold Tier health checks and return results."""
    checks = [
        check_odoo_health(),
        check_social_media_queue(),
        check_pending_approvals(),
        check_pending_briefings(),
    ]
    return checks


# ---------------------------------------------------------------------------
# Schedule checking
# ---------------------------------------------------------------------------


def _hour_key(name: str) -> str:
    """Return a key like 'service_name:2026-02-15 14' to track hourly runs."""
    return f"{name}:{datetime.now().strftime('%Y-%m-%d %H')}"


def should_run_social_media(hour: int) -> bool:
    """Check if social_media_poster should run now."""
    if hour not in SOCIAL_MEDIA_HOURS:
        return False
    key = _hour_key('social_media_poster')
    if key in _scheduled_runs:
        return False
    return True


def should_run_briefing(day_name: str, hour: int) -> bool:
    """Check if ceo_briefing should run now."""
    if day_name != BRIEFING_DAY or hour != BRIEFING_HOUR:
        return False
    key = _hour_key('ceo_briefing')
    if key in _scheduled_runs:
        return False
    return True


def should_run_linkedin(day_name: str, hour: int) -> bool:
    """Check if linkedin_poster's window is open."""
    try:
        start_h, end_h = [int(x) for x in OPTIMAL_HOURS.split('-')]
    except ValueError:
        start_h, end_h = 9, 11
    return day_name in POSTING_DAYS and start_h <= hour <= end_h


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------


def get_service_status(name: str) -> Dict:
    """Return a status dict for a single service."""
    proc = _processes.get(name)
    state = load_state()
    svc_state = state.get('services', {}).get(name, {})
    pid = 0
    status = 'not started'
    last_activity = ''

    if proc is not None and proc.poll() is None:
        pid = proc.pid
        status = 'running'
        last_activity = svc_state.get('started_at', '')
    elif svc_state.get('pid') and is_pid_alive(svc_state['pid']):
        pid = svc_state['pid']
        status = 'running (detached)'
        last_activity = svc_state.get('started_at', '')
    elif svc_state.get('status') == 'completed':
        status = 'completed'
        last_activity = svc_state.get('last_run', svc_state.get('started_at', ''))
    elif svc_state.get('status') == 'error':
        status = f'error (exit {svc_state.get("exit_code", "?")})'
        last_activity = svc_state.get('last_run', '')
    elif svc_state.get('status') == 'stopped':
        status = 'stopped'
        last_activity = svc_state.get('stopped_at', svc_state.get('started_at', ''))
    else:
        status = 'not started'

    return {
        'name': name,
        'pid': pid,
        'status': status,
        'mode': SERVICES[name]['mode'],
        'description': SERVICES[name]['description'],
        'last_activity': last_activity[:19] if last_activity else '-',
    }


def print_status():
    """Print a formatted status table to the terminal."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print()
    print('=' * 95)
    print(f'  AI Employee Vault (Gold Tier) -- Service Status    {now_str}')
    print('=' * 95)
    print(f'  {"Service":<22} {"PID":<8} {"Status":<24} {"Mode":<12} {"Last Activity"}')
    print('-' * 95)

    running = 0
    total = len(SERVICES)

    for name in SERVICES:
        info = get_service_status(name)
        pid_str = str(info['pid']) if info['pid'] else '-'
        status = info['status']

        if 'running' in status:
            badge = '[OK]'
            running += 1
        elif 'completed' in status:
            badge = '[OK]'
        elif 'error' in status:
            badge = '[!!]'
        elif status == 'stopped':
            badge = '[--]'
        else:
            badge = '[  ]'

        print(
            f'  {badge} {name:<18} {pid_str:<8} {status:<24} '
            f'{info["mode"]:<12} {info["last_activity"]}'
        )

    print('-' * 95)
    print(f'  {running}/{total} services active')

    # Gold Tier health checks
    print()
    print(f'  {"Gold Tier Health Checks":}')
    print('-' * 95)

    checks = run_health_checks()
    for chk in checks:
        if 'count' in chk:
            print(f'    {chk["name"]:<30} {chk["count"]} item(s)')
        elif 'status' in chk:
            ok = 'healthy' in chk['status']
            badge = '[OK]' if ok else '[!!]'
            print(f'  {badge} {chk["name"]:<28} {chk["status"]}  ({chk.get("url", "")})')

    print('=' * 95)
    print()


# ---------------------------------------------------------------------------
# Health monitor loop
# ---------------------------------------------------------------------------


def health_check_loop():
    """
    Continuously monitor all services.  Restart crashed continuous services.
    Trigger scheduled services in their time windows.
    """
    logger.info(f'[HEALTH] Monitor running — checking every {HEALTH_CHECK_INTERVAL}s')
    restart_counts: Dict[str, int] = {name: 0 for name in SERVICES}
    max_restarts = 5  # per service, before we give up

    try:
        while True:
            now = datetime.now()
            now_str = now.strftime('%H:%M:%S')
            day_name = now.strftime('%A')
            hour = now.hour

            all_ok = True

            # --- Check continuous services ---
            for name, svc in SERVICES.items():
                if svc['mode'] != 'continuous':
                    continue

                proc = _processes.get(name)

                if proc is not None and proc.poll() is None:
                    continue  # healthy

                all_ok = False

                if restart_counts[name] >= max_restarts:
                    if restart_counts[name] == max_restarts:
                        logger.error(
                            f'[HEALTH] {name} has crashed {max_restarts} times — '
                            'giving up auto-restart. Use --restart to reset.'
                        )
                        restart_counts[name] += 1  # only log once
                    continue

                exit_code = proc.returncode if proc else 'N/A'
                logger.warning(
                    f'[HEALTH] {name} is not running (exit={exit_code}) — restarting'
                )
                if start_service(name):
                    restart_counts[name] += 1
                    logger.info(
                        f'[HEALTH] {name} restarted '
                        f'({restart_counts[name]}/{max_restarts} restarts)'
                    )
                else:
                    restart_counts[name] += 1
                    logger.error(f'[HEALTH] {name} failed to restart')

            # --- Check linkedin_poster (long-running scheduled) ---
            if should_run_linkedin(day_name, hour):
                proc = _processes.get('linkedin_poster')
                if proc is None or proc.poll() is not None:
                    logger.info(f'[SCHEDULE] LinkedIn posting window open — starting')
                    start_service('linkedin_poster')
            else:
                # Outside window — stop if running
                proc = _processes.get('linkedin_poster')
                if proc is not None and proc.poll() is None:
                    logger.info(f'[SCHEDULE] LinkedIn posting window closed — stopping')
                    stop_service('linkedin_poster')

            # --- Trigger social_media_poster (one-shot) ---
            if should_run_social_media(hour):
                logger.info(f'[SCHEDULE] Social media posting time ({hour}:00) — running')
                _scheduled_runs[_hour_key('social_media_poster')] = now.isoformat()
                run_oneshot('social_media_poster')

            # --- Trigger CEO briefing (one-shot, weekly) ---
            if should_run_briefing(day_name, hour):
                logger.info(f'[SCHEDULE] CEO briefing time ({BRIEFING_DAY} {BRIEFING_HOUR}:00) — generating')
                _scheduled_runs[_hour_key('ceo_briefing')] = now.isoformat()
                run_oneshot('ceo_briefing')

            # --- Gold Tier health checks (log periodically) ---
            odoo_health = check_odoo_health()
            if 'unreachable' in odoo_health['status']:
                logger.warning(f'[HEALTH] Odoo: {odoo_health["status"]}')
                all_ok = False

            approval_info = check_pending_approvals()
            if approval_info['count'] > 5:
                logger.warning(
                    f'[HEALTH] {approval_info["count"]} items in approval queue — '
                    'consider reviewing'
                )

            status_label = 'ALL OK' if all_ok else 'ISSUES DETECTED'
            logger.info(f'[HEALTH] [{now_str}] {status_label}')

            time.sleep(HEALTH_CHECK_INTERVAL)

    except KeyboardInterrupt:
        pass  # caller handles shutdown


# ---------------------------------------------------------------------------
# Graceful shutdown handler
# ---------------------------------------------------------------------------

_shutting_down = False


def _shutdown_handler(signum, frame):
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info('[SHUTDOWN] Signal received — stopping all services...')
    stop_all()
    logger.info('[SHUTDOWN] Complete')
    sys.exit(0)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description='AI Employee Vault — Task Scheduler (Gold Tier)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python task_scheduler.py --start-all\n'
            '  python task_scheduler.py --status\n'
            '  python task_scheduler.py --start gmail_watcher\n'
            '  python task_scheduler.py --restart orchestrator\n'
            '  python task_scheduler.py --start-odoo\n'
            '  python task_scheduler.py --health\n'
            '  python task_scheduler.py --stop-all\n'
        ),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start-all', action='store_true', help='Start all services')
    group.add_argument('--stop-all', action='store_true', help='Stop all services')
    group.add_argument('--status', action='store_true', help='Show service status')
    group.add_argument('--start', metavar='SERVICE', help='Start a single service')
    group.add_argument('--stop', metavar='SERVICE', help='Stop a single service')
    group.add_argument('--restart', metavar='SERVICE', help='Restart a single service')
    group.add_argument('--start-odoo', action='store_true', help='Start Odoo via docker-compose')
    group.add_argument('--health', action='store_true', help='Run a one-shot health check')

    args = parser.parse_args()

    # Register shutdown handlers
    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    # Rehydrate any processes from state file (for --stop / --status)
    state = load_state()
    for name, info in state.get('services', {}).items():
        if name not in _processes and is_pid_alive(info.get('pid', 0)):
            pass  # handled by get_service_status / stop_service

    if args.status:
        print_status()
        return

    if args.health:
        print_status()
        return

    if args.start_odoo:
        success = start_odoo()
        if success:
            # Wait a moment then check health
            time.sleep(3)
            odoo = check_odoo_health()
            print(f'  Odoo: {odoo["status"]}')
        return

    if args.stop_all:
        stop_all()
        print_status()
        return

    if args.stop:
        if args.stop not in SERVICES:
            print(f'Unknown service: {args.stop}')
            print(f'Available: {", ".join(SERVICES.keys())}')
            return
        stop_service(args.stop)
        print_status()
        return

    if args.start:
        if args.start not in SERVICES:
            print(f'Unknown service: {args.start}')
            print(f'Available: {", ".join(SERVICES.keys())}')
            return
        start_service(args.start)
        print_status()
        return

    if args.restart:
        if args.restart not in SERVICES:
            print(f'Unknown service: {args.restart}')
            print(f'Available: {", ".join(SERVICES.keys())}')
            return
        restart_service(args.restart)
        print_status()
        return

    if args.start_all:
        logger.info('=' * 60)
        logger.info('AI EMPLOYEE VAULT — MASTER SCHEDULER (Gold Tier)')
        logger.info(f'Services: {len(SERVICES)} total '
                     f'({sum(1 for s in SERVICES.values() if s["mode"] == "continuous")} continuous, '
                     f'{sum(1 for s in SERVICES.values() if s["mode"] == "scheduled")} scheduled, '
                     f'{sum(1 for s in SERVICES.values() if s["mode"] == "on_demand")} on-demand)')
        logger.info(f'Health check interval: {HEALTH_CHECK_INTERVAL}s')
        logger.info(f'LinkedIn posting days: {", ".join(POSTING_DAYS)}')
        logger.info(f'LinkedIn optimal hours: {OPTIMAL_HOURS}')
        logger.info(f'Social media hours: {", ".join(f"{h}:00" for h in SOCIAL_MEDIA_HOURS)}')
        logger.info(f'CEO briefing: {BRIEFING_DAY} {BRIEFING_HOUR}:00')
        logger.info(f'Odoo URL: {ODOO_URL}')
        logger.info('=' * 60)

        start_all()
        print_status()

        logger.info('[SCHEDULER] Entering health-check loop (Ctrl+C to stop)')
        health_check_loop()

        # If we exit the loop (KeyboardInterrupt), shut down
        stop_all()
        print_status()


if __name__ == '__main__':
    main()
