#!/usr/bin/env python3
"""
Recursive Learning BTC Predictor with Meta-Learning
Paper trading Bitcoin predictions with learning from extremes + meta-analysis.
"""

import os
import json
import sqlite3
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List
import requests
from anthropic import Anthropic
from openai import OpenAI

# Configuration
PREDICTION_INTERVAL_MINS = 5
BATCH_SIZE = 20
EXTREME_PERCENTILE = 10
CONTEXT_EXAMPLES = 10
META_LEARNING_INTERVAL = 5  # Analyze meta-patterns every N batches (100 predictions)
DB_PATH = "predictions.db"

@dataclass
class Prediction:
    id: Optional[int]
    timestamp: str
    current_price: float
    predicted_direction: str
    predicted_target: float
    confidence: int
    reasoning: str
    resolved_at: Optional[str] = None
    actual_price: Optional[float] = None
    actual_direction: Optional[str] = None
    direction_correct: Optional[bool] = None
    target_error_pct: Optional[float] = None
    calibration_score: Optional[float] = None
    is_extreme: Optional[bool] = None
    extreme_reason: Optional[str] = None
    learning_extracted: Optional[str] = None


@dataclass
class VerifierPrediction:
    id: Optional[int]
    prediction_id: int
    timestamp: str
    agrees_with_claude: bool
    confidence_claude_correct: int
    reasoning: str
    concerns: List[str]
    meta_rule_violations: List[str]
    resolved_at: Optional[str] = None
    gpt_was_correct: Optional[bool] = None
    is_extreme: Optional[bool] = None
    extreme_reason: Optional[str] = None
    learning_extracted: Optional[str] = None


