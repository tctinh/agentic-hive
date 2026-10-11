# A small game-development demo

This demo uses Hive's actual dashboard and devlog renderer. Its member names,
Room messages, activity, and host metrics are fictional. The story paraphrases
the Debter Room's completed Drowned World playground: a creator asks for an
isolated playable prototype; members connect terrain, saves, cards, and hordes;
a native click check catches an input problem; the team delivers one devlog for
review. Members remain generalists in one Room.

The four game images are unchanged native captures from that actual prototype.
The [sample article](debter-playground/index.md) preserves its review status and
evidence limits. It is a shortened documentation excerpt, not the complete
project devlog or a new gameplay run.

Run from a checkout with Python 3.10+ and Hive's Python dependencies installed
(`markdown-it-py`, plus `tomli` on Python 3.10):

```sh
python3 docs/demo/serve.py
```

Open <http://127.0.0.1:8197> for members and Room, or
<http://127.0.0.1:8197/#devlog=debter%2Fdrowned-chunk-playground> for the article.
The fixture binds only to localhost, copies its files into a temporary habitat,
and rejects writes and terminal connections. It neither reads the live Hive
habitat nor starts real members. Stop it with Ctrl+C.

## Capture provenance

Source: Debter's `drowned-chunk-playground` devlog, evidence commit
`d5a4eac90de6f6c5a0e173d06cca073773b4398e`. The
[media manifest](debter-playground/media_manifest.json) retains hashes and paths
relative to the prototype's evidence folder; private host paths and raw receipts
are omitted. Public demo captures contain only the game viewport and sanitized
dashboard data.

Gameplay art uses Hana Caraka by [Otterisk](https://otterisk.itch.io/).
Raw sprite sheets, licensed asset packs, and game source are not included.
