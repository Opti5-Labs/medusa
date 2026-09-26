# Golden Bob run for the OptiLearn demo

Drop a recorded Bob investigation here as `investigation.jsonl` and the backend replays it
(`BOB_MODE=replay`) instead of calling Granite for the investigators. Until that file exists,
the demo investigators run live on Granite and are labelled as such in the UI.

Never hand-write or edit the transcript: the UI labels it "Recorded Bob session", so it must be
real Bob output. Review it for secrets and personal paths before committing.

## Format

One JSON object per line. The first line is a header:

```json
{"recorded": true, "captured_with": "Bob Shell <version>", "captured_at": "2026-09-26"}
```

Then one line per event, in order:

```json
{"source": "investigator:runtime", "level": "info", "message": "..."}
{"source": "investigator:repository", "level": "info", "message": "..."}
{"source": "investigator:skeptic", "level": "info", "message": "..."}
{"source": "synthesis", "level": "result", "message": "..."}
```

`source` is one of `investigator:runtime`, `investigator:repository`, `investigator:skeptic`,
`synthesis`. `level` is `info`, `warn` or `result`.

The last line is the synthesis:

```json
{"synthesis": {"root_cause": "...", "confidence": 0.9}}
```
