#!/bin/sh
cd "$(dirname "$0")"
while true; do
  git pull -q
  pip install -q -r requirements.txt
  python3 headless.py
  sleep 2
done
