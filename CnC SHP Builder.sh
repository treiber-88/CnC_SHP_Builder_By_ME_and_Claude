#!/bin/bash
cd "$(dirname "$0")"
python3 main.py
if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Could not start the SHP Builder."
    echo "Make sure Python 3 and Pillow are installed."
    echo "Run:  pip3 install pillow"
    read -p "Press Enter to close..."
fi
