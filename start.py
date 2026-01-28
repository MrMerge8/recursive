#!/usr/bin/env python3
"""
Orchestrates startup of dashboard and predictor services.
Handles database migration for Railway deployment.
"""

import os
import sys
import time
import shutil
import sqlite3
import subprocess
import signal

# Check if running on Railway (with persistent volume)
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
IS_RAILWAY = os.path.exists(DATA_DIR) and DATA_DIR != '/data' or os.environ.get('RAILWAY_ENVIRONMENT')

# Timeframe configurations
TIMEFRAMES = {
    '5min': {'interval': 5, 'db': 'predictions_5min.db'},
    '15min': {'interval': 15, 'db': 'predictions_15min.db'},
    '1h': {'interval': 60, 'db': 'predictions_1h.db'},
}

def setup_database():
    """Setup database paths for Railway or local deployment."""
    # Determine the data directory
    if IS_RAILWAY and os.path.exists(DATA_DIR):
        print(f"🗄️  Railway detected, using persistent volume: {DATA_DIR}")
        db_dir = DATA_DIR
    else:
        print("🗄️  Running locally, using current directory for databases")
        db_dir = "."
    
    # Set up database paths for each timeframe
    for tf, config in TIMEFRAMES.items():
        db_name = config['db']
        db_path = os.path.join(db_dir, db_name) if db_dir != "." else db_name
        
        env_var = f"DB_PATH_{tf.upper().replace('-', '_')}"
        os.environ[env_var] = db_path
        print(f"   {tf}: {db_path}")
    
    # Legacy DB path (not really used but kept for compatibility)
    os.environ['DB_PATH'] = os.path.join(db_dir, 'predictions.db') if db_dir != "." else 'predictions.db'

def main():
    """Start dashboard and predictor services."""
    print("=" * 50, flush=True)
    print("🐍 Recursive - Starting Services", flush=True)
    print("=" * 50, flush=True)
    
    # Check API keys
    print(f"\n🔑 API Keys:", flush=True)
    print(f"   ANTHROPIC_API_KEY: {'✅ Set' if os.environ.get('ANTHROPIC_API_KEY') else '❌ NOT SET'}", flush=True)
    print(f"   OPENAI_API_KEY: {'✅ Set' if os.environ.get('OPENAI_API_KEY') else '❌ NOT SET'}", flush=True)
    
    setup_database()
    
    processes = {}
    
    # Start dashboard
    print("\n🌐 Starting dashboard server...")
    dashboard_proc = subprocess.Popen(
        [sys.executable, 'dashboard.py'],
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    processes['dashboard'] = dashboard_proc
    time.sleep(2)
    
    # Start predictors for each timeframe
    for tf, config in TIMEFRAMES.items():
        print(f"\n🔮 Starting {tf} predictor...")
        env = os.environ.copy()
        env['PREDICTION_TIMEFRAME'] = tf
        env['PREDICTION_INTERVAL'] = str(config['interval'])
        env['DB_PATH'] = os.environ.get(f"DB_PATH_{tf.upper().replace('-', '_')}", config['db'])
        
        print(f"   DB_PATH={env['DB_PATH']}")
        print(f"   PREDICTION_INTERVAL={env['PREDICTION_INTERVAL']}")
        
        predictor_proc = subprocess.Popen(
            [sys.executable, 'predictor.py'],
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        processes[f'predictor_{tf}'] = predictor_proc
        time.sleep(1)
    
    print("\n✅ All services started!")
    print("=" * 50)
    
    # Monitor processes
    def signal_handler(signum, frame):
        print("\n🛑 Shutting down...")
        for name, proc in processes.items():
            proc.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Keep running and restart failed processes
    while True:
        for name, proc in list(processes.items()):
            # Check if process is still running
            if proc.poll() is not None:
                print(f"⚠️  {name} exited with code {proc.returncode}, restarting...")
                
                if name == 'dashboard':
                    new_proc = subprocess.Popen(
                        [sys.executable, 'dashboard.py'],
                        stdout=sys.stdout,
                        stderr=sys.stderr
                    )
                    processes['dashboard'] = new_proc
                elif name.startswith('predictor_'):
                    tf = name.replace('predictor_', '')
                    config = TIMEFRAMES[tf]
                    env = os.environ.copy()
                    env['PREDICTION_TIMEFRAME'] = tf
                    env['PREDICTION_INTERVAL'] = str(config['interval'])
                    env['DB_PATH'] = os.environ.get(f"DB_PATH_{tf.upper().replace('-', '_')}", config['db'])
                    
                    new_proc = subprocess.Popen(
                        [sys.executable, 'predictor.py'],
                        env=env,
                        stdout=sys.stdout,
                        stderr=sys.stderr
                    )
                    processes[name] = new_proc
                
                time.sleep(5)
        
        time.sleep(10)

if __name__ == "__main__":
    main()

