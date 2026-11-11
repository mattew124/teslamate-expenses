# Publishing with Traefik and Cloudflare

This page is a worked example of putting the app on the internet through a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/),
gated by [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/), with
Traefik routing the request to the app. It assumes TeslaMate is already published the same way, on
`teslamate.example.com`, and puts the app at `https://teslamate.example.com/expenses/`.

!!! danger "No login of its own"

    The app has **no login of its own**. Cloudflare Access is the only thing between the internet and
    your expenses, so create the Access application (step 3) **before** you publish the hostname or the
    Traefik route. A route with no Access application is open to everyone.

## How a request travels

```
 browser ──► Cloudflare edge ──► tunnel ──► cloudflared ──► Traefik :80 ──► expenses :8080
             (TLS, Access login)  (outbound only)           (`web` entrypoint)
```

- Cloudflare ends TLS and makes you log in (Access) before anything reaches your network.
- `cloudflared` runs on your server and opens an **outbound** connection to Cloudflare, so you open
  no inbound port and forward nothing on your router.
- `cloudflared` then speaks plain HTTP to Traefik's `web` entrypoint (port 80) over a Docker network
  they share. That hop never leaves your server, so the Traefik router for it has no TLS.

## 1. The Traefik route

You need a Traefik entrypoint on port 80, called `web` here, that is **not** published to the
internet: only `cloudflared` reaches it, over the shared Docker network. Don't put a global
HTTP-to-HTTPS redirect on it. `cloudflared` talks plain HTTP, so the redirect would loop.

Add a second compose file next to `docker-compose.yml`, `docker-compose.public.yml`:

```yaml
# Publishes the UI on Traefik's plain-HTTP entrypoint, for cloudflared only.
# The app has NO login of its own: only ever use this behind Cloudflare Access.
services:
  expenses:
    ports: !reset []
    networks:
      - teslamate
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=${PROXY_NETWORK:-proxy}"
      - "traefik.http.services.tm-expenses.loadbalancer.server.port=8080"
      # Priority beats a catch-all router on the same host, such as TeslaMate's own.
      - "traefik.http.routers.tm-expenses.rule=Host(`${FQDN_TM:?}`) && PathPrefix(`${BASE_PATH:-/expenses}`)"
      - "traefik.http.routers.tm-expenses.priority=100"
      - "traefik.http.routers.tm-expenses.entrypoints=web"
      - "traefik.http.routers.tm-expenses.service=tm-expenses"

networks:
  proxy:
    name: ${PROXY_NETWORK:-proxy}
    external: true
```

Set `PROXY_NETWORK` in `.env` to the Docker network Traefik is on, and `FQDN_TM` to your hostname.
Then start it:

```bash
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d
```

Two details matter:

- **Scope the rule with `Host()`.** Without it the router matches `/expenses` on *every* hostname
  that reaches the entrypoint.
- **Give it a priority.** TeslaMate's own router is usually a catch-all for `/`, and Traefik ranks
  routers by rule length unless you say otherwise. `priority=100` makes `/expenses` win.

Want a private, LAN-only address as well (say a `websecure` entrypoint with a certificate)? Use
`docker-compose.traefik.yml` alongside this file, with an `AUTH_MIDDLEWARE` of your own. That route
doesn't pass through Cloudflare, so it needs its own gate. Both files add the same
`tm-expenses` service label; the repeat is harmless.

## 2. The tunnel (cloudflared)

If TeslaMate is already published through a tunnel on this hostname, **skip to step 3**: the same
tunnel and public hostname already carry `/expenses`, because they forward the whole hostname to
Traefik.

Otherwise, in the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/):

1. Go to **Networks → Tunnels → Create a tunnel**, choose **Cloudflared**, and name it.
2. Choose the **Docker** connector and copy the token from the command it shows (the long string
   after `--token`). Treat it like a password.
