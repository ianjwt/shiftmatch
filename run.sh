#!/bin/bash
# ShiftMatch — Install dependencies and start the server

set -e

cd "$(dirname "$0")"

echo ""
echo "  ShiftMatch — Speed date your way to work"
echo "  ========================================="
echo ""

# Install dependencies
if [ -f requirements.txt ]; then
    echo "  Installing dependencies..."
    pip3 install -q -r requirements.txt
    echo "  Done."
    echo ""
fi

# Start Flask
echo "  Starting server on http://localhost:5050"
echo "  Press Ctrl+C to stop."
echo ""

python3 app.py
