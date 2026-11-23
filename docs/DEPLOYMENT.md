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

After the pull request is reviewed and merged into `main`:

1. Open Settings → Pages.
2. Set the publishing source to **Deploy from a branch**.
3. Select branch `gh-pages` and folder `/ (root)`.
4. Save. The generation workflow creates/updates this branch on its first successful run.
5. In Settings → Actions → General, ensure the repository permits actions to create and approve pull requests / write repository contents as required. The workflow declares `contents: write`.

The first successful generation publishes the site and its map. Every successful run is retained under `maps/history/`; the latest is also published under `maps/latest/`.

## 3. GitHub OAuth application

Create a GitHub OAuth App under GitHub Settings → Developer settings → OAuth Apps.

- Application name: Pune Travel-Time Maps
- Homepage URL: the GitHub Pages URL for this repository
- Authorization callback URL: the Cloudflare Worker URL followed by `/oauth/callback`

The Worker needs these secrets/variables:

| Name | Purpose |
|---|---|
| `GITHUB_CLIENT_ID` | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret |
| `GITHUB_TOKEN` | Fine-grained PAT with Actions read/write permission for this repository |
| `SESSION_SECRET` | Random secret of at least 32 characters |
| `ALLOWED_GITHUB_USER` | GitHub login allowed to start generations |
| `PAGES_URL` | Public GitHub Pages URL |

Only the authorised GitHub account can start a generation. The public can view the map and its archive without signing in. Never put the GitHub token or OAuth client secret in website JavaScript.

## 4. Deploy the Cloudflare Worker

Create a Worker and connect it to this repository's `worker/` directory, or paste `worker/index.js` into the Cloudflare dashboard editor. Set the variables and secrets listed above in Worker Settings → Variables and Secrets.

Set the worker URL in `site/app.js` by replacing `https://YOUR-WORKER.your-subdomain.workers.dev`. The Worker must allow the Pages origin in its CORS policy. Redeploy the Worker after changing its code or variables.

## 5. Validate

1. Sign in on the public website with the authorised GitHub account.
2. Press **Generate Pune Map**.
3. Check the Actions run if it fails. The workflow retains diagnostic artifacts for 14 days.
4. Confirm the latest map loads, the image overlays load, and the archive contains the new run.
5. Test the public site in a signed-out/private browser window.

## Free-tier and operational limits

GitHub-hosted runner availability, execution time, artifact retention and monthly usage limits are subject to GitHub's current plan and policy. The standard Ubuntu runner is temporary; OSRM is rebuilt for each generation and is not a permanent public routing service. A regional PBF extract is important because a full-country extract is unnecessarily large for this task.

The map is a research visualisation based on OSM road data, not live traffic. Keep OSM attribution visible and comply with the OSM tile usage policy. Historical versions consume repository Pages storage and deployment time; periodically prune old archives if they become large.
