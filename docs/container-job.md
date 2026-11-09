# Container job

With the `container_job` feature on, a project can run its package as a
scheduled Azure Container Apps job. The feature ships the image build
(`Dockerfile`), its workflow (`.github/workflows/image.yml`), and
`scripts/container_job.py`, which creates the job and everything it needs.

## 1. Check the package can run unattended

In a container, nobody answers prompts. Check that the package:

- Resolves its settings with no `config.toml`: from defaults, or from
  environment variables set on the job (`[job.env]` in the job's config file).
  See [Config system](config-system.md).
- Reads secrets through the job's managed identity: the Key Vault backend's
  `DefaultAzureCredential` picks the identity from an `AZURE_CLIENT_ID`
  variable on the job.
- Logs to the console. Log Analytics keeps the output; the package needs no log
  file.
- Never prompts or opens a browser.
- Imports every module on `<entrypoint> --help`: that is the image's smoke
  test.

## 2. Create the job

Copy `scripts/container_job.example.toml` somewhere outside the repo's tracked
files, fill it in, and run the setup with an account that can grant roles:

```bash
uv run scripts/container_job.py path/to/your.toml --dry-run
uv run scripts/container_job.py path/to/your.toml
```

It creates whatever is missing (the shared resources, the job's identities and
roles, the job, the repo variables, the failed-run alert) and updates whatever
differs from the file. To change a setting later, edit the file and run it
again.

## 3. How the image gets there

The job starts on Microsoft's quickstart image. Each push to `main` then builds
the package's image, publishes it, and points the job at it. A scheduled run
also rebuilds `main` weekly on a freshly pulled base image; the cron in
`image.yml` sets how often.
