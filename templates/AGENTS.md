# AGENTS.md: Starter Template

This file is the first document an agent reads when it starts work in this repository. An agent here means a software programme that reads instructions, uses tools, and changes code on your behalf, so this file tells that programme how your project works before it touches anything. Fill in every square-bracket placeholder with your own project details, then keep this file short and current, because an agent follows what it reads here before it follows anything else.

## Tech stack and versions

- Language and version: [for example, Python 3.12]
- Package manager and lock file: [for example, pip with requirements.txt, or npm with package-lock.json]
- Frameworks and libraries: [for example, FastAPI for the web layer and pytest for testing]
- Runtime services: [for example, PostgreSQL 16 running locally, or none]
- How to install dependencies: `[for example, pip install -r requirements.txt]`
- How to start the application locally: `[for example, python -m app.main]`

## Architecture and conventions

The architecture is the shape of the code, meaning which parts exist and how they talk to each other. Describe that shape here in a few lines so the agent can place new code in the right part of the project.

- Entry point: [for example, `src/main.py` starts the server and wires the routes together]
- Layering: [for example, routes handle Hypertext Transfer Protocol (HTTP) requests, services hold the business rules, and repositories talk to the database]
- Where new features go: [for example, a new endpoint needs a route in `src/routes/`, a service in `src/services/`, and a test in `tests/`]
- Naming and style: [for example, use descriptive function names, keep functions short, and format with the project formatter before committing]
- Configuration: [for example, settings come from environment variables listed in `.env.example`, and secrets are never written into source code]
- Tests: [for example, every new behaviour needs at least one test that fails before the change and passes after it]

## Verification commands

Verification is the step where the agent proves its change works by running a command and reading the result, rather than by guessing. Run these commands in order, and treat a failure in any of them as work that is still unfinished.

1. Install or refresh dependencies: `[for example, pip install -r requirements.txt]`
2. Run the test suite: `[for example, pytest]`
3. Run the type checker, if the project uses one: `[for example, mypy src/]`
4. Run the linter or formatter check: `[for example, ruff check .]`
5. Start the application and check its health endpoint: `[for example, curl http://localhost:8000/health]`

Declare work done only after every command above passes. If a command fails, fix the cause, run the commands again from the start, and record the final passing result in the loop state file described in the [templates README](./README.md).

## Never do without asking

- Never change a database schema, delete data, or run a migration against a shared or production database.
- Never add, remove, or upgrade a dependency without explaining why the change is needed.
- Never change an Application Programming Interface (API) response shape, a public endpoint, or a configuration value that other services depend on.
- Never commit or push code, and never open a pull request, until you have been asked to.
- Never read or copy a secret, a password, or an access token out of the repository or out of a local configuration file.
- Never work around a failing verification command by skipping the test, weakening the check, or marking a feature as passing by hand.

If any of these steps looks necessary, stop, explain what you want to do and why, and wait for an answer.
