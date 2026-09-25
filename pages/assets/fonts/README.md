# Fonts

Self-hosted so the demo makes no third-party requests.

- `sq-serif.woff2`: Source Serif 4 variable (roman), © Adobe, SIL OFL 1.1
  (`SourceSerif4-OFL.md`). Subset to Latin-1 plus the punctuation and symbols the
  page uses, `wght` limited to 400–650, `opsz` kept. Renamed "SQ Serif" because
  "Source" is a Reserved Font Name and a subset is a Modified Version.
- `commit-mono-400.woff2`: Commit Mono 1.143 regular, SIL OFL 1.1
  (`CommitMono-OFL.txt`), subset the same way.

Built with fontTools (`pyftsubset`-equivalent API) from the upstream releases
`adobe-fonts/source-serif` 4.005R and `eigilnikolajsen/commit-mono` v1.143.
