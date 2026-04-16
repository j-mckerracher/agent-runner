#!/usr/bin/env bash
# Agent Runner container entrypoint.
# Configures the environment and delegates to the headless runner.
#
# Usage (via docker run):
#   docker run agent-runner:latest [runner args...]
#
# The runner args are passed directly to run_headless.py.

set -euo pipefail

# Reset any host-injected time environment variables that could affect
# reproducibility. This ensures runs inside the container see a clean clock.
unset SOURCE_DATE_EPOCH 2>/dev/null || true
unset FAKETIME 2>/dev/null || true
unset LIBFAKETIME 2>/dev/null || true

# Set Python path to include all workspace packages
export PYTHONPATH="/workspace/packages/shared:/workspace/packages/runner:/workspace/packages/registry:/workspace/packages/harness${PYTHONPATH:+:$PYTHONPATH}"

# Disable Python output buffering for real-time event streaming
export PYTHONUNBUFFERED=1

# Execute the headless runner with all provided arguments
exec python3 /workspace/bin/run_headless.py "$@"
