# Sandbox image for agent-generated Python code.
#
# Build it with:
#   docker build -t agent-sandbox:ch06 -f Dockerfile .
#
# Deliberately minimal: a slim Python image, nothing else installed.
# Packages the agent needs are installed at template-build time (in a
# derived image), never by the sandboxed code at run time — because
# `pip install` inside the sandbox is a network call, and the sandbox
# in the exercises has no network.
#
# The security controls live in the `docker run` flags set by
# sandbox_runner.py (limits, no network, read-only filesystem,
# non-root user, dropped capabilities), not in this file. An image is
# packaging; the flags are the sandbox.

FROM python:3.12-slim

# Non-root user the runner will run as (UID 10001).
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /work \
    && chown agent:agent /work

WORKDIR /work

# The samples, so the container can run them without a volume mount
# (a mount would be another channel into the sandbox — see Exercise 2).
COPY untrusted_samples.py /work/untrusted_samples.py

USER 10001:10001

CMD ["python"]
