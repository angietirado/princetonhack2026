# Project workflow

- Use Docker Compose as the default way to run and verify this project.
- Keep the README and user-facing startup instructions Docker-first. Host npm commands are optional implementation details, not the primary workflow.
- Run dashboard regression tests through the `dashboard-tests` service in the `tools` profile.
- For credential-free checks, pass `--env-file .env.example` explicitly. Do not read the private `.env`, print its contents, or load it indirectly during agent verification.
- Preserve existing user changes and generated data when rebuilding or restarting containers.
