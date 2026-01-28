#!/usr/bin/env python3
"""
Recursive Dashboard - Dual Recursive Verification System
Multi-timeframe BTC prediction with Claude + GPT-4 verification
"""

import sqlite3
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import os

# Timeframe configurations
TIMEFRAMES = {
    '5': {'name': '5M', 'interval': 5, 'db': 'predictions_5min.db', 'label': '5-min cycles'},
    '15': {'name': '15M', 'interval': 15, 'db': 'predictions_15min.db', 'label': '15-min cycles'},
    '60': {'name': '1H', 'interval': 60, 'db': 'predictions_1h.db', 'label': '1-hour cycles'},
}

DEFAULT_TIMEFRAME = '5'
PORT = 8080

# API authentication for external predictions (local LLM)
API_KEY = os.environ.get('RAILWAY_API_KEY', '')


def save_external_prediction(db_path: str, prediction: dict) -> int:
    """Save a prediction from external source (local LLM) to the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            current_price REAL NOT NULL,
            predicted_direction TEXT NOT NULL,
            predicted_target REAL NOT NULL,
            confidence INTEGER NOT NULL,
            reasoning TEXT,
            resolved_at TEXT,
            actual_price REAL,
            actual_direction TEXT,
            direction_correct BOOLEAN,
            target_error_pct REAL,
            calibration_score REAL,
            is_extreme BOOLEAN,
            extreme_reason TEXT,
            learning_extracted TEXT,
            source TEXT DEFAULT 'local_llm'
        )
    """)
    
    # Check if source column exists, add if not
    cursor.execute("PRAGMA table_info(predictions)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'source' not in columns:
        cursor.execute("ALTER TABLE predictions ADD COLUMN source TEXT DEFAULT 'claude'")
    
    cursor.execute("""
        INSERT INTO predictions (
            timestamp, current_price, predicted_direction, predicted_target,
            confidence, reasoning, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        prediction.get('timestamp', datetime.now(timezone.utc).isoformat()),
        prediction['current_price'],
        prediction['direction'].upper(),
        prediction['target'],
        prediction['confidence'],
        prediction.get('reasoning', ''),
        prediction.get('source', 'local_llm')
    ))
    
    pred_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pred_id

def get_db_path(timeframe='5'):
    """Get database path for a given timeframe."""
    config = TIMEFRAMES.get(timeframe, TIMEFRAMES[DEFAULT_TIMEFRAME])
    db_name = config['db']
    
    # Check Railway volume first
    data_dir = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
    if os.path.exists(data_dir):
        volume_path = os.path.join(data_dir, db_name)
        if os.path.exists(volume_path):
            return volume_path
    
    # Fall back to local
    if os.path.exists(db_name):
        return db_name
    
    # Fall back to legacy single db
    return os.environ.get('DB_PATH', 'predictions.db')

def get_html_template(timeframe='5'):
    tf_config = TIMEFRAMES.get(timeframe, TIMEFRAMES[DEFAULT_TIMEFRAME])
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recursive - BTC Predictor</title>
    
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        :root {
            --bg: #FAFAFA;
            --surface: #FFFFFF;
            --surface-raised: #FFFFFF;
            --border: #E5E5E5;
            --border-light: #F0F0F0;
            --text: #171717;
            --text-secondary: #525252;
            --text-muted: #A3A3A3;
            --accent: #2563EB;
            --success: #10B981;
            --success-bg: #ECFDF5;
            --error: #EF4444;
            --error-bg: #FEF2F2;
            --warning: #F59E0B;
            --warning-bg: #FFFBEB;
            --purple: #8B5CF6;
            --purple-bg: #F5F3FF;
            --cyan: #06B6D4;
            --cyan-bg: #ECFEFF;
        }
        
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            min-height: 100vh;
            font-size: 14px;
        }
        
        .container {
            max-width: 1000px;
            margin: 0 auto;
            padding: 32px 24px;
        }
        
        /* Header */
        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 32px;
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo-icon {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, var(--purple) 0%%, var(--accent) 100%%);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }
        
        .logo-text h1 {
            font-size: 18px;
            font-weight: 600;
            letter-spacing: -0.3px;
        }
        
        .logo-text span {
            font-size: 12px;
            color: var(--text-muted);
        }
        
        .status-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            background: var(--success-bg);
            color: var(--success);
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .status-dot {
            width: 6px;
            height: 6px;
            background: var(--success);
            border-radius: 50%%;
            animation: pulse 2s ease-in-out infinite;
        }
        
        @keyframes pulse {
            0%%, 100%% { opacity: 1; }
            50%% { opacity: 0.4; }
        }
        
        /* Current Prediction Card */
        .current-prediction {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }
        
        .current-prediction-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
        }
        
        .current-prediction-label {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .current-prediction-time {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--text-muted);
        }
        
        .current-prediction-main {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 16px;
        }
        
        .direction-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
        }
        
        .direction-badge.up {
            background: var(--success-bg);
            color: var(--success);
        }
        
        .direction-badge.down {
            background: var(--error-bg);
            color: var(--error);
        }
        
        .direction-badge .arrow {
            font-size: 18px;
        }
        
        .prediction-target {
            font-family: 'JetBrains Mono', monospace;
            font-size: 24px;
            font-weight: 600;
            color: var(--text);
        }
        
        .prediction-confidence {
            margin-left: auto;
            text-align: right;
        }
        
        .confidence-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 20px;
            font-weight: 600;
            color: var(--accent);
        }
        
        .confidence-label {
            font-size: 11px;
            color: var(--text-muted);
        }
        
        .progress-bar {
            height: 4px;
            background: var(--border-light);
            border-radius: 2px;
            overflow: hidden;
            margin-bottom: 12px;
        }
        
        .progress-fill {
            height: 100%%;
            background: linear-gradient(90deg, var(--accent), var(--purple));
            border-radius: 2px;
            transition: width 1s linear;
        }
        
        .prediction-reasoning {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.6;
            padding: 12px;
            background: var(--bg);
            border-radius: 8px;
        }
        
        .no-prediction {
            text-align: center;
            padding: 32px;
            color: var(--text-muted);
        }
        
        /* Stats Row */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
            margin-bottom: 24px;
        }
        
        @media (max-width: 768px) {
            .stats-row {
                grid-template-columns: repeat(3, 1fr);
            }
        }
        
        .stat-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
        }
        
        .stat-label {
            font-size: 11px;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.3px;
            margin-bottom: 4px;
        }
        
        .stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 22px;
            font-weight: 600;
        }
        
        .stat-value.success { color: var(--success); }
        .stat-value.error { color: var(--error); }
        .stat-value.warning { color: var(--warning); }
        .stat-value.accent { color: var(--accent); }
        .stat-value.purple { color: var(--purple); }
        .stat-value.cyan { color: var(--cyan); }
        
        .stat-sub {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 2px;
        }
        
        /* Two Column Layout */
        .two-col {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-bottom: 24px;
        }
        
        @media (max-width: 768px) {
            .two-col {
                grid-template-columns: 1fr;
            }
        }
        
        /* Section Card */
        .section-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
        }
        
        .section-header {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-light);
            background: var(--bg);
        }
        
        .section-icon {
            font-size: 14px;
        }
        
        .section-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--text);
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }
        
        .section-count {
            margin-left: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 500;
            color: var(--text-muted);
            background: var(--border-light);
            padding: 2px 8px;
            border-radius: 10px;
        }
        
        .section-content {
            padding: 12px;
            max-height: 280px;
            overflow-y: auto;
        }
        
        /* Meta Rules */
        .meta-rule {
            padding: 12px;
            border-radius: 8px;
            background: var(--cyan-bg);
            margin-bottom: 8px;
        }
        
        .meta-rule:last-child {
            margin-bottom: 0;
        }
        
        .meta-rule-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 6px;
        }
        
        .meta-rule-type {
            font-size: 10px;
            font-weight: 600;
            color: var(--cyan);
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }
        
        .meta-rule-confidence {
            margin-left: auto;
            font-size: 10px;
            color: var(--text-muted);
        }
        
        .meta-rule-text {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
        }
        
        /* Learnings */
        .learning {
            padding: 12px;
            border-radius: 8px;
            background: var(--purple-bg);
            margin-bottom: 8px;
        }
        
        .learning:last-child {
            margin-bottom: 0;
        }
        
        .learning-reason {
            font-size: 10px;
            font-weight: 600;
            color: var(--purple);
            text-transform: uppercase;
            letter-spacing: 0.3px;
            margin-bottom: 4px;
        }
        
        .learning-text {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
        }
        
        /* Expandable cards */
        .expandable-card {
            cursor: pointer;
            transition: box-shadow 0.15s ease;
        }
        
        .expandable-card:hover {
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        
        .expandable-short {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.5;
        }
        
        .expandable-full {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.6;
        }
        
        .expandable-section {
            margin-bottom: 12px;
        }
        
        .expandable-section:last-child {
            margin-bottom: 0;
        }
        
        .expandable-label {
            font-size: 10px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.3px;
            margin-bottom: 4px;
        }
        
        .expand-hint {
            display: inline-block;
            font-size: 11px;
            color: var(--accent);
            opacity: 0.7;
            margin-left: 8px;
            transition: opacity 0.15s ease;
        }
        
        .expandable-card:hover .expand-hint {
            opacity: 1;
        }
        
        .row-hint {
            opacity: 0.4;
            margin-left: 4px;
        }
        
        .prediction-row:hover .row-hint {
            opacity: 1;
        }
        
        /* Recent Streak */
        .streak-container {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        
        .streak-item {
            width: 28px;
            height: 28px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
        }
        
        .streak-item.win {
            background: var(--success-bg);
            color: var(--success);
        }
        
        .streak-item.loss {
            background: var(--error-bg);
            color: var(--error);
        }
        
        .streak-item.pending {
            background: var(--warning-bg);
            color: var(--warning);
        }
        
        /* Predictions Table */
        .predictions-section {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
        }
        
        .predictions-table {
            width: 100%%;
            border-collapse: collapse;
            table-layout: fixed;
        }
        
        .predictions-table th,
        .predictions-table td {
            padding: 14px 16px;
            font-size: 13px;
            border-bottom: 1px solid var(--border-light);
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .predictions-table th {
            text-align: left;
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.3px;
            background: var(--bg);
            border-bottom: 1px solid var(--border);
        }
        
        .predictions-table tr:last-child td {
            border-bottom: none;
        }
        
        .predictions-table tr.prediction-row {
            cursor: pointer;
            transition: background 0.15s ease;
        }
        
        .predictions-table tr.prediction-row:hover {
            background: var(--bg);
        }
        
        .reasoning-row {
            display: none;
        }
        
        .reasoning-row.visible {
            display: table-row;
        }
        
        .reasoning-row td {
            padding: 0 16px 16px 16px;
            background: var(--bg);
        }
        
        .reasoning-box {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }
        
        .reasoning-box-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-light);
        }
        
        .reasoning-box-stat {
            font-size: 12px;
        }
        
        .reasoning-box-stat span {
            color: var(--text-muted);
        }
        
        .reasoning-box-stat strong {
            color: var(--text);
            font-weight: 600;
        }
        
        .reasoning-label {
            font-size: 10px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        
        .reasoning-text {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.7;
        }
        
        .mini-badge {
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }
        
        .mini-badge.up {
            background: var(--success-bg);
            color: var(--success);
        }
        
        .mini-badge.down {
            background: var(--error-bg);
            color: var(--error);
        }
        
        .result-correct {
            color: var(--success);
            font-weight: 600;
        }
        
        .result-wrong {
            color: var(--error);
            font-weight: 600;
        }
        
        .result-pending {
            color: var(--warning);
        }
        
        .mono {
            font-family: 'JetBrains Mono', monospace;
        }
        
        .empty-state {
            padding: 24px;
            text-align: center;
            color: var(--text-muted);
            font-size: 13px;
        }
        
        /* Footer */
        footer {
            text-align: center;
            padding-top: 16px;
            font-size: 11px;
            color: var(--text-muted);
        }
        
        /* Dual Prediction View */
        .dual-prediction-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }
        
        @media (max-width: 768px) {
            .dual-prediction-grid {
                grid-template-columns: 1fr;
            }
        }
        
        .claude-prediction {
            padding: 16px;
            background: var(--purple-bg);
            border-radius: 10px;
            border: 1px solid var(--purple);
        }
        
        .gpt-verification {
            padding: 16px;
            background: #E8F5E9;
            border-radius: 10px;
            border: 1px solid #10a37f;
        }
        
        .model-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
        }
        
        .model-icon {
            width: 24px;
            height: 24px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
        }
        
        .model-icon.claude {
            background: var(--purple);
            color: white;
        }
        
        .model-icon.gpt {
            background: #10a37f;
            color: white;
        }
        
        .model-name {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .model-name.claude {
            color: var(--purple);
        }
        
        .model-name.gpt {
            color: #10a37f;
        }
        
        .verdict-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
        }
        
        .verdict-badge.agrees {
            background: #C8E6C9;
            color: #2E7D32;
        }
        
        .verdict-badge.disagrees {
            background: #FFCDD2;
            color: #C62828;
        }
        
        .consensus-signal {
            padding: 12px 16px;
            border-radius: 8px;
            text-align: center;
            margin-bottom: 16px;
        }
        
        .consensus-signal.strong {
            background: linear-gradient(135deg, #C8E6C9, #A5D6A7);
            border: 1px solid #4CAF50;
        }
        
        .consensus-signal.weak {
            background: linear-gradient(135deg, #FFF9C4, #FFF59D);
            border: 1px solid #FFC107;
        }
        
        .consensus-signal.disagreement {
            background: linear-gradient(135deg, #FFCDD2, #EF9A9A);
            border: 1px solid #F44336;
        }
        
        .consensus-signal.veto {
            background: linear-gradient(135deg, #FFCDD2, #E57373);
            border: 2px solid #D32F2F;
        }
        
        .consensus-label {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }
        
        .consensus-value {
            font-size: 16px;
            font-weight: 700;
        }
        
        /* Verifier Stats */
        .verifier-stats {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            margin-top: 12px;
            margin-bottom: 20px;
        }
        
        @media (max-width: 768px) {
            .verifier-stats {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        
        .verifier-stat-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-left: 3px solid #10a37f;
            border-radius: 8px;
            padding: 12px;
        }
        
        .verifier-stat-label {
            font-size: 10px;
            font-weight: 500;
            color: #10a37f;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }
        
        .verifier-stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 20px;
            font-weight: 600;
            color: #10a37f;
        }
        
        .verifier-stat-sub {
            font-size: 10px;
            color: var(--text-muted);
        }
        
        /* Verifier learning card */
        .learning-card {
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            background: var(--surface);
            border: 1px solid var(--border);
        }
        
        .learning-card.verifier {
            border-left: 3px solid #10a37f;
            background: #E8F5E9;
        }
        
        .learning-context {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            margin-bottom: 6px;
        }
        
        .learning-context.caught {
            color: #2E7D32;
        }
        
        .learning-context.false-alarm {
            color: #C62828;
        }
        
        .learning-context.blind-spot {
            color: #F57C00;
        }
        
        /* Verifier Meta Rules */
        .verifier-meta-rule {
            padding: 12px;
            border-radius: 8px;
            background: #E8F5E9;
            border-left: 3px solid #10a37f;
            margin-bottom: 8px;
        }
        
        .verifier-meta-rule-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 6px;
        }
        
        .verifier-meta-rule-type {
            font-size: 10px;
            font-weight: 600;
            color: #10a37f;
            text-transform: uppercase;
        }
        
        .verifier-meta-rule-confidence {
            margin-left: auto;
            font-size: 10px;
            color: var(--text-muted);
        }
        
        /* Dark Theme */
        body.dark {
            --bg: #0a0a0a;
            --surface: #141414;
            --surface-raised: #1a1a1a;
            --border: #2a2a2a;
            --border-light: #222222;
            --text: #fafafa;
            --text-secondary: #a3a3a3;
            --text-muted: #666666;
        }
        
        body.dark .gpt-verification {
            background: #0d2818;
            border-color: #10a37f;
        }
        
        body.dark .verifier-meta-rule,
        body.dark .learning-card.verifier {
            background: #0d2818;
        }
        
        /* Timeframe Tabs */
        .timeframe-tabs {
            display: flex;
            gap: 4px;
            background: var(--surface);
            padding: 4px;
            border-radius: 8px;
            border: 1px solid var(--border);
        }
        
        .timeframe-tab {
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-decoration: none;
            transition: all 0.15s ease;
        }
        
        .timeframe-tab:hover {
            color: var(--text);
            background: var(--bg);
        }
        
        .timeframe-tab.active {
            background: var(--accent);
            color: white;
        }
        
        /* Theme Toggle */
        .theme-toggle {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--surface);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            transition: all 0.15s ease;
        }
        
        .theme-toggle:hover {
            background: var(--bg);
        }
        
        /* Header Layout */
        .header-left {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        
        .header-right {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .brand {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, var(--purple), var(--accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .subtitle {
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 24px;
        }
        
        .learning-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            background: var(--purple-bg);
            color: var(--purple);
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }
        
        body.dark .learning-badge {
            background: rgba(139, 92, 246, 0.2);
        }
        
        /* GPT Verification Inline */
        .verification-inline {
            margin-top: 16px;
            padding: 16px;
            background: #E8F5E9;
            border-radius: 10px;
            border: 1px solid #10a37f;
        }
        
        body.dark .verification-inline {
            background: #0d2818;
        }
        
        .verification-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }
        
        .verification-verdict {
            font-size: 14px;
            font-weight: 600;
        }
        
        .verification-verdict.agrees {
            color: #2E7D32;
        }
        
        .verification-verdict.disagrees {
            color: #C62828;
        }
        
        .verification-confidence {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            font-weight: 600;
            color: #10a37f;
        }
        
        .verification-reasoning {
            font-size: 12px;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }
        
        .verification-concerns {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        
        .verification-concerns li {
            font-size: 11px;
            color: var(--text-secondary);
            padding: 4px 0;
            padding-left: 16px;
            position: relative;
        }
        
        .verification-concerns li::before {
            content: '⚠️';
            position: absolute;
            left: 0;
            font-size: 10px;
        }
        
        /* Consensus Signal Inline */
        .consensus-inline {
            margin-top: 12px;
            padding: 12px 16px;
            border-radius: 8px;
            text-align: center;
        }
        
        .consensus-inline.strong {
            background: linear-gradient(135deg, #C8E6C9, #A5D6A7);
            border: 1px solid #4CAF50;
        }
        
        .consensus-inline.weak {
            background: linear-gradient(135deg, #FFF9C4, #FFF59D);
            border: 1px solid #FFC107;
        }
        
        .consensus-inline.disagreement {
            background: linear-gradient(135deg, #FFECB3, #FFE082);
            border: 1px solid #FF9800;
        }
        
        .consensus-inline.veto {
            background: linear-gradient(135deg, #FFCDD2, #EF9A9A);
            border: 1px solid #F44336;
        }
        
        body.dark .consensus-inline.strong {
            background: rgba(76, 175, 80, 0.2);
            border-color: #4CAF50;
        }
        
        body.dark .consensus-inline.weak {
            background: rgba(255, 193, 7, 0.2);
            border-color: #FFC107;
        }
        
        body.dark .consensus-inline.disagreement {
            background: rgba(255, 152, 0, 0.2);
            border-color: #FF9800;
        }
        
        body.dark .consensus-inline.veto {
            background: rgba(244, 67, 54, 0.2);
            border-color: #F44336;
        }
        
        .consensus-type {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
        }
        
        .consensus-name {
            font-size: 14px;
            font-weight: 700;
            color: var(--text);
        }
        
        @media (max-width: 768px) {
            .header-left {
                flex-wrap: wrap;
                gap: 8px;
            }
            
            .brand {
                font-size: 16px;
            }
            
            .timeframe-tab {
                padding: 4px 10px;
                font-size: 11px;
            }
            
            .verification-inline {
                padding: 12px;
            }
        }
    </style>
    <script>
        // Prediction timestamp from server
        const predictionTimestamp = %%PREDICTION_TIMESTAMP%%;
        const isResolved = %%IS_RESOLVED%%;
        const predictionDuration = 300; // 5 minutes in seconds
        let hasTriggeredRefresh = false;
        
        function updateCountdown() {
            if (!predictionTimestamp) return;
            
            const now = Date.now() / 1000;
            const elapsed = now - predictionTimestamp;
            const remaining = Math.max(0, predictionDuration - elapsed);
            
            const mins = Math.floor(remaining / 60);
            const secs = Math.floor(remaining % 60);
            
            const timeEl = document.getElementById('time-remaining');
            const progressEl = document.getElementById('progress-fill');
            
            if (timeEl && !isResolved) {
                if (remaining > 0) {
                    timeEl.textContent = `${mins}:${secs.toString().padStart(2, '0')} remaining`;
                } else {
                    timeEl.textContent = 'Resolving...';
                }
            }
            
            if (progressEl && !isResolved) {
                const progress = Math.min(100, (elapsed / predictionDuration) * 100);
                progressEl.style.width = progress + '%';
            }
            
            // Auto-refresh when timer hits zero (give 10 seconds for resolution)
            if (remaining <= 0 && !hasTriggeredRefresh) {
                hasTriggeredRefresh = true;
                setTimeout(() => {
                    location.reload();
                }, 10000); // Wait 10 seconds for prediction to resolve, then refresh
            }
        }
        
        // Update every second
        setInterval(updateCountdown, 1000);
        updateCountdown();
        
        // Also poll every 30 seconds to catch new predictions
        setInterval(() => location.reload(), 30000);
        
        // Theme toggle
        function toggleTheme() {
            document.body.classList.toggle('dark');
            const isDark = document.body.classList.contains('dark');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
            document.getElementById('theme-icon').textContent = isDark ? '🌙' : '☀️';
        }
        
        // Load saved theme
        (function() {
            const savedTheme = localStorage.getItem('theme');
            if (savedTheme === 'dark') {
                document.body.classList.add('dark');
            }
        })();
        
        // Update theme icon on load
        document.addEventListener('DOMContentLoaded', function() {
            const isDark = document.body.classList.contains('dark');
            const icon = document.getElementById('theme-icon');
            if (icon) icon.textContent = isDark ? '🌙' : '☀️';
        });
        
        // Toggle reasoning row visibility
        function toggleReasoning(id) {
            const row = document.getElementById('reasoning-' + id);
            if (row) {
                row.classList.toggle('visible');
            }
        }
        
        // Toggle expandable cards (meta rules and learnings)
        function toggleExpand(id) {
            const shortEl = document.getElementById(id + '-short');
            const fullEl = document.getElementById(id + '-full');
            
            if (shortEl && fullEl) {
                if (fullEl.style.display === 'none') {
                    shortEl.style.display = 'none';
                    fullEl.style.display = 'block';
                } else {
                    shortEl.style.display = 'block';
                    fullEl.style.display = 'none';
                }
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-left">
                <span class="brand">RECURSIVE</span>
                <div class="timeframe-tabs">
                    <a href="?tf=5" class="timeframe-tab %%TAB_5_ACTIVE%%">5M</a>
                    <a href="?tf=15" class="timeframe-tab %%TAB_15_ACTIVE%%">15M</a>
                    <a href="?tf=60" class="timeframe-tab %%TAB_60_ACTIVE%%">1H</a>
                </div>
            </div>
            <div class="header-right">
                <button class="theme-toggle" onclick="toggleTheme()">
                    <span id="theme-icon">☀️</span>
                </button>
                <div class="learning-badge">
                    <span>🧠</span>
                    Learning
                </div>
            </div>
        </header>
        <div class="subtitle">Recursive Learning Intelligence BTC/USDT • %%TIMEFRAME_LABEL%%</div>
        
        <!-- Current Prediction -->
        %%CURRENT_PREDICTION_HTML%%
        
        <!-- Stats Row -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-label">Predictions</div>
                <div class="stat-value">%%TOTAL%%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Accuracy</div>
                <div class="stat-value %%ACCURACY_CLASS%%">%%ACCURACY%%</div>
                <div class="stat-sub">%%CORRECT%% / %%RESOLVED%% correct</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Calibration</div>
                <div class="stat-value accent">%%CALIBRATION%%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Learnings</div>
                <div class="stat-value purple">%%EXTREMES%%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Meta-Rules</div>
                <div class="stat-value cyan">%%META_RULES%%</div>
                <div class="stat-sub">Next analysis: %%NEXT_META%%</div>
            </div>
        </div>
        
        <!-- GPT-4 Verifier Stats -->
        <div class="verifier-stats">
            <div class="verifier-stat-card">
                <div class="verifier-stat-label">🔍 GPT-4 Verifier</div>
                <div class="verifier-stat-value">%%VERIFIER_ACCURACY%%</div>
                <div class="verifier-stat-sub">%%VERIFIER_CORRECT%% / %%VERIFIER_TOTAL%% correct</div>
            </div>
            <div class="verifier-stat-card">
                <div class="verifier-stat-label">🎯 Catches</div>
                <div class="verifier-stat-value">%%VERIFIER_CATCHES%%</div>
                <div class="verifier-stat-sub">Claude errors caught</div>
            </div>
            <div class="verifier-stat-card">
                <div class="verifier-stat-label">⚠️ False Alarms</div>
                <div class="verifier-stat-value">%%VERIFIER_FALSE_ALARMS%%</div>
                <div class="verifier-stat-sub">Wrongly doubted</div>
            </div>
            <div class="verifier-stat-card">
                <div class="verifier-stat-label">🤝 Consensus Win</div>
                <div class="verifier-stat-value">%%CONSENSUS_WIN_RATE%%</div>
                <div class="verifier-stat-sub">When both agree</div>
            </div>
        </div>
        
        <!-- Recent Streak -->
        <div class="section-card" style="margin-bottom: 24px;">
            <div class="section-header">
                <span class="section-icon">📈</span>
                <span class="section-title">Recent Results</span>
            </div>
            <div class="section-content">
                <div class="streak-container">
                    %%STREAK_HTML%%
                </div>
            </div>
        </div>
        
        <!-- Two Column: Meta Rules + Learnings -->
        <div class="two-col">
            <div class="section-card">
                <div class="section-header">
                    <span class="section-icon">🧠</span>
                    <span class="section-title">Active Meta-Rules</span>
                    <span class="section-count">%%META_RULES%%</span>
                </div>
                <div class="section-content">
                    %%META_RULES_HTML%%
                </div>
            </div>
            
            <div class="section-card">
                <div class="section-header">
                    <span class="section-icon">💡</span>
                    <span class="section-title">Recent Learnings</span>
                    <span class="section-count">%%EXTREMES%%</span>
                </div>
                <div class="section-content">
                    %%LEARNINGS_HTML%%
                </div>
            </div>
        </div>
        
        <!-- GPT-4 Verifier Section -->
        <div class="two-col">
            <div class="section-card" style="border-top: 3px solid #10a37f;">
                <div class="section-header">
                    <span class="section-icon">🔍</span>
                    <span class="section-title">GPT-4 Meta-Rules</span>
                    <span class="section-count">%%VERIFIER_META_RULES_COUNT%%</span>
                </div>
                <div class="section-content">
                    %%VERIFIER_META_RULES_HTML%%
                </div>
            </div>
            
            <div class="section-card" style="border-top: 3px solid #10a37f;">
                <div class="section-header">
                    <span class="section-icon">💡</span>
                    <span class="section-title">GPT-4 Learnings</span>
                    <span class="section-count">%%VERIFIER_LEARNINGS_COUNT%%</span>
                </div>
                <div class="section-content">
                    %%VERIFIER_LEARNINGS_HTML%%
                </div>
            </div>
        </div>
        
        <!-- Consensus Analysis -->
        <div class="section-card" style="margin-bottom: 24px;">
            <div class="section-header">
                <span class="section-icon">🎯</span>
                <span class="section-title">Consensus Analysis</span>
            </div>
            <div class="section-content">
                <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; text-align: center;">
                    <div>
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">Agreed</div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 600; color: var(--success);">%%CONSENSUS_AGREED%%</div>
                        <div style="font-size: 10px; color: var(--text-muted);">%%CONSENSUS_WIN_RATE%% win</div>
                    </div>
                    <div>
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">Disagreed</div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 600; color: var(--warning);">%%CONSENSUS_DISAGREED%%</div>
                        <div style="font-size: 10px; color: var(--text-muted);">split</div>
                    </div>
                    <div>
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">Catches</div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 600; color: #10a37f;">%%CONSENSUS_CATCHES%%</div>
                        <div style="font-size: 10px; color: var(--text-muted);">GPT saved</div>
                    </div>
                    <div>
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">False Alarms</div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 600; color: var(--error);">%%CONSENSUS_FALSE_ALARMS%%</div>
                        <div style="font-size: 10px; color: var(--text-muted);">GPT wrong</div>
                    </div>
                    <div>
                        <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase;">Blind Spots</div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 600; color: var(--text-secondary);">%%CONSENSUS_BLIND_SPOTS%%</div>
                        <div style="font-size: 10px; color: var(--text-muted);">both wrong</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Predictions Table -->
        <div class="predictions-section">
            <div class="section-header">
                <span class="section-icon">📊</span>
                <span class="section-title">Prediction History</span>
            </div>
            <table class="predictions-table">
                <colgroup>
                    <col style="width: 12%%;">
                    <col style="width: 25%%;">
                    <col style="width: 15%%;">
                    <col style="width: 20%%;">
                    <col style="width: 13%%;">
                    <col style="width: 15%%;">
                </colgroup>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Prediction</th>
                        <th>Confidence</th>
                        <th>Actual</th>
                        <th>Result</th>
                        <th>Error</th>
                    </tr>
                </thead>
                <tbody>
                    %%PREDICTIONS_HTML%%
                </tbody>
            </table>
        </div>
        
        <footer>
            ◉ Recursive • Learning Intelligence
        </footer>
    </div>
</body>
</html>"""

def get_stats(db_path=None):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    stats = {}
    cursor.execute("SELECT COUNT(*) FROM predictions")
    stats['total'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE direction_correct = 1")
    stats['correct'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE resolved_at IS NOT NULL")
    stats['resolved'] = cursor.fetchone()[0]
    
    stats['accuracy'] = (stats['correct'] / stats['resolved'] * 100) if stats['resolved'] > 0 else 0
    stats['accuracy_class'] = 'success' if stats['accuracy'] >= 55 else 'error' if stats['accuracy'] < 45 else 'warning'
    
    cursor.execute("SELECT AVG(target_error_pct) FROM predictions WHERE resolved_at IS NOT NULL")
    stats['avg_error'] = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT AVG(calibration_score) FROM predictions WHERE resolved_at IS NOT NULL")
    stats['calibration'] = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE is_extreme = 1")
    stats['extremes'] = cursor.fetchone()[0]
    
    # Meta rules count and next analysis
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta_learnings'")
    if cursor.fetchone():
        cursor.execute("SELECT COUNT(*) FROM meta_learnings WHERE is_active = 1")
        stats['meta_rules'] = cursor.fetchone()[0]
        cursor.execute("SELECT MAX(predictions_analyzed) FROM meta_learnings")
        last_analyzed = cursor.fetchone()[0] or 0
        stats['next_meta'] = max(0, (last_analyzed + 100) - stats['total'])
    else:
        stats['meta_rules'] = 0
        stats['next_meta'] = 100 - stats['total'] if stats['total'] < 100 else 0
    
    conn.close()
    return stats

def get_current_prediction(db_path=None):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM predictions 
        ORDER BY timestamp DESC 
        LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_recent_predictions(db_path=None, limit=15):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM predictions 
        WHERE resolved_at IS NOT NULL
        ORDER BY timestamp DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_recent_streak(db_path=None, limit=20):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT direction_correct, resolved_at FROM predictions 
        ORDER BY timestamp DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_learnings(db_path=None, limit=5):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT extreme_reason, learning_extracted FROM predictions 
        WHERE is_extreme = 1 AND learning_extracted IS NOT NULL
        ORDER BY timestamp DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_meta_rules(db_path=None, limit=5):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta_learnings'")
    if not cursor.fetchone():
        conn.close()
        return []
    
    cursor.execute("""
        SELECT * FROM meta_learnings 
        WHERE is_active = 1
        ORDER BY confidence_score DESC, timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_verifier_stats(db_path):
    """Get GPT-4 verifier statistics."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    stats = {'total': 0, 'resolved': 0, 'correct': 0, 'accuracy': 0, 'catches': 0, 'false_alarms': 0}
    
    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verifier_predictions'")
    if not cursor.fetchone():
        conn.close()
        return stats
    
    cursor.execute("SELECT COUNT(*) FROM verifier_predictions")
    stats['total'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM verifier_predictions WHERE resolved_at IS NOT NULL")
    stats['resolved'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM verifier_predictions WHERE gpt_was_correct = 1")
    stats['correct'] = cursor.fetchone()[0]
    
    stats['accuracy'] = (stats['correct'] / stats['resolved'] * 100) if stats['resolved'] > 0 else 0
    
    # Catches: GPT disagreed and was right (Claude was wrong)
    cursor.execute("""
        SELECT COUNT(*) FROM verifier_predictions vp
        JOIN predictions p ON vp.prediction_id = p.id
        WHERE vp.agrees_with_claude = 0 AND p.direction_correct = 0
    """)
    stats['catches'] = cursor.fetchone()[0]
    
    # False alarms: GPT disagreed but was wrong (Claude was right)
    cursor.execute("""
        SELECT COUNT(*) FROM verifier_predictions vp
        JOIN predictions p ON vp.prediction_id = p.id
        WHERE vp.agrees_with_claude = 0 AND p.direction_correct = 1
    """)
    stats['false_alarms'] = cursor.fetchone()[0]
    
    conn.close()
    return stats


def get_verifier_meta_rules(db_path, limit=5):
    """Get GPT-4 verifier meta-rules."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verifier_meta_learnings'")
    if not cursor.fetchone():
        conn.close()
        return []
    
    cursor.execute("""
        SELECT * FROM verifier_meta_learnings 
        WHERE is_active = 1
        ORDER BY confidence_score DESC, timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_verifier_learnings(db_path, limit=5):
    """Get recent verifier learnings (extreme cases with extracted learnings)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verifier_predictions'")
    if not cursor.fetchone():
        conn.close()
        return []
    
    cursor.execute("""
        SELECT vp.*, p.direction_correct as claude_correct
        FROM verifier_predictions vp
        JOIN predictions p ON vp.prediction_id = p.id
        WHERE vp.is_extreme = 1 AND vp.learning_extracted IS NOT NULL
        ORDER BY vp.timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_verifier_learnings_count(db_path):
    """Get count of verifier learnings."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verifier_predictions'")
    if not cursor.fetchone():
        conn.close()
        return 0
    
    cursor.execute("SELECT COUNT(*) FROM verifier_predictions WHERE is_extreme = 1 AND learning_extracted IS NOT NULL")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_consensus_stats(db_path):
    """Get consensus outcome statistics."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    stats = {'agreed': 0, 'disagreed': 0, 'agreed_win_rate': 0, 'catches': 0, 'false_alarms': 0, 'blind_spots': 0}
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='consensus_outcomes'")
    if not cursor.fetchone():
        conn.close()
        return stats
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 1")
    stats['agreed'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 0")
    stats['disagreed'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 1 AND claude_correct = 1")
    agreed_wins = cursor.fetchone()[0]
    stats['agreed_win_rate'] = (agreed_wins / stats['agreed'] * 100) if stats['agreed'] > 0 else 0
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'gpt_caught_error'")
    stats['catches'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'gpt_false_alarm'")
    stats['false_alarms'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'shared_blind_spot'")
    stats['blind_spots'] = cursor.fetchone()[0]
    
    conn.close()
    return stats


def get_current_verifier_prediction(db_path, prediction_id):
    """Get the verifier prediction for a given prediction."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verifier_predictions'")
    if not cursor.fetchone():
        conn.close()
        return None
    
    cursor.execute("""
        SELECT * FROM verifier_predictions WHERE prediction_id = ?
    """, (prediction_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def render_current_prediction(pred, verifier_pred=None, interval_mins=5):
    if not pred:
        return '<div class="current-prediction"><div class="no-prediction">No predictions yet. Start the predictor to begin.</div></div>', 0, True
    
    direction = pred['predicted_direction']
    dir_class = 'up' if direction == 'UP' else 'down'
    arrow = '↑' if direction == 'UP' else '↓'
    
    # Calculate timestamp for JS
    is_resolved = pred['resolved_at'] is not None
    try:
        pred_time = datetime.fromisoformat(pred['timestamp'].replace('Z', '+00:00'))
        timestamp_unix = pred_time.timestamp()
    except:
        timestamp_unix = 0
    
    # Calculate time remaining if not resolved
    interval_secs = interval_mins * 60
    if not is_resolved:
        try:
            now = datetime.now(timezone.utc)
            elapsed = (now - pred_time).total_seconds()
            remaining = max(0, interval_secs - elapsed)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            time_str = f"{mins}:{secs:02d} remaining"
            progress = min(100, (elapsed / interval_secs) * 100)
        except:
            time_str = "Waiting..."
            progress = 50
        status = "WAITING FOR RESOLUTION"
        status_icon = "⏳"
    else:
        time_str = "✓ resolved"
        progress = 100
        status = "RESOLVED"
        status_icon = "✓"
    
    reasoning = pred.get('reasoning', '')
    reasoning_escaped = reasoning.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') if reasoning else 'No reasoning recorded'
    reasoning_short = reasoning_escaped[:150] + '...' if len(reasoning_escaped) > 150 else reasoning_escaped
    
    # GPT-4 Verification HTML
    verification_html = ""
    consensus_html = ""
    
    if verifier_pred:
        agrees = verifier_pred.get('agrees_with_claude', True)
        confidence = verifier_pred.get('confidence_claude_correct', 50)
        v_reasoning = verifier_pred.get('reasoning', '')
        concerns = verifier_pred.get('concerns', [])
        
        if isinstance(concerns, str):
            try:
                concerns = json.loads(concerns)
            except:
                concerns = []
        
        verdict_class = 'agrees' if agrees else 'disagrees'
        verdict_text = '✓ AGREES' if agrees else '✗ DISAGREES'
        
        v_reasoning_escaped = v_reasoning.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') if v_reasoning else ''
        v_reasoning_short = v_reasoning_escaped[:100] + '...' if len(v_reasoning_escaped) > 100 else v_reasoning_escaped
        
        concerns_html = ""
        if concerns:
            concerns_items = "".join([f"<li>{c}</li>" for c in concerns[:3]])
            concerns_html = f'<ul class="verification-concerns">{concerns_items}</ul>'
        
        verification_html = f"""
        <div class="verification-inline">
            <div class="verification-header">
                <span style="font-size: 12px; color: #10a37f; font-weight: 600;">🔍 GPT-4 VERIFICATION</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                <span class="verification-verdict {verdict_class}">{verdict_text}</span>
                <span class="verification-confidence">{confidence}%</span>
            </div>
            <div class="verification-reasoning">{v_reasoning_short}</div>
            {concerns_html}
        </div>
        """
        
        # Consensus signal
        if agrees:
            if confidence >= 70:
                consensus_class = "strong"
                consensus_name = "CONSENSUS STRONG"
            else:
                consensus_class = "weak"
                consensus_name = "CONSENSUS WEAK"
        else:
            if confidence <= 30:
                consensus_class = "veto"
                consensus_name = "VERIFIER VETO"
            else:
                consensus_class = "disagreement"
                consensus_name = "DISAGREEMENT"
        
        consensus_html = f"""
        <div class="consensus-inline {consensus_class}">
            <div class="consensus-type">CONSENSUS SIGNAL</div>
            <div class="consensus-name">{consensus_name}</div>
        </div>
        """
    
    html = f"""
    <div class="current-prediction">
        <div class="current-prediction-header">
            <span class="current-prediction-label">🔮 CURRENT PREDICTION • {status}</span>
            <span class="current-prediction-time" id="time-remaining">{time_str}</span>
        </div>
        <div class="current-prediction-main">
            <div class="direction-badge {dir_class}">
                <span class="arrow">{arrow}</span>
                {direction}
            </div>
            <div class="prediction-target">${pred['predicted_target']:,.2f}</div>
            <div class="prediction-confidence">
                <div class="confidence-value">{pred['confidence']}%</div>
                <div class="confidence-label">confidence</div>
            </div>
        </div>
        <div class="progress-bar">
            <div class="progress-fill" id="progress-fill" style="width: {progress}%"></div>
        </div>
        <div class="prediction-reasoning expandable-card" onclick="toggleExpand('current-reasoning')">
            <div class="expandable-short" id="current-reasoning-short">{reasoning_short} <span class="expand-hint">Click to expand</span></div>
            <div class="expandable-full" id="current-reasoning-full" style="display: none;">{reasoning_escaped} <span class="expand-hint">Click to collapse</span></div>
        </div>
        {verification_html}
        {consensus_html}
    </div>
    """
    return html, timestamp_unix, is_resolved

def render_streak(results):
    items = []
    for r in reversed(results):  # Oldest first visually
        if r['resolved_at'] is None:
            items.append('<div class="streak-item pending">?</div>')
        elif r['direction_correct']:
            items.append('<div class="streak-item win">W</div>')
        else:
            items.append('<div class="streak-item loss">L</div>')
    
    if not items:
        return '<div class="empty-state">No results yet</div>'
    
    return ''.join(items)

def render_predictions(predictions):
    rows = []
    for idx, p in enumerate(predictions):
        pred_id = p.get('id', 0)
        result_class = 'result-correct' if p['direction_correct'] else 'result-wrong'
        result = '✓' if p['direction_correct'] else '✗'
        
        dir_class = 'up' if p['predicted_direction'] == 'UP' else 'down'
        dir_arrow = '↑' if p['predicted_direction'] == 'UP' else '↓'
        
        time_short = p['timestamp'][11:16] if p['timestamp'] else '—'
        
        # Get reasoning and escape HTML
        reasoning = p.get('reasoning', '') or 'No reasoning recorded'
        reasoning = reasoning.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # Calculate price change
        price_change = p['actual_price'] - p['current_price']
        price_change_pct = (price_change / p['current_price']) * 100
        change_class = 'success' if price_change >= 0 else 'error'
        
        # Check if extreme
        is_extreme = p.get('is_extreme', False)
        extreme_badge = '<span class="mini-badge" style="background: var(--purple-bg); color: var(--purple); margin-left: 8px;">EXTREME</span>' if is_extreme else ''
        
        # First 2 rows expanded by default
        expanded_class = ' visible' if idx < 2 else ''
        
        rows.append(f"""
            <tr class="prediction-row" onclick="toggleReasoning({pred_id})">
                <td><span class="mono">{time_short}</span></td>
                <td><span class="mini-badge {dir_class}">{dir_arrow} {p['predicted_direction']}</span> <span class="mono">${p['predicted_target']:,.2f}</span></td>
                <td><span class="mono">{p['confidence']}%</span></td>
                <td><span class="mono">${p['actual_price']:,.2f}</span></td>
                <td><span class="{result_class}">{result}</span>{extreme_badge}</td>
                <td><span class="mono">{p['target_error_pct']:.2f}%</span> <span class="expand-hint row-hint">↓</span></td>
            </tr>
            <tr class="reasoning-row{expanded_class}" id="reasoning-{pred_id}">
                <td colspan="6">
                    <div class="reasoning-box">
                        <div class="reasoning-box-header">
                            <div class="reasoning-box-stat"><span>Entry:</span> <strong>${p['current_price']:,.2f}</strong></div>
                            <div class="reasoning-box-stat"><span>Target:</span> <strong>${p['predicted_target']:,.2f}</strong></div>
                            <div class="reasoning-box-stat"><span>Actual:</span> <strong>${p['actual_price']:,.2f}</strong></div>
                            <div class="reasoning-box-stat"><span>Move:</span> <strong style="color: var(--{change_class})">{price_change_pct:+.3f}%</strong></div>
                            <div class="reasoning-box-stat"><span>Calibration:</span> <strong>{p.get('calibration_score', 0):.2f}</strong></div>
                        </div>
                        <div class="reasoning-label">LLM Analysis</div>
                        <div class="reasoning-text">{reasoning}</div>
                    </div>
                </td>
            </tr>
        """)
    
    if not rows:
        return '<tr><td colspan="6" class="empty-state">No resolved predictions yet</td></tr>'
    
    return '\n'.join(rows)

def render_learnings(learnings):
    if not learnings:
        return '<div class="empty-state">Learnings appear after batch analysis</div>'
    
    items = []
    for idx, l in enumerate(learnings):
        learning_id = idx
        reason = (l['extreme_reason'] or 'Unknown')
        text = l['learning_extracted'] or ''
        
        # Escape HTML
        reason = reason.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # Truncated version
        text_short = text[:100] + '...' if len(text) > 100 else text
        
        items.append(f"""
            <div class="learning expandable-card" onclick="toggleExpand('learning-{learning_id}')">
                <div class="learning-reason">{reason}</div>
                <div class="expandable-short" id="learning-{learning_id}-short">{text_short} <span class="expand-hint">↓ expand</span></div>
                <div class="expandable-full" id="learning-{learning_id}-full" style="display: none;">{text} <span class="expand-hint">↑ collapse</span></div>
            </div>
        """)
    return '\n'.join(items)

def render_meta_rules(meta_rules):
    if not meta_rules:
        return '<div class="empty-state">Run <code>python3 predictor.py meta</code> to generate</div>'
    
    items = []
    for idx, rule in enumerate(meta_rules):
        rule_id = rule.get('id', idx)
        pattern_type = rule.get('pattern_type', 'unknown')
        pattern_desc = rule.get('pattern_description', '')
        meta_rule = rule.get('meta_rule', '')
        confidence = rule.get('confidence_score', 0)
        
        # Escape HTML
        pattern_desc = pattern_desc.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        meta_rule = meta_rule.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # Truncated version
        meta_rule_short = meta_rule[:100] + '...' if len(meta_rule) > 100 else meta_rule
        
        items.append(f"""
            <div class="meta-rule expandable-card" onclick="toggleExpand('meta-{rule_id}')">
                <div class="meta-rule-header">
                    <span class="meta-rule-type">{pattern_type}</span>
                    <span class="meta-rule-confidence">{confidence:.0%}</span>
                </div>
                <div class="expandable-short" id="meta-{rule_id}-short">{meta_rule_short} <span class="expand-hint">↓ expand</span></div>
                <div class="expandable-full" id="meta-{rule_id}-full" style="display: none;">
                    <div class="expandable-section">
                        <div class="expandable-label">Rule</div>
                        <div>{meta_rule}</div>
                    </div>
                    <div class="expandable-section">
                        <div class="expandable-label">Pattern</div>
                        <div>{pattern_desc}</div>
                    </div>
                    <span class="expand-hint">↑ collapse</span>
                </div>
            </div>
        """)
    return '\n'.join(items)


def render_verifier_meta_rules(meta_rules):
    """Render GPT-4 verifier meta-rules."""
    if not meta_rules:
        return '<div class="empty-state" style="font-size: 11px; color: var(--text-muted);">Verifier meta-rules will appear after sufficient data</div>'
    
    items = []
    for idx, rule in enumerate(meta_rules):
        rule_id = rule.get('id', idx)
        pattern_type = rule.get('pattern_type', 'unknown')
        meta_rule = rule.get('meta_rule', '')
        pattern_desc = rule.get('pattern_description', '')
        confidence = rule.get('confidence_score', 0)
        
        # Escape HTML
        meta_rule = meta_rule.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        pattern_desc = pattern_desc.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        meta_rule_short = meta_rule[:100] + '...' if len(meta_rule) > 100 else meta_rule
        
        items.append(f"""
            <div class="verifier-meta-rule expandable-card" onclick="toggleExpand('vmeta-{rule_id}')">
                <div class="verifier-meta-rule-header">
                    <span class="verifier-meta-rule-type">{pattern_type}</span>
                    <span class="verifier-meta-rule-confidence">{confidence:.0%}</span>
                </div>
                <div class="expandable-short" id="vmeta-{rule_id}-short">{meta_rule_short} <span class="expand-hint">↓ expand</span></div>
                <div class="expandable-full" id="vmeta-{rule_id}-full" style="display: none;">
                    <div class="expandable-section">
                        <div class="expandable-label">Rule</div>
                        <div>{meta_rule}</div>
                    </div>
                    <div class="expandable-section">
                        <div class="expandable-label">Pattern</div>
                        <div>{pattern_desc}</div>
                    </div>
                    <span class="expand-hint">↑ collapse</span>
                </div>
            </div>
        """)
    return '\n'.join(items)


def render_verifier_learnings(learnings):
    """Render GPT-4 verifier learnings."""
    if not learnings:
        return '<div class="empty-state" style="font-size: 11px; color: var(--text-muted);">GPT-4 learnings will appear after ~8 predictions resolve</div>'
    
    items = []
    for idx, l in enumerate(learnings):
        learning_id = f"vl{idx}"
        extreme_reason = l.get('extreme_reason', 'Unknown')
        learning = l.get('learning_extracted', '')
        
        # Determine context type
        if 'caught' in extreme_reason.lower():
            context_class = 'caught'
            context_text = 'CAUGHT ERROR'
        elif 'false alarm' in extreme_reason.lower():
            context_class = 'false-alarm'
            context_text = 'FALSE ALARM'
        else:
            context_class = 'blind-spot'
            context_text = extreme_reason[:30].upper()
        
        # Escape HTML
        learning = learning.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        learning_short = learning[:100] + '...' if len(learning) > 100 else learning
        
        items.append(f"""
            <div class="learning-card verifier expandable-card" onclick="toggleExpand('{learning_id}')">
                <div class="learning-context {context_class}">{context_text}</div>
                <div class="expandable-short" id="{learning_id}-short">
                    {learning_short} <span class="expand-hint">↓</span>
                </div>
                <div class="expandable-full" id="{learning_id}-full" style="display: none;">
                    <div class="expandable-section">
                        <div class="expandable-label">Learning</div>
                        <div>{learning}</div>
                    </div>
                    <div class="expandable-section">
                        <div class="expandable-label">Context</div>
                        <div>{extreme_reason}</div>
                    </div>
                    <span class="expand-hint">↑</span>
                </div>
            </div>
        """)
    return '\n'.join(items)


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        if 'favicon' not in args[0]:
            SimpleHTTPRequestHandler.log_message(self, format, *args)
    
    def do_GET(self):
        # Parse URL
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        
        # Get timeframe from query string
        timeframe = query.get('tf', [DEFAULT_TIMEFRAME])[0]
        if timeframe not in TIMEFRAMES:
            timeframe = DEFAULT_TIMEFRAME
        
        tf_config = TIMEFRAMES[timeframe]
        db_path = get_db_path(timeframe)
        
        if path == '/' or path == '/index.html' or path.startswith('/?'):
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            stats = get_stats(db_path)
            current = get_current_prediction(db_path)
            predictions = get_recent_predictions(db_path)
            streak = get_recent_streak(db_path)
            learnings = get_learnings(db_path)
            meta_rules = get_meta_rules(db_path)
            
            # Verifier data
            verifier_stats = get_verifier_stats(db_path)
            verifier_meta_rules = get_verifier_meta_rules(db_path)
            verifier_learnings = get_verifier_learnings(db_path)
            verifier_learnings_count = get_verifier_learnings_count(db_path)
            consensus_stats = get_consensus_stats(db_path)
            
            # Get verifier prediction for current prediction
            verifier_pred = None
            if current and current.get('id'):
                verifier_pred = get_current_verifier_prediction(db_path, current['id'])
            
            current_html, pred_timestamp, is_resolved = render_current_prediction(
                current, verifier_pred, tf_config['interval']
            )
            
            html = get_html_template(timeframe)
            html = html.replace('%%PREDICTION_TIMESTAMP%%', str(pred_timestamp))
            html = html.replace('%%IS_RESOLVED%%', 'true' if is_resolved else 'false')
            html = html.replace('%%TOTAL%%', str(stats['total']))
            html = html.replace('%%CORRECT%%', str(stats['correct']))
            html = html.replace('%%RESOLVED%%', str(stats['resolved']))
            html = html.replace('%%ACCURACY_CLASS%%', stats['accuracy_class'])
            html = html.replace('%%ACCURACY%%', f"{stats['accuracy']:.1f}%")
            html = html.replace('%%CALIBRATION%%', f"{stats['calibration']:.2f}")
            html = html.replace('%%EXTREMES%%', str(stats['extremes']))
            html = html.replace('%%META_RULES%%', str(stats['meta_rules']))
            html = html.replace('%%NEXT_META%%', f"in {stats['next_meta']} preds" if stats['next_meta'] > 0 else "ready")
            html = html.replace('%%CURRENT_PREDICTION_HTML%%', current_html)
            html = html.replace('%%STREAK_HTML%%', render_streak(streak))
            html = html.replace('%%PREDICTIONS_HTML%%', render_predictions(predictions))
            html = html.replace('%%LEARNINGS_HTML%%', render_learnings(learnings))
            html = html.replace('%%META_RULES_HTML%%', render_meta_rules(meta_rules))
            
            # Verifier replacements
            html = html.replace('%%VERIFIER_ACCURACY%%', f"{verifier_stats['accuracy']:.1f}%")
            html = html.replace('%%VERIFIER_CORRECT%%', str(verifier_stats['correct']))
            html = html.replace('%%VERIFIER_TOTAL%%', str(verifier_stats['resolved']))
            html = html.replace('%%VERIFIER_CATCHES%%', str(verifier_stats['catches']))
            html = html.replace('%%VERIFIER_FALSE_ALARMS%%', str(verifier_stats['false_alarms']))
            html = html.replace('%%VERIFIER_META_RULES_COUNT%%', str(len(verifier_meta_rules)))
            html = html.replace('%%VERIFIER_META_RULES_HTML%%', render_verifier_meta_rules(verifier_meta_rules))
            html = html.replace('%%VERIFIER_LEARNINGS_COUNT%%', str(verifier_learnings_count))
            html = html.replace('%%VERIFIER_LEARNINGS_HTML%%', render_verifier_learnings(verifier_learnings))
            
            # Consensus replacements
            html = html.replace('%%CONSENSUS_WIN_RATE%%', f"{consensus_stats['agreed_win_rate']:.1f}%")
            html = html.replace('%%CONSENSUS_AGREED%%', str(consensus_stats['agreed']))
            html = html.replace('%%CONSENSUS_DISAGREED%%', str(consensus_stats['disagreed']))
            html = html.replace('%%CONSENSUS_CATCHES%%', str(consensus_stats['catches']))
            html = html.replace('%%CONSENSUS_FALSE_ALARMS%%', str(consensus_stats['false_alarms']))
            html = html.replace('%%CONSENSUS_BLIND_SPOTS%%', str(consensus_stats['blind_spots']))
            
            # Timeframe replacements
            html = html.replace('%%TIMEFRAME_LABEL%%', tf_config['label'])
            html = html.replace('%%TAB_5_ACTIVE%%', 'active' if timeframe == '5' else '')
            html = html.replace('%%TAB_15_ACTIVE%%', 'active' if timeframe == '15' else '')
            html = html.replace('%%TAB_60_ACTIVE%%', 'active' if timeframe == '60' else '')
            
            # Update prediction duration for JS
            html = html.replace('const predictionDuration = 300;', f'const predictionDuration = {tf_config["interval"] * 60};')
            
            self.wfile.write(html.encode('utf-8'))
        elif self.path == '/api/stats':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(get_stats()).encode())
        elif self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)
    
    def do_POST(self):
        """Handle POST requests - API endpoints for external predictions."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/api/prediction':
            # Authenticate
            auth_header = self.headers.get('Authorization', '')
            if API_KEY and not auth_header.startswith('Bearer '):
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing Authorization header'}).encode())
                return
            
            provided_key = auth_header.replace('Bearer ', '') if auth_header else ''
            if API_KEY and provided_key != API_KEY:
                self.send_response(403)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid API key'}).encode())
                return
            
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
                return
            
            # Validate required fields
            required = ['current_price', 'direction', 'target', 'confidence']
            missing = [f for f in required if f not in data]
            if missing:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Missing fields: {missing}'}).encode())
                return
            
            # Get timeframe from data or default to 5min
            timeframe = data.get('timeframe', '5')
            if timeframe not in TIMEFRAMES:
                timeframe = DEFAULT_TIMEFRAME
            
            db_path = get_db_path(timeframe)
            
            try:
                pred_id = save_external_prediction(db_path, data)
                print(f"📥 Received prediction from local LLM: {data['direction']} ${data['target']:,.2f} ({data['confidence']}%)")
                
                self.send_response(201)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'prediction_id': pred_id,
                    'timeframe': timeframe
                }).encode())
            except Exception as e:
                print(f"❌ Error saving prediction: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        
        elif path == '/api/resolve':
            # Endpoint to resolve a prediction with actual price
            auth_header = self.headers.get('Authorization', '')
            provided_key = auth_header.replace('Bearer ', '') if auth_header else ''
            if API_KEY and provided_key != API_KEY:
                self.send_response(403)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid API key'}).encode())
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
                return
            
            required = ['prediction_id', 'actual_price']
            missing = [f for f in required if f not in data]
            if missing:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Missing fields: {missing}'}).encode())
                return
            
            timeframe = data.get('timeframe', '5')
            db_path = get_db_path(timeframe)
            
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Get the prediction
                cursor.execute("SELECT * FROM predictions WHERE id = ?", (data['prediction_id'],))
                row = cursor.fetchone()
                
                if not row:
                    conn.close()
                    self.send_response(404)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Prediction not found'}).encode())
                    return
                
                # Get column names
                cursor.execute("PRAGMA table_info(predictions)")
                columns = {col[1]: idx for idx, col in enumerate(cursor.fetchall())}
                
                current_price = row[columns['current_price']]
                predicted_direction = row[columns['predicted_direction']]
                predicted_target = row[columns['predicted_target']]
                confidence = row[columns['confidence']]
                actual_price = data['actual_price']
                
                actual_direction = 'UP' if actual_price > current_price else 'DOWN'
                direction_correct = predicted_direction == actual_direction
                target_error_pct = abs(actual_price - predicted_target) / current_price * 100
                calibration_score = confidence / 100 if direction_correct else (100 - confidence) / 100
                
                cursor.execute("""
                    UPDATE predictions SET
                        resolved_at = ?,
                        actual_price = ?,
                        actual_direction = ?,
                        direction_correct = ?,
                        target_error_pct = ?,
                        calibration_score = ?
                    WHERE id = ?
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    actual_price,
                    actual_direction,
                    direction_correct,
                    target_error_pct,
                    calibration_score,
                    data['prediction_id']
                ))
                conn.commit()
                conn.close()
                
                result_str = '✓ Correct' if direction_correct else '✗ Wrong'
                print(f"📊 Resolved prediction {data['prediction_id']}: {result_str} (error: {target_error_pct:.2f}%)")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'direction_correct': direction_correct,
                    'target_error_pct': target_error_pct,
                    'calibration_score': calibration_score
                }).encode())
            except Exception as e:
                print(f"❌ Error resolving prediction: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        else:
            self.send_error(404)

def run_server():
    # Check for at least one database
    found_db = False
    for tf, config in TIMEFRAMES.items():
        db_path = get_db_path(tf)
        if os.path.exists(db_path):
            found_db = True
            break
    
    if not found_db:
        print("No database found. Run predictor.py first to create one.")
        print("Expected databases: predictions_5min.db, predictions_15min.db, predictions_1h.db")
        return
    
    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print(f"\n  🐍 Recursive Dashboard")
    print(f"  http://localhost:{PORT}")
    print(f"\n  Timeframes: 5M, 15M, 1H")
    print(f"  Press Ctrl+C to stop\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping dashboard...")
        server.shutdown()

if __name__ == "__main__":
    run_server()

