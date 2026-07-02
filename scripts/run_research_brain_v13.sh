#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
python -m joanbot.brain.institutional_research_brain_v13
