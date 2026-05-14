# Contributing a Wordnet to Cygnet

We welcome new open wordnets!

This guide explains how to prepare a wordnet for inclusion in Cygnet.

---

## Requirements

### Format

Your wordnet must be in
[Global WordNet LMF](https://globalwordnet.github.io/schemas/) format —
either:

- a **single XML file** (`.xml`, `.xml.gz`, or `.xml.xz`), or
- a **package** — a tar archive (`.tar.xz`, `.tar.gz`, or `.tar.bz2`) containing
  a top-level directory with the LMF XML file and optional supporting files (see
  [Package format](#use-a-package) below).

`.zip` archives are **not** supported.

The XML must declare **DTD version 1.1 or later**:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "https://globalwordnet.github.io/schemas/WN-LMF-1.1.dtd">
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
  <Lexicon id="example-en"
           label="Example English Wordnet"
           language="en"
           email="you@example.org"
           license="https://creativecommons.org/licenses/by/4.0/"
           version="1.0"
           url="https://github.com/yourorg/example-wn"
           citation="Author (2025). Example Wordnet. In *Proceedings of GWC 2025*."
           dc:description="A brief description of the wordnet, its coverage, and provenance."
           dc:publisher="Your Organisation"
           dc:format="OMW-LMF"
>    ...
  </Lexicon>
</LexicalResource>
```

#### Lexicon attributes

**Required:**

| Attribute | Description |
|---|---|
| `id` | Short identifier for the lexicon (see note below) |
| `label` | Full human-readable name, e.g. `Princeton WordNet` |
| `language` | [BCP 47](https://tools.ietf.org/html/bcp47) language tag — two-letter if available, else three-letter (e.g. `en`, `id`, `zsm`). Use [this tool](https://r12a.github.io/apps/subtags/) to look up tags. |
| `email` | Contact email address |
| `license` | License URL (see [Open license](#open-license)) |
| `version` | Version string (see note below) |

**Recommended:**

| Attribute | Description |
|---|---|
| `url` | Homepage URL for the project |
| `citation` | Canonical citation in Markdown |
| `dc:description` | Human-readable description of the resource (Markdown) |
| `dc:publisher` | Organisation that produces the resource |
| `dc:format` | Set to `OMW-LMF` |
| `confidenceScore` | Confidence in correctness, 0–1; defaults to 1 if omitted. Only values of 1 are considered for ILI inclusion. |

**`id`** — A short, 3–7 character lowercase ASCII identifier for the lexicon
(e.g. `pwn`, `bhsind`). Lexicon IDs are persistent across versions and
registered with the ILI maintainers, who keep a list of known IDs. If you do
not have an ID yet, contact us. For multi-language resources, suffix the ID
with the language code: e.g. `wnbahasa_zsm`, `wnbahasa_id`.

**`version`** — Use either [Semantic Versioning](http://semver.org/)
(`major.minor`, e.g. `3.0`, `1.3`) or date versioning
(`YYYY.MM.DD` or `YYYY.MM` or `YYYY`, e.g. `2026.05.12`).

### Open license

The wordnet must be released under a license that is open according to the
[Open Definition](https://opendefinition.org/licenses/) or the
[Open Source Initiative](https://opensource.org/licenses).  The Princeton
WordNet license is also accepted — use the value `wordnet` for the `license`
attribute in that case.

Common open licenses:

| License | SPDX identifier |
|---|---|
| [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | `CC0-1.0` |
| [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | `CC-BY-4.0` |
| [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | `CC-BY-SA-4.0` |

Use the full license URL as the attribute value (e.g.
`license="https://creativecommons.org/licenses/by/4.0/"`).

Licenses with a **NonCommercial (NC)** clause do not qualify as open and will
not be accepted.  Wordnets with no license cannot be accepted.

### Stable URL

The XML file or package archive must be downloadable from a **stable public
URL** — a GitHub release asset is ideal.  The URL is what gets added to
`wordnets.toml`.

### Validation

Your wordnet must pass `wn validate` with **no errors** (error codes starting
with `E`):

```bash
# Install the wn library if needed
pip install wn

# Validate
python -m wn validate your-wordnet.xml
```

Warnings (`W` codes) are informational; errors (`E` codes) will prevent the
wordnet from loading.

---

## Recommendations

### Add a description and citation

The `dc:description` and `citation` attributes are displayed on the
[Wordnet Summary](https://cygnet.maudslay.eu/#/about) and Publications pages.
Include:

- `dc:description` — what the wordnet covers, how it was built, and a pointer
  to the source data.
- `citation` — the canonical reference to cite when using the wordnet.

### Use a package

A **package** (a directory or tar archive) is preferred over a bare XML file
because it can include a `citation.bib` file alongside the data, which Cygnet
uses to populate the publications page.

A minimal package layout:

```
example-wn-1.0/
├── example-wn.xml       ← LMF XML (required)
├── LICENSE              ← license text (recommended)
├── README.md            ← description and acknowledgements (recommended)
└── citation.bib         ← BibTeX entry (recommended)
```

Pack it as:

```bash
tar -cJf example-wn-1.0.tar.xz example-wn-1.0/   # xz-compressed (preferred, smaller)
tar -czf example-wn-1.0.tar.gz  example-wn-1.0/   # gzip-compressed (also accepted)
```

---

## Submitting

Open an issue or pull request on
[GitHub](https://github.com/omwn/cygnet) with:

1. The stable URL to your XML file or package archive.
2. The BCP 47 language code for the wordnet.
3. A brief note on what it covers and where it came from.

We will add the URL to `wordnets.toml` and include it in the next build.
