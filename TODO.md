# TODO

## Replace the Cygnet XML schema with GWA WN-LMF

Goal: drop `cyg/cygnet.xsd` and make Step 2 produce, for each source, the
best WN-LMF 1.4 wordnet we can build from it: metadata repaired, text
normalised, duplicates removed, relations fixed, examples linked to
senses.  Those cleaned files are then merged.  The public products
(`cygnet.db.gz`, `provenance.db.gz`, the per-resource provenance splits)
and the web UI read only SQLite, so neither changes.

Status (2026-11-09): planned, not started.  Supersedes the 2026-10-11
plan.  Decisions taken on 2026-11-09:

- The intermediate is one cleaned LMF file per source lexicon, useful on
  its own (loads in `wn`, validates, can be sent upstream as "your
  wordnet with the fixes applied").  Merge then does only cross-wordnet
  work.
- Example annotations live inside the file: a synset-level Example is
  moved under the Sense whose word it contains, and the word's position
  goes in `note="offsets=start,end"`.
- English definitions in non-English wordnets are kept in the cleaned
  file but explicitly marked `language="en"`; merge drops them, as it
  already does.
- Relations are stored in one direction only; the DB fills in inverses.
  Relations already present in OEWN are no longer dropped.
- A cleaned file whose content differs from the source gets `.🦢`
  appended to `Lexicon@version`.  A pass-through keeps the upstream
  version.
- Cleaning passes run in order of importance: metadata, normalisation,
  structure, dedup and trimming, relations, and example linking last, on
  the already-cleaned data.

Reviewed twice by Codex (2026-11-09); its findings are folded in and the
reports are kept outside the repo.

### Why it is feasible

The current intermediate is already LMF in all but name: converters and
merge use LMF relation names, and the root carries Lexicon attributes.
The converter's passes stay; only what they emit changes.

| Cygnet | WN-LMF 1.4 |
|---|---|
| Resource / root attributes | Lexicon attributes |
| Concept | Synset (`@ili`; local concept = `ili=""`) |
| Lexeme / Wordform[0] / Wordform[1..] | LexicalEntry / Lemma / Form |
| Script, Pronunciation (+audio) | `Lemma`/`Form@script`, Pronunciation |
| Sense | Sense (`@synset`) |
| Gloss | Definition (`@language`) |
| Example / AnnotatedToken | Example under its Sense + `note="offsets=…"` |
| SenseRelation / ConceptRelation | SenseRelation / SynsetRelation, forward direction only |
| `subtype` / `subtype_label` | the source's own `relType`, `dc:type` and label attribute (`dc:description` or `dc:relation`), kept as written; a finer `dc:type` on a known `relType` stays on that type, not on `other` |
| Provenance element | implicit: the Lexicon is the resource, element ids are the original ids; ids absorbed by within-file dedup go in `note="merged=…"` |
| CILI file, GlossTag, MT glosses | sidecars read by merge (below) |

### Step 2: the cleaning passes, in order

`cyg/clean.py` (`LmfCleaner`, replacing `WordNetToCygnetConverter`)
reads one source and writes one LMF file per Lexicon or LexiconExtension.
Each pass logs what it changed, in the same `{stem}_log.json` that
`scripts/report.py` and `scripts/log_summary.py` read, so every fix is
reportable upstream.  Passes run in this order so that later ones see
clean input; example linking in particular runs on normalised text with
duplicates already gone.

The cleaner **edits the source, it does not rebuild it**: anything it
has no opinion on (`status`, `confidenceScore`, `dc:*` attributes,
`Tag`, `Count`, `SyntacticBehaviour`, `subcat`, `members`,
`sourceSense`, `lexfile`, `ILIDefinition`, existing notes) passes
through untouched.  The converter today rebuilds elements from records
and drops all of these; that is why its memory work (chunked records)
exists.  Sources run to ~100 MB uncompressed (OEWN, estwn) and plWordNet
is larger, so a full lxml tree is 2–3 GB; the cleaner therefore makes
two streaming passes: pass A collects the indexes every decision needs
(ids, forms per entry, ILI counts, relation set, sense inventory for
example linking), pass B re-streams the file and writes each element
out, edited or dropped according to those indexes.  Everything in the
seven passes below is expressible that way.

1. **Metadata.**  Lexicon `id`, `label`, `language` (the `fix_language`
   override for invalid codes), `email`, `license` (Latvian is wrong at
   source, confirmed CC BY-SA 4.0), `version`, `url`, `citation`.
   `Requires`/`Extends` are kept as written, pointing at upstream
   versions.  The header is written exactly as `wn.lmf` requires or it
   refuses the file: `<?xml version="1.0" encoding="UTF-8"?>` and
   `<!DOCTYPE LexicalResource SYSTEM "https://globalwordnet.github.io/schemas/WN-LMF-1.4.dtd">`.
   The Lexicon `note` ("cleaned by Cygnet; inverse relations omitted;
   changes listed in <stem>_log.json", no date, so re-cleaning is
   idempotent) and the `.🦢` version suffix are decided in pass 7, only
   for a file whose content changed.
2. **Text normalisation.**  NFC on every `writtenForm`, Definition,
   Example and Pronunciation; whitespace collapsed; double-encoded HTML
   entities unescaped (the `html_entity_fixes` count); blank Definitions
   and Examples removed (`wn validate` W305/W306).
3. **Structure.**  Invalid or empty `partOfSpeech` → `u` (estwn, odwn,
   wordnet_lv, kenet); `s` is kept, merge maps it.  LexicalEntry without
   a Lemma dropped; Sense whose synset is missing dropped (E204);
   duplicate ids resolved (E101).  `dc:type` URIs shortened
   (`_shorten_uri`).  Definitions get an explicit `language`, using the
   existing per-definition check, so English glosses in a non-English
   lexicon are marked `en`.
4. **Dedup and trimming.**  LexicalEntries with the same (language,
   POS, set of written forms) merged, keeping all pronunciations,
   scripts and audio (`_merge_wordform_data`); the absorbed entries' ids
   are recorded on the survivor as `note="merged=id1 id2"` so merge can
   still write a provenance row per original id and `#/search?id=`
   deep links keep working (56K such merges today, mostly KeNet and
   ODWN).  Duplicate Senses for one (entry, synset) collapsed the same
   way; elements below the confidence threshold (0.6) dropped; an ILI
   shared by several synsets, or unknown to CILI, is cleared so the
   synset becomes a local concept (`ili=""`).  Synsets with no senses
   are **kept**: an empty ILI-linked synset is often the intermediate
   node of a hypernym chain, and `lexicalized="false"` ones are
   documented lexical gaps (merge continues to skip those).  `mul`
   member synsets get their `ili` filled in from the concepts lexicon,
   so the cleaned file stands alone.  Relations are never lost to
   dedup: every id reference (`Sense@synset`, relation targets,
   `members`, `subcat`, `sourceSense`, `SyntacticBehaviour@senses`) to
   an absorbed entry or sense is retargeted to the survivor before pass
   5 dedups, so relations from both halves end up on the kept element.
   Only a reference to an element that was actually removed (below the
   confidence threshold, or a Sense whose synset does not exist) is
   removed with it, and the log counts them.
5. **Relations.**  Direction swaps from `relation_direction_swaps.json`
   applied; duplicates and self-loops removed (W403, W502); each
   reversible relation reduced to its forward type (the keys of
   `CONCEPT_REVERSE_RELATIONS` / `SENSE_REVERSE_RELATIONS`), symmetric
   ones kept once with the lower id first; unknown types kept as-is.
   Relations also present in OEWN are **kept** (merge dedups them);
   today's `relations_path` / `skipped_existing_relations` logic goes.
   Cycle-closing hypernym edges are still removed per file
   (`check_and_remove_new_cycles` moves here from merge, or merge keeps
   it on the union graph; decide in step 2 below).
6. **Example linking.**  The spaCy pass, unchanged in method
   (`_initialize_nlp_tools`, `_find_best_match`,
   `_expand_to_token_boundaries`, batching), now on clean text.  A
   synset-level Example is moved under the matched Sense, inserted after
   that Sense's last SenseRelation or Example and before any Count (DTD
   order `SenseRelation*, Example*, Count*`).  The match goes in
   `note="offsets=…"` (format below).  A synset-level example whose
   sense cannot be determined stays under the Synset.
