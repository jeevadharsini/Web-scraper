import os
import sys

# Lets the tests import app.py and scraper.py from the project folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
