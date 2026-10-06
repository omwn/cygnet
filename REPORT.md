# Wordnet Quality Reports

`scripts/report.py` scans one or more pre-synthesised Cygnet XML files and
produces a human-readable summary of every issue that would cause Cygnet to
silently drop or modify data — phrased for upstream wordnet maintainers who
may not be familiar with Cygnet's internals.

For the complementary question — not "what's wrong" but "how big is it and
what's it made of" — see `scripts/statistics.py`:

```bash
uv run python scripts/statistics.py --md > reports/_statistics.md
```

It reports overall counts (languages, concepts, senses, relations,
definitions, examples) plus breakdowns by part of speech and relation type,
read directly from the built `web/cygnet.db`.

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

## One Markdown report per wordnet

`--all`/`--md` concatenates every wordnet into a single stream. To get one
Markdown file per wordnet instead (e.g. for browsing individually in
`reports/`, or linking a specific wordnet's report from an issue), loop over
the pre-synth files yourself and redirect each invocation:

```bash
mkdir -p reports
for f in bin/cygnets_presynth/*.xml; do
  stem=$(basename "$f" .xml)
  uv run python scripts/report.py --md "$f" > "reports/${stem}.md"
done
```

This produces `reports/own-pt-1.0.0.md`, `reports/wordnet_lv-1.0.md`, etc. —
one file per resource. `cili-1.0.xml` (the interlingual index, not a
wordnet) produces an empty file, since `report_file()` intentionally prints
nothing for it — delete or ignore `reports/cili-1.0.md`.

Each `uv run` invocation re-resolves the environment, so looping like this
over all ~78 wordnets takes a couple of minutes.

---

## Cross-wordnet issue summary

`--summary` re-groups every wordnet's issues by *issue type* instead of by
file, so you can see how one kind of problem (e.g. "Concepts without
definitions") is distributed across every wordnet at a glance, ranked
worst-first:

```bash
# Plain text, every wordnet in bin/cygnets_presynth/
uv run python scripts/report.py --summary

# Markdown
uv run python scripts/report.py --md --summary > reports/_summary.md

# Restrict to specific files (otherwise --summary implies --all)
uv run python scripts/report.py --summary bin/cygnets_presynth/wordnet_lv-1.0.xml bin/cygnets_presynth/estwn-2.7.0.xml
```

This is the better tool when the question is "which wordnets have issue X
and how much" rather than "what's wrong with wordnet Y" — e.g. it's how the
two `Unrecognised part-of-speech code` entries below were found to span
`estwn`/`odwn-nl`/`wordnet_lv` (shared empty-string code) and `kenet` (code
`'i'`) respectively.

Within a single wordnet's report, when one concept dominates the raw
`failed_matches` log (e.g. a common, heavily-inflected word with many
corpus examples that all fail morphological matching), the **Example
sentences not matched to a sense** section samples across distinct
concepts rather than showing the same one N times — so a 10-item sample is
representative of the file's spread of problems, not just whichever concept
happened to appear first.

`--summary`'s output always opens with a **Senses per lemma, by wordnet**
table, ranked highest-first regardless of issue severity. A healthy wordnet
is usually in the low single digits; a ratio of dozens or more is a strong
signal of a conversion bug rather than genuine polysemy — e.g. a bilingual
dictionary converter linking one lemma to every synset that matches its
gloss, instead of just the correct sense (this is exactly how
`ancientgreek-grc`'s ~37/lemma ratio and the resulting POS-mismatch flood
were traced back to a specific bug in its upstream converter script).

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
| **Hypernym cycles spanning multiple wordnets** | A relation from this file creates a cycle when combined with relations already merged from other wordnets, caught immediately after this file is merged | The offending relation is removed; the existing cross-wordnet chain is shown |
| **Hypernym cycles found only after the full build (residual)** | A relation from this file only closes a cycle once *later*-merged wordnets are also in the graph, so no per-file check (including the one above) could catch it at merge time — see `resolve_residual_cycles()` in `cyg/merge.py` | Caught by one final whole-graph check after every wordnet is merged; the resource judged most likely to be wrong (`cyg.merge.LOW_TRUST_RESOURCES`) has its relation removed |
| **Relations reversed relative to another wordnet** | This file asserts `A hypernym B` but another wordnet already established `B hypernym A` | The conflicting relation is skipped |
| **Duplicate IDs** | A concept, entry, or sense ID appears more than once | Duplicate concepts crash the build; duplicate entries/senses are merged or skipped |
| **Unrecognised part-of-speech code** | A `<Synset>` or `<Lemma>` uses a `partOfSpeech` value Cygnet doesn't recognise (valid: `n`, `v`, `a`, `r`, `s`, `c`, `p`, `x`, `u`) — including a missing/empty attribute | Coerced to `UNK`; the concept's real part of speech is lost |

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

- **Hypernym cycles** (both the per-file and "found only after the full
  build" sections) — the "existing chain" shows the path already in the
  database, helping the maintainer identify which link in their hierarchy
  is incorrect. The residual section in particular can surface a genuine
  error in a wordnet that looks clean on its own — it only became visible
  once other wordnets were merged in.

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
