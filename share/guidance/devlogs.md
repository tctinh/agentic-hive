# Optional dev-log publishing guidance

When a dev-log is useful or requested, write a concise article the
Beekeeper can read in the WebUI. A concise completion handoff is sufficient
for ordinary work; no article is mandatory. Use an ordinary Markdown bundle at
$HIVE_ROOT/artifacts/devlogs/<project-slug>/<article-slug>/index.md, with
captures beside it. Lowercase slugs may contain letters, digits, dots, dashes,
and underscores. Describe the intended experience, what changed, and evidence
and remaining limits. Prefer a useful wrap-up over an implementation diary;
trivial fixes need no article. An EXPLORE proposal must stay labeled as a
hypothesis, not a shipped feature. Do not post duplicate articles for one shared
feature; integrate relevant peer evidence into the existing bundle.

Optional TOML metadata between +++ lines can set title, summary, author, date
(ISO timestamp), commit, and cover (a relative image path). Without metadata,
the reader uses the first heading and file date. Include your member name in
author. Use `![What the capture shows](screen.png)` for local PNG/JPG/GIF/WebP
images and the same syntax for MP4/WebM clips; the reader supplies playback
controls. Keep paths inside the bundle, with no remote media or raw HTML.
Use real native captures when relevant and label demo/simulation evidence.
No need to invent screenshots for nonvisual changes. Gather media first, then
write index.md last (or atomically replace it), so readers see a complete
article. This makes it visible in Hive's Dev logs tab; it does not publish to
the public internet. A short link `/#devlog=<project-slug>%2F<article-slug>` may
accompany a useful completion handoff in the relevant channel. No publication queue or routine status posts are needed.

