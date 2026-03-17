#!/bin/bash
# Run once before the first `docker compose up`.
# Creates the host directories that are bind-mounted into the containers.

set -e

mkdir -p logs backups

echo "Done. You can now run: docker compose up -d"
