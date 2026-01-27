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
    """Setup database paths for Railway persistent volume."""
    if IS_RAILWAY and os.path.exists(DATA_DIR):
        print(f"🗄️  Railway detected, using persistent volume: {DATA_DIR}")
        
        for tf, config in TIMEFRAMES.items():
            db_name = config['db']
            source_db = db_name
            target_db = os.path.join(DATA_DIR, db_name)
            
            # If target doesn't exist but source does, migrate it
            if os.path.exists(source_db) and not os.path.exists(target_db):
                print(f"   Migrating {source_db} to {target_db}")
                shutil.copy2(source_db, target_db)
            elif os.path.exists(source_db) and os.path.exists(target_db):
                # Check if target is empty but source has data
                try:
                    conn = sqlite3.connect(target_db)
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM predictions")
                    target_count = cursor.fetchone()[0]
                    conn.close()
                    
                    if target_count == 0:
                        conn = sqlite3.connect(source_db)
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM predictions")
                        source_count = cursor.fetchone()[0]
                        conn.close()
                        
                        if source_count > 0:
                            print(f"   Target empty, migrating {source_count} predictions from {source_db}")
                            shutil.copy2(source_db, target_db)
                except Exception as e:
                    print(f"   Migration check failed: {e}")
            
            # Set environment variable for this timeframe's DB path
            env_var = f"DB_PATH_{tf.upper().replace('-', '_')}"
            if os.path.exists(target_db):
                os.environ[env_var] = target_db
                print(f"   {tf}: {target_db}")
            elif os.path.exists(source_db):
                os.environ[env_var] = source_db
                print(f"   {tf}: {source_db} (local)")
        
        # Also handle legacy single predictions.db
        legacy_source = 'predictions.db'
        legacy_target = os.path.join(DATA_DIR, 'predictions.db')
        if os.path.exists(legacy_source) and not os.path.exists(legacy_target):
            shutil.copy2(legacy_source, legacy_target)
        os.environ['DB_PATH'] = legacy_target if os.path.exists(legacy_target) else legacy_source
    else:
        print("🗄️  Running locally, using local database files")
        for tf, config in TIMEFRAMES.items():
            env_var = f"DB_PATH_{tf.upper().replace('-', '_')}"
            os.environ[env_var] = config['db']
        os.environ['DB_PATH'] = 'predictions.db'

def main():
    """Start dashboard and predictor services."""
    print("=" * 50)
    print("🐍 Recursive - Starting Services")
    print("=" * 50)
    
    setup_database()
    
    processes = {}
    
    # Start dashboard
    print("\n🌐 Starting dashboard server...")
    dashboard_proc = subprocess.Popen(
        [sys.executable, 'dashboard.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True
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
        
        predictor_proc = subprocess.Popen(
            [sys.executable, 'predictor.py'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True
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
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                        universal_newlines=True
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
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                        universal_newlines=True
                    )
                    processes[name] = new_proc
                
                time.sleep(5)
        
        time.sleep(10)

if __name__ == "__main__":
    main()

