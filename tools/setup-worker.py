#!/usr/bin/env python3
"""Configure a local worker using a launch agent (macOS) or user systemd (Linux)."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

home=Path.home()
repo=Path(__file__).resolve().parents[1]
cli=repo/"bin/threadwell"
p=argparse.ArgumentParser()
p.add_argument("--host",required=True)
p.add_argument("--url",required=True)
p.add_argument("--native-updates",action="store_true",help="Run each native CLI updater at most daily on invitation")
p.add_argument("--workspace",type=Path,default=home/"agent")
p.add_argument("--rework",action="store_true",help="Allow owner rework requests to start full-permission starter sessions on this host")
p.add_argument("--notify",help="Command run as CMD MESSAGE TITLE when a rework run ends")
p.add_argument("transport",nargs=argparse.REMAINDER,help="After --, the executable and argv for store.py RPC")
args=p.parse_args()
cmd=args.transport[1:] if args.transport[:1]==["--"] else args.transport
if not cmd:
    p.error("provide an RPC transport after --")
config=home/".config/threadwell"
config.mkdir(mode=0o700,parents=True,exist_ok=True)
target_config=config/"config.json"
settings=json.loads(target_config.read_text()) if target_config.exists() else {}
settings.update(host=args.host,url=args.url,workspace=str(args.workspace),transport=cmd,
    updates={peer:[peer,"update"] for peer in ("codex","claude")} if args.native_updates else {})
if args.rework:
    settings["rework"]={"enabled":True,"timeout":1800,"claude_args":["--permission-mode","bypassPermissions"],
        "codex_args":["--dangerously-bypass-approvals-and-sandbox"],"env":{},**{k:v for k,v in settings.get("rework",{}).items() if k!="enabled"}}
if args.notify:
    settings["notify"]=[args.notify]
target_config.write_text(json.dumps(settings,indent=2))
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