7. **Version and write.**  If passes 1–6 changed anything (the log is
   non-empty, or the canonical serialisation differs), the Lexicon
   `note` from pass 1 is added, `version` gets `.🦢` appended unless it
   already ends in it, and the file is written; otherwise the source is
   copied as-is (decompressed if it was `.gz`/`.xz`) and the log records
   `unchanged: true`.  Output file names use the upstream version
   (`{id}-{upstream version}.xml`) so they stay ASCII.  Then validate:
   `wn.lmf.load()` on the header and structure (the exact declaration
   and DOCTYPE, no unknown elements), DTD via `xmlstarlet` (ID
   uniqueness, IDREF resolution), and `wn.validate()` with E-codes fatal
   and W-codes written to the log, W404 (missing reverse relation)
   excluded because it is by design.  `wn.validate()` does not support a
   LexiconExtension and `wn.add()` skips one whose base is not installed;
   no current source is an extension, so if one arrives it gets the DTD
   check plus our own reference checks, and its test fixture includes
   its base.  `wn.add()` is used only in the test suite, on fixtures.

On the current build logs, 80 of ~210 lexicons would need nothing and
pass through (every `omw-pronoun-*`, most `omw-*-2.0`, abwn, copwn,
kurdnet, mow, oswn, …); 35 change only their Example elements (oewn,
galnet, bangla, omw-bg/ca/es/eu/it/sq, all `tufs-*`, usgw-gd, …).  Those
counts will shift once pass 1 fixes headers and pass 3 marks definition
languages.

