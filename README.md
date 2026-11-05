# Switch IPA public build workflow

This repository only contains a GitHub Actions workflow. It does not store the
game, encryption keys, or the built IPA.

The workflow downloads the game assets and `prod.keys` from the private
repository `mitu810/switch-ipa-builder` through the `PRIVATE_REPO_TOKEN`
secret, builds the IPA, splits it into release-sized parts, and uploads those
parts to a release in the private repository.

Run it from Actions → Build Switch Game IPA (public workflow, private assets) →
Run workflow.
