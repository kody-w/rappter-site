#!/usr/bin/env python3
"""mesh/tools/build.py — REPLAY the offers chain into the JSON projections.

The chain (mesh/chain/offers.jsonl) is the truth; offers.json is generated
from it and NEVER hand-edited. Every frame is verified with the reference
implementation before anything is emitted; one bad frame refuses the whole
build (refuse, never repair).

  python3 mesh/tools/build.py           regenerate mesh/offers.json
  python3 mesh/tools/build.py --check   verify chain + confirm offers.json
                                        matches the replay (CI gate; exit 1
                                        on drift)

Deterministic on purpose: the output derives only from the chain, so the
--check diff is meaningful. No timestamps are invented at build time.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MESH = HERE.parent
sys.path.insert(0, str(HERE))
import rapp1 as rapp  # the vendored public reference implementation


def load_chain():
    frames = []
    with open(MESH / "chain" / "offers.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                frames.append(json.loads(line))
    return frames


def verify_chain(frames):
    rid = json.loads((MESH / "rappid.json").read_text())["rappid"]
    head = None
    for i, fr in enumerate(frames):
        ok = rapp.verify_frame(fr, head=head, stream_id_of_record=rid)
        if not ok[0]:
            raise SystemExit(f"REFUSED: frame {i} does not verify: {ok}")
        head = fr
    return rid


def replay(frames, rid):
    genesis = frames[0]["payload"]
    if genesis.get("mesh") != "genesis":
        raise SystemExit("REFUSED: frame 0 is not the mesh genesis")
    offers, notices = [], []
    for fr in frames[1:]:
        p = fr["payload"]
        kind = p.get("mesh")
        if kind == "offer":
            offers.append({**p["offer"],
                           "frame_hash": fr["frame_hash"],
                           "seq": fr["seq"]})
        elif kind == "notice":
            notices.append({**p["notice"],
                            "frame_hash": fr["frame_hash"],
                            "seq": fr["seq"]})
        else:
            # Refuse, never repair: a validly-hashed frame this replay does
            # not understand must stop the build, not vanish from the board.
            raise SystemExit(f"REFUSED: frame {fr['seq']} has unknown mesh kind {kind!r}")
    return {
        "schemaVersion": "rapp-mesh/1",
        "stream": rid,
        "head": {"seq": frames[-1]["seq"],
                 "frame_hash": frames[-1]["frame_hash"]},
        "what": genesis["charter"]["what"],
        "operator": genesis["charter"]["operator"],
        "constraints": genesis["charter"]["constraints"],
        "unit_note": "Prices are rpp — rated agent-work units. Rated means measured on a standing harness; launch prices are ESTIMATES until an offer's first calibration run publishes a measured rating (see notices). The rating method itself is offer agent-rating-calibration.",
        "notices": notices,
        "verify": {
            "chain": "https://rappter.com/mesh/chain/offers.jsonl",
            "how": "replay with https://github.com/kody-w/rapp-1 rapp.py verify_frame, or in-browser at https://rappter.com/mesh/",
        },
        "claim": {
            "human": "https://rappter.com/mesh/#hire",
            "agent": {"method": "POST",
                      "url": "https://formspree.io/f/mgawgado",
                      "content_type": "application/json",
                      "fields": {"offer_slug": "string, required",
                                 "contact": "string, required — where the artifact goes",
                                 "scope": "string, required — the bounded task",
                                 "source": "mesh-agent"}},
        },
        "offers": offers,
    }


def main():
    args = sys.argv[1:]
    if args not in ([], ["--check"]):
        # A typo like --chekc must never silently regenerate and exit 0.
        print(__doc__)
        raise SystemExit(2)
    frames = load_chain()
    rid = verify_chain(frames)
    doc = replay(frames, rid)
    out = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    target = MESH / "offers.json"
    if args == ["--check"]:
        if not target.exists() or target.read_text(encoding="utf-8") != out:
            raise SystemExit("DRIFT: offers.json does not match the chain replay — regenerate with build.py, never hand-edit")
        print(f"OK: {len(frames)} frames verify · offers.json matches the replay")
        return
    target.write_text(out, encoding="utf-8")
    print(f"replayed {len(frames)} frames -> offers.json ({len(doc['offers'])} offers) · head {doc['head']['frame_hash'][:16]}")


if __name__ == "__main__":
    main()
