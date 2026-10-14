# Contributing

- One change per pull request, with a test. New source features get a synthetic fixture in
  `godot_gulp/fixtures.py` (authored with OpenUSD at test time) and an assert in `tests/` on the written glTF.
- Never commit third-party assets, converted packs or their reports. Fixtures are synthetic only.
- No machine paths, user names or hostnames in code, docs or tests: configuration comes from arguments or the
  environment (`GODOT_GULP_GODOT`, `GODOT_GULP_JOBS`).
- Commit messages, like code, never name a commercial asset pack, its vendor or the project you tested it in: say
  "a vendor pack" or "a test corpus" and give the numbers. Maintainers can keep a local deny list in
  `.git/info/deny-words` (one case-insensitive pattern per line); the sample `pre-push` hook in `tools/pre-push`
  refuses a push whose messages match it, and runs gitleaks over the pushed commits.
- Run `pytest` and, with Godot 4.x on `PATH`, `godot-gulp conformance` before you open the pull request.
- Ported code keeps its original notice: add the source, licence and what was ported to `THIRD_PARTY.md`, and mark
  the ported functions in a comment.
- By contributing you agree your work is licensed under Apache-2.0.
