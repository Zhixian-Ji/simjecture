"""Small archived launch records for reader compatibility tests; never execute these."""

import hashlib
import json
from pathlib import Path


def write_legacy_launch(request):
    root = Path(request.output_directory)
    operator = root / "operator_input"
    operator.mkdir(parents=True, exist_ok=True)
    (operator / "hypothesis.txt").write_text(request.hypothesis)
    record = request.model_dump(mode="json", exclude={"hypothesis", "instruction", "executable"})
    record.update(
        schema_version="0.3.0",
        hypothesis_file="hypothesis.txt",
        hypothesis_sha256=hashlib.sha256(request.hypothesis.encode()).hexdigest(),
        instruction_file=None,
        dsh_session_id="archived-session" if request.engine == "dsh" else None,
        automatic_resume_blockers=[],
    )
    (operator / "launch.json").write_text(json.dumps(record))
    return root
