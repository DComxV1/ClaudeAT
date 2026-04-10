#!/usr/bin/env python3
"""
====================================================
  Crypto Bot Hourly Scheduler
  Runs crypto_bot.py every hour automatically.
  Press Ctrl+C to stop.
====================================================

Usage:
    python scheduler.py               # Run hourly (default)
    python scheduler.py --interval 30 # Run every 30 minutes
    python scheduler.py --once        # Run one time and exit
"""

import time
import logging
import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCHEDULER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("scheduler.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("Scheduler")

BOT_SCRIPT = Path(__file__).parent / "crypto_bot.py"


def run_bot():
    """Execute the trading bot as a subprocess."""
    log.info("▶️  Launching crypto_bot.py ...")
    try:
        result = subprocess.run(
            [sys.executable, str(BOT_SCRIPT)],
            capture_output=False,  # Let output go to console
            text=True,
        )
        if result.returncode == 0:
            log.info("✅ Bot cycle completed successfully.")
        else:
            log.warning(f"⚠️  Bot exited with code {result.returncode}")
    except Exception as e:
        log.error(f"❌ Failed to run bot: {e}")


def main():
    parser = argparse.ArgumentParser(description="Crypto Bot Scheduler")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Minutes between runs (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit",
    )
    args = parser.parse_args()

    if args.once:
        run_bot()
        return

    interval_seconds = args.interval * 60
    log.info(f"🕐 Scheduler started — running every {args.interval} minute(s).")
    log.info("   Press Ctrl+C to stop.\n")

    # Run immediately on start
    run_bot()

    while True:
        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        log.info(f"⏳ Next run at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            log.info("\n🛑 Scheduler stopped by user.")
            break
        run_bot()


if __name__ == "__main__":
    main()
