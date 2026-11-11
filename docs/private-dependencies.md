# Private dependencies

A project whose dependencies include private git repos needs credentials for
CI and Dependabot to read them. With the `private_repo_deps` feature on, the
project's workflows carry the steps that use them. Those steps skip until the
repo has the `GH_APP_CLIENT_ID` variable, so CI works before the setup and
picks the credentials up after it.
`.github/dependabot.yml` can't skip a step, so its registry ships commented
out. The steps below create the credentials and turn Dependabot's registry on.

CI reads the private repos with a GitHub App's token. Dependabot can't use the
App when it updates `uv.lock`, so it gets its own fine-grained personal access
token.

## 1. Create the GitHub App

In the GitHub account: Settings > Developer settings > GitHub Apps > New.

- Permissions > Repository permissions > Contents: Read-only (Metadata: Read
  is added automatically). Nothing else.
- Webhook > uncheck Active (the App needs no events).
- Where can this be installed > Only on this account.

## 2. Make a private key

On the App's page, choose "Generate a private key" and download the `.pem`
file. Note the Client ID shown at the top of the page.

## 3. Install the App

Install it onto the private repos it reads: Org Settings > the App > Install >
select repositories.

## 4. List the private repos in the workflows

Each token step in `.github/workflows/` ends with a placeholder list
(`git grep -n "FIXME list the private repos"` finds them):

```yaml
owner: ""
repositories: private-repo-1, private-repo-2
```

Replace the placeholder with the private repos' names, comma-separated. The
token CI mints reads only those repos.

`owner: ""` means the project repo's owner. For repos under another owner,
set `owner:` to it, and keep the list non-empty: an owner with an empty list
gives the token every repo the App is installed on.

## 5. Save the App's credentials in the project

In the project's repo. The key is saved twice because Dependabot's CI runs
can't read the repo's other secrets.

```bash
gh secret set GH_APP_PRIVATE_KEY < path/to/app.private-key.pem
gh secret set GH_APP_PRIVATE_KEY --app dependabot < path/to/app.private-key.pem
gh variable set GH_APP_CLIENT_ID --body "<app client id>"
```

```powershell
Get-Content -Raw path\to\app.private-key.pem | gh secret set GH_APP_PRIVATE_KEY
Get-Content -Raw path\to\app.private-key.pem | gh secret set GH_APP_PRIVATE_KEY --app dependabot
gh variable set GH_APP_CLIENT_ID --body "<app client id>"
```

From the next run on, CI mints a token and reads the private repos.

## 6. Give Dependabot its own token

Create a fine-grained personal access token: Settings > Developer settings >
Personal access tokens > Fine-grained tokens.

- Resource owner > the account that owns the private repos.
- Repository access > Only select repositories > the private repos the project
  depends on.
- Permissions > Repository permissions > Contents: Read-only. Nothing else.
- Expiration > set one, and note when to rotate it.

Save it in the project's repo (`gh` prompts for the value):

```bash
gh secret set DEPENDABOT_GIT_TOKEN --app dependabot
```

## 7. Turn on Dependabot's registry

Uncomment both blocks that start with `# FIXME uncomment` in
`.github/dependabot.yml`.

## 8. Update the token action

Pin `actions/create-github-app-token` to its latest trusted commit.
