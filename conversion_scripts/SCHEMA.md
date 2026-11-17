# Cygnet SQLite Schema

The web interface uses two SQLite databases:

- **`cygnet.db`** — the main dictionary database (served as `cygnet.db.gz`)
- **`provenance.db`** — optional source attribution data (served as `provenance.db.gz`)

`cygnet.db` is downloaded automatically on first visit and cached in IndexedDB.
`provenance.db` is only fetched on demand via the "Load provenance data" button
in the Concept view, since it adds ~70 MB to the download.

---

The main schema follows the
[Python `wn` module](https://github.com/goodmami/wn)'s SQLite layout where
possible, with modifications for Cygnet's merged, ILI-indexed structure.

The schema is optimized for size: text ID columns are omitted (the web
interface uses integer rowids for all lookups), language codes are
normalized into a lookup table, and only indexes needed by the web
interface are created.

![ER diagram](schema.svg)

The diagram source is in [`schema.mmd`](schema.mmd) (Mermaid format).
To regenerate the SVG after editing:

```sh
npx @mermaid-js/mermaid-cli -i conversion_scripts/schema.mmd -o conversion_scripts/schema.svg -b white
```

## Tables

### languages

Lookup table for language codes, referenced by integer FK from entries
and definitions.

```sql
CREATE TABLE languages (
    rowid INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE   -- ISO 639: "en", "de", "cmn-Hans"
);
```

```
rowid  code
─────  ────────
1      ckb
2      de
3      en
...
```

### synsets

A synset (= Cygnet Concept) groups senses that share a meaning.

```sql
CREATE TABLE synsets (
    rowid INTEGER PRIMARY KEY,
    ili   TEXT,           -- bare ILI identifier: "i85041"
    pos   TEXT NOT NULL   -- NOUN, VERB, ADJ, ADV, ...
);
```

```
rowid  ili     pos
─────  ──────  ────
1      i1      ADJ
35549  i35549  NOUN
```

**Differs from `wn`:** no `lexicon_rowid` (Cygnet merges all wordnets).
No text `id` column (saves ~4 MB; ILI is used for display).

### entries

An entry (= Cygnet Lexeme) is a word in a specific language with a
grammatical category.

```sql
CREATE TABLE entries (
    rowid          INTEGER PRIMARY KEY,
    language_rowid INTEGER NOT NULL REFERENCES languages(rowid),
    pos            TEXT NOT NULL   -- NOUN, VERB, ADJ, ADV, ...
);
```

```
rowid   language_rowid  pos
──────  ──────────────  ────
88234   3               NOUN
```

**Differs from `wn`:** `language_rowid` FK instead of text (in `wn`,
language comes from a `lexicons` table). No text `id` column (saves
~80 MB).

### forms

Surface forms (wordforms) for an entry. Rank 0 is the lemma (citation form);
higher ranks are inflected/variant forms.

```sql
CREATE TABLE forms (
    rowid           INTEGER PRIMARY KEY,
    entry_rowid     INTEGER NOT NULL REFERENCES entries(rowid),
    form            TEXT NOT NULL,     -- surface form: "banks"
    normalized_form TEXT,              -- accent-stripped lowercase: "banks"
    rank            INTEGER DEFAULT 1  -- 0 = lemma
);
```

```
rowid  entry_rowid  form   normalized_form  rank
─────  ───────────  ─────  ───────────────  ────
1      88234        bank   bank             0
2      88234        banks  banks            1
```

**Differs from `wn`:** added `normalized_form` for accent-insensitive
search. No UNIQUE constraint (duplicates filtered at insert time).

### senses

A sense links an entry to a synset -- it represents one meaning of a word.

```sql
CREATE TABLE senses (
    rowid        INTEGER PRIMARY KEY,
    entry_rowid  INTEGER NOT NULL REFERENCES entries(rowid),
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid),
    sense_index  INTEGER DEFAULT 1   -- disambiguation: bank_1 vs bank_2
);
```

```
rowid   entry_rowid  synset_rowid  sense_index
──────  ───────────  ────────────  ───────────
127932  88234        35549         1
127933  88234        31364         2
```

`sense_index` is computed per (language, lemma, pos) group, so English
"bank" as a noun has indices 1--18 while German "Bank" as a noun has 1--3.

**Differs from `wn`:** no text `id` column (saves ~149 MB).

### definitions

Definitions (= Cygnet Glosses) for a synset in a given language.

```sql
CREATE TABLE definitions (
    rowid          INTEGER PRIMARY KEY,
    synset_rowid   INTEGER NOT NULL REFERENCES synsets(rowid),
    definition     TEXT,
    language_rowid INTEGER REFERENCES languages(rowid)
);
```

```
rowid  synset_rowid  definition                                                   language_rowid
─────  ────────────  ──────────────────────────────────────────────────────────── ──────────────
1      1             (usually followed by `to') having the necessary means or ... 3
```

A synset may have definitions in multiple languages.

### synset_relations

Directed relations between synsets (hypernymy, meronymy, etc.).
Both forward and inverse directions are stored explicitly.

`subtype`/`subtype_label` hold a finer-grained sub-relation the source
gives beyond `type_rowid` (e.g. plWordnet's `dc:type="plwn:56"` on a
`relType="diminutive"` relation, with `dc:description` as its
human-readable label) -- NULL for the vast majority of relations, and
always NULL for an inverse this pipeline generated itself.

```sql
CREATE TABLE synset_relations (
    rowid         INTEGER PRIMARY KEY,
    source_rowid  INTEGER NOT NULL REFERENCES synsets(rowid),
    target_rowid  INTEGER NOT NULL REFERENCES synsets(rowid),
    type_rowid    INTEGER NOT NULL REFERENCES relation_types(rowid),
    subtype       TEXT,
    subtype_label TEXT
);
```

### sense_relations

Directed relations between senses (antonymy, derivation, etc.).
Same `subtype`/`subtype_label` convention as `synset_relations`.

```sql
CREATE TABLE sense_relations (
    rowid         INTEGER PRIMARY KEY,
    source_rowid  INTEGER NOT NULL REFERENCES senses(rowid),
    target_rowid  INTEGER NOT NULL REFERENCES senses(rowid),
    type_rowid    INTEGER NOT NULL REFERENCES relation_types(rowid),
    subtype       TEXT,
    subtype_label TEXT
);
```

### relation_types

Lookup table mapping relation type names to integer IDs.

```
rowid  type
─────  ──────────────────
1      antonym
2      derivation
3      derivation_of
4      pertainym
...
9      class_hypernym
10     class_hyponym
```

22 types total: antonym, derivation, derivation_of, pertainym, pertainym_of,
participle, participle_of, opposite, class_hypernym, class_hyponym,
part_meronym, part_holonym, member_meronym, member_holonym, entails,
entailed_by, substance_meronym, substance_holonym, causes, caused_by,
instance_hypernym, instance_hyponym.

### examples

Example sentences, stored once and linked to senses via a junction table.

```sql
CREATE TABLE examples (
    rowid   INTEGER PRIMARY KEY,
    example TEXT NOT NULL   -- "they pulled the canoe up on the bank"
);

CREATE TABLE sense_examples (
    rowid         INTEGER PRIMARY KEY,
    sense_rowid   INTEGER NOT NULL REFERENCES senses(rowid),
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid)
);
```

### example_annotations

Character-offset annotations marking which token in an example corresponds
to which sense.

```sql
CREATE TABLE example_annotations (
    rowid         INTEGER PRIMARY KEY,
    example_rowid INTEGER NOT NULL REFERENCES examples(rowid),
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL,
    sense_rowid   INTEGER NOT NULL REFERENCES senses(rowid)
);
```

```
example_rowid  start_offset  end_offset  sense_rowid
─────────────  ────────────  ──────────  ───────────
1              32            36          127932
```

This means characters 32--36 of example 1 ("bank") are annotated as
sense 127932.

### definition_annotations

Same structure as example_annotations, for definitions. Currently empty
(no definition annotations in the source data).

```sql
CREATE TABLE definition_annotations (
    rowid            INTEGER PRIMARY KEY,
    definition_rowid INTEGER NOT NULL REFERENCES definitions(rowid),
    start_offset     INTEGER NOT NULL,
    end_offset       INTEGER NOT NULL,
    sense_rowid      INTEGER NOT NULL REFERENCES senses(rowid)
);
```

### resources

Metadata for each contributing wordnet source, populated from the
`<ResourcesLayer>` in the merged XML. One row per source.

```sql
CREATE TABLE resources (
    rowid            INTEGER PRIMARY KEY,
    code             TEXT NOT NULL,                        -- source id: "oewn", "omw-arb"
    version          TEXT,                                 -- source version: "2025", "2.0"
    label            TEXT,                                 -- human name: "Open English Wordnet"
    language_rowid   INTEGER REFERENCES languages(rowid),  -- primary language
    url              TEXT,                                 -- project URL
    citation         TEXT,                                 -- bibliographic citation
    licence          TEXT,                                 -- licence URL or identifier
    email            TEXT,
    status           TEXT,
    confidence_score REAL,
    extra            TEXT                                  -- JSON: any non-standard LMF attributes
);
```

```
rowid  code    version  label                  language_rowid  url                                          licence
─────  ──────  ───────  ─────────────────────  ──────────────  ───────────────────────────────────────────  ──────────────────────────────────────────
1      oewn    2025     Open English Wordnet   3               https://github.com/globalwordnet/english-…   https://creativecommons.org/licenses/by/4.0
2      odenet  1.4      Offenes Deutsches WN   2               https://ikum.mediencampus.h-da.de/…         https://creativecommons.org/licenses/by-sa/4.0/
```

The `extra` column holds any LMF attributes not covered by the named
columns (e.g. `dc:publisher`, `dc:description` from OdeNet), serialised
as a JSON object.

### core_synsets

Marks the 4 960 synsets belonging to the OMWN core concept set (those with
an ILI listed in `cyg/data/wn-core-ili.tab`).  Used by the web UI to prefer
well-known concepts when selecting example lemmas and relation pairs on the
per-wordnet summary page, and to display a ✪ badge in the concept view.

```sql
CREATE TABLE core_synsets (
    synset_rowid INTEGER NOT NULL REFERENCES synsets(rowid)
);
CREATE INDEX idx_core_synsets ON core_synsets(synset_rowid);
```

Populated by `MergeBuilder.load_core_synsets()` during the build pipeline
(phase 7, after `insert_resources()`).  The table is absent in DBs built
before this feature was added; the UI handles this gracefully.

## Example queries

### Look up a word

```sql
-- Find all English senses of "bank"
SELECT s.rowid, f.form, l.code as language, syn.pos, s.sense_index
FROM forms f
JOIN entries e ON f.entry_rowid = e.rowid
JOIN languages l ON e.language_rowid = l.rowid
JOIN senses s ON s.entry_rowid = e.rowid
JOIN synsets syn ON s.synset_rowid = syn.rowid
WHERE f.normalized_form = 'bank' AND l.code = 'en';
```

```
rowid   form  language  pos   sense_index
──────  ────  ────────  ────  ───────────
127932  bank  en        NOUN  1
127933  bank  en        NOUN  2
...
127949  bank  en        VERB  1
```

### Get definitions for a synset

```sql
SELECT l.code as language, d.definition
FROM definitions d
JOIN languages l ON d.language_rowid = l.rowid
WHERE d.synset_rowid = 35549
ORDER BY l.code;
```

```
language  definition
────────  ──────────────────────────────────────────────────
bg        Място в непосредствена близост до море или река.
ca        Terra inclinada (especialment la que se troba al...
en        sloping land (especially the slope beside a body of water)
ro        Perete, margine (abruptă) a unui râu, ...
```

### Get examples with annotations for a sense

```sql
SELECT e.example, ea.start_offset, ea.end_offset
FROM examples e
JOIN sense_examples se ON se.example_rowid = e.rowid
JOIN example_annotations ea ON ea.example_rowid = e.rowid
    AND ea.sense_rowid = se.sense_rowid
WHERE se.sense_rowid = 127932;
```

```
example                                start_offset  end_offset
─────────────────────────────────────  ────────────  ──────────
they pulled the canoe up on the bank   32            36
he sat on the bank of the river and    14            18
```

## Indexes

Only indexes used by the web interface are created:

| Index | Column(s) | Purpose |
|---|---|---|
| `idx_forms_normalized` | `forms.normalized_form` | Word search |
| `idx_forms_entry` | `forms.entry_rowid` | Look up forms for an entry |
| `idx_senses_entry` | `senses.entry_rowid` | Look up senses for an entry |
| `idx_senses_synset` | `senses.synset_rowid` | Look up senses for a synset |
| `idx_definitions_synset` | `definitions.synset_rowid` | Definitions for a synset |
| `idx_synset_relations_source` | `synset_relations.source_rowid` | Outgoing synset relations |
| `idx_sense_relations_source` | `sense_relations.source_rowid` | Outgoing sense relations |
| `idx_sense_examples_sense` | `sense_examples.sense_rowid` | Examples for a sense |
| `idx_example_annotations_example` | `example_annotations.example_rowid` | Annotations for an example |

---

## Provenance database (`provenance.db`)

The provenance database records which source wordnet each merged item came
from. It is a standalone file — it has no foreign keys back to `cygnet.db`.
~3.83 million rows across 7 entity types.

### prov_resources

Lookup table for resource codes (one row per contributing wordnet).

```sql
CREATE TABLE prov_resources (
    rowid INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE   -- matches resources.code in cygnet.db: "oewn", "pwn", ...
);
```

### prov_tables

Lookup table for the `cygnet.db` table names that can have provenance.

```sql
CREATE TABLE prov_tables (
    rowid INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE   -- "synsets", "entries", "senses", "definitions",
                                 -- "examples", "sense_relations", "synset_relations"
);
```

### provenance

One row per (item, source) pair. `item_rowid` matches the `rowid` of the
corresponding row in `cygnet.db`.

```sql
CREATE TABLE provenance (
    rowid          INTEGER PRIMARY KEY,
    table_rowid    INTEGER NOT NULL REFERENCES prov_tables(rowid),
    item_rowid     INTEGER NOT NULL,
    resource_rowid INTEGER NOT NULL REFERENCES prov_resources(rowid),
    version        TEXT,          -- source version: "2025", "3.1"
    original_id    TEXT NOT NULL  -- original text ID in the source: "oewn-00001234-n"
);
CREATE INDEX idx_provenance_lookup ON provenance(table_rowid, item_rowid);
```

An item that was merged from multiple wordnets has one provenance row per
source. Forward relation rows only are attributed (inverse rows synthesised
by Cygnet carry no provenance).

### Provenance query example

```sql
-- Which wordnets contributed to synset rowid 35549?
SELECT pr.code, p.version, p.original_id
FROM provenance p
JOIN prov_tables pt  ON p.table_rowid    = pt.rowid
JOIN prov_resources pr ON p.resource_rowid = pr.rowid
WHERE pt.name = 'synsets' AND p.item_rowid = 35549;
```

```
code   version  original_id
─────  ───────  ───────────────────
oewn   2025     oewn-09107022-n
pwn    3.1      pwn-09107022-n
```

### Provenance row distribution (approximate)

| Table | Rows |
|---|---|
| senses | 1,800,000 |
| entries (lexemes) | 1,300,000 |
| definitions (glosses) | 296,000 |
| synset_relations | 145,000 |
| synsets | 121,000 |
| examples | 84,000 |
| sense_relations | 48,000 |
| **Total** | **~3,830,000** |

---

## Differences from `wn` module schema

| `wn` module | Cygnet DB | Reason |
|---|---|---|
| `lexicons` table with `language` | `languages` lookup table | Cygnet merges all wordnets; no per-lexicon metadata |
| `lexicon_rowid` FK on most tables | absent | Single merged resource |
| Text `id` column on synsets, entries, senses | absent | Saves ~232 MB; web interface uses integer rowids |
| `forms` UNIQUE(entry_rowid, form) | no constraint | Saves ~35 MB autoindex; dupes filtered at insert |
| language as TEXT in entries, definitions | `language_rowid` INTEGER FK | Saves ~20 MB |
| `lexicons` table (id, label, version, url, …) | `resources` table | Equivalent metadata; Cygnet adds `citation`, `email`, `confidence_score`, `extra` |

---

## The pre-synth intermediate (`bin/cygnets_presynth/`)

The databases are built from a directory of cleaned source wordnets,
written by Step 2 (`2_batch_convert_lmfs.py`, `cyg/clean.py`) and read by
Step 6 (`6_synthesise.py`, `cyg/merge.py`):

| File | Written by | Content |
|---|---|---|
| `<lexicon id>-<version>.xml` | Step 2 | One source lexicon as WN-LMF 1.4, cleaned (see below).  Loads in the `wn` library and validates against the DTD |
| `<stem>_log.json` | Step 2 | What the cleaner changed or dropped, and why; read by `scripts/report.py` and `scripts/log_summary.py` |
| `manifest.txt` | Step 2 | The files this build planned, after a line naming the cleaner version that made them; Step 6 ignores any other `.xml` in the directory |
| `sources.json` | Step 2 | Where each cleaned file came from, by lexicon id: the wordnets.toml URL, the stem of its supporting files in `bin/raw_wns/extras/`, and the concepts lexicon a release member Requires; Step 8 packages from it and Step 6 puts the URL in the summaries |
| `relation_direction_swaps.json` | Step 1.5 | Relation types whose direction a source has systematically reversed, by lexicon id; the cleaner swaps them |
| `cili.tsv` | Step 1 (a link to `bin/cili.tsv`) | The CILI release: ILI, status, PWN origin and English definition per concept |
| `cili_glosstag.json` | Step 4 (`--with-glosstag`) | `{ili: [[start, end, OEWN sense id], …]}`, the GlossTag sense annotations of the CILI definitions |
| `mtg.tsv` | Step 5 (`--with-translate`) | English machine translations of the non-English definitions of local concepts |

### The cleaned files

A cleaned file is the source edited, not rebuilt: anything the cleaner has
no opinion on passes through (`status`, `confidenceScore`, `dc:*`, `Tag`,
`Count`, `SyntacticBehaviour`, existing notes).  A file the cleaner changed
gets `.🦢` appended to `Lexicon@version` and a `note` on the Lexicon naming
its log; a file it did not change is copied byte for byte under the
upstream version.  The passes, in order:

1. **metadata** — the language code fixed or overridden from
   `wordnets.toml`, known licence errors corrected, the header attributes
   `wn` requires present;
2. **text** — NFC, whitespace collapsed, double-encoded HTML entities
   decoded, blank Definitions and Examples removed; every Definition
   carries `language` (an English definition in a non-English wordnet is
   marked `language="en"`, and the merge drops it in favour of CILI's);
3. **structure** — an invalid part-of-speech code becomes `u`; entries
   without a lemma and senses whose synset is missing are dropped;
4. **dedup and trimming** — entries with the same written forms are merged
   (the dropped ids listed in `note="merged=id …"`), duplicate senses
   collapsed, low-confidence elements dropped, repeated or unknown ILIs
   cleared, a `mul` member's ILIs filled in from its concepts lexicon;
5. **relations** — stored in the forward direction only (`hypernym`,
   `mero_*`, `causes`, `entails`, …), symmetric relations once; duplicates,
   self-loops and dangling targets removed; directions swapped where
   Step 1.5 found them reversed.  The merge regenerates the inverses;
6. **examples** — a synset-level `Example` moves under the `Sense` whose
   word it contains, the word's span in `note="offsets=start,end"`; one
   whose sense could not be located stays under the Synset and is not
   merged.

The `note` attribute carries machine-readable keys as `key=value` pairs
separated by spaces: `offsets=start,end[;start,end…]` on an Example,
`merged=id …` on an entry that absorbed others, and `translated_from=xx` on
a Definition that is a machine translation.  A relation type outside the
DTD's enumeration is written as `relType="other"` with the type in
`dc:type`; a finer sub-type keeps `dc:type` and its label in
`dc:description`, which the DB stores as `subtype` / `subtype_label`.

### Concepts across files

A synset with an ILI is unified across files by it.  One without
(`ili=""`) becomes a local concept keyed `<lexicon id>.<synset id>`.  A
multi-lexicon release — a concepts lexicon (`language="mul"`) whose
Synsets carry the definitions and ILIs, and member lexicons that
`Requires` it with stub Synsets — shares a concept per `dc:identifier`,
keyed `<concepts lexicon id>.<identifier>`.  The entries of a `mul`
lexicon are placeholders the DTD demands and are not merged.

The merge then does only the cross-wordnet work: entries are unified by
(language, part of speech, written forms) and case variants merged; the
first definition per (synset, language) wins, so CILI and OEWN, merged
first, supply the English; inverse relations are generated, cross-file
duplicates dropped, a relation that contradicts one already merged is
recorded in `relation_conflicts.json` and skipped, and an edge that would
close a hypernym cycle is removed.  Provenance takes the resource and
version from the Lexicon header and the original id from the Synset,
LexicalEntry and Sense ids, plus every id in a `merged=` note.

### The download archives (`web/lmf/`)

Step 8 (`8_package_lmf.py`, `cyg/package.py`) packages each cleaned file
as `<id>-<version>.tar.gz`: the file, its `_log.json`, and the
`citation.bib`, `README` and `LICENSE` its source archive came with (kept
by the download step in `bin/raw_wns/extras/<url stem>/`); a release
without a `citation.bib` gets one made from the Lexicon header, and a
member of a multi-lexicon release also carries the concepts lexicon it
Requires.  An archive whose wordnet is no longer in `sources.json` (dropped
from the configuration, or re-versioned) is removed, since the release
workflow attaches every archive in the directory to the GitHub release;
a wordnet's summary page links to its archive and to the original release.

### The export (`--with-xml`)

`7_validate_and_export.py` (`cyg/export.py`) writes the merged database
back out as WN-LMF 1.4: `cygnet.xml`, with every contributing source in
`dc:source` as space-separated `resource:version:original_id` tokens, and
`cygnet_small.xml` without them.  The file is a multi-lexicon release of
the kind the pipeline merges: a concepts lexicon `cygnet` (language `mul`)
holds every concept once, with its definitions in every language and its
relations, and one lexicon `cygnet-<lang>` per language, which `Requires`
it, holds that language's entries and senses.  A concept without an ILI
is identified by `dc:identifier="cyg:<rowid>"`.  Each Lexicon is validated
against the DTD as a document of its own, because libxml2 reports
spurious duplicate IDs past about a million of them.
