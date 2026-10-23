<!-- Generated from sites.toml by scripts/sites.py; do not edit. -->

# Sites using Cygnet

| Site | Repository | Languages | Maintainer | Web maintainer | Notes |
|---|---|---|---|---|---|
| [Cygnet](https://cygnet.maudslay.eu/) | [omwn/cygnet](https://github.com/omwn/cygnet) (`main`) | all | Francis Bond | Francis Bond | Upstream: the source of the shared files. |
| [Old Javanese Wordnet](https://omwn.github.io/OJW/) | [omwn/OJW](https://github.com/omwn/OJW) (`master`) | kaw | David Moeljadi, Zakariya Pamuji Aminullah | Francis Bond | Origin repository is davidmoeljadi/OJW; the site is deployed from the omwn/OJW fork. |
| [Myanmar Open Wordnet](https://wordnet.burmese.sg/) | [omwn/mow](https://github.com/omwn/mow) (`master`) | my | Wenjie Wang, Francis Bond | Francis Bond | Also served at https://omwn.github.io/mow/. |
| [OMW Kinship](https://omwn.github.io/omw-kinship/) | [omwn/omw-kinship](https://github.com/omwn/omw-kinship) (`main`) | mul | Francis Bond | Francis Bond | A 'mul' release built with --all-languages. Not yet deployed: the repository and its first release are still to be published. |
| [OMW Pronouns](https://omwn.github.io/omw-pronoun/) | [omwn/omw-pronoun](https://github.com/omwn/omw-pronoun) (`main`) | mul | Francis Bond | Francis Bond | A 'mul' release built with --all-languages. Not yet deployed: the site needs its first release with the databases attached. |
| [OpenWordnet-PT](https://own-pt.github.io/openWordnet-PT/) | [own-pt/openWordnet-PT](https://github.com/own-pt/openWordnet-PT) (`master`) | pt | Alexandre Rademaker, Valeria de Paiva | Francis Bond | Deployed by a Pages workflow on each release. The local clone's origin is the omwn/openWordnet-PT fork; open update PRs against own-pt, adding --head omwn:cygnet-$C to gh pr create. |

## Update policy

* `web/index.html` is generic: all branding comes from each site's
  `local.json`, so the shared files can be copied without editing.
* Updates are never pushed directly to a site.  After a Cygnet change to a
  shared file, the web maintainer copies it to each site on a new branch,
  checks the site locally (`run.sh`), and opens a pull request naming the
  Cygnet commit it came from, e.g. `Update index.html from cygnet (148b4ffd)`.
  The site's maintainer reviews and merges it.
* If a change needs a new `local.json` key, document it in `CUSTOMIZE.md`
  and give it a default in `index.html`, so sites that have not updated
  their config keep working.
* Run `scripts/sites.py status` to see which sites are out of date.

Shared files: `index.html`, `relations.json`.

## Opening update PRs

Set `C` to the Cygnet commit being propagated (`C=$(git -C ~/git/cygnet rev-parse --short HEAD)`), then for each site:

### Old Javanese Wordnet

```bash
cd ~/git/OJW
git switch master && git pull
git switch -c "cygnet-$C"
cp ~/git/cygnet/web/index.html ~/git/cygnet/web/relations.json docs/
git add docs/index.html docs/relations.json
git commit -m "Update web UI from cygnet ($C)"
git push -u origin HEAD
gh pr create -R omwn/OJW --base master --fill
```

### Myanmar Open Wordnet

```bash
cd ~/git/mow
git switch master && git pull
git switch -c "cygnet-$C"
cp ~/git/cygnet/web/index.html ~/git/cygnet/web/relations.json docs/
git add docs/index.html docs/relations.json
git commit -m "Update web UI from cygnet ($C)"
git push -u origin HEAD
gh pr create -R omwn/mow --base master --fill
```

### OMW Kinship

```bash
cd ~/git/omw-kinship
git switch main && git pull
git switch -c "cygnet-$C"
cp ~/git/cygnet/web/index.html ~/git/cygnet/web/relations.json docs/
git add docs/index.html docs/relations.json
git commit -m "Update web UI from cygnet ($C)"
git push -u origin HEAD
gh pr create -R omwn/omw-kinship --base main --fill
```

### OMW Pronouns

```bash
cd ~/git/omw-pronoun
git switch main && git pull
git switch -c "cygnet-$C"
cp ~/git/cygnet/web/index.html ~/git/cygnet/web/relations.json docs/
git add docs/index.html docs/relations.json
git commit -m "Update web UI from cygnet ($C)"
git push -u origin HEAD
gh pr create -R omwn/omw-pronoun --base main --fill
```

### OpenWordnet-PT

```bash
cd ~/git/openWordnet-PT
git switch master && git pull
git switch -c "cygnet-$C"
cp ~/git/cygnet/web/index.html ~/git/cygnet/web/relations.json docs/
git add docs/index.html docs/relations.json
git commit -m "Update web UI from cygnet ($C)"
git push -u origin HEAD
gh pr create -R own-pt/openWordnet-PT --base master --fill
```
