#!/usr/bin/env python3
"""Regression tests for exact bind-source topology verification."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "lib/verify_bind_mount.py"


def run(fixture, source_record, target_record):
    records = fixture / "records.json"
    records.write_text(json.dumps({"source": source_record, "target": target_record}))
    completed = subprocess.run(
        [str(VERIFIER), "--source", "/source/tree", "--target", "/target"],
        env={**os.environ, "PATH": f"{fixture}:{os.environ['PATH']}",
             "MOCK_RECORDS": str(records)},
    )
    return completed.returncode


def main():
    with tempfile.TemporaryDirectory(prefix="bind-topology-") as temporary:
        fixture = Path(temporary)
        mock = fixture / "findmnt"
        mock.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "data=json.load(open(os.environ['MOCK_RECORDS']))\n"
            "key='source' if '-T' in sys.argv else 'target'\n"
            "print(json.dumps({'filesystems':[data[key]]}))\n"
        )
        mock.chmod(0o755)
        source = {"source": "/dev/loop0", "target": "/source", "fstype": "btrfs",
                  "maj:min": "7:0", "fsroot": "/root-A"}
        target = {"source": "/dev/loop0[/root-A/tree]", "target": "/target", "fstype": "btrfs",
                  "maj:min": "7:0", "fsroot": "/root-A/tree"}
        assert run(fixture, source, target) == 0
        wrong_root = {**target, "fsroot": "/root-B/tree"}
        assert run(fixture, source, wrong_root) != 0
        assert run(fixture, source, {**target, "source": "/dev/loop1[/root-A/tree]"}) != 0
        assert run(fixture, source, {**target, "target": "/alias"}) != 0
        records = fixture / "records.json"
        records.write_text("not json")
        completed = subprocess.run(
            [str(VERIFIER), "--source", "/source/tree", "--target", "/target"],
            env={**os.environ, "PATH": f"{fixture}:{os.environ['PATH']}",
                 "MOCK_RECORDS": str(records)},
        )
        assert completed.returncode != 0


if __name__ == "__main__":
    main()
