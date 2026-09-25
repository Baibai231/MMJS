"""Identity record for the paper's OMEN attacker. This is not an OMEN implementation.

The HTPG paper uses OMEN as an ordered Markov guesser. The local Markov
substitute must not be reported under this name. Author code is
https://github.com/RUB-SysSec/OMEN. A no-feedback file or stdout mode is
required for cross-site evaluation. The plaintext-simulation mode can change
its length schedule after successful guesses, so it is a different experiment.
"""
from __future__ import annotations

from pathlib import Path


UPSTREAM_URL = "https://github.com/RUB-SysSec/OMEN"
PAPER_ROLE = "R3"
NO_FEEDBACK_MODE = "file_or_stdout_no_success_feedback"
NOT_INTEGRATED = "author_omen_not_vendored"


def omen_status(binary: str | Path | None = None) -> dict:
    """Return whether a local author binary exists. Never emits guesses."""
    path = None if binary is None else Path(binary)
    present = bool(path is not None and path.is_file())
    return {
        "attacker": "OMEN",
        "integrated": False,
        "binary_present": present,
        "upstream": UPSTREAM_URL,
        "required_mode": NO_FEEDBACK_MODE,
        "status": "incomplete" if not present else "binary_present_but_not_wired",
        "reason": NOT_INTEGRATED,
        "markov_substitute_is_omen": False,
        "paper_budget_1e8_completed": False,
        "guesses_emitted": 0,
    }