The `.🦢` suffix: `wn` treats a lexicon version as an opaque string (its
own docs use `1.3+omw`), so `oewn:2025+.🦢` works as a specifier.  Other
sources' `<Requires ref="omw-en" version="2.0"/>` keep pointing at
upstream; the cleaned set is not a self-contained release.

### Example annotations: the `note` attribute

```xml
<Sense id="estwn-et-1234-s">
  <Example note="offsets=4,9">Ta jookseb hommikuti.</Example>
</Sense>
```

- `offsets` is a space-separated list of `start,end` pairs: 0-based code
  point offsets into the Example text as written in the file, end
  exclusive.  One span is the normal case; the list allows a discontinuous
  or repeated match later without a format change.
- The parent Sense is the annotated sense.  Merge writes one
  `example_annotations` row per span (today every annotation already
  points at the owning sense).  Examples left under a Synset are ignored
  by merge.
- Existing notes are kept.  TUFS already uses `note="R=…;F=…"` and EstWN
  `note="@is_primary"`, so Cygnet appends its key with `;` and merge only
  looks for `(^|;)offsets=`.  If the source already has an `offsets`
  key, the cleaner keeps the source's value and logs a collision.
  Re-running the cleaner on its own output replaces the key it wrote.
  Merge validates each span (`start < end <= len(text)`) and drops a
  malformed one with a warning.
- Sense-level examples whose lemma spaCy cannot locate get no `offsets`
  but still have an owner; merge keeps them (today they are dropped as
  "skipped").
- Choosing a Sense for a synset-level Example is a heuristic (first
  member lemma found in the text, as today), so the cleaned file asserts
  more than the source did.  The DB has always recorded one owner, so
  nothing the DB keeps is lost.

### Step 6: what merge still does

Merge trusts the cleaned files and keeps only the cross-wordnet work:

- Synsets unified by `@ili`; `ili=""` → a local concept keyed
  `{lexicon}.{synset id}` (today's sequential `{lexicon}.iN` numbering
  goes).  The DB never stores concept ids, only the ILI or NULL, so
  nothing in `cygnet.db` changes; the one consumer of local concept ids
  is the MT-gloss sidecar, whose translations of local-concept glosses
  are lost once when the keys change and regenerated by the next
  translation run.
- CILI concepts, status and English definitions from `cili.tsv`
  (replacing `cili-1.0.xml` and `1_extract_cili.py`).
- Lexemes unified across files by (language, POS, written forms), then
  `merge_case_variants()`.
- Definitions: first per (synset, language) wins, so English definitions
  from non-English wordnets are dropped because CILI/OEWN merge first;
  for a local concept they are kept.  `translated_from` comes from the
  MT-gloss sidecar.
- Relations: inverses generated, cross-file dedup by (source, target,
  type), reversed-relation conflicts recorded in `conflicts.json`,
  cycle detection on the union graph.
