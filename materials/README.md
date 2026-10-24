# Reference materials

The active application lives in [`../src/tacto_negotiator`](../src/tacto_negotiator).
Its runtime and tests do not import this directory.

- `Tacto Challenge Presentation.pdf`: supplied challenge slides.
- `starter_kit/`: the original supplied repository, restored from local import commit
  `0166e6948f53ecbf629ab371135f69ec3243f998` after the implemented buyer was migrated.
  All 319 originally tracked files retain their original contents and file modes.

The archive is source reference, not a second active application. Existing ignored local
configuration and environment files were preserved during the directory move; they are
not part of the original Git snapshot, documentation inventory or package build.

Verify the tracked archive by comparing these Git tree IDs after the migration commit:

```sh
git rev-parse 0166e6948f53ecbf629ab371135f69ec3243f998:starter_kit
git rev-parse HEAD:materials/starter_kit
```

The IDs should match. See [`../NOTICE.md`](../NOTICE.md) for licensing, active dataset
attribution and historical benchmark provenance.