# ===== VERIFIER SYSTEM CONFIG =====
VERIFIER_ENABLED = os.environ.get("VERIFIER_ENABLED", "true").lower() == "true"
VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "gpt-4o")
VERIFIER_BATCH_SIZE = 8  # Smaller batch for verifier learning


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
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
                learning_extracted TEXT
            )
        """)
        
        # Meta-learning table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                predictions_analyzed INTEGER,
                learnings_analyzed INTEGER,
                accuracy_at_analysis REAL,
                pattern_type TEXT,
                pattern_description TEXT,
                meta_rule TEXT,
                confidence_score REAL,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        
        # Performance tracking for meta-rules
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_rule_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_learning_id INTEGER,
                timestamp TEXT NOT NULL,
                predictions_since INTEGER,
                accuracy_before REAL,
                accuracy_after REAL,
                improvement REAL,
                FOREIGN KEY (meta_learning_id) REFERENCES meta_learnings(id)
            )
        """)
        
        # Verifier predictions (GPT-4 verifying Claude)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verifier_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                agrees_with_claude BOOLEAN NOT NULL,
                confidence_claude_correct INTEGER NOT NULL,
                reasoning TEXT,
                concerns TEXT,
                meta_rule_violations TEXT,
                resolved_at TEXT,
                gpt_was_correct BOOLEAN,
                is_extreme BOOLEAN,
                extreme_reason TEXT,
                learning_extracted TEXT,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id)
            )
        """)
        
        # Verifier meta-learnings (GPT-4's own meta-rules)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verifier_meta_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                predictions_analyzed INTEGER,
                learnings_analyzed INTEGER,
                accuracy_at_analysis REAL,
                pattern_type TEXT,
                pattern_description TEXT,
                meta_rule TEXT,
                confidence_score REAL,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        
        # Consensus outcomes tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consensus_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                models_agreed BOOLEAN NOT NULL,
                consensus_direction TEXT,
                consensus_confidence INTEGER,
                claude_correct BOOLEAN,
                gpt_correct BOOLEAN,
                outcome_type TEXT,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    # ===== VERIFIER DATABASE METHODS =====
    
    def save_verifier_prediction(self, vpred: 'VerifierPrediction') -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO verifier_predictions (
                prediction_id, timestamp, agrees_with_claude, confidence_claude_correct,
                reasoning, concerns, meta_rule_violations
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            vpred.prediction_id, vpred.timestamp, vpred.agrees_with_claude,
            vpred.confidence_claude_correct, vpred.reasoning,
            json.dumps(vpred.concerns), json.dumps(vpred.meta_rule_violations)
        ))
        vpred_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return vpred_id
    
    def update_verifier_resolution(self, vpred: 'VerifierPrediction'):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE verifier_predictions SET
                resolved_at = ?,
                gpt_was_correct = ?,
                is_extreme = ?,
                extreme_reason = ?,
                learning_extracted = ?
            WHERE id = ?
        """, (
            vpred.resolved_at, vpred.gpt_was_correct, vpred.is_extreme,
            vpred.extreme_reason, vpred.learning_extracted, vpred.id
        ))
        conn.commit()
        conn.close()
    
    def get_verifier_for_prediction(self, prediction_id: int) -> Optional['VerifierPrediction']:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM verifier_predictions WHERE prediction_id = ?
        """, (prediction_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return VerifierPrediction(
                id=row['id'],
                prediction_id=row['prediction_id'],
                timestamp=row['timestamp'],
                agrees_with_claude=bool(row['agrees_with_claude']),
                confidence_claude_correct=row['confidence_claude_correct'],
                reasoning=row['reasoning'],
                concerns=json.loads(row['concerns']) if row['concerns'] else [],
                meta_rule_violations=json.loads(row['meta_rule_violations']) if row['meta_rule_violations'] else [],
                resolved_at=row['resolved_at'],
                gpt_was_correct=bool(row['gpt_was_correct']) if row['gpt_was_correct'] is not None else None,
                is_extreme=bool(row['is_extreme']) if row['is_extreme'] is not None else None,
                extreme_reason=row['extreme_reason'],
                learning_extracted=row['learning_extracted']
            )
        return None
    
    def get_verifier_extremes(self, limit: int = 10) -> List['VerifierPrediction']:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM verifier_predictions 
            WHERE is_extreme = 1 AND learning_extracted IS NOT NULL
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_verifier_prediction(r) for r in rows]
    
    def get_verifier_recent_for_batch(self, batch_size: int = VERIFIER_BATCH_SIZE) -> List['VerifierPrediction']:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM verifier_predictions 
            WHERE resolved_at IS NOT NULL AND is_extreme IS NULL
            ORDER BY timestamp DESC LIMIT ?
        """, (batch_size,))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_verifier_prediction(r) for r in rows]
    
    def get_verifier_stats(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM verifier_predictions")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM verifier_predictions WHERE resolved_at IS NOT NULL")
        resolved = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM verifier_predictions WHERE gpt_was_correct = 1")
        correct = cursor.fetchone()[0]
        
        # Catches: GPT disagreed and Claude was wrong
        cursor.execute("""
            SELECT COUNT(*) FROM verifier_predictions vp
            JOIN predictions p ON vp.prediction_id = p.id
            WHERE vp.agrees_with_claude = 0 AND p.direction_correct = 0
        """)
        catches = cursor.fetchone()[0]
        
        # False alarms: GPT disagreed but Claude was right
        cursor.execute("""
            SELECT COUNT(*) FROM verifier_predictions vp
            JOIN predictions p ON vp.prediction_id = p.id
            WHERE vp.agrees_with_claude = 0 AND p.direction_correct = 1
        """)
        false_alarms = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "total": total,
            "resolved": resolved,
            "correct": correct,
            "accuracy": (correct / resolved * 100) if resolved > 0 else 0,
            "catches": catches,
            "false_alarms": false_alarms
        }
    
    def save_verifier_meta_learning(self, meta_data: dict) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO verifier_meta_learnings (
                timestamp, predictions_analyzed, learnings_analyzed,
                accuracy_at_analysis, pattern_type, pattern_description,
                meta_rule, confidence_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meta_data['timestamp'], meta_data['predictions_analyzed'],
            meta_data['learnings_analyzed'], meta_data['accuracy_at_analysis'],
            meta_data['pattern_type'], meta_data['pattern_description'],
            meta_data['meta_rule'], meta_data['confidence_score']
        ))
        meta_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return meta_id
    
    def get_verifier_meta_rules(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM verifier_meta_learnings 
            WHERE is_active = 1
            ORDER BY confidence_score DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def save_consensus_outcome(self, outcome: dict):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO consensus_outcomes (
                prediction_id, timestamp, models_agreed, consensus_direction,
                consensus_confidence, claude_correct, gpt_correct, outcome_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            outcome['prediction_id'], outcome['timestamp'], outcome['models_agreed'],
            outcome['consensus_direction'], outcome['consensus_confidence'],
            outcome['claude_correct'], outcome['gpt_correct'], outcome['outcome_type']
        ))
        conn.commit()
        conn.close()
    
    def get_consensus_stats(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 1")
        agreed = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 0")
        disagreed = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE models_agreed = 1 AND claude_correct = 1")
        agreed_wins = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'gpt_caught_error'")
        catches = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'gpt_false_alarm'")
        false_alarms = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM consensus_outcomes WHERE outcome_type = 'shared_blind_spot'")
        blind_spots = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "agreed": agreed,
            "disagreed": disagreed,
            "agreed_win_rate": (agreed_wins / agreed * 100) if agreed > 0 else 0,
            "catches": catches,
            "false_alarms": false_alarms,
            "blind_spots": blind_spots
        }
    
    def _row_to_verifier_prediction(self, row) -> 'VerifierPrediction':
        return VerifierPrediction(
            id=row['id'],
            prediction_id=row['prediction_id'],
            timestamp=row['timestamp'],
            agrees_with_claude=bool(row['agrees_with_claude']),
            confidence_claude_correct=row['confidence_claude_correct'],
            reasoning=row['reasoning'],
            concerns=json.loads(row['concerns']) if row['concerns'] else [],
            meta_rule_violations=json.loads(row['meta_rule_violations']) if row['meta_rule_violations'] else [],
            resolved_at=row['resolved_at'],
            gpt_was_correct=bool(row['gpt_was_correct']) if row['gpt_was_correct'] is not None else None,
            is_extreme=bool(row['is_extreme']) if row['is_extreme'] is not None else None,
            extreme_reason=row['extreme_reason'],
            learning_extracted=row['learning_extracted']
        )
    
    def save_prediction(self, pred: Prediction) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO predictions (
                timestamp, current_price, predicted_direction, predicted_target,
                confidence, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            pred.timestamp, pred.current_price, pred.predicted_direction,
            pred.predicted_target, pred.confidence, pred.reasoning
        ))
        pred_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return pred_id
    
    def update_resolution(self, pred: Prediction):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE predictions SET
                resolved_at = ?,
                actual_price = ?,
                actual_direction = ?,
                direction_correct = ?,
                target_error_pct = ?,
                calibration_score = ?,
                is_extreme = ?,
                extreme_reason = ?,
                learning_extracted = ?
            WHERE id = ?
        """, (
            pred.resolved_at, pred.actual_price, pred.actual_direction,
            pred.direction_correct, pred.target_error_pct, pred.calibration_score,
            pred.is_extreme, pred.extreme_reason, pred.learning_extracted,
            pred.id
        ))
        conn.commit()
        conn.close()
    
    def get_extremes(self, limit: int = CONTEXT_EXAMPLES) -> List[Prediction]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM predictions 
            WHERE is_extreme = 1 AND learning_extracted IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_prediction(r) for r in rows]
    
    def get_all_extremes(self) -> List[Prediction]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM predictions 
            WHERE is_extreme = 1 AND learning_extracted IS NOT NULL
            ORDER BY timestamp ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_prediction(r) for r in rows]
    
    def get_recent_for_batch_analysis(self, batch_size: int = BATCH_SIZE) -> List[Prediction]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM predictions 
            WHERE resolved_at IS NOT NULL AND is_extreme IS NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """, (batch_size,))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_prediction(r) for r in rows]
    
    def get_resolved(self, limit: int = 100) -> List[Prediction]:
        conn = sqlite3.connect(self.db_path)
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
        return [self._row_to_prediction(r) for r in rows]
    
    def get_stats(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM predictions")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM predictions WHERE resolved_at IS NOT NULL")
        resolved = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM predictions WHERE direction_correct = 1")
        correct = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(target_error_pct) FROM predictions WHERE resolved_at IS NOT NULL")
        avg_error = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(calibration_score) FROM predictions WHERE resolved_at IS NOT NULL")
        avg_calibration = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM predictions WHERE is_extreme = 1")
        extremes = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM meta_learnings WHERE is_active = 1")
        meta_rules = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "total_predictions": total,
            "resolved": resolved,
            "correct_direction": correct,
            "accuracy_pct": (correct / resolved * 100) if resolved > 0 else 0,
            "avg_target_error_pct": avg_error or 0,
            "avg_calibration": avg_calibration or 0,
            "extremes_captured": extremes,
            "active_meta_rules": meta_rules
        }
    
    def get_accuracy_for_range(self, start_id: int, end_id: int) -> float:
        """Get accuracy for a specific range of predictions."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN direction_correct = 1 THEN 1 ELSE 0 END) as correct
            FROM predictions 
            WHERE id BETWEEN ? AND ? AND resolved_at IS NOT NULL
        """, (start_id, end_id))
        row = cursor.fetchone()
        conn.close()
        
        if row[0] == 0:
            return 0
        return (row[1] / row[0]) * 100
    
    def save_meta_learning(self, meta_data: dict) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO meta_learnings (
                timestamp, predictions_analyzed, learnings_analyzed,
                accuracy_at_analysis, pattern_type, pattern_description,
                meta_rule, confidence_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meta_data['timestamp'],
            meta_data['predictions_analyzed'],
            meta_data['learnings_analyzed'],
            meta_data['accuracy_at_analysis'],
            meta_data['pattern_type'],
            meta_data['pattern_description'],
            meta_data['meta_rule'],
            meta_data['confidence_score']
        ))
        meta_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return meta_id
    
    def get_active_meta_rules(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM meta_learnings 
            WHERE is_active = 1
            ORDER BY confidence_score DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_last_meta_analysis_count(self) -> int:
        """Get the prediction count at last meta-analysis."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT predictions_analyzed FROM meta_learnings 
            ORDER BY timestamp DESC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else 0
    
    def export_to_json(self, filepath: str = "predictions_export.json"):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM predictions ORDER BY timestamp ASC")
        rows = cursor.fetchall()
        conn.close()
        
        data = [dict(row) for row in rows]
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        return len(data)
    
    def export_to_csv(self, filepath: str = "predictions_export.csv"):
        import csv
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM predictions ORDER BY timestamp ASC")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return 0
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        return len(rows)
    
    def _row_to_prediction(self, row) -> Prediction:
        return Prediction(
            id=row['id'],
            timestamp=row['timestamp'],
            current_price=row['current_price'],
            predicted_direction=row['predicted_direction'],
            predicted_target=row['predicted_target'],
            confidence=row['confidence'],
            reasoning=row['reasoning'],
            resolved_at=row['resolved_at'],
            actual_price=row['actual_price'],
            actual_direction=row['actual_direction'],
            direction_correct=bool(row['direction_correct']) if row['direction_correct'] is not None else None,
            target_error_pct=row['target_error_pct'],
            calibration_score=row['calibration_score'],
            is_extreme=bool(row['is_extreme']) if row['is_extreme'] is not None else None,
            extreme_reason=row['extreme_reason'],
            learning_extracted=row['learning_extracted']
        )


class BinanceClient:
    BASE_URL = "https://api.binance.com/api/v3"
    
    def get_btc_price(self) -> float:
        response = requests.get(f"{self.BASE_URL}/ticker/price", params={"symbol": "BTCUSDT"})
        response.raise_for_status()
        return float(response.json()["price"])
    
    def get_recent_klines(self, interval: str = "5m", limit: int = 288) -> List[dict]:
        response = requests.get(f"{self.BASE_URL}/klines", params={
            "symbol": "BTCUSDT",
            "interval": interval,
            "limit": limit
        })
        response.raise_for_status()
        klines = response.json()
        
        return [{
            "open_time": datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).isoformat(),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "change_pct": round((float(k[4]) - float(k[1])) / float(k[1]) * 100, 3)
        } for k in klines]
    
    def get_24h_stats(self) -> dict:
        response = requests.get(f"{self.BASE_URL}/ticker/24hr", params={"symbol": "BTCUSDT"})
        response.raise_for_status()
        data = response.json()
        return {
            "price_change_pct": float(data["priceChangePercent"]),
            "high_24h": float(data["highPrice"]),
            "low_24h": float(data["lowPrice"]),
            "volume_24h": float(data["volume"]),
            "quote_volume_24h": float(data["quoteVolume"]),
            "weighted_avg_price": float(data["weightedAvgPrice"])
        }


class MetaLearner:
    """Analyzes patterns in learnings to generate meta-rules."""
    
    def __init__(self, client: Anthropic, db: Database):
        self.client = client
        self.db = db
    
    def should_analyze(self) -> bool:
        """Check if it's time for meta-analysis."""
        stats = self.db.get_stats()
        last_count = self.db.get_last_meta_analysis_count()
        current_count = stats['total_predictions']
        
        # Analyze every META_LEARNING_INTERVAL batches (e.g., every 100 predictions)
        threshold = META_LEARNING_INTERVAL * BATCH_SIZE
        return (current_count - last_count) >= threshold
    
    def analyze(self) -> List[dict]:
        """Perform meta-analysis on accumulated learnings."""
        extremes = self.db.get_all_extremes()
        stats = self.db.get_stats()
        
        if len(extremes) < 20:
            print("   Not enough learnings for meta-analysis yet")
            return []
        
        # Categorize learnings
        high_conf_wrong = [e for e in extremes if not e.direction_correct and e.confidence >= 70]
        low_conf_right = [e for e in extremes if e.direction_correct and e.confidence <= 40]
        accurate_targets = [e for e in extremes if e.target_error_pct <= 0.05]
        large_misses = [e for e in extremes if e.target_error_pct >= 0.15]
        
        # Build analysis prompt
        prompt = self._build_meta_prompt(extremes, stats, {
            'high_conf_wrong': high_conf_wrong,
            'low_conf_right': low_conf_right,
            'accurate_targets': accurate_targets,
            'large_misses': large_misses
        })
        
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.content[0].text
        
        # Parse meta-learnings
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        if json_match:
            meta_results = json.loads(json_match.group(1))
        else:
            try:
                meta_results = json.loads(response_text)
            except:
                print("   Failed to parse meta-learning response")
                return []
        
        # Save meta-learnings
        saved = []
        for meta in meta_results.get('patterns', []):
            meta_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'predictions_analyzed': stats['total_predictions'],
                'learnings_analyzed': len(extremes),
                'accuracy_at_analysis': stats['accuracy_pct'],
                'pattern_type': meta.get('type', 'unknown'),
                'pattern_description': meta.get('description', ''),
                'meta_rule': meta.get('rule', ''),
                'confidence_score': meta.get('confidence', 0.5)
            }
            meta_id = self.db.save_meta_learning(meta_data)
            meta_data['id'] = meta_id
            saved.append(meta_data)
        
        return saved
    
    def _build_meta_prompt(self, extremes: List[Prediction], stats: dict, categories: dict) -> str:
        # Sample learnings from each category
        def sample_learnings(preds, n=5):
            sampled = preds[-n:] if len(preds) > n else preds
            return "\n".join([f"  - {p.learning_extracted}" for p in sampled if p.learning_extracted])
        
        return f"""You are a meta-learning system analyzing patterns in trading prediction learnings.

## Current Performance
- Total Predictions: {stats['total_predictions']}
- Accuracy: {stats['accuracy_pct']:.1f}%
- Total Learnings: {len(extremes)}

## Learning Categories

### High Confidence but Wrong ({len(categories['high_conf_wrong'])} cases)
These are predictions where we were confident (70%+) but got the direction wrong:
{sample_learnings(categories['high_conf_wrong'])}

### Low Confidence but Right ({len(categories['low_conf_right'])} cases)
These are predictions where we had low confidence (40% or less) but were actually correct:
{sample_learnings(categories['low_conf_right'])}

### Exceptionally Accurate Targets ({len(categories['accurate_targets'])} cases)
These predictions hit very close to the target price:
{sample_learnings(categories['accurate_targets'])}

### Large Target Misses ({len(categories['large_misses'])} cases)
These predictions had significant errors in price targets:
{sample_learnings(categories['large_misses'])}

## Your Task

Analyze these learnings to identify META-PATTERNS - recurring themes or systematic errors that appear across multiple learnings.

For each pattern you identify, provide:
1. **Type**: Category of pattern (overconfidence, momentum_misread, volatility_underestimate, etc.)
2. **Description**: What the pattern is and how often it appears
3. **Rule**: A concrete rule to apply in future predictions to address this pattern
4. **Confidence**: How confident you are this pattern is real (0.0-1.0)

Focus on actionable patterns that could improve prediction accuracy.

Respond in this exact JSON format:
```json
{{
    "patterns": [
        {{
            "type": "pattern_type",
            "description": "Description of the pattern observed",
            "rule": "Specific rule to apply: When X, do Y instead of Z",
            "confidence": 0.8
        }}
    ],
    "summary": "Brief overall assessment of prediction quality and main areas for improvement"
}}
```

Identify 2-4 of the most significant patterns. Quality over quantity."""
    
    def get_meta_context(self) -> str:
        """Build context string from active meta-rules."""
        meta_rules = self.db.get_active_meta_rules()
        
        if not meta_rules:
            return ""
        
        context_parts = ["\n## Meta-Learning Rules (from pattern analysis)\n"]
        
        for i, rule in enumerate(meta_rules, 1):
            context_parts.append(f"""
### Meta-Rule {i} ({rule['pattern_type']})
- **Pattern**: {rule['pattern_description']}
- **Rule**: {rule['meta_rule']}
- **Confidence**: {rule['confidence_score']:.0%}
""")
        
        return "\n".join(context_parts)


