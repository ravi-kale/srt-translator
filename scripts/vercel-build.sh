#!/usr/bin/env bash
# Vercel build: compile the React frontend into frontend/dist (served via app.frontend()).
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