- `lexicalized="false"` synsets and senses skipped.
- `dc:type` → `subtype`, and `dc:description`/`dc:relation` →
  `subtype_label`, exactly as `_relation_subtype()` reads them today;
  `web/relations.json` is only the display lookup.
- Example `note` parsing; `definition_annotations` from the GlossTag
  sidecar.
- Provenance: `resource`/`version` from the Lexicon header,
  `original_id` from Synset / LexicalEntry / Sense ids plus every id in
  a `merged=` note.  Definitions, examples and relations carry only the
  resource, as now.  A merged lexeme gets one row per contributing file
  because `_insert_prov()` is called per file.  Deep links and the
  per-resource provenance splits are built from the DB and unaffected.
- Post-processing, unchanged and still part of the product definition:
  `merge_case_variants()`, `cascade_delete()` (synsets without a
  definition and everything hanging off them), `remove_orphans()`,
  `compute_sense_indices()`, `detect_cycles()`, then resources table,
  summaries, search index.  These are covered by the old/new parity
  comparison, since the cleaner now keeps lexical-gap and sense-less
  synsets in the file and marks definition languages, all of which feed
  `cascade_delete()`.

### Sidecars

1. **CILI** cannot be a lexicon (`LexicalEntry+` required): merge reads
   `cili.tsv` directly.  The cleaner also loads it, for the ILI checks in
   pass 4 and the POS-mismatch diagnostics.
2. **GlossTag** (optional Steps 3–4) annotates CILI / PWN 3.0 glosses with
   several senses each; Step 4 writes `bin/cygnets_presynth/cili_glosstag.json`
   (`ili → [[start, end, oewn sense id], …]`) for merge.
3. **Machine-translated glosses** are not a wordnet (a `LexiconExtension`
   extends one lexicon; a `Lexicon` needs entries).  Step 5 reads
   Definitions from the cleaned LMF files and writes `mtg.tsv`
   (`ili_or_concept, language, translated_from, text`); merge reads it
   last as today.  The `translated-glosses` release asset,
   `scripts/upload_translations.sh` and `release.yml` change filename once.

### Plan

Each step leaves the test suite green and, until step 5, the DB produced
by the new path is compared against the old one (counts per table and per
resource, `conflicts.json`, `scripts/report.py` output, the
`web/summaries/*.json` files).  Before step 3 changes the sidecar inputs,
a full old-path build (`bin/cygnets_presynth/`, `web/cygnet.db`,
`web/provenance.db`) is copied aside as the frozen baseline, since the
old merge cannot read the new sidecars.  The Step 2 cache keys on output
file name alone, so during the transition the cleaner writes to
`bin/lmf_presynth/`; when the old path is deleted the directory takes the
old name and the log gains `format: "wn-lmf-1.4"`.

1. **Prep, needed regardless.**  Fix the stale relation filter in
   `conversion_scripts/7_validate_and_export.py:20-30` (`class_hypernym`,
   `member_meronym`, … while the DB uses `hypernym`, `mero_member`, …).
   Move the shared tables and helpers (`CONFIDENCE_THRESHOLD`,
   `below_confidence_threshold()`, `load_direction_swap_config()`, the
   relation-type dicts, `fix_language`) into a small `cyg/lmf.py` both
   sides import.
2. **Merge reads LMF.**  Add `MergeBuilder.process_lmf_file()` beside
   `process_file()`, dispatching on the root tag.  Iterparse over
   Lexicon → Synset / LexicalEntry / Sense / Definition / Example /
   SynsetRelation / SenseRelation; the `note` parser; the `lexicalized`
   skip; `dc:type` lookup.  Decide here whether per-file cycle removal
   stays in merge or moves to the cleaner.  `6_synthesise.py` accepts a
   mixed directory.  Test by merging a raw pass-through source (omw-sk)
   directly and comparing with its converted output.
3. **Cleaner.**  `cyg/clean.py` with the seven passes above, lifting the
   converter's existing logic pass by pass; `2_batch_convert_lmfs.py`
   calls it, writing to `bin/lmf_presynth/`; the `mul` planning shrinks
   to "clean the concepts lexicon first".  `scripts/report.py` and
   `scripts/log_summary.py` move with it.  Freeze the baseline, then:
   merge reads `cili.tsv`, delete `1_extract_cili.py`;
   `4_add_glosstag_to_cili.py` writes `cili_glosstag.json`;
   `5_translate_defns.py` writes `mtg.tsv`; update `release.yml`,
   `scripts/upload_translations.sh`, `build.sh` (Steps 1, 3–5 and the
   `--translate-only` message), `test_translate_defns.py`.  Rebuild,
   compare DBs.
