# Wordnet Quality Reports

`scripts/report.py` scans one or more pre-synthesised Cygnet XML files and
produces a human-readable summary of every issue that would cause Cygnet to
silently drop or modify data — phrased for upstream wordnet maintainers who
may not be familiar with Cygnet's internals.

---

## Generating a report

```bash
# Report for a single wordnet
uv run python scripts/report.py bin/cygnets_presynth/own-pt-1.0.0.xml

# Report for every wordnet (appends a separator between each)
uv run python scripts/report.py --all

# Save to a file
uv run python scripts/report.py --all > reports/all.txt

# Markdown output (suitable for GitHub Issues or wikis)
uv run python scripts/report.py --md bin/cygnets_presynth/dn-2025-07-03.xml
```

The report is purely informational — it writes to stdout and always exits 0.

---

## What the report checks

Issues are grouped into three severity levels: **CRITICAL** (data is lost),
**WARNING** (possible errors), and **INFO** (for awareness).

### Sources used

The script combines three sources, using whichever are available:

| Source | When available |
|---|---|
| The pre-synth XML file itself | Always |
| `bin/cygnets_presynth/{name}_log.json` | After running conversion scripts 1–5 |
| `bin/relation_conflicts.json` | After running the full build (`build.sh`) |

---

### CRITICAL issues — data is lost

| Issue | Cause | What Cygnet does |
|---|---|---|
| **Concepts without definitions** | `<Concept>` in this file has no matching `<Gloss>` | Concept and all its senses are deleted |
| **Word entries with no wordforms** | `<Lexeme>` has no `<Wordform>` children | Entry and all its senses are silently skipped |
| **Hypernym loops (within this file)** | A set of `hypernym`/`instance_hypernym` relations forms a cycle internally | The relation that closes the loop is removed |
| **Hypernym cycles spanning multiple wordnets** | A relation from this file creates a cycle when combined with relations from other wordnets | The offending relation is removed; the existing cross-wordnet chain is shown |
| **Relations reversed relative to another wordnet** | This file asserts `A hypernym B` but another wordnet already established `B hypernym A` | The conflicting relation is skipped |
| **Duplicate IDs** | A concept, entry, or sense ID appears more than once | Duplicate concepts crash the build; duplicate entries/senses are merged or skipped |

### WARNING issues — possible errors

| Issue | Cause | What Cygnet does |
|---|---|---|
| **Contradictory relations within this file** | Same directed relation asserted in both directions (e.g. `A hypernym B` and `B hypernym A`) | First accepted; second skipped. Concept IDs are shown with their wordforms (e.g. `dog hypernym animal`) |
| **Senses referencing undeclared entries** | `<Sense signifier="…">` points to an entry ID not in this file | Sense silently skipped |
| **Example sentences with no matching sense annotations** | `<Example>` has no `<AnnotatedToken sense="…">` whose sense ID is in this file | Example silently discarded |
| **Self-referential relations** | `source == target` in a `<ConceptRelation>` or `<SenseRelation>` | Silently skipped |
| **POS mismatches: synset vs CILI concept** | Synset's declared POS differs from CILI's record for the same concept | CILI's POS is used; synset's is ignored |
| **POS mismatches: lexeme vs its concept** | Lexeme's POS differs from its linked concept's POS | Stored as-is; may indicate a wrong sense link |
| **Relations with incompatible POS categories** | Source and target have POS categories that are incompatible for the relation type | Relation skipped |
| **Senses with unresolvable synset (conversion time)** | At conversion, the synset ID in the source LMF could not be found | Sense absent from the pre-synth file |
| **Example sentences not matched to a sense (conversion time)** | Two sub-cases: (1) the concept had no senses so no wordforms could be identified — marked `no senses/wordforms found`; (2) the target lemma could not be matched in the sentence after morphological analysis | Example absent from the pre-synth file |

### INFO issues — for awareness

| Issue | Cause | What Cygnet does |
|---|---|---|
| **Non-standard relation types** | Relation type is not in the GWA standard set | Stored as-is; may not be understood by other tools |
| **Concept relations already covered by another wordnet** | Relation is a duplicate of one already loaded from another source | Silently ignored (stored once) |
| **Synsets without a CILI mapping** | Synset has no ILI entry and is assigned a wordnet-local ID | Not interlinked with other wordnets |
| **Defined concepts with no senses in this file** | Concept has a gloss but no word sense in this file links to it | Remains as a definition-only synset unless another wordnet provides senses |

---

## Sending a report to an upstream maintainer

When filing a bug report with an upstream wordnet team, the most actionable
sections are:

- **Relations reversed relative to another wordnet** — typically indicates
  hypernym direction is inverted (more-general → more-specific instead of
  more-specific → more-general). Show the table of examples.

- **Hypernym cycles** — the "existing chain" shows the path already in the
  database, helping the maintainer identify which link in their hierarchy
  is incorrect.

- **Concepts without definitions** — straightforward: a synset ID is missing
  its gloss.

The Markdown output (`--md`) is convenient for pasting directly into a GitHub
Issue or a wiki page.

---

## Relation direction convention

Cygnet follows the Global WordNet Association standard:

| Relation | Direction |
|---|---|
| `hypernym` | more-specific → more-general (`dog hypernym animal`) |
| `hyponym` | more-general → more-specific (auto-generated; do not assert explicitly) |
| `mero_member` | whole → part (`forest mero_member tree`) |
| `holo_member` | part → whole (auto-generated) |
| `causes` | cause → effect |
| `entails` | entailing → entailed |

Only assert one direction of each pair — Cygnet generates the inverse automatically.

See the [Global WordNet LMF schema](https://globalwordnet.github.io/schemas/)
for the full relation type list.
