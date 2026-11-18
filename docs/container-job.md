# Container job

With the `container_job` feature on, a project can run its package as one or
more scheduled Azure Container Apps jobs. The feature ships the image build
(`Dockerfile`), its workflow (`.github/workflows/image.yml`), and
`scripts/container_job.py`, which creates the jobs and everything they need.

A repo builds one image. Every job runs that image, with its own schedule,
arguments and identity, so a repo with several jobs gives its console script a
subcommand per job and names the subcommand in each job's arguments.

## 1. Check the package can run unattended

In a container, nobody answers prompts. Check that the package:

- Resolves its settings with no `config.toml`: from defaults, or from
  environment variables set on the job (the `[job.env]` table under its
  `[[job]]` in the config file). See [Config system](config-system.md).
- Reads secrets through the job's managed identity: the Key Vault backend's
  `DefaultAzureCredential` picks the identity from an `AZURE_CLIENT_ID`
  variable on the job.
- Logs to the console. Log Analytics keeps the output; the package needs no log
  file.
- Never prompts or opens a browser.
- Imports every module on `<entrypoint> --help`: that is the image's smoke
  test. With a subcommand per job, import each subcommand's module where the
  console script starts, not inside the subcommand, so the test covers them
  all.

## 2. Create the jobs

Copy `scripts/container_job.example.toml` somewhere outside the repo's tracked
files, fill it in, and run the setup with an account that can grant roles:

```bash
uv run scripts/container_job.py path/to/your.toml --dry-run
uv run scripts/container_job.py path/to/your.toml
```

The file has one `[[job]]` table per job and one `[[identity]]` table per
identity the jobs run as. An identity reads exactly the Key Vault secrets its
table lists. Give every job its own identity, unless jobs need the very same
access: those can name the same one.

The script creates whatever is missing (the shared resources, the identities
and their roles, the jobs, the repo variables, a failed-run alert per job) and
updates whatever differs from the file. To change a setting or add a job
later, edit the file and run it again.

## 3. Dropping a job

Delete its `[[job]]` table, and its `[[identity]]` table if no other job runs
as it, then run the script again. It removes the job, its alert rule, and the
identity with every role the identity holds.

The resource group is shared with other repos' jobs. The script tells this
repo's resources from theirs by an Azure resource tag,
`container-job-repository`, which it sets to the repo's name on every job,
alert rule and identity it manages. It removes only resources that carry this
repo's tag, and it stops when a name in the file belongs to a resource with
another repo's tag.

## 4. The shared registry

The registry is shared with other repos' jobs too. Each repo's image has its
own name in it, the GitHub repo's name, and every registry role the script
grants reaches that image name alone: the jobs' identities can pull this
repo's image, the CI identity can push it, and neither can touch another
repo's. So one repo's CI can't replace the image another repo's jobs run.
GitHub repos that share a registry need different names, since the image name
leaves out the repo's owner.

Azure limits a role to one image name only in a registry whose role assignment
mode is "RBAC Registry + ABAC Repository Permissions". A registry the script
creates is in that mode. The script stops at an existing registry in the other
mode. To switch one:

1. Run `az acr update --name <registry> --resource-group <resource group>
   --role-assignment-mode rbac-abac`.
2. Run the script in every repo that uses the registry.

## 5. How the image gets there

The first jobs start on Microsoft's quickstart image. Each push to `main` then
builds the package's image, publishes it, and points every job at it. A job
added later starts on the image the others already run. A scheduled run also
rebuilds `main` weekly on a freshly pulled base image; the cron in `image.yml`
sets how often.

The workflow points the jobs at the image by its hash (digest), so a job runs
exactly the image the workflow built and tested. The image tags it also
publishes, the package's version and the commit, are for people looking an
image up: an image tag can be moved to another image later, and a hash can't.
