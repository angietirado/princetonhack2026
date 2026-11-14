# Development

Use Docker Compose from the root README for this fork. The active backend is `spending_api.merchant_main:create_app`; the active frontend is the root HTML, JavaScript and CSS. The inherited React scaffold and personal-spending startup scripts are legacy reference material, not the Regulars startup path.

Always pass `--env-file .env.example` for credential-free verification. Docker runs on ports 5193 and 8785 with its own project and data volume. No default merchant code reads the private `.env` file.

Source changes require rebuilding the corresponding image. The `backend-tests` and `dashboard-tests` services in the `tools` profile provide reproducible checks without host package installs.
