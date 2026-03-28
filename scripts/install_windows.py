"""
Windows startup task installer.

Registers Engram as a Windows Task Scheduler job that launches on user logon,
runs silently in the background, and restarts automatically on failure.

Run once with: python scripts/install_windows.py
Uninstall with: python scripts/install_windows.py --uninstall
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TASK_NAME = "EngramSecondBrain"
ENGRAM_ROOT = Path(__file__).parent.parent.resolve()
ENTRY_POINT = ENGRAM_ROOT / "main.py"
PYTHON = Path(sys.executable)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def install() -> None:
    """Register the Engram startup task in Windows Task Scheduler."""
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Engram — local-first semantic lifelogging engine</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{PYTHON}</Command>
      <Arguments>"{ENTRY_POINT}"</Arguments>
      <WorkingDirectory>{ENGRAM_ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    xml_path = ENGRAM_ROOT / "engram_task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    result = _run([
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/XML", str(xml_path),
        "/F",
    ])
    xml_path.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"✓ Engram registered as startup task: {TASK_NAME}")
        print(f"  Entry: {ENTRY_POINT}")
        print(f"  Run 'schtasks /Run /TN {TASK_NAME}' to start it now.")
    else:
        print(f"✗ Failed to register task:\n{result.stderr}")
        sys.exit(1)


def uninstall() -> None:
    """Remove the Engram startup task."""
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode == 0:
        print(f"✓ Task '{TASK_NAME}' removed.")
    else:
        print(f"Note: {result.stderr.strip()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Engram Windows startup installer")
    parser.add_argument("--uninstall", action="store_true", help="Remove the startup task")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    else:
        install()
