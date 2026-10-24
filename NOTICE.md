# Source and data provenance

Tacto Negotiator is the implemented buyer application in `src/tacto_negotiator`.
It derives from the Tacto Technology GmbH negotiation arena starter kit:
https://github.com/tacto-playground/negotiation-arena-kit

The original local import is commit `0166e6948f53ecbf629ab371135f69ec3243f998`.
Its `starter_kit/` tree is restored under `materials/starter_kit/` as reference material.
The application does not import or execute code from that archive.

The original MIT copyright notice is retained in `LICENSE`. The public dataset
in `data/` is copied from the supplied snapshot and retains its attribution files;
its CC BY 4.0 license is in `LICENSE-data`. Documentation adapted from the challenge
brief retains links to the archived original sources.

Recorded experiments in `docs/results/` predate the package migration. Their source
hashes and commit IDs deliberately remain unchanged. Historical `arena_kit` modules
correspond to today's `tacto_negotiator` modules; use the recorded commits when
reproducing those exact source hashes. Migration checks are run separately.
