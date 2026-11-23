# Public website deployment

The application uses GitHub Pages for the public website, GitHub Actions for temporary compute, a self-hosted OSRM engine built from a regional Pune PBF extract, and a Cloudflare Worker for authenticated workflow triggering.

## 1. Regional Pune OSM extract

Create a custom **PBF** extract at [BBBike Extract Service](https://extract.bbbike.org/). Use a bounding box that covers the sampling grid plus a routing margin:

- Southwest: longitude **73.70**, latitude **18.37**
- Northeast: longitude **74.02**, latitude **18.67**

Choose PBF format. BBBike emails a download link after preparing the extract. Keep that download URL private. Add it to the repository as an Actions secret named `OSM_PBF_URL`:

Repository → Settings → Secrets and variables → Actions → New repository secret.

The extract URL may expire. If a later generation fails to download it, create a fresh extract and update the secret.

## 2. GitHub Actions and Pages

After the pull request is reviewed and merged into `main`, enable Pages. The initial `gh-pages` branch already contains the public website shell and a placeholder map page:

1. Open Settings → Pages.
2. Set the publishing source to **Deploy from a branch**.
3. Select branch `gh-pages` and folder `/ (root)`.
4. Save. Each successful generation updates this branch with the latest map and retained history.
5. In Settings → Actions → General, ensure the repository permits actions to create and approve pull requests / write repository contents as required. The workflow declares `contents: write`.

The first successful generation publishes the site and its map. Every successful run is retained under `maps/history/`; the latest is also published under `maps/latest/`.

## 3. GitHub OAuth application

Create a GitHub OAuth App under GitHub Settings → Developer settings → OAuth Apps.

- Application name: Pune Travel-Time Maps
- Homepage URL: the GitHub Pages URL for this repository
- Authorization callback URL: the Cloudflare Worker URL followed by `/oauth/callback`

The Worker needs these secrets/variables. The OAuth callback verifies the GitHub login and returns a short-lived signed session assertion in the URL fragment. The website stores this assertion in session storage and sends it to the Worker as a bearer token. The GitHub OAuth access token itself is not retained in the browser session:

| Name | Purpose |
|---|---|
| `GITHUB_CLIENT_ID` | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret |
| `GITHUB_TOKEN` | Fine-grained PAT with Actions read/write permission for this repository |
| `SESSION_SECRET` | Random secret of at least 32 characters, used to sign short-lived browser sessions |
| `ALLOWED_GITHUB_USER` | GitHub login allowed to start generations |
| `PAGES_URL` | Full public GitHub Pages project URL, including repository path |
| `PAGES_ORIGIN` | Origin only, such as `https://rajgpawar21.github.io` |

Anyone with a GitHub account can sign in to start a generation under the current `*` setting. The public can view the map and its archive without signing in. Never put the GitHub token or OAuth client secret in website JavaScript.

## 4. Deploy the Cloudflare Worker

Create a Worker and connect it to this repository's `worker/` directory, or paste `worker/index.js` into the Cloudflare dashboard editor. Set the variables and secrets listed above in Worker Settings → Variables and Secrets.

Set `PAGES_URL` to the full GitHub Pages project URL and `PAGES_ORIGIN` to its origin only (scheme plus hostname). Set `ALLOWED_GITHUB_USER` to `*` to allow any GitHub-authenticated user to trigger a map, or set it to a specific GitHub login to restrict generation. The current public-authenticated setting prevents concurrent runs but does not impose a per-user daily quota; anyone with a GitHub account can request a generation. Set the Worker URL in `site/app.js` by replacing `https://YOUR-WORKER.your-subdomain.workers.dev` with the deployed Worker origin. The Worker must allow the Pages origin in its CORS policy. Redeploy the Worker after changing its code or variables.

## 5. Validate

1. Sign in on the public website with a GitHub account.
2. Press **Generate Pune Map**.
3. Check the Actions run if it fails. The workflow retains diagnostic artifacts for 14 days.
4. Confirm the latest map loads, the image overlays load, and the archive contains the new run.
5. Test the public site in a signed-out/private browser window.

## Free-tier and operational limits

GitHub-hosted runner availability, execution time, artifact retention and monthly usage limits are subject to GitHub's current plan and policy. The standard Ubuntu runner is temporary; OSRM is rebuilt for each generation and is not a permanent public routing service. A regional PBF extract is important because a full-country extract is unnecessarily large for this task.

The map is a research visualisation based on OSM road data, not live traffic. Keep OSM attribution visible and comply with the OSM tile usage policy. Historical versions consume repository Pages storage and deployment time; periodically prune old archives if they become large.
