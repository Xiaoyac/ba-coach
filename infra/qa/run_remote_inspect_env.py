"""Load production protected env then run the read-only inspector."""
from dotenv import load_dotenv
import runpy
import sys

for path in ("/etc/bacoach/backend.env", "/etc/bacoach/pa-push.env", "/etc/bacoach/workbench-safety.env"):
    load_dotenv(path, override=True)
target, *args = sys.argv[1:]
sys.argv = [target, *args]
runpy.run_path(target, run_name="__main__")
