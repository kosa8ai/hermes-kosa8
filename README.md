# kosa8 Cloud terminal backend for Hermes Agent

Run Hermes's terminal, file and code tools in a [kosa8 Cloud](https://kosa8.com) sandbox instead
of on your machine. Each sandbox is a microVM with its own kernel; Hermes reaches it over the
kosa8 Cloud HTTPS API.

## Install

```bash
hermes plugins install kosa8          # from the Hermes plugin catalog, once listed
# or straight from this repository:
hermes plugins install https://github.com/kosa8ai/hermes-kosa8
hermes plugins enable kosa8
```

## Configure

1. Create an API key in the kosa8 Cloud dashboard (<https://app.kosa8.com>). kosa8 Cloud is a
   paid service; plans start at $29/month.
2. Put it in your profile's `.env` (or a Hermes secret source) as `KOSA8_API_KEY`.
3. Select the backend:

```bash
hermes config set terminal.backend kosa8
```

`hermes doctor` shows whether the key is set. Optional settings
(`hermes config set plugins.entries.kosa8.settings.<key> <value>`):

| Setting | Default | Meaning |
|---|---|---|
| `template` | `universal-2x4` | Template each new sandbox starts from (`GET /v1/templates`). Hermes needs `bash`, which the `universal-*` templates (Ubuntu 24.04) have; the Alpine templates (`base`, `python`, `node`) do not. |
| `region` | org default | Region id, such as `eu-hel1`. |
| `sleep_after_s` | `900` | Idle seconds before kosa8 Cloud puts the sandbox to sleep (`0` = never). |
| `ttl_s` | none | Seconds until kosa8 Cloud deletes the sandbox (60 to 7,776,000). |
| `base_url` | `https://api.kosa8.com` | API address. |

## How it behaves

- **One sandbox per Hermes task.** Commands start in `/workspace`. Working directory and exported
  variables carry over between commands, as on the other Hermes backends.
- **Persistence.** With `terminal.container_persistent: true` (the Hermes default), cleanup puts the
  sandbox to sleep and records its id in the plugin's Hermes state; the next session of the same
  task wakes it with its disk as it was. With `container_persistent: false`, cleanup deletes it.
- **Timeouts** are enforced inside the sandbox (`timeout -s KILL`) and by the API, and answer exit
  code 124. Long commands use streamed exec, so they are not cut at the API's 90 s buffered limit.
- **Commands and stdin of any size**: anything too large for one API request is staged in
  `/workspace/.kosa8-run` and removed after the command.
- **Not supported yet:** Bot Desktop / `computer_use` placement (`supports_streams`), hosted
  scopes, a per-task image override (sandboxes start from the configured template), and
  cancelling a running command mid-way (an interrupt stops waiting; the command itself ends at
  its timeout).
- **Runs in process.** Like every Hermes terminal backend, its environment object cannot cross the
  plugin-host boundary: with `plugins.isolation: host`, Hermes refuses to create it and names
  `plugins.isolation: in_process`.

## What it sends and stores (disclosure)

- **Network:** HTTPS calls to the kosa8 Cloud API only (`https://api.kosa8.com` by default, or your
  `base_url`): create, get, sleep, wake and delete a sandbox, run a command (`/exec`) and write
  staging files (`/files`). Every command Hermes runs on this backend, with its stdin, is sent to
  kosa8 Cloud and runs there.
- **Credentials:** `KOSA8_API_KEY`, read only through Hermes's secret scope
  (`agent.secret_scope.get_secret`), sent as a bearer token to the API address above, and listed in
  `strip_env_keys` so commands Hermes spawns cannot read it. The plugin stores no credentials.
- **Local data:** the id of each task's persistent sandbox, in the plugin's own Hermes state
  (`ctx.state`). Nothing else is written on your machine.
- **Shell commands:** only inside the kosa8 sandbox; nothing runs on the host.
- **No telemetry**, no background processes on the host, no self-updating code.

kosa8 Cloud bills for sandbox time on your plan: delete sandboxes you no longer need (or use
`container_persistent: false` or `ttl_s`). kosa8's SOC 2 audit is in progress; there is no report yet.

## Develop

```bash
# in an environment with hermes-agent installed (Python 3.14):
python -m pytest
hermes plugins validate . --install-deps
```

The tests use a fake kosa8 Cloud (`tests/fake_kosa8.py`) that runs each exec as a local
subprocess; nothing talks to the network. `_kosa8.py` is the kosa8 Cloud transport shared with
`langchain-kosa8`, `inspect-kosa8` and `harbor-kosa8` and is kept identical to those copies.

## Licence

MIT
