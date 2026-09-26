# Demo reasoning-mode issue context

Real source excerpts from the real OptiLearn project
(<https://github.com/Ilakiancs/OptiLearn>), used to give the demo's five
non-Whisper issues something real to investigate. Unlike the Whisper bug
(`app/demo/optilearn.py`), these have no bundled sandbox harness, so
Reproduce/Debug run as text-only reasoning for them — but "no sandbox" isn't
an excuse to fabricate the code Bob and Granite read.

Each file here is a real excerpt, sliced from the real file at the commit
right before the real fix that later landed for it — so the bug being
described is actually present in the bundled code, not asserted about code
that's already been fixed. Line numbers in each `_manifest.json` are the
real line numbers from that revision.

| Scenario           | Commit fixing it (parent = what's bundled)        | File(s)                                                      |
| ------------------ | --------------------------------------------------- | ------------------------------------------------------------ |
| `cache_key`         | `91cb905` Fix /explain and /ask wrong cache key      | `app/api/routes/feature1.py`                                 |
| `persona_call`       | `838fa3a` fix persona call URL                       | `app/api/routes/persona.py`                                  |
| `offline_toggle`     | `2ee473b` fix force offline network status check     | `app/services/model_client.py`                                |
| `thinking_config`    | `4bc418b` Fix ThinkingConfig pydantic crash; TTS spam | `app/services/model_client.py`, `app/services/tts_client.py` |
| `model_tag_match`    | `8d3e91d` fix grade_level parse and model name match  | `app/main.py`, `app/services/model_client.py`, `app/api/routes/translate.py` |

To regenerate or add another one: fetch the file at the fix commit's first
parent via the GitHub API (`GET /repos/Ilakiancs/OptiLearn/contents/{path}?ref={parent_sha}`),
slice a window around the real function, and write `_manifest.json` as
`{"<repo-relative path>": {"file": "<bundled filename>", "start_line": <real line the excerpt starts at>}}`.
`app/demo/loader.py`'s `load_issue_context()` reads it back into the
`{path#Lline: text}` shape `format_sources()` already expects.
