---
name: verify-release
description: Verify a cu-local release — CI green, leak scan clean, fresh-clone install works, GitHub page renders.
---
1. `scripts/leak-scan.sh` must print `clean`.
2. `gh run list -R puretensor/cu-local -L 1` must show the latest run as success.
3. Fresh clone on a Mac: `git clone … && cd cu-local && ./install.sh && cu-local --version && cu-local doctor --endpoint URL`.
4. Fetch https://github.com/puretensor/cu-local and compare it against the README: the hero SVG and the bench chart load and the quickstart commands match.
