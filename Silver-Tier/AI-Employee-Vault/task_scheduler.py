"""
Task Scheduler — AI Employee Vault (Silver Tier)

Master control script that launches, monitors, and restarts every service
in the AI Employee system.

Managed services:
    gmail_watcher       Watchers/gmail_watcher.py        (continuous)
    file_watcher        Watchers/file_watcher.py         (continuous)
    orchestrator        orchestrator.py                  (continuous)
    approval_manager    approval_manager.py              (continuous)
    linkedin_poster     Watchers/linkedin_poster.py      (scheduled — posting days only)

Usage:
    python task_scheduler.py --start-all          Start every service
    python task_scheduler.py --stop-all           Gracefully stop everything
    python task_scheduler.py --status             Show live status table
    python task_scheduler.py --start <name>       Start a single service
    python task_scheduler.py --stop  <name>       Stop  a single service
    python task_scheduler.py --restart <name>     Restart a single service

Environment Variables (from .env):
    SCHEDULER_HEALTH_CHECK_INTERVAL   Seconds between health checks (default: 60)
    LINKEDIN_POSTING_DAYS             Comma-separated day names
    LINKEDIN_OPTIMAL_HOURS            Hour range e.g. 9-11
    LOGS_DIR                          Folder for scheduler.log
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

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
    'linkedin_poster': {
        'script': str(SCRIPT_DIR / 'Watchers' / 'linkedin_poster.py'),
        'args': ['--schedule'],
        'mode': 'scheduled',
        'description': 'LinkedIn post generator (posting days only)',
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


def is_pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is still running."""
    if pid <= 0:
        return False
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
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

# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


def start_all():
    logger.info('=' * 60)
    logger.info('AI Employee — Starting All Services')
    logger.info('=' * 60)
    for name in SERVICES:
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
# Status display
# ---------------------------------------------------------------------------


def get_service_status(name: str) -> Dict:
    """Return a status dict for a single service."""
    proc = _processes.get(name)
    state = load_state()
    svc_state = state.get('services', {}).get(name, {})
    pid = 0
    status = 'not started'
    started = ''

    if proc is not None and proc.poll() is None:
        pid = proc.pid
        status = 'running'
        started = svc_state.get('started_at', '')
    elif svc_state.get('pid'):
        pid = svc_state['pid']
        if is_pid_alive(pid):
            status = 'running (detached)'
            started = svc_state.get('started_at', '')
        else:
            status = 'stopped'
            started = svc_state.get('stopped_at', svc_state.get('started_at', ''))
    else:
        status = 'not started'

    return {
        'name': name,
        'pid': pid,
        'status': status,
        'mode': SERVICES[name]['mode'],
        'description': SERVICES[name]['description'],
        'started': started,
    }


def print_status():
    """Print a formatted status table to the terminal."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print()
    print('=' * 78)
    print(f'  AI Employee Vault — Service Status          {now_str}')
    print('=' * 78)
    print(f'  {"Service":<22} {"PID":<8} {"Status":<22} {"Mode":<12}')
    print('-' * 78)

    running = 0
    total = len(SERVICES)

    for name in SERVICES:
        info = get_service_status(name)
        pid_str = str(info['pid']) if info['pid'] else '-'
        status = info['status']

        # Colour hints for terminals that support ANSI
        if 'running' in status:
            badge = '[OK]'
            running += 1
        elif status == 'stopped':
            badge = '[--]'
        else:
            badge = '[  ]'

        print(f'  {badge} {name:<18} {pid_str:<8} {status:<22} {info["mode"]:<12}')

    print('-' * 78)
    print(f'  {running}/{total} services running')
    print('=' * 78)
    print()


# ---------------------------------------------------------------------------
# Health monitor loop
# ---------------------------------------------------------------------------


def health_check_loop():
    """
    Continuously monitor all services.  Restart any that have crashed.
    Also triggers the linkedin_poster on its schedule.
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

            for name, svc in SERVICES.items():
                proc = _processes.get(name)

                # Is the process alive?
                if proc is not None and proc.poll() is None:
                    continue  # healthy

                # It crashed or was never started
                all_ok = False

                # For scheduled services, only restart during their window
                if svc['mode'] == 'scheduled':
                    try:
                        start_h, end_h = [int(x) for x in OPTIMAL_HOURS.split('-')]
                    except ValueError:
                        start_h, end_h = 9, 11
                    if day_name not in POSTING_DAYS or not (start_h <= hour <= end_h):
                        continue  # not in window — leave it stopped

                if restart_counts[name] >= max_restarts:
                    if restart_counts[name] == max_restarts:
                        logger.error(
                            f'[HEALTH] {name} has crashed {max_restarts} times — '
                            'giving up auto-restart. Use --restart to reset.'
                        )
                        restart_counts[name] += 1  # only log once
                    continue

                # Attempt restart
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
        description='AI Employee Vault — Task Scheduler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python task_scheduler.py --start-all\n'
            '  python task_scheduler.py --status\n'
            '  python task_scheduler.py --start gmail_watcher\n'
            '  python task_scheduler.py --restart orchestrator\n'
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

    args = parser.parse_args()

    # Register shutdown handlers
    signal.signal(signal.SIGINT, _shutdown_handler)
    try:
        signal.signal(signal.SIGTERM, _shutdown_handler)
    except OSError:
        pass  # SIGTERM handler not supported on Windows

    # Rehydrate any processes from state file (for --stop / --status)
    state = load_state()
    for name, info in state.get('services', {}).items():
        if name not in _processes and is_pid_alive(info.get('pid', 0)):
            # We don't have the Popen object, but we know the PID
            pass  # handled by get_service_status / stop_service

    if args.status:
        print_status()
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
        logger.info('AI EMPLOYEE VAULT — MASTER SCHEDULER')
        logger.info(f'Services: {len(SERVICES)}')
        logger.info(f'Health check interval: {HEALTH_CHECK_INTERVAL}s')
        logger.info(f'LinkedIn posting days: {", ".join(POSTING_DAYS)}')
        logger.info(f'LinkedIn optimal hours: {OPTIMAL_HOURS}')
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
