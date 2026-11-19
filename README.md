# Bohr Product Arena — skill packages

Skill packages submitted to the Bohr AI Product Weekly Contest (period 4, 学术效率).

Each directory under `p4/` is one challenge's package: `SKILL.md` describes the contract and the
single command an agent runs; `scripts/` holds the implementation. The packages are
deterministic (standard library only where possible), write their deliverables under
`/app/output`, and emit `result.json` with the case id, evidence locators, uncertainty and a
machine-checkable verification block.

Layout: `p4/<challenge-id>/SKILL.md`, `p4/<challenge-id>/scripts/<entry>.py`.
