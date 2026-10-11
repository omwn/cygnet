<!-- Generated from sites.toml by scripts/sites.py; do not edit. -->

# Sites using Cygnet

| Site | Repository | Languages | Maintainer | Web maintainer | Notes |
|---|---|---|---|---|---|
| [Cygnet](https://cygnet.maudslay.eu/) | [omwn/cygnet](https://github.com/omwn/cygnet) (`main`) | all | Francis Bond | Francis Bond | Upstream: the source of the shared files. |
| [Old Javanese Wordnet](https://omwn.github.io/OJW/) | [omwn/OJW](https://github.com/omwn/OJW) (`master`) | kaw | David Moeljadi, Zakariya Pamuji Aminullah | Francis Bond | Origin repository is davidmoeljadi/OJW; the site is deployed from the omwn/OJW fork. |
| [Myanmar Open Wordnet](https://wordnet.burmese.sg/) | [omwn/mow](https://github.com/omwn/mow) (`master`) | my | Wenjie Wang, Francis Bond | Francis Bond | Also served at https://omwn.github.io/mow/. |

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
