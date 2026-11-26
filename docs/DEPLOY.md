# Deploy to a VPS from GitHub

A push to `main` runs CI. When every check passes, GitHub connects to your server over SSH and runs
[deploy/deploy.sh](../deploy/deploy.sh), which updates the server's clone to the checked commit, builds the images
**on the server** and restarts the stack. The step is off until you switch it on.

> **Not yet tried on a real server.** The script's safety checks and the Compose settings were checked on a developer PC.
> The SSH step and a build on a VPS have not been run.

## 1. Prepare the server (once)

You need Docker Engine with the Compose plugin, and git. The console build is the heavy part: allow 2 GB of memory or
more (an estimate, not measured).

Use a separate user for deployments and add it to the `docker` group. That group can control the whole machine, so
treat this user's key like an administrator's.

```bash
git clone https://github.com/mmi404/fuelwatch.git ~/fuelwatch
cd ~/fuelwatch
cp .env.example .env
chmod 600 .env
```

Set these in `.env`. The script refuses to run without them:

| Setting | Value | Why |
|---|---|---|
| `BIND_ADDRESS` | `127.0.0.1` | Only the console is reachable from outside. The simulator has no sign-in at all |
| `ACCOUNTS`, or `OPERATOR_TOKEN` | your own keys, from `openssl rand -hex 16` | `demo-token` is public |
| `GRAFANA_ADMIN_PASSWORD` | your own | The default is `admin` |
| `DASHBOARD_PREFILL_TOKEN` | leave unset | It sends the token to every visitor |

A firewall such as `ufw` does **not** block ports that Docker publishes. `BIND_ADDRESS` is what protects them.

Then deploy once by hand:

```bash
bash deploy/deploy.sh
```

## 2. Give GitHub a key (once)

On your own computer, make a key used only for this, and add the public half to the server:

```bash
ssh-keygen -t ed25519 -f fuelwatch_deploy -N ""
ssh-copy-id -i fuelwatch_deploy.pub DEPLOY_USER@YOUR_SERVER
ssh-keyscan YOUR_SERVER
```

Compare the host key from `ssh-keyscan` with the server's own (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`,
run on the server) before you trust it.

In the GitHub repository, open **Settings > Secrets and variables > Actions**.

| Kind | Name | Value |
|---|---|---|
| Secret | `VPS_HOST` | the server's name or address |
| Secret | `VPS_USER` | the deployment user |
| Secret | `VPS_SSH_KEY` | the whole private key file `fuelwatch_deploy` |
| Secret | `VPS_KNOWN_HOSTS` | the `ssh-keyscan` output, exactly. For a port other than 22, use `ssh-keyscan -p PORT` |
| Variable | `DEPLOY_ENABLED` | `true` |
| Variable (optional) | `VPS_PORT` | SSH port, `22` if unset |
| Variable (optional) | `VPS_PATH` | the clone's folder, `fuelwatch` (in the user's home) if unset |

## 3. Use it

Push to `main` and open the **Actions** tab. The job **Deploy to the VPS** runs last. If it is shown as skipped,
`DEPLOY_ENABLED` is not `true`. Pull requests never deploy.

- **To stop deploying:** delete `DEPLOY_ENABLED` or set it to anything else.
- **To roll back:** `git revert` the bad commit and push. The script only moves forward.
- **If the script says it cannot fast-forward:** a tracked file was edited on the server. Check `git status` there.
- **Downtime:** containers that changed are restarted, so expect a short gap. A failed image build leaves the running
  containers as they were. A new version that builds but does not become healthy is **not** rolled back by itself.

## 4. Reaching it

The console is at `http://YOUR_SERVER:3000`. **Anyone who can open it can see the network's data**; a key is needed only
to change something.

It is plain HTTP, so a key typed there crosses the network unencrypted. For anything beyond a short demo, put a reverse
proxy with HTTPS (Caddy, nginx or similar) in front and set `FRONTEND_BIND_ADDRESS=127.0.0.1`.

Grafana, Prometheus and the others listen only on the server itself. Reach them through SSH, for example Grafana:

```bash
ssh -L 3001:127.0.0.1:3001 DEPLOY_USER@YOUR_SERVER
# then open http://localhost:3001 on your computer
```

For alerts in Discord on the server, add `COMPOSE_PROFILES=discord` and `DISCORD_WEBHOOK_URL` to the server's `.env`
(see the README).