3. Under **Public Hostname** (or **Published application routes**), add a row:

    | Field | Value |
    |---|---|
    | Subdomain / domain | `teslamate.example.com` |
    | Path | *leave empty*, so the whole hostname goes to Traefik and TeslaMate and Grafana work too |
    | Service type | `HTTP` |
    | URL | `traefik:80` (the name of your Traefik container or service on the shared network) |

4. Run `cloudflared` as a container on the **same Docker network as Traefik**, usually in Traefik's
   own compose file. Keep the token in an untracked `.env`:

    ```yaml
    services:
      cloudflared:
        image: cloudflare/cloudflared:latest
        restart: unless-stopped
        command: tunnel --no-autoupdate run
        environment:
          - TUNNEL_TOKEN=${CLOUDFLARE_TOKEN}
        networks:
          - proxy
    ```

    ```bash
    docker compose up -d cloudflared
    ```

    The tunnel shows **Healthy** in the dashboard once it connects. Cloudflare creates the DNS record
    for the hostname for you.

Traefik chooses the service from the `Host` header and path, so one tunnel row serves every app on
the hostname. You add a row only for a new hostname.

## 3. Cloudflare Access

Access puts a login in front of the hostname. Do this **before** step 2 if the hostname is new.

1. In Zero Trust, go to **Access → Applications → Add an application → Self-hosted**.
2. Set the **Application domain** to `teslamate.example.com` and leave the **Path** empty, so Access
   covers the whole hostname: TeslaMate, Grafana and the expenses app together. Gating only
   `/expenses` would leave the rest of the hostname open.
3. Add a policy with the action **Allow**, and include only the people who should see your costs:
   specific email addresses, or a group from your identity provider. Don't use *Everyone*.
4. Under **Login methods**, pick your identity provider. The one-time PIN sent by email works with
   no setup.
5. Save.

An application with no Allow policy denies everyone, which is the safe failure. Set a short
**session duration** (the default is 24 hours) if the expenses are sensitive.

## 4. Check it

From a machine **outside** your network, or a private browser window, open
`https://teslamate.example.com/expenses/`. You should land on a Cloudflare Access login page, not
on the app. Log in with an allowed identity and the app loads.

The same check from the command line:

```bash
curl -sI https://teslamate.example.com/expenses/ | head -3
```

Expect `HTTP/2 302` with a `location:` header on `cloudflareaccess.com`. A `200` or a response from
the app means Access isn't covering this path: stop, and fix step 3.

To confirm Traefik has the route (run on the server, with Traefik's container name for `traefik`):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: teslamate.example.com' \
  http://traefik/expenses/healthz   # 200
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `404 page not found` from Traefik | The router isn't loaded. Check `docker compose logs expenses`; Traefik hides unhealthy containers, so wait for **healthy**. Confirm `FQDN_TM` matches the hostname exactly. |
| TeslaMate's home page appears at `/expenses` | The router lost to TeslaMate's catch-all. Check `priority=100` and the `Host()` rule. |
| Redirect loop | A global HTTP-to-HTTPS redirect on the `web` entrypoint. Remove it; Cloudflare already ends TLS. |
| `502 Bad Gateway` from Cloudflare | `cloudflared` can't reach Traefik. Check both are on the same Docker network and the service URL is right. |
| Tunnel shows **Inactive** | `cloudflared` isn't running, or `TUNNEL_TOKEN` is wrong. Check `docker compose logs cloudflared`. |
| Links in the app point to the wrong address | Set the app and dashboard addresses on the app's **Settings** page. |

## Things to keep in mind

- Anyone your Access policy allows gets the **full** app: they can add, edit and delete expenses.
  Keep the policy narrow.
- Cloudflare Access protects the public hostname only. Anything that reaches Traefik's port 80
  directly, from your LAN or another container, skips it, so never publish the `web` entrypoint to
  the internet.
- Apps that can't complete a browser login (a phone app calling an API, say) can't pass Access.
  Cloudflare offers service tokens for those, but the expenses UI is a browser app, so you don't need them.
