#!/usr/bin/env bash
# init.sh: environment health check.
#
# Run this script before starting work, so a broken environment is found
# early instead of halfway through a task. A health check here means a
# short, automatic test of one part of the setup, and the script prints a
# clear pass or fail line for each part it checks. It stops at the first
# failure and exits with a non-zero status, which is the shell signal that
# something went wrong, so fix that failure and run the script again.
#
# Replace every square-bracket placeholder with your own project commands
# before using this script. Run it with: bash init.sh

set -u

fail() {
  echo "FAIL: $1"
  exit 1
}

pass() {
  echo "PASS: $1"
}

# 1. Language runtime version.
# Check that the expected language runtime is installed and that its
# version matches the one the project needs.
# Example: python3 --version should print the version from AGENTS.md.
if ! command -v [runtime-command] >/dev/null 2>&1; then
  fail "language runtime [runtime-command] is not installed"
fi
[version-check-command]
if [ $? -ne 0 ]; then
  fail "language runtime version does not match [expected version]"
fi
pass "language runtime version is [expected version]"

# 2. Required tools are present.
# Check each tool the project depends on, such as a package manager, a
# test runner, or a database client, and add one check per tool.
for tool in [tool-one] [tool-two] [tool-three]; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    fail "required tool $tool is not installed"
  fi
done
pass "required tools are present: [tool-one], [tool-two], [tool-three]"

# 3. Dependencies are installed.
# Check that the project dependencies are present, so the code can import
# what it needs. Replace the command below with your own install check.
[dependency-check-command]
if [ $? -ne 0 ]; then
  fail "dependencies are not installed; run [install-command] and try again"
fi
pass "dependencies are installed"

# 4. Tests can run.
# Run a fast smoke check that the test suite starts, so a broken test
# setup is caught before any code changes. Replace the command below with
# the command that collects or lists your own tests.
[test-smoke-command]
if [ $? -ne 0 ]; then
  fail "tests cannot run; fix the test setup and try again"
fi
pass "tests can run"

echo "All environment checks passed."
