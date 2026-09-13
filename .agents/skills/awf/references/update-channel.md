# Release discovery

Configure owner-controlled `update-channel.json` using [the example](update-channel.example.json). Preserve existing settings; never embed credentials or machine paths from another host.

Run `scripts/check_updates.py --project ABSOLUTE_PROJECT --channel CHANNEL_LOCATION`. Add `--version 1.8` for a family or `--version 1.8.0` for an exact release. Missing/unavailable metadata does not establish latest.

The helper reads bounded channel/project metadata; it does not download/install a release. HTTPS access requires actual host access; private transport is external. Verify advertised archive/manifest digests separately before adoption. A copied channel is a snapshot; future discovery needs maintained publication.
