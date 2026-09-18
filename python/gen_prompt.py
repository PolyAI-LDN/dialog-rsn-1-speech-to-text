"""Write node/prompt.js from prompt.py, so the two agents share one prompt."""

import json
import pathlib

from prompt import GREETING, INSTRUCTIONS

pathlib.Path(__file__).resolve().parent.parent.joinpath("node", "prompt.js").write_text(
    "// The support agent's instructions. All the data it knows is in here, no tools.\n"
    "// Generated from ../python/prompt.py by gen_prompt.py; edit prompt.py and rerun it.\n\n"
    f"export const INSTRUCTIONS = {json.dumps(INSTRUCTIONS)};\n\n"
    f"export const GREETING = {json.dumps(GREETING)};\n"
)
print("wrote node/prompt.js")