4. **Export.**  `7_validate_and_export.py` emits WN-LMF 1.4 from the DB.
   A merged lexeme belongs to several resources, so the export is one
   Lexicon per language (`cygnet-{lang}`), synsets with `@ili`,
   examples under their Sense with `note="offsets=…"` (an example linked
   to several senses is repeated under each), forward relations only.
   The provenance variant puts contributing sources in `dc:source` as
   space-separated `resource:version:original_id` tokens; the small
   variant omits it.  Validate with the DTD via `xmlstarlet`.
5. **Delete** `cyg/cygnet.xsd`, `WordNetToCygnetConverter`,
   `process_file()` and the Cygnet-tag `_do_*` handlers,
   `parse_annotated_sentence()`, `build_annotated_sentence()`; rename
   the output directory.
6. **Tests.**  Rewrite fixtures that build Cygnet XML: `conftest.py`,
   `test_pipeline.py`, `test_report.py`, `test_merge_utils.py`,
   `test_conflict_detection.py`, `test_direction_swap_fix.py`,
   `test_multilex.py`, `test_repeated_ili.py`,
   `test_html_entity_unescaping.py`, `test_converter_memory.py`,
   `test_multilang_definitions.py`, `test_resource_provenance.py`,
   `test_wordnet_summaries.py`, `test_precompute_data.py`.  New tests:
   one per cleaning pass on a tiny LMF fixture, pass order (example
   linking sees normalised text), pass-through detection and the `.🦢`
   rule (including idempotence on a second run), `note` writing and
   parsing (existing TUFS-style note, semicolons inside values,
   duplicate key, reversed or out-of-range spans, empty note, `merged=`
   ids), Example insertion before Count, reference repair after dedup
   and trimming (every IDREF attribute listed in pass 4), a preservation
   test that a fixture carrying `status`, `confidenceScore`, `dc:*`,
   `Tag`, `Count`, `SyntacticBehaviour`, `subcat`, `members`,
   `sourceSense` and `ILIDefinition` round-trips them, `wn.lmf.load()`
   and `wn.add()` of a cleaned fixture, the `cili.tsv` and `mtg.tsv`
   readers.  Also
   `scripts/make_table_examples.py`, `scripts/paper_facts.py`,
   `scripts/check_all_relation_directions.py` (reads `Synset@ili` and
   `SynsetRelation` straight from the raw LMF with lxml, so it is
   unaffected by the Cygnet XML going away).
7. **Docs.**  `conversion_scripts/SCHEMA.md` (intermediate section),
   `CUSTOMIZE.md`, `README.md`, `REPORT.md`, the `wordnet-cygnet` skill.

Rough size: ~2,000 lines touched.  The converter's passes survive, its
emitter and the Cygnet-side readers go.  Suite today: 615 tests.

### Behaviour changes to expect in the DB

- Sense-level examples whose lemma was not found are now kept; example
  counts rise for estwn, the `tufs-*` lexicons and others with
  sense-level examples.
- Relations present in both OEWN and another wordnet now get a
  provenance row for each (today only OEWN's); relation counts are
  unchanged because merge dedups by key.
- Versions shown in the Wordnets tab carry `.🦢` for cleaned sources.
- Machine-translated glosses of local (non-ILI) concepts disappear
  until the next translation run, because their sidecar keys change.
- Nothing else should change; any other diff in the step-2/3
  comparisons is a bug.

### Possible upstream proposals for WN-LMF 1.5

- Annotated tokens in Definition/Example (mixed content
  `<Token sense="…">`, or a stand-off `<Annotation>` element); the
  `note="offsets=…"` convention is the interim.
- A Lexicon-level flag that inverse relations are implied, so a
  forward-only file is not a W404 storm.
- `Definition@translatedFrom` for machine-translated glosses.
- Repeatable / multi-valued provenance (`dc:source`).
- Allow synset-only lexicons (relax `LexicalEntry+`), which would let
  CILI and the MT glosses be ordinary LMF.