class Verifier:
    """GPT-4 based verifier that checks Claude's predictions."""
    
    def __init__(self, db: Database):
        self.client = OpenAI()
        self.db = db
    
    def verify_prediction(self, pred: Prediction, market: dict, claude_meta_rules: List[dict]) -> VerifierPrediction:
        """Have GPT-4 verify Claude's prediction."""
        prompt = self._build_verification_prompt(pred, market, claude_meta_rules)
        
        response = self.client.chat.completions.create(
            model=VERIFIER_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.choices[0].message.content
        
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            result = json.loads(response_text)
        
        vpred = VerifierPrediction(
            id=None,
            prediction_id=pred.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agrees_with_claude=result.get('agrees', True),
            confidence_claude_correct=int(result.get('confidence_correct', 50)),
            reasoning=result.get('reasoning', ''),
            concerns=result.get('concerns', []),
            meta_rule_violations=result.get('meta_rule_violations', [])
        )
        
        vpred.id = self.db.save_verifier_prediction(vpred)
        return vpred
    
    def _build_verification_prompt(self, pred: Prediction, market: dict, claude_meta_rules: List[dict]) -> str:
        meta_rules_text = ""
        if claude_meta_rules:
            meta_rules_text = "\n## Claude's Active Meta-Rules (learned patterns)\n"
            for i, rule in enumerate(claude_meta_rules, 1):
                meta_rules_text += f"{i}. [{rule.get('pattern_type', 'unknown')}] {rule.get('meta_rule', '')}\n"
        
        verifier_context = self._get_verifier_context()
        
        return f"""You are GPT-4, acting as an independent verifier for a Claude-based BTC prediction system.

## Your Role
Review Claude's prediction and determine if you agree with it. Your job is to:
1. Identify potential errors in Claude's reasoning
2. Check if Claude is following its own meta-rules
3. Provide an independent assessment

## Claude's Prediction
- **Direction**: {pred.predicted_direction}
- **Target Price**: ${pred.predicted_target:,.2f}
- **Confidence**: {pred.confidence}%
- **Current Price**: ${pred.current_price:,.2f}
- **Reasoning**: {pred.reasoning}

## Current Market Data
- **Trend**: {market.get('trend', 'N/A')}
- **1h Momentum**: {market.get('momentum_1h_pct', 0):+.2f}%
- **4h Momentum**: {market.get('momentum_4h_pct', 0):+.2f}%
- **Volatility**: {market.get('volatility_pct', 0):.3f}%
- **Volume Ratio**: {market.get('volume_ratio', 1):.2f}x average
- **Position in Day Range**: {market.get('position_in_day_range_pct', 50):.1f}%
{meta_rules_text}
{verifier_context}

## Your Task
Analyze Claude's prediction and provide your verdict:
1. Do you agree with Claude's direction call?
2. How confident are you that Claude will be correct?
3. What concerns do you have?
4. Is Claude violating any of its own meta-rules?

Respond in this exact JSON format:
```json
{{
    "agrees": true/false,
    "confidence_correct": 0-100,
    "reasoning": "Your analysis of Claude's prediction",
    "concerns": ["List of specific concerns"],
    "meta_rule_violations": ["List any meta-rules Claude might be violating"]
}}
```"""
    
    def _get_verifier_context(self) -> str:
        """Build context from verifier's own learnings."""
        meta_rules = self.db.get_verifier_meta_rules()
        extremes = self.db.get_verifier_extremes(5)
        
        context_parts = []
        
        if meta_rules:
            context_parts.append("\n## Your Own Meta-Rules (from past verification)")
            for i, rule in enumerate(meta_rules, 1):
                context_parts.append(f"{i}. [{rule.get('pattern_type', 'unknown')}] {rule.get('meta_rule', '')}")
        
        if extremes:
            context_parts.append("\n## Your Recent Learnings")
            for ex in extremes[:3]:
                context_parts.append(f"- {ex.learning_extracted[:100]}...")
        
        return "\n".join(context_parts) if context_parts else ""
    
    def resolve_verification(self, vpred: VerifierPrediction, claude_was_correct: bool) -> VerifierPrediction:
        """Resolve a verification prediction."""
        vpred.resolved_at = datetime.now(timezone.utc).isoformat()
        
        # GPT was correct if:
        # - GPT agreed and Claude was correct, OR
        # - GPT disagreed and Claude was wrong
        if vpred.agrees_with_claude:
            vpred.gpt_was_correct = claude_was_correct
        else:
            vpred.gpt_was_correct = not claude_was_correct
        
        self.db.update_verifier_resolution(vpred)
        return vpred
    
    def analyze_batch_for_extremes(self) -> List[VerifierPrediction]:
        """Analyze verifier predictions for extreme cases."""
        batch = self.db.get_verifier_recent_for_batch(VERIFIER_BATCH_SIZE)
        
        if len(batch) < VERIFIER_BATCH_SIZE:
            return []
        
        extremes = []
        for vpred in batch:
            is_extreme = False
            extreme_reason = None
            
            # High confidence but wrong
            if not vpred.gpt_was_correct and vpred.confidence_claude_correct >= 80:
                is_extreme = True
                extreme_reason = f"High confidence ({vpred.confidence_claude_correct}%) but wrong"
            elif not vpred.gpt_was_correct and vpred.confidence_claude_correct <= 20:
                is_extreme = True
                extreme_reason = f"Low confidence ({vpred.confidence_claude_correct}%) but wrong"
            # Correctly caught error
            elif vpred.gpt_was_correct and not vpred.agrees_with_claude:
                is_extreme = True
                extreme_reason = "Correctly caught Claude error"
            # False alarm
            elif not vpred.gpt_was_correct and not vpred.agrees_with_claude:
                is_extreme = True
                extreme_reason = "False alarm - wrongly disagreed with Claude"
            
            if is_extreme:
                vpred.is_extreme = True
                vpred.extreme_reason = extreme_reason
                vpred.learning_extracted = self._extract_learning(vpred)
                extremes.append(vpred)
            else:
                vpred.is_extreme = False
            
            self.db.update_verifier_resolution(vpred)
        
        return extremes
    
    def _extract_learning(self, vpred: VerifierPrediction) -> str:
        """Extract learning from extreme verification."""
        prompt = f"""Analyze this verification outcome and extract a learning for improving future verifications.

## Your Verification
- Agreed with Claude: {vpred.agrees_with_claude}
- Confidence Claude correct: {vpred.confidence_claude_correct}%
- Your reasoning: {vpred.reasoning}
- Your concerns: {vpred.concerns}

## Outcome
- You were {"CORRECT" if vpred.gpt_was_correct else "WRONG"}
- Why extreme: {vpred.extreme_reason}

Extract a single, actionable learning (1-2 sentences) for improving your verification accuracy."""

        response = self.client.chat.completions.create(
            model=VERIFIER_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.choices[0].message.content.strip()


class Predictor:
    def __init__(self):
        self.client = Anthropic()
        self.db = Database()
        self.binance = BinanceClient()
        self.meta_learner = MetaLearner(self.client, self.db)
        
        # Verifier (GPT-4)
        if VERIFIER_ENABLED:
            self.verifier = Verifier(self.db)
        else:
            self.verifier = None
    
    def build_context(self) -> str:
        """Build context from past extreme predictions and meta-rules."""
        extremes = self.db.get_extremes(limit=CONTEXT_EXAMPLES)
        meta_context = self.meta_learner.get_meta_context()
        
        if not extremes and not meta_context:
            return "No historical learnings yet. This is an early prediction."
        
        context_parts = []
        
        # Add meta-rules first (higher-level guidance)
        if meta_context:
            context_parts.append(meta_context)
        
        # Add specific learnings
        if extremes:
            context_parts.append("## Learnings from Past Predictions (Extremes)\n")
            
            for i, ex in enumerate(extremes, 1):
                context_parts.append(f"""
### Learning {i}
- **Prediction**: {ex.predicted_direction} to ${ex.predicted_target:.2f} ({ex.confidence}% confidence)
- **Actual**: {ex.actual_direction} to ${ex.actual_price:.2f}
- **Result**: {"✓ Correct" if ex.direction_correct else "✗ Wrong"} direction, {ex.target_error_pct:.2f}% target error
- **Why Extreme**: {ex.extreme_reason}
- **Learning**: {ex.learning_extracted}
""")
        
        return "\n".join(context_parts)
    
    def analyze_market_structure(self, klines: List[dict]) -> dict:
        if len(klines) < 12:
            return {}
        
        closes = [k['close'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        volumes = [k['volume'] for k in klines]
        
        current_price = closes[-1]
        
        ma_12 = sum(closes[-12:]) / 12
        ma_48 = sum(closes[-48:]) / 48 if len(closes) >= 48 else sum(closes) / len(closes)
        ma_288 = sum(closes) / len(closes)
        
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]
        avg_return = sum(returns) / len(returns)
        volatility = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
        
        momentum_1h = (closes[-1] - closes[-12]) / closes[-12] * 100 if len(closes) >= 12 else 0
        momentum_4h = (closes[-1] - closes[-48]) / closes[-48] * 100 if len(closes) >= 48 else 0
        
        recent_high = max(highs[-24:])
        recent_low = min(lows[-24:])
        day_high = max(highs)
        day_low = min(lows)
        
        avg_volume = sum(volumes) / len(volumes)
        recent_volume = sum(volumes[-6:]) / 6
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1
        
        if current_price > ma_12 > ma_48 > ma_288:
            trend = "STRONG UPTREND"
        elif current_price > ma_12 > ma_48:
            trend = "UPTREND"
        elif current_price < ma_12 < ma_48 < ma_288:
            trend = "STRONG DOWNTREND"
        elif current_price < ma_12 < ma_48:
            trend = "DOWNTREND"
        else:
            trend = "RANGING/CHOPPY"
        
        day_range = day_high - day_low
        position_in_range = ((current_price - day_low) / day_range * 100) if day_range > 0 else 50
        
        return {
            "trend": trend,
            "ma_1h": ma_12,
            "ma_4h": ma_48,
            "ma_24h": ma_288,
            "volatility_pct": volatility,
            "momentum_1h_pct": momentum_1h,
            "momentum_4h_pct": momentum_4h,
            "day_high": day_high,
            "day_low": day_low,
            "recent_high_2h": recent_high,
            "recent_low_2h": recent_low,
            "volume_ratio": volume_ratio,
            "position_in_day_range_pct": position_in_range
        }
    
    def format_recent_candles(self, klines: List[dict], count: int = 12) -> str:
        recent = klines[-count:]
        lines = []
        for k in recent:
            time_str = k['open_time'][11:16]
            lines.append(
                f"  {time_str} | O:{k['open']:,.2f} H:{k['high']:,.2f} L:{k['low']:,.2f} C:{k['close']:,.2f} | {k['change_pct']:+.3f}%"
            )
        return "\n".join(lines)
    
    def make_prediction(self) -> Prediction:
        current_price = self.binance.get_btc_price()
        klines = self.binance.get_recent_klines(limit=288)
        stats_24h = self.binance.get_24h_stats()
        market = self.analyze_market_structure(klines)
        context = self.build_context()
        stats = self.db.get_stats()
        
        recent_candles = self.format_recent_candles(klines, count=12)
        
        prompt = f"""You are a BTC price prediction system. Your goal is to predict the direction and target price of BTC/USDT in the next 5 minutes.

## Current State
- **Current Price**: ${current_price:,.2f}
- **Time (UTC)**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}

## 24-Hour Statistics
- Price Change: {stats_24h['price_change_pct']:+.2f}%
- 24h High: ${stats_24h['high_24h']:,.2f}
- 24h Low: ${stats_24h['low_24h']:,.2f}
- 24h Volume: {stats_24h['volume_24h']:,.2f} BTC
- VWAP: ${stats_24h['weighted_avg_price']:,.2f}

## Market Structure Analysis (from 24h of 5-min data)
- **Trend**: {market.get('trend', 'N/A')}
- Moving Averages: 1h=${market.get('ma_1h', 0):,.2f} | 4h=${market.get('ma_4h', 0):,.2f} | 24h=${market.get('ma_24h', 0):,.2f}
- Volatility (5-min returns): {market.get('volatility_pct', 0):.3f}%
- Momentum: 1h={market.get('momentum_1h_pct', 0):+.2f}% | 4h={market.get('momentum_4h_pct', 0):+.2f}%
- Day Range: ${market.get('day_low', 0):,.2f} - ${market.get('day_high', 0):,.2f}
- Recent Range (2h): ${market.get('recent_low_2h', 0):,.2f} - ${market.get('recent_high_2h', 0):,.2f}
- Position in Day Range: {market.get('position_in_day_range_pct', 50):.1f}%
- Volume Ratio (recent/avg): {market.get('volume_ratio', 1):.2f}x

## Recent Price Action (last hour, 5-min candles)
{recent_candles}

## Your Track Record
- Total Predictions: {stats['total_predictions']}
- Accuracy: {stats['accuracy_pct']:.1f}%
- Avg Target Error: {stats['avg_target_error_pct']:.2f}%
- Avg Calibration: {stats['avg_calibration']:.2f}
- Active Meta-Rules: {stats.get('active_meta_rules', 0)}

{context}

## Your Task
Based on ALL the data above (including meta-rules and learnings), predict:
1. **Direction**: Will price be UP or DOWN in 5 minutes?
2. **Target**: What specific price do you predict?
3. **Confidence**: How confident are you? (0-100%)

IMPORTANT: Apply any relevant meta-rules from above. They represent patterns learned from past mistakes.

Be detailed in your reasoning - explain which factors and rules influenced your prediction.

Respond in this exact JSON format:
```json
{{
    "direction": "UP" or "DOWN",
    "target": <number>,
    "confidence": <0-100>,
    "reasoning": "<detailed explanation referencing specific data points and any meta-rules applied>"
}}
```"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.content[0].text
        
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        if json_match:
            prediction_data = json.loads(json_match.group(1))
        else:
            prediction_data = json.loads(response_text)
        
        pred = Prediction(
            id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            current_price=current_price,
            predicted_direction=prediction_data["direction"].upper(),
            predicted_target=float(prediction_data["target"]),
            confidence=int(prediction_data["confidence"]),
            reasoning=prediction_data["reasoning"]
        )
        
        pred.id = self.db.save_prediction(pred)
        return pred
    
    def resolve_prediction(self, pred: Prediction) -> Prediction:
        actual_price = self.binance.get_btc_price()
        
        pred.resolved_at = datetime.now(timezone.utc).isoformat()
        pred.actual_price = actual_price
        pred.actual_direction = "UP" if actual_price > pred.current_price else "DOWN"
        pred.direction_correct = pred.predicted_direction == pred.actual_direction
        pred.target_error_pct = abs(actual_price - pred.predicted_target) / pred.current_price * 100
        
        if pred.direction_correct:
            pred.calibration_score = pred.confidence / 100
        else:
            pred.calibration_score = (100 - pred.confidence) / 100
        
        self.db.update_resolution(pred)
        return pred
    
    def analyze_batch_for_extremes(self) -> List[Prediction]:
        batch = self.db.get_recent_for_batch_analysis(BATCH_SIZE)
        
        if len(batch) < BATCH_SIZE:
            print(f"Not enough predictions for batch analysis ({len(batch)}/{BATCH_SIZE})")
            return []
        
        calibration_scores = [p.calibration_score for p in batch]
        target_errors = [p.target_error_pct for p in batch]
        
        error_threshold_high = sorted(target_errors)[int(len(batch) * (1 - EXTREME_PERCENTILE/100))]
        
        extremes = []
        
        for pred in batch:
            is_extreme = False
            extreme_reason = None
            
            # High confidence but wrong (tightened to 75%+)
            if not pred.direction_correct and pred.confidence >= 75:
                is_extreme = True
                extreme_reason = f"High confidence ({pred.confidence}%) but wrong"
            
            # Low confidence but correct (tightened to 35% or less)
            elif pred.direction_correct and pred.confidence <= 35:
                is_extreme = True
                extreme_reason = f"Low confidence ({pred.confidence}%) but correct"
            
            # Exceptional target accuracy (0.05%)
            elif pred.target_error_pct <= 0.05:
                is_extreme = True
                extreme_reason = f"Exceptional target accuracy ({pred.target_error_pct:.3f}%)"
            
            # Large target miss (top 10% of errors in batch)
            elif pred.target_error_pct >= error_threshold_high:
                is_extreme = True
                extreme_reason = f"Large target miss ({pred.target_error_pct:.2f}%)"
            
            pred.is_extreme = is_extreme
            
            if is_extreme:
                pred.extreme_reason = extreme_reason
                pred.learning_extracted = self._extract_learning(pred)
                extremes.append(pred)
            
            self.db.update_resolution(pred)
        
        return extremes
    
    def _extract_learning(self, pred: Prediction) -> str:
        prompt = f"""Analyze this extreme prediction and extract a concise learning.

## The Prediction
- Time: {pred.timestamp}
- Starting Price: ${pred.current_price:.2f}
- Predicted: {pred.predicted_direction} to ${pred.predicted_target:.2f} ({pred.confidence}% confidence)
- Reasoning: {pred.reasoning}

## The Outcome
- Actual Price: ${pred.actual_price:.2f}
- Actual Direction: {pred.actual_direction}
- Result: {"Correct" if pred.direction_correct else "Wrong"} direction
- Target Error: {pred.target_error_pct:.2f}%

## Why This Is Extreme
{pred.extreme_reason}

Extract a single, actionable learning (1-2 sentences) that could improve future predictions. Focus on what pattern or mistake this reveals."""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text.strip()
    
    def run_meta_analysis_if_needed(self) -> List[dict]:
        """Check if meta-analysis is due and run it."""
        if self.meta_learner.should_analyze():
            print("\n🧠 Running meta-analysis on accumulated learnings...")
            meta_results = self.meta_learner.analyze()
            if meta_results:
                print(f"   Generated {len(meta_results)} new meta-rules:")
                for meta in meta_results:
                    print(f"   - [{meta['pattern_type']}] {meta['meta_rule'][:60]}...")
            return meta_results
        return []


def determine_consensus(pred: Prediction, vpred: VerifierPrediction) -> dict:
    """Determine consensus signal from Claude and GPT-4."""
    if vpred.agrees_with_claude:
        if vpred.confidence_claude_correct >= 70:
            signal = "CONSENSUS_STRONG"
            strength = "HIGH"
        else:
            signal = "CONSENSUS_WEAK"
            strength = "MEDIUM"
        direction = pred.predicted_direction
        confidence = (pred.confidence + vpred.confidence_claude_correct) // 2
    else:
        if vpred.confidence_claude_correct <= 30:
            signal = "VERIFIER_VETO"
            strength = "HIGH"
        else:
            signal = "DISAGREEMENT"
            strength = "LOW"
        direction = pred.predicted_direction  # Still use Claude's direction
        confidence = pred.confidence // 2  # Reduced confidence
    
    return {
        "signal": signal,
        "direction": direction,
        "confidence": confidence,
        "strength": strength
    }


def classify_outcome(pred: Prediction, vpred: VerifierPrediction) -> str:
    """Classify the outcome type after resolution."""
    claude_correct = pred.direction_correct
    gpt_agreed = vpred.agrees_with_claude
    
    if gpt_agreed and claude_correct:
        return "consensus_win"
    elif gpt_agreed and not claude_correct:
        return "shared_blind_spot"
    elif not gpt_agreed and not claude_correct:
        return "gpt_caught_error"
    else:  # not gpt_agreed and claude_correct
        return "gpt_false_alarm"


def run_single_cycle(predictor: Predictor):
    print("\n" + "="*60)
    print(f"🔮 Making prediction at {datetime.now(timezone.utc).isoformat()}")
    
    # Step 1: Get market data
    klines = predictor.binance.get_recent_klines(limit=288)
    market = predictor.analyze_market_structure(klines)
    
    # Step 2: Claude makes prediction
    pred = predictor.make_prediction()
    print(f"📊 Claude: {pred.predicted_direction} to ${pred.predicted_target:,.2f}")
    print(f"   Confidence: {pred.confidence}%")
    print(f"   Current: ${pred.current_price:,.2f}")
    
    # Step 3: GPT-4 verifies (if enabled)
    vpred = None
    consensus = None
    if predictor.verifier:
        print(f"\n🔍 GPT-4 Verification...")
        try:
            claude_meta_rules = predictor.db.get_active_meta_rules()
            vpred = predictor.verifier.verify_prediction(pred, market, claude_meta_rules)
            
            agrees_text = "AGREES" if vpred.agrees_with_claude else "DISAGREES"
            print(f"   {agrees_text} with Claude ({vpred.confidence_claude_correct}% confidence)")
            if vpred.concerns:
                print(f"   Concerns: {vpred.concerns}")
            
            # Determine consensus
            consensus = determine_consensus(pred, vpred)
            print(f"\n📊 CONSENSUS: {consensus['signal']} - {consensus['direction']} ({consensus['strength']})")
            
        except Exception as e:
            print(f"   ⚠️ Verification failed: {e}")
    
    print(f"\n⏳ Waiting {PREDICTION_INTERVAL_MINS} minutes for resolution...")
    time.sleep(PREDICTION_INTERVAL_MINS * 60)
    
    # Step 4: Resolve Claude's prediction
    pred = predictor.resolve_prediction(pred)
    print(f"\n✅ Claude Resolved!")
    print(f"   Actual: ${pred.actual_price:,.2f} ({pred.actual_direction})")
    print(f"   Direction: {'✓ Correct' if pred.direction_correct else '✗ Wrong'}")
    print(f"   Target Error: {pred.target_error_pct:.2f}%")
    
    # Step 5: Resolve GPT-4 verification
    if vpred and predictor.verifier:
        vpred = predictor.verifier.resolve_verification(vpred, pred.direction_correct)
        print(f"\n🔍 GPT-4 Resolved: {'✓ Correct' if vpred.gpt_was_correct else '✗ Wrong'}")
        
        # Record consensus outcome
        outcome_type = classify_outcome(pred, vpred)
        predictor.db.save_consensus_outcome({
            'prediction_id': pred.id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'models_agreed': vpred.agrees_with_claude,
            'consensus_direction': consensus['direction'] if consensus else pred.predicted_direction,
            'consensus_confidence': consensus['confidence'] if consensus else pred.confidence,
            'claude_correct': pred.direction_correct,
            'gpt_correct': vpred.gpt_was_correct,
            'outcome_type': outcome_type
        })
        print(f"   Outcome: {outcome_type}")
    
    # Step 6: Batch analysis for Claude
    pending_batch = predictor.db.get_recent_for_batch_analysis()
    if len(pending_batch) >= BATCH_SIZE:
        print(f"\n🔬 Analyzing Claude batch for extremes...")
        extremes = predictor.analyze_batch_for_extremes()
        print(f"   Found {len(extremes)} extreme predictions")
    
    # Step 7: Batch analysis for GPT-4
    if predictor.verifier:
        verifier_batch = predictor.db.get_verifier_recent_for_batch()
        if len(verifier_batch) >= VERIFIER_BATCH_SIZE:
            print(f"\n🔬 Analyzing GPT-4 batch for extremes...")
            verifier_extremes = predictor.verifier.analyze_batch_for_extremes()
            print(f"   Found {len(verifier_extremes)} verifier extremes")
    
    # Step 8: Meta-analysis for Claude
    predictor.run_meta_analysis_if_needed()
    
    # Print stats
    stats = predictor.db.get_stats()
    print(f"\n📈 Overall Stats:")
    print(f"   Total: {stats['total_predictions']}")
    print(f"   Accuracy: {stats['accuracy_pct']:.1f}%")
    print(f"   Avg Error: {stats['avg_target_error_pct']:.2f}%")
    print(f"   Extremes: {stats['extremes_captured']}")
    print(f"   Meta-Rules: {stats.get('active_meta_rules', 0)}")
    
    if predictor.verifier:
        vstats = predictor.db.get_verifier_stats()
        print(f"\n🔍 Verifier Stats:")
        print(f"   Accuracy: {vstats['accuracy']:.1f}%")
        print(f"   Catches: {vstats['catches']}")
        print(f"   False Alarms: {vstats['false_alarms']}")


def run_continuous(predictor: Predictor):
    print("🚀 Starting Recursive Learning BTC Predictor (with Meta-Learning)")
    print(f"   Interval: {PREDICTION_INTERVAL_MINS} minutes")
    print(f"   Batch Size: {BATCH_SIZE}")
    print(f"   Meta-Analysis: Every {META_LEARNING_INTERVAL * BATCH_SIZE} predictions")
    print(f"   Data: 24 hours of 5-min candles")
    print("   Press Ctrl+C to stop\n")
    
    while True:
        try:
            run_single_cycle(predictor)
        except KeyboardInterrupt:
            print("\n\n👋 Stopping predictor...")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("   Waiting 60s before retry...")
            time.sleep(60)


def show_status(predictor: Predictor):
    stats = predictor.db.get_stats()
    extremes = predictor.db.get_extremes(5)
    recent = predictor.db.get_resolved(5)
    meta_rules = predictor.db.get_active_meta_rules()
    
    print("\n" + "="*60)
    print("📊 RECURSIVE LEARNING BTC PREDICTOR - STATUS")
    print("="*60)
    
    print(f"\n📈 Overall Stats:")
    print(f"   Total Predictions: {stats['total_predictions']}")
    print(f"   Resolved: {stats['resolved']}")
    print(f"   Direction Accuracy: {stats['accuracy_pct']:.1f}%")
    print(f"   Avg Target Error: {stats['avg_target_error_pct']:.2f}%")
    print(f"   Avg Calibration: {stats['avg_calibration']:.2f}")
    print(f"   Extremes Captured: {stats['extremes_captured']}")
    print(f"   Active Meta-Rules: {stats.get('active_meta_rules', 0)}")
    
    if recent:
        print(f"\n🕐 Recent Predictions:")
        for r in recent[:5]:
            status = "✓" if r.direction_correct else "✗"
            print(f"   {status} {r.predicted_direction} @ {r.confidence}% → {r.actual_direction} (err: {r.target_error_pct:.2f}%)")
    
    if meta_rules:
        print(f"\n🧠 Active Meta-Rules:")
        for rule in meta_rules[:3]:
            print(f"   [{rule['pattern_type']}] {rule['meta_rule'][:70]}...")
    
    if extremes:
        print(f"\n⚡ Recent Learnings (from extremes):")
        for ex in extremes[:3]:
            print(f"   • {ex.learning_extracted[:100]}...")


def export_data(predictor: Predictor, format: str = "json"):
    if format == "json":
        count = predictor.db.export_to_json("predictions_export.json")
        print(f"✓ Exported {count} predictions to predictions_export.json")
    elif format == "csv":
        count = predictor.db.export_to_csv("predictions_export.csv")
        print(f"✓ Exported {count} predictions to predictions_export.csv")
    else:
        print(f"Unknown format: {format}")


def force_meta_analysis(predictor: Predictor):
    """Force a meta-analysis regardless of threshold."""
    print("🧠 Forcing meta-analysis...")
    meta_results = predictor.meta_learner.analyze()
    if meta_results:
        print(f"✓ Generated {len(meta_results)} new meta-rules:")
        for meta in meta_results:
            print(f"\n   [{meta['pattern_type']}]")
            print(f"   Pattern: {meta['pattern_description'][:100]}...")
            print(f"   Rule: {meta['meta_rule']}")
            print(f"   Confidence: {meta['confidence_score']:.0%}")
    else:
        print("   No patterns identified (may need more learnings)")


if __name__ == "__main__":
    import sys
    
    predictor = Predictor()
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "status":
            show_status(predictor)
        elif cmd == "once":
            run_single_cycle(predictor)
        elif cmd == "export":
            fmt = sys.argv[2] if len(sys.argv) > 2 else "json"
            export_data(predictor, fmt)
        elif cmd == "meta":
            force_meta_analysis(predictor)
        else:
            print("Usage: python predictor.py [status|once|export [json|csv]|meta]")
            print("  No args: Run continuous loop")
            print("  status:  Show current stats")
            print("  once:    Run single prediction cycle")
            print("  export:  Export data to JSON or CSV")
            print("  meta:    Force meta-analysis now")
    else:
        run_continuous(predictor)
