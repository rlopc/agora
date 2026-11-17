# Contributing

- Issues are welcome: bug reports and feature requests, using the issue forms.
- External pull requests are not accepted for now.
- Security reports do not go in issues: follow [SECURITY.md](SECURITY.md).
- Branches are short, named `<type>/<short-description>` and created from `main`, the only permanent branch.
- Pull requests are merged with squash, so each commit on `main` links to its PR.
- The pull request title becomes the commit message on `main`, so CI checks that it follows [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<area>): <summary>`, in the imperative.
  - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `build`, `ci`, `perf`, `style`, `revert`. Only changes to what is released (the `agora` package) use a type that opens a release (`feat`, `fix`, `perf`); development tooling uses one that does not (`build`, `ci`, `chore`, …). Dependency updates are `build(deps)`.
  - Areas: `core`, `store`, `web`, `mcp`, `agents`, `tools`, `cli`, `examples`, `docs`, `ci`, `repo`, `deps`.
- Releases are made by release-please: merging its release PR tags `vX.Y.Z`, updates `CHANGELOG.md` and attaches the wheel with its provenance. `CHANGELOG.md` and `.release-please-manifest.json` are generated: never edit them by hand.
