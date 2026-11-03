"""
WSGI Entry Point for PythonAnywhere and Production WSGI Servers (Gunicorn, uWSGI).

PythonAnywhere Configuration:
1. Go to the "Web" tab on PythonAnywhere.
2. In the "Code" section, open your WSGI configuration file.
3. Replace its content with:
   import sys
   import os

   path = '/home/<your-username>/taskflow-python-server'
   if path not in sys.path:
       sys.path.insert(0, path)

   from wsgi import application
"""

import os
import sys

# Add current folder to sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from app import create_app

# The WSGI callable object required by PythonAnywhere & Gunicorn
application = create_app()

if __name__ == "__main__":
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
