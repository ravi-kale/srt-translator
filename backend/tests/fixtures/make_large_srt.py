"""Generate a large synthetic .srt for load-testing the UI and pipeline.

Usage:  python make_large_srt.py [N_BLOCKS] [OUT_PATH]     (defaults: 3000, large_3000.srt)
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

OPENERS = ["Listen", "Wait", "Look", "Okay", "Hey", "Honestly", "Seriously", "Come on", "Well", "So"]
SUBJECTS = ["the shipment", "your brother", "the captain", "this whole plan", "the old warehouse", "my car",
            "the witness", "that phone call", "the money", "our last chance"]
VERBS = ["is going to fall apart", "was never part of the deal", "keeps getting worse", "changes everything",
         "doesn't add up", "is exactly what they wanted", "won't wait for us", "could get us all killed"]
IDIOMS = ["Let's cut to the chase.", "That's a piece of cake.", "Break a leg.", "Don't spill the beans.",
          "It's raining cats and dogs.", "We're back to square one.", "Hold your horses!", "You hit the nail on the head."]


def ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"large_{n}.srt")
    rng = random.Random(42)
    t = 1.0
    lines: list[str] = []
    i = 1
    while i <= n:
        kind = rng.random()
        if kind < 0.25 and i + 1 <= n:
            # A sentence split across two cues.
            first = f"{rng.choice(OPENERS)}, {rng.choice(SUBJECTS)}"
            second = f"{rng.choice(VERBS)}."
            texts = [first, second]
        elif kind < 0.35:
            texts = [rng.choice(IDIOMS)]
        elif kind < 0.40:
            texts = ["- Really?\n- Really."]
        else:
            texts = [f"{rng.choice(OPENERS)}, {rng.choice(SUBJECTS)} {rng.choice(VERBS)}{rng.choice(['.', '?', '!'])}"]
        for text in texts:
            if i > n:
                break
            dur = round(rng.uniform(1.0, 4.5), 3)
            lines.append(f"{i}\n{ts(t)} --> {ts(t + dur)}\n{text}")
            t += dur + (rng.uniform(1.6, 4.0) if rng.random() < 0.12 else rng.uniform(0.05, 0.4))
            i += 1
    out.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {n} blocks to {out}")


if __name__ == "__main__":
    main()
