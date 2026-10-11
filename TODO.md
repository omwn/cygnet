# TODO

## Replace the Cygnet XML schema with GWA WN-LMF

Goal: drop `cyg/cygnet.xsd` and use (enhanced) GWA WN-LMF 1.4 as the
intermediate format.  The public products (`cygnet.db.gz`,
`provenance.db.gz`) and the web UI read only SQLite, so neither changes.

### Why it is feasible

The current intermediate XML is already LMF in all but name: converters and
merge use LMF relation names, and the root carries Lexicon attributes.

| Cygnet | WN-LMF 1.4 |
|---|---|
| Resource / root attributes | Lexicon attributes |
| Concept | Synset (`@ili`, `@partOfSpeech`; NREF→x, UNK→u, CONJ→c, ADP→p) |
| Local concept (`oewn.i1`) | Synset with `ili=""` (or `"in"` + ILIDefinition) |
| Lexeme / Wordform[0] / Wordform[1..] | LexicalEntry / Lemma / Form |
| Script, Pronunciation (+audio) | `Lemma`/`Form@script`, Pronunciation |
| Sense | Sense (`@synset`) |
| Gloss | Definition (`@language`) |
| Example | Example (under Sense or Synset) |
| SenseRelation / ConceptRelation | SenseRelation / SynsetRelation |
| `domain`, `has_domain`, `constitutive`, synset-level `derivation`/`pertainym`/`participle` | `relType="other"` + `dc:type` |
| MT glosses file (`mtg-1.0.xml`) | LexiconExtension → ExternalSynset/Definition |

### Gaps and workarounds

1. **Sense-annotated tokens in glosses/examples** (glosstag, spaCy):
   Definition and Example are PCDATA only.  Use a stand-off TSV/JSON
   (definition/example id, start, end, sense id) next to the LMF.
2. **One concept across languages**: LMF synsets belong to one lexicon.
   Keep one Lexicon per source, synsets keyed by `@ili`; SQLite stays the
   canonical merged product.
3. **CILI as a concepts-only lexicon** is invalid (`LexicalEntry+`
   required): read `cili.tsv` directly and drop `1_extract_cili.py`.
4. **MT-gloss flag and multi-source provenance**: no native attribute.
   Use `dc:source` / `dc:creator` / `status="machine-translated"`;
   per-source files make provenance implicit.

### Plan

1. Fix (or retire) the stale relation filter in
   `conversion_scripts/7_validate_and_export.py:20-30` — it uses old XSD
   names (`class_hypernym`, …) so `--with-xml` drops most concept
   relations.  Needed regardless.
2. `cyg/converters.py`: rewrite the emit code (`build_output_xml` and the
   element building in pass 1–4) to write LMF 1.4, one Lexicon per source.
   Move the lexeme dedup key (`lang.POS.form`) into merge.
3. `cyg/merge.py`: switch the `_do_*` handlers / `process_file`
   (~lines 533–1020) to LMF tags; DB post-processing is unchanged.
4. Read CILI from `cili.tsv`; remove step 1.
5. `4_add_glosstag_to_cili.py`: emit stand-off annotations.
   `5_translate_defns.py`: emit a LexiconExtension; update
   `release.yml` and `scripts/upload_translations.sh` for the new mtg format.
6. `7_validate_and_export.py`: emit per-language LMF (plus an ILI-linked
   relations lexicon if needed) and validate with `wn.validate` / the DTD.
7. Update `scripts/report.py` and tests that build Cygnet XML
   (`test_pipeline.py`, `test_report.py`, `conftest.py`,
   `test_merge_utils.py`, `test_conflict_detection.py`).
8. Delete `cyg/cygnet.xsd`.

Rough size: ~1,500 lines touched.

### Possible upstream proposals for WN-LMF 1.5

- Annotated tokens in Definition/Example (mixed content
  `<Token sense="…">`, or a stand-off `<Annotation>` element).
- `Definition@translatedFrom` for machine-translated glosses.
- Repeatable / multi-valued provenance (`dc:source`).
- Allow synset-only lexicons (relax `LexicalEntry+`).
