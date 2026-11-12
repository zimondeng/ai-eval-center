# Domain Docs

This repository uses a single-context layout:

- `GLOSSARY.md` at the repository root defines domain vocabulary.
- `docs/adr/` contains architectural decision records.

Before exploring an area, read the root glossary and any ADRs relevant
to that area. If a file or directory is absent, proceed without
suggesting that it be created upfront; `/domain-modeling` creates domain
documentation when terms or decisions are resolved.

Use glossary terms in issues, proposals, hypotheses, and tests. If a
needed concept is absent, consider whether it reflects a real gap in
the domain language and note it for `/domain-modeling`.

If a proposal conflicts with an ADR, identify the conflict explicitly
and explain why the decision may need to be reopened.
