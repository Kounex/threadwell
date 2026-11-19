#!/usr/bin/env python3
"""Configure a local worker using a launch agent (macOS) or user systemd (Linux)."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

home=Path.home()
repo=Path(__file__).resolve().parents[1]
cli=repo/"bin/threadwell"
p=argparse.ArgumentParser()
p.add_argument("--host",required=True)
p.add_argument("--url",required=True)
p.add_argument("--workspace",type=Path,default=home/"agent")
p.add_argument("transport",nargs=argparse.REMAINDER,help="After --, the executable and argv for store.py RPC")
args=p.parse_args()
cmd=args.transport[1:] if args.transport[:1]==["--"] else args.transport
if not cmd:
    p.error("provide an RPC transport after --")
config=home/".config/threadwell"
config.mkdir(mode=0o700,parents=True,exist_ok=True)
(config/"config.json").write_text(json.dumps(dict(host=args.host,url=args.url,workspace=str(args.workspace),transport=cmd),indent=2))
local=home/".local/bin/threadwell"
local.parent.mkdir(parents=True,exist_ok=True)
if not local.exists():
    local.symlink_to(cli)
elif local.resolve()!=cli:
    sys.exit(f"{local} already points elsewhere")
path=os.pathsep.join(dict.fromkeys([str(home/".local/bin"),str(Path(sys.executable).parent),*os.environ["PATH"].split(os.pathsep)]))
if sys.platform=="darwin":
    label="local.threadwell.worker"
    target=home/"Library/LaunchAgents"/(label+".plist")
    logs=home/"Library/Logs/Threadwell"
    logs.mkdir(parents=True,exist_ok=True)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(plistlib.dumps(dict(Label=label,ProgramArguments=[sys.executable,str(cli),"worker"],
        EnvironmentVariables={"PATH":path},RunAtLoad=True,KeepAlive=True,ThrottleInterval=15,
        StandardErrorPath=str(logs/"worker.log"),StandardOutPath=str(logs/"worker.log"))))
    domain=f"gui/{os.getuid()}"
    subprocess.run(["launchctl","bootout",f"{domain}/{label}"],capture_output=True)
    subprocess.run(["launchctl","bootstrap",domain,str(target)],check=True)
else:
    target=home/".config/systemd/user/threadwell-worker.service"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(f"""[Unit]
Description=Threadwell consultation launcher
After=network-online.target
[Service]
ExecStart="{sys.executable}" "{cli}" worker
Environment="PATH={path}"
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
""")
    for command in (["daemon-reload"],["enable","threadwell-worker"],["restart","threadwell-worker"]):
        subprocess.run(["systemctl","--user",*command],check=True)
