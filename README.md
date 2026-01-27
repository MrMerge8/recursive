# 🔮 Recursive

**Recursive Learning Intelligence** - A dual-model BTC prediction system that learns from its mistakes.

## Features

- **Dual Verification**: Claude makes predictions, GPT-4 verifies them
- **Multi-Timeframe**: 5-minute, 15-minute, and 1-hour prediction cycles
- **Meta-Learning**: Automatically extracts patterns from past mistakes
- **Consensus Signals**: Strong/weak agreement between models
- **Real-time Dashboard**: Live predictions with countdown timers

## How It Works

1. **Predict**: Claude analyzes 24h of price data and makes a prediction (direction + target + confidence)
2. **Verify**: GPT-4 independently reviews Claude's reasoning and flags concerns
3. **Wait**: System waits for the outcome (5/15/60 min depending on timeframe)
4. **Score**: Compares predictions to reality, calculates calibration scores
5. **Learn**: Extracts learnings from extreme predictions (high confidence + wrong, etc.)
6. **Meta-Analyze**: Every 100 predictions, identifies higher-level patterns
7. **Recurse**: Future predictions include learnings and meta-rules in context

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set API keys
export ANTHROPIC_API_KEY=your_key_here
export OPENAI_API_KEY=your_key_here

# Run all services (dashboard + predictors)
python start.py

# Or run individually:
python dashboard.py          # Web dashboard on :8080
python predictor.py          # Continuous predictions
python predictor.py once     # Single prediction cycle
python predictor.py status   # Show stats
python predictor.py meta     # Force meta-analysis
```

## Files

- `start.py` - Orchestrates all services for deployment
- `predictor.py` - Main prediction engine with dual-model verification
- `dashboard.py` - Real-time web dashboard
- `predictions_*.db` - SQLite databases per timeframe

## Railway Deployment

This project is configured for Railway deployment with:
- `Procfile` - Defines the start command
- `railway.json` - Build configuration
- `.railway/config.json` - Railway settings

Environment variables needed:
- `ANTHROPIC_API_KEY` - For Claude predictions
- `OPENAI_API_KEY` - For GPT-4 verification
- `PORT` - Set automatically by Railway

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  Binance API    │     │   Past Data     │
│  (Price Feed)   │     │  (Learnings)    │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│           CLAUDE (Predictor)            │
│  • Analyzes market structure            │
│  • Applies meta-rules                   │
│  • Makes direction/target/confidence    │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│           GPT-4 (Verifier)              │
│  • Reviews Claude's reasoning           │
│  • Checks meta-rule compliance          │
│  • Flags concerns                       │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│         CONSENSUS SIGNAL                │
│  • STRONG: Both agree, high confidence  │
│  • WEAK: Agree but lower confidence     │
│  • DISAGREEMENT: Models differ          │
│  • VETO: Verifier strongly disagrees    │
└─────────────────────────────────────────┘
```

## Metrics

- **Predictions**: Total made across all timeframes
- **Accuracy**: % of correct direction predictions
- **Calibration**: How well confidence matches reality
- **Meta-Rules**: Learned patterns being applied
- **Catches**: Errors GPT-4 caught before resolution
- **False Alarms**: Times GPT-4 wrongly disagreed
