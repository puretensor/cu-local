# Contributing

Thanks for helping. The most useful contribution right now is a **hardware report**: run the reference bench on your setup and open an issue with the "Hardware report" template.

```bash
cu-local doctor --endpoint http://YOUR-SERVER:PORT/v1
cu-local bench  --endpoint http://YOUR-SERVER:PORT/v1 --repeats 2 --label "my setup"
```

Attach `results.json` from the folder it prints.

## Code

- `cu_local.py` is the whole tool. Keep it to one file, standard library only, and Python 3.9 compatible (the macOS system Python). That means no `X | Y` type hints and no `match`.
- Run the tests before a PR: `python3 -m unittest discover -s tests -v`. They are offline and run on Linux.
- Never commit anything copied from ChatGPT.app or `~/.codex`. That includes plugin manifests, model catalogs and instruction text, even as test fixtures. cu-local reads them from the user's install at runtime.
- Run `sh scripts/leak-scan.sh` before pushing.
