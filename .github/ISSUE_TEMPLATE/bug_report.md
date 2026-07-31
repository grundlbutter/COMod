---
name: Bug report
about: Something broke, rendered wrong, or refused to run
---

**Before you paste anything:**

* Do **not** attach `login_history.json`, anything from `out/`, or a zip of
  your repo folder — those can contain your account name, character names,
  and machine paths.
* If an error message contains `C:\Users\<your name>\...`, redact the name.
* `py -3 tests/test_sanitization.py` output is safe to paste: findings from
  your local list name the file, never the identifier.

**What happened**

**What you expected**

**The exact error text** (redacted as above)

**Health report** — output of:

```
py -3 tools/coviewer.py --health
```

(That report includes where your install was found — the path is fine to
redact too, the *how it was found* line is the useful part.)
