# Contributing

Small project, small rules.

## Before your first commit

```bash
pip install -r requirements.txt
py -3 tests/test_sanitization.py --init-local   # protect your own identifiers
```

`--init-local` writes `.sanitize-local` (gitignored) from your install's
`login_history.json`, so the sanitization check can catch *your* account and
character names before they land in a commit. It prints only counts, never
names. See the README section "Protecting your own identifiers".

## Before you push

```bash
py -3 tests/test_sanitization.py    # no PII, no absolute paths
py -3 tools/test_viewer.py          # the main suite
```

Run both, and know what each can tell you:

* `tests/test_sanitization.py` needs no install and is expected to pass. It
  can only check the committed hash list, so **your own** identifiers are
  caught only by a local run after `--init-local`. That is the correct trade;
  the alternative is uploading everyone's names to a CI provider.
* `tools/test_viewer.py` **does not skip all of its install-dependent arms.**
  Many do skip -- 440 of 964 on the last CI run -- but that run still ended
  `FAILED (failures=32, errors=17)` with no game present. So a green local run
  without an install is a partial measurement. With an install it takes far
  longer than CI's 38 s: the arms CI skips do real content scans.

**CI is currently switched off** -- `.github/workflows/ci.yml` is disabled, so
opening a pull request runs nothing at all. Do not read a missing check as a
pass.

## House rules

* **The game install is read-only.** `tools/comod.py` is the only code
  allowed to write into it, ever. New tools read.
* **Stdlib first.** The dependency list is `pillow` and `numpy`, and it is a
  feature. A new dependency needs a reason the standard library cannot answer.
* **Paths from the network or the UI go through `safepath.confine()`.** No
  exceptions; there is a test that checks the wiring.
* **Mark findings *verified* or *inferred*.** Format docs state which fields
  were proved against real files and which are educated guesses. Keep that
  distinction when you edit them.
* **No absolute paths, no personal names, no server names.** The sanitization
  test enforces the first two. For the third: this project is about the file
  formats, not about any particular place to play.

## Do not attach captures, logs with names in them, or your `login_history.json`

When reporting a bug, paste the specific error text. If a path in it contains
your Windows username, redact it. The issue template says the same thing.
