#!/bin/bash
cd /Users/zeyuli/CodeProject/milky-frog-lite
source .venv/bin/activate
python -m pytest tests/ -v --tb=short 2>&1
