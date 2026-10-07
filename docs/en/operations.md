# Installation and operation

Russian (original): [docs/operations.md](../operations.md)

A guide for the owner of an installation: a person with Linux who sets FlyArchive up on their own machine, creates an archive and looks after it. You have your own directory, your own
models and your own archive, so nothing "as the author has it" is required of you: the defaults are taken from the code (the reference is printed by `python3 tools/settings.py --reference`),
and everything that depends on your machine is given as a setting. How the archive is built is described in `docs/en/architecture.md`, the requirements card by card in `docs/en/requirements.md`, and how a model should use the archive
in `docs/en/for-llm.md`. The path from cloning the repository to the first document you find, which takes a few minutes, is at the beginning of `README.en.md`.

The archive command is `flyarchive` (the file `tools/flyarchive`). Until the installation has put it on the search path (PATH), call it by its path from the repository directory:
`python3 tools/flyarchive doctor`. Further in the text it is written simply as `flyarchive`. The help of the command and of all its subcommands is `--help`; the help, the messages
and the names of the archive's working directories (`входящие` (inbox), `очередь` (review queue), `карантин` (quarantine), `квитанции` (receipts), `удалённое` (deleted)) are in
Russian, and the screen of the plugin for the shell (a client program that talks to a model, such as DSH) is in English.

## What you need and the environment check

### System and Python

Linux only: the code relies on `fcntl`, `resource`, bubblewrap and `systemctl --user`. On Windows, work in WSL2. On any other system, every command except the help answers with a refusal
and exit code 2. You need Python 3.10 or newer (the required libraries cannot be installed on older versions); it has been tested on 3.12. The servers and the command are written with
the Python standard library plus the libraries from the files listed below.

Install the libraries into a virtual environment: on Ubuntu 24.04 and Debian 12 the system `pip` refuses to install packages outside an environment (`externally-managed-environment`).
On Debian and Ubuntu, the package `python3-venv` is needed for it. Create and activate the environment from the clone directory, and activate it again in every new terminal session:

```bash
python3 -m venv .venv && . .venv/bin/activate
```

### Dependency files

| File | What it contains | What happens if you do not install it |
|---|---|---|
| `requirements.txt` | required: `lancedb`, `pyarrow`, `pymupdf` | you cannot create the index table or search; pdf files are not checked at intake and their text does not get into the index |
| `requirements-optional.txt` | optional: libraries for formats and features, one per line with a note | only what the note names does not work |
| `requirements-dev.txt` | `pytest`, for the tests | you cannot run the tests |

```bash
. .venv/bin/activate                                       # the environment from the section "System and Python"
python3 -m pip install -r requirements.txt                 # required
python3 -m pip install -r requirements-optional.txt        # all optional libraries at once (or one library: python3 -m pip install NAME)
```

What each library from `requirements-optional.txt` provides:

| Library | What it enables | Without it |
|---|---|---|
| `Pillow` | preview of png and jpeg files, reducing the size of pages before they are sent to the model | pictures are not shown, pages go to the model as they are |
| `python-docx` | Word documents (.docx) | they do not get into the index and are not read by the documents server |
| `openpyxl` | Excel spreadsheets (.xlsx, .xlsm) | the same |
| `python-pptx` | presentations (.pptx) | the same |
| `extract-msg` | Outlook mail messages (.msg) | they are not parsed at intake and do not get into the index; `flyarchive known build` does not read their keys and writes a remark; accepting a message from the queue and an intake pass name the library in a remark too |
| `olefile` | old Office formats (.doc, .xls, .ppt) and .msg | they are recognized as «не документ» (not a document) and are not accepted |
| `docling` | parsing documents with tables kept intact in the documents server, recognizing scans locally; heavy | these two ways of reading are not available |
| `matplotlib` | charts in the documents server | the chart tool answers with a refusal (code 503) that names the library and hints what to install |
| `reportlab` | creating pdf files in the documents server (for Russian text a font with Cyrillic is also needed, see "Programs") | pdf files are not produced |

Without these libraries the archive keeps working, but not everything: a document stays in the archive, but its text is not in the index, and intake by itself does not warn about this.
What exactly does not work without each library is named by `flyarchive doctor`. A documents server that needs a missing library or program answers the request with a refusal that names
it and gives a hint (response code 503), not with a dropped connection, and stays alive; a model gets this through MCP as a tool error. The documents server reads Visio only in the format `.vsdx`: it does not read the old binary `.vsd` and answers it with an error that explains why.

### Programs

None of them is required for the core (accepting files, the index, search); each one enables its own feature.

| Program | Ubuntu and Debian package | What it is for | Without it |
|---|---|---|---|
| `bwrap` (bubblewrap) | `bubblewrap` | the sandbox: preview of unverified files, running the shells of external models, image description (the picture is prepared by the preview) | preview, the sandbox and image description do not work; on a plain Ubuntu 24.04 outside WSL2 a system setting may forbid the sandbox, and `flyarchive doctor` will show it |
| `7z` (or `7za`, `7zz`) | `p7zip-full` (gives `7z` and `7za`); newer releases also have `7zip` (gives `7zz`) | 7z and rar archive files | they are not unpacked (at intake they go to quarantine) and are not shown; zip and tar files are read without it |
| `dot` (graphviz) | `graphviz` | diagrams in the documents server | diagrams answer with a refusal (code 503) that names the program; the service stays alive |
| `docker` | `docker.io` | preview of Office files, HTML and metafiles in a container without a network | they are not shown; the rest of the preview works |
| `systemctl` | `systemd` (usually already installed) | services and the intake pass timer | set up by hand, see "Without services and without a shell" |
| `dsh` and node | `nodejs` and `npm` for node; `dsh` itself is installed through `npm`, see "The DSH shell and plugin" | the DSH web shell with the Archive section | the archive works without it |
| `tailscale` | it is not in the distribution's repositories: install it following the instructions of the Tailscale project | the gateway for access from other machines | the archive is visible only from the machine where it is installed |

Packages are installed with `sudo apt install <package>`. `flyarchive doctor` tells you which of these are missing on your machine: it looks for the programs by the names of the executable
files from the first column (it does not look for node, only for `dsh`). It also tries a found `bwrap` with one short launch: on a plain Ubuntu 24.04 outside WSL2 a system setting may
forbid the sandbox (unprivileged namespaces), and then the program is there but cannot create a sandbox: `doctor` writes this as a separate line «по желанию» (optional; see
"Troubleshooting common failures"). The node version: the plugin has been tested on 22; if your distribution has an older one, install a current one in the way
the Node.js project describes.

A font with Cyrillic for pdf is not a program, but one feature does not work without it: the documents server builds a pdf (`make_document` with the kind pdf) with a font that is on the
machine, because the standard pdf fonts do not know Cyrillic. It takes the `.ttf` file named by the setting `pdf_font`, and if it is empty, the first of the families DejaVu Sans,
Liberation Sans, Noto Sans, FreeSans found in the system's font directories (`/usr/share/fonts`, `/usr/local/share/fonts`, `~/.local/share/fonts`, `~/.fonts`). If there is no font,
a pdf with Russian text is not built: the server answers with a refusal (code 503) and names the package; text in Latin letters only is built without a font too. On Debian and Ubuntu the font
comes in the package `fonts-dejavu-core` (`sudo apt install fonts-dejavu-core`); whether a font was found is shown by the font line in `flyarchive doctor`.

Under WSL2, systemd is turned on by the line `systemd=true` in the `[boot]` section of the file `/etc/wsl.conf` (this is a WSL setting, not a FlyArchive one). For user services to start at boot
without anyone logging in to a session, enable linger: `loginctl enable-linger <user>`.

### Embedding service

A required part: without it there is neither indexing nor search. You need a service with the `/api/embed` interface, like that of ollama (tested with ollama). The address is the setting
`embed_url` (default `http://127.0.0.1:11434/api/embed`), the model is `embed_model` (default `bge-m3`), the dimension is `embed_dim` (1024; it is built into the index table).
Embeddings are computed on the CPU (the request goes with `num_gpu: 0`); the setting `embed_gpu` moves them to the GPU.

**How to install and start ollama.** It is a program that runs as a service: it answers HTTP requests for as long as it is running. The official download page is
[ollama.com/download](https://ollama.com/download), on the Ollama project's site. Take the install command from that page and read it before you run it: it is not given here. Then three steps:

1. The service has to be running for as long as the archive is. Where the system starts it, it starts by itself after installation. Where the system does not (for example, WSL2 without
   systemd), open a second terminal, run `ollama serve` and leave it open.
2. Download the model: `ollama pull bge-m3`. It takes about a gigabyte on disk; the download happens once.
3. Check: `flyarchive doctor` prints «служба векторов отвечает: <адрес>, модель bge-m3, размерность 1024» (the embedding service answers: the address, model bge-m3, dimension 1024).
   If it does not answer, see "Troubleshooting common failures", the case "The embedding service is not installed or not running".

### The check

```bash
python3 tools/flyarchive doctor              # check the environment; changes and creates nothing, not even the archive directory
python3 tools/flyarchive doctor --json       # the same output, for programs
```

The command checks the system, the Python version, the libraries, the programs (it also tries to launch a found `bwrap`), the font with Cyrillic for pdf, the preview interpreter,
the embedding service (one request: whether it answers and whether the dimension is the right one), whether the
archive has been created, whether it has an index table, and whether a local model is set. There is one line per check: `ok`, `НЕТ` (NO: a required item is missing; exit code 1) or
`по желанию` (optional: an optional item is missing; the line hints what to install). `flyarchive init` and `flyarchive install` end with the same kind of line about what is missing; in them
the embedding service is named with the words «служба векторов» (embedding service), and the name of the address setting (`embed_url`) stands in the detailed check line.
For the required libraries, the check does more than look them up: it also loads them. If the files are there but loading fails, the line is `НЕТ` with the first line of the error. The optional
libraries are only looked up, because loading some of them takes tens of seconds.

Three lines of the check deserve an explanation. A program `bwrap` that is found but cannot create a sandbox (this happens when the system forbids unprivileged namespaces) gives a separate
line «по желанию» (optional) «программа bwrap найдена, но песочницу создать не может (причина): без неё …» (the program bwrap is found, but it cannot create a sandbox (the reason):
without it …) with the message code `doctor.sandbox_blocked`; in `--json` it has the same `id` as a found program, `tool.bwrap`. The line `preview_python` names the interpreter with which
the preview launches the worker process, and says «по желанию» if it cannot be safely given to the sandbox (this is the refusal `preview.python_outside`, see "Troubleshooting common
failures"). The line `font` shows whether a font with Cyrillic for pdf was found (see "Programs"): the font is looked up only by file names, nothing is opened or created.

The first request to the embedding service may load the model from disk, so waiting for the answer can take up to a minute. If there is no answer within three seconds, the command in text
mode writes one line to stderr saying that it has not hung (with `--json`, nothing extra). No answer within a minute gives a separate message «повтори проверку» (repeat the check), not
«служба не отвечает» (the service does not answer): that message is for a refused connection, which comes at once. `flyarchive install --dry-run` and `--json`, like
`flyarchive init --json`, do not query the embedding service.

## Installation

### With one command

Run the installation from the same virtual environment where the libraries are installed: it writes into the service files the interpreter it was started with, that is, the interpreter of
the environment (`.venv/bin/python3`), and so the services work with the same libraries. It writes the same interpreter into the command launcher (step 5): the installed command
also runs with these libraries.

```bash
python3 tools/flyarchive install --dry-run         # what will be written and started; touches nothing
python3 tools/flyarchive install                   # the archive, services and timer, plugin, shell configuration file, the command on the search path, start
python3 tools/flyarchive install --gateway auto    # the same, plus the gateway: the address and the node name are taken from tailscale
```

The installation goes step by step, and each step is printed:

1. **The archive.** The same as `flyarchive init`: the directories `secrets`, `logs`, `index`, `corpus` with the owner's permissions, the service token, an empty known-files base,
   an empty index table with a full-text index. What already exists is not recreated or changed.
2. **Services and timer.** The files are written from the templates in `tools/templates` into the directory of systemd user services. If there are no intake settings (`inbox.json`),
   they are created with the defaults together with the inbox folder; existing settings are not rewritten.
3. **The shell plugin.** The source of `dsh-plugin` is put into the DSH shell profile.
4. **The shell configuration file** `<archive directory>/dsh.patch.yml`: the connection of the archive's MCP server and of the plugin. There are no models or their providers in it.
5. **The command on the search path.** The command launcher `<archive directory>/bin/flyarchive` (a short sh file that runs the command with the installation's interpreter) and a link
   `flyarchive` to it in the user's directory of commands.
6. **Starting the services.** `systemctl --user daemon-reload`, then `enable --now` for the services in the order of their dependencies and for the intake pass timer.

Options:

| Option | What it does |
|---|---|
| `--dry-run` | prints every file (with its content or its fingerprint, the command launcher too), the link to the command and every systemctl command; writes nothing |
| `--no-start` | writes the files and does not call systemctl; the commands that you must run yourself are printed |
| `--no-plugin` | does not touch the shell profile |
| `--no-dsh` | does not install the shell service and the plugin, even if `dsh` is on the search path |
| `--gateway auto` | asks tailscale for the address and the node name and appends to `settings.json` only the missing keys, and only an address from the private network range (100.64/10) |
| `--gateway off` | does not install the gateway and does not query the private network |
| `--json` | a report for programs: the path of the command launcher is in the key `launcher`, the state of the link is in `command` |

Running it again is safe: the files are rewritten with the same content, other services (not `flyarchive-…`) and the neighboring profile directories are not touched, and the archive data does
not change. A missing `systemctl` or a missing shell profile directory is not a failure: the step is skipped with a note on what to repeat. If `dsh` is not on the search path, the shell
service and the plugin are not installed (otherwise the service would fail with code 127 every ten seconds), the rest is installed, and the installation says what the shell is and how to
install it.

### What is written where

| What | Where | How it is set |
|---|---|---|
| data, logs, secrets, index | the archive directory: `~/flyarchive` or `FLYARCHIVE_HOME` | the environment variable `FLYARCHIVE_HOME` (the directory cannot be set in `settings.json`) |
| services and timer | `flyarchive-search.service`, `flyarchive-office.service`, `flyarchive-mcp.service`, `flyarchive-gateway.service`, `flyarchive-dsh.service`, `flyarchive-inbox.service`, `flyarchive-inbox.timer` in the directory of user services | the setting `units_dir` (default `~/.config/systemd/user`) |
| plugin | `<profile>/node_modules/flyarchive-dsh-plugin` | the setting `dsh_profile` (default `~/.dsh/profiles/web`) |
| command launcher | `<archive directory>/bin/flyarchive` | assembled by the installation: an sh file with permissions 0700 |
| link to the command | `<command directory>/flyarchive` (points to the launcher) | the setting `bin_dir` (default `~/.local/bin`; empty means do not create the link) |
| intake settings | `<archive directory>/inbox.json` | the command `flyarchive inbox set` and the Intake section in DSH |
| shell configuration file | `<archive directory>/dsh.patch.yml` | assembled by the installation |
| service logs | `<archive directory>/logs/<name>.log` | the service templates |

The gateway is installed only when `gateway_bind` and `public_url` are set (or `--gateway auto` is given); otherwise the installation says so and installs the rest.

### Code and data

The code of the services is the `tools` directory of the repository from which the installation was run. The service file receives the path to it, the Python interpreter that the installation
was run with, and, in a single `Environment` line, the archive directory: there are no addresses or node names in the services, the gateway reads them from the settings. The data, the logs
and the secrets lie only in the archive directory; there is no code there, only the command launcher `bin/flyarchive`: a short sh file written by the installation (it names the code and the
interpreter, but is not code itself). Two practical consequences:

- the services and the command launcher are started with the Python that the installation was run with: the libraries from the dependency files must be installed for it (with a virtual environment, run the
  installation from it);
- the path to the code and to the archive must not contain a space or the characters `%`, `"`, `'`, `\`, `$`: systemd parses them in its own way, so the installation refuses before
  writing anything and names the path.

Set up a production machine from a separate copy of the repository at the chosen version, not from the directory where development goes on: an edit in the working directory changes what the
services run after a restart. The version is recorded in one place, `tools/version.py`: it is reported by `flyarchive --version`, by the MCP adapter when it introduces itself to a client, by the
descriptions of the search service and the documents server, and by the plugin package (`dsh-plugin/package.json`); what changed between versions is in `CHANGELOG.en.md`.

### The command on the search path

The installation writes the command launcher `<archive directory>/bin/flyarchive` and puts a link `<bin_dir>/flyarchive` pointing to it. The launcher is a short sh file with permissions 0700:
it runs the command file with the interpreter that the installation was run with (`exec '<interpreter>' '<code>/flyarchive' "$@"`). It is needed because the first line of the command file is
`#!/usr/bin/env python3`, while the libraries are installed in the Python (most often in the environment) that you installed with: a link straight to the command file would, in a new shell where
the environment is not activated, run the system Python without the libraries. The command is also called through the launcher by the shell plugin (the line `command` in `dsh.patch.yml`) and by
`tools/dsh-web-start`, so the installed command does not need the environment to be activated. The installation rewrites the launcher at every run; you do not edit it by hand.

If the directory of commands does not exist, it creates it; if the link already points to the launcher, it writes «уже есть» (already exists); if someone else's file or a link to something else is already there, it does not
touch it and names the `ln -s` command with which you do it by hand; if the directory is not in `PATH`, it names the line for the profile of your command-line shell
(`export PATH="<directory>:$PATH"` in `~/.profile` or `~/.bashrc`). `--dry-run` shows the launcher among the files and the link that it would create or replace; with `--json` the path of the
launcher is the key `launcher`, and in the key `command` the field `state` names the state of the link: `created` (created), `exists` (already pointed to the launcher), `replaced` (a link to
`tools/flyarchive` left over from a build before version 0.1 has been replaced with a link to the launcher), `foreign` (someone else's name is left alone), `disabled` (the setting `bin_dir` is empty:
no link is put) or `failed` (the link could not be put; the key `notes` names the `ln -s` command for doing it by hand). A trial run creates nothing, so in it `free` (the name is free) stands instead of
`created`, and `legacy` instead of `replaced`. If you do not need the services yet, `python3 tools/flyarchive install --no-start` writes the files and puts the link in place, but does not start
anything.

### Updating

Get the new version in the repository copy, activate the same virtual environment, update the libraries, run the installation again and restart the services: the installation does not restart
what is already running. Activate the environment before the installation: the installation writes into the service files and into the command launcher the Python that started it.

```bash
git pull
. .venv/bin/activate                              # the environment from the clone directory, the same as at the first installation
python3 -m pip install -r requirements.txt        # new library versions, if they changed; the format features come from requirements-optional.txt
flyarchive install
systemctl --user restart flyarchive-search flyarchive-office flyarchive-mcp flyarchive-gateway
systemctl --user restart flyarchive-dsh    # last
```

Restart the gateway only if it is installed. If the plugin or the service templates have changed, running the installation again updates them too.

### Services

| Service | Default address | What it does |
|---|---|---|
| `flyarchive-search` | `127.0.0.1:8765` (`search_port`) | the search page, `/api/search`, `/doc`, `/openapi.json` |
| `flyarchive-office` | `127.0.0.1:8766` (`office_port`) | documents and graphics, accepting a document, `/openapi.json` |
| `flyarchive-mcp` | `127.0.0.1:8767` (`mcp_port`) | the MCP adapter on top of the first two |
| `flyarchive-gateway` | `<gateway_bind>:8780` (`gateway_port`) | access from other machines |
| `flyarchive-dsh` | `127.0.0.1:3080` (`dsh_port`) | the DSH shell with the plugin |
| `flyarchive-inbox.timer` | every 30 minutes | starts `flyarchive-inbox.service`: the intake pass over the inbox folder |

The search service, the documents server and the adapter listen on the loopback interface only (the listen address is not configurable): only the gateway faces the outside.

One feature of the search and documents services is not in the table above:

- **`/openapi.json`.** The search and documents services give a description of their tools in the OpenAPI format at this address (`http://127.0.0.1:8765/openapi.json` and
  `http://127.0.0.1:8766/openapi.json` with the default ports). A shell that takes its tools over OpenAPI rather than over MCP reads it. The description is given to whoever comes with a token
  (the "read" level or higher), and only on the loopback interface: the gateway has no such address.

```bash
systemctl --user status  flyarchive-search
systemctl --user restart flyarchive-search
systemctl --user list-timers flyarchive-inbox.timer
```

The order matters. DSH asks the adapter for the list of tools once, at startup: if it starts earlier, it ends up without tools and does not complain. That is why `tools/dsh-web-start` waits
for the adapter's port (up to two minutes; it checks the port with the same Python that reads the settings, and it does not need the program `ss`) and only then starts the shell, and the
shell must be restarted last.

This advice is for whoever edits the service templates in `tools/templates` and adds dependency lines to them: do not give the services `After=default.target`. That target is what starts them
(the templates have `WantedBy=default.target`), so systemd will find a dependency loop and silently drop DSH, specifically, from the start queue. If you do not edit the templates, this paragraph is not about you.

A service does not read `.bashrc`. Everything a process needs is set in the unit or in the launching script: the installation puts into the `PATH` of the shell service the directory where
`dsh` was found, `%h/.npm-global/bin` and the system directories; the keys are read by the launcher (see "The DSH shell and plugin"). A directory with a space is not written into the `PATH`
of the service: the installation names it, and the service looks for `dsh` in `~/.npm-global/bin` and the system directories. Under WSL, run the installation from a Linux terminal session,
not by a command from Windows: otherwise Windows directories end up in `PATH`.

### What the installation does not do

It does not install the libraries, ollama and the embedding model, `dsh`, docker or tailscale; it does not download the image for Office preview (that is `flyarchive preview setup`); it does
not set a model. What is left for you to do is printed by `init` and `install` at the end: the preview image, the local model, the inbox folder.

### How to remove the system

The archive command has no removal subcommand: the installation writes files in several places, and you can remove them by hand, step by step. You run all the commands below yourself;
the paths are the defaults, and if you changed the settings `units_dir`, `bin_dir`, `dsh_profile` or `sandbox_dir`, or the archive directory (`FLYARCHIVE_HOME`), use your own. Skip the services and the timer that you did not install
(the gateway and the DSH shell): for their names systemctl answers that there is no such file.

```bash
systemctl --user disable --now flyarchive-inbox.timer        # 1. stop and turn off the intake timer and the services
systemctl --user stop flyarchive-inbox.service               #    an intake pass, if one is running right now
systemctl --user disable --now flyarchive-dsh flyarchive-gateway flyarchive-mcp flyarchive-office flyarchive-search
rm -f ~/.config/systemd/user/flyarchive-{search,office,mcp,gateway,dsh,inbox}.service ~/.config/systemd/user/flyarchive-inbox.timer   # 2. remove the service files
systemctl --user daemon-reload                               #    systemd forgets what was removed
ls -l ~/.local/bin/flyarchive                                # 3. the command link: it should lead to the launcher in the archive directory
rm ~/.local/bin/flyarchive                                   #    if it does, remove it
rm -r ~/flyarchive/bin                                       #    the command launcher lies in the archive directory: remove it, and do not touch the archive data
rm -r ~/.dsh/profiles/web/node_modules/flyarchive-dsh-plugin # 4. the plugin in the profile of the DSH shell
rm -r ~/.local/share/flyarchive-sandbox                      # 5. the sandbox directory of the external models' shells, if you ran any
docker image rm gotenberg/gotenberg:8                        # 6. the Office preview image; its name is recorded in preview.json in the archive directory
```

What stays, and is not removed by these commands:

- **The archive directory** (`~/flyarchive` or `FLYARCHIVE_HOME`): the corpus, the index, tokens, logs, the review queue, quarantine, receipts. This is your data: delete it yourself when you
  are sure you no longer need it, and not before.
- **The inbox folder and the return folder.** By default they lie in the archive directory; if you set them with `flyarchive inbox set --path`, they are separate directories. The files in them are
  yours, and they stay until you delete them.
- **The repository clone and the `.venv` environment.** These are ordinary directories: delete them when you no longer need the system. The DSH shell, if you installed it, is removed by its own tools
  (`npm uninstall -g @deepseek-ai/dsh`).

## Without services and without a shell

The core works through commands alone, without systemd, without DSH, without docker, without tailscale and without a local model. The end-to-end path without services is covered by the test
`tests/test_clean_install.py`; the "First five minutes" block in `README.en.md` leads through it with the samples from `examples/`.

| What you want to do | What you need for it | What you do not need |
|---|---|---|
| create an archive, accept documents with the command `flyarchive inbox run`, search with the command `flyarchive search` | Linux, Python, the required libraries, the embedding service | systemd, DSH, docker, tailscale, a model, bwrap |
| intake passes on a schedule | `systemctl` and the timer; without systemd, any scheduler that calls `flyarchive inbox run` | — |
| the search page and MCP | the running servers `tools/webui.py`, `tools/office_server.py`, `tools/mcp_server.py` | systemd (you can start them by hand), DSH |
| preview of pdf files, pictures, text, mail messages, archive files, calendars | `bwrap` | docker |
| preview of Office files, HTML, metafiles | `bwrap`, docker and the image (`flyarchive preview setup`) | — |
| model check of documents | a local model with an OpenAI-compatible interface | without it: `--llm off` |
| image description for pictures and scans | `bwrap` and a local model with vision | a cloud model (pictures are never sent to the cloud) |
| shells of external models in the sandbox | `bwrap` | — |
| the Archive section and tab | `dsh`, node, systemd (for the service) or a manual start | — |
| access from other machines | tailscale and the gateway | — |

Starting the services by hand (each one is a separate process; the archive directory is given by `FLYARCHIVE_HOME` if it is not `~/flyarchive`):

```bash
python3 tools/flyarchive init                                  # the archive directory, the token, the known-files base, the index table
python3 tools/flyarchive inbox set --path ~/flyarchive-inbox --llm off --cloud off --no-timer
python3 tools/flyarchive inbox run --wait                      # run an intake pass over the inbox folder now
python3 tools/flyarchive search "запрос"                       # search from the terminal ("запрос" means "query")
python3 tools/webui.py &                                       # the search page on search_port
python3 tools/office_server.py &                               # documents and graphics on office_port
python3 tools/mcp_server.py &                                  # the MCP adapter on mcp_port
```

`--no-timer` in `flyarchive inbox set` only saves the settings: the service and timer files are not written and `systemctl` is not called; without the option the command writes them and enables
the timer (and without systemd it reports on stderr that the timer is not enabled: this is not a failure). An intake pass without the timer is started with the command `flyarchive inbox run`.
Search from the terminal is `flyarchive search` (the same as `python3 tools/search.py`), with the options `-k`, `--source`, `--since`, `--space`, `--today`, `--full`, `--json`. When the search service is
running, the search page is opened with a one-time link: `flyarchive open`.

## Settings

### Where they live and how they combine

There are three layers, and each next one is stronger than the previous: **the default in the code < the file `settings.json` in the archive directory < the environment variable
`FLYARCHIVE_<KEY IN CAPITALS>`**. The archive directory (`home`) is taken only from the variable `FLYARCHIVE_HOME`, otherwise it is `~/flyarchive`. The file is a JSON object with keys from the
schema; it does not accept a key that is not in the schema (a typo does not pass silently); it must belong to you and must not allow writing by the group and others, otherwise it is not read.
A setting's value does not get into an error message: only the key, the source and what was expected are there. There are no secrets in the settings: the local model's key is read from the
variable `FLYARCHIVE_LLM_KEY` or from a file whose path is set by `llm_key_file`, and the key of the fallback cloud model is read from the variable `FLYARCHIVE_LLM_CLOUD_KEY` or from a file whose
path is set by `llm_cloud_key_file`.

An example `settings.json` (every key is optional; in the sample, "имя-твоей-модели" stands for the name of your model):

```json
{
  "llm_local_url": "http://127.0.0.1:8080",
  "llm_local_model": "имя-твоей-модели",
  "search_port": 8765
}
```

To see what is in effect and where each value comes from (`default`, `file`, `env`):

```bash
python3 tools/settings.py                    # the effective values and the source of each
python3 tools/settings.py --json             # the same, for programs
python3 tools/settings.py --get search_port  # one value; a list is printed one line per element
```

The reference of all the settings (key, type, default, bounds, description, environment variable) is printed by the command:

```bash
python3 tools/settings.py --reference
```

The intake settings (the inbox folder, the intake pass period, the model check, the fallback cloud model, the threshold, the unpacking limits, image description) are not in the reference: they
live in `inbox.json` in the archive directory, they are changed by `flyarchive inbox set` and the Intake section in DSH, and environment variables and `settings.json` do not set them. Each setting has one environment variable (`FLYARCHIVE_<KEY IN CAPITALS>`); the exception is the model keys: the keys themselves are not in
the schema, so their variables `FLYARCHIVE_LLM_KEY` and `FLYARCHIVE_LLM_CLOUD_KEY` are named separately, and the schema holds only the paths to the key files. The font for pdf (`pdf_font`) is in the
reference too: it is the path to a `.ttf` file.

### Reference of the settings

All the settings of the schema, in the order of the schema. `python3 tools/settings.py --reference` prints the type and the bounds of each value; every setting has one environment variable.
A list in a variable is written separated by commas. An empty value of a path, an address or a name means "not set".

| Key | Default | What it is |
|---|---|---|
| `home` | `~/flyarchive` | the archive directory; it is taken only from the variable `FLYARCHIVE_HOME` and cannot be in `settings.json` |
| `llm_key_file` | empty | the file with the key of the local model; the key itself is read only from the variable `FLYARCHIVE_LLM_KEY` |
| `llm_cloud_key_file` | empty | the file with the key of the fallback cloud model (the key itself is in the variable `FLYARCHIVE_LLM_CLOUD_KEY`); when not set, a dummy key goes to the server |
| `pdf_font` | empty | the `.ttf` font file with Cyrillic for the pdf files that the documents server builds; when not set, the font is looked up among the usual system fonts |
| `env_file` | empty | an optional file with environment variables (for example, the key of the cloud model) kept outside the repository |
| `dsh_profile` | `~/.dsh/profiles/web` | the DSH shell profile where the installation puts the plugin |
| `units_dir` | `~/.config/systemd/user` | the directory of the user systemd services where the installation writes the service files |
| `sandbox_dir` | `~/.local/share/flyarchive-sandbox` | the sandbox directory for running an external shell |
| `bin_dir` | `~/.local/bin` | the directory of the user's commands: the installation puts a link to `flyarchive` in it; empty means do not put it |
| `search_port` | `8765` | the port of the search service and the search page |
| `office_port` | `8766` | the port of the documents server |
| `mcp_port` | `8767` | the port of the MCP adapter |
| `mcp_url` | `http://127.0.0.1:8767/mcp` | the address of the MCP adapter as the clients see it |
| `gateway_port` | `8780` | the port of the gateway into the private network |
| `gateway_bind` | empty | the address the gateway listens on; empty means the gateway is off |
| `public_url` | empty | the external address of the gateway for links to documents |
| `public_hosts` | empty list | the node names under which the gateway accepts requests from outside |
| `hosts` | empty list | your own node names, besides the loopback, that the services accept in the Host header; a request with another name in it is not served |
| `dsh_port` | `3080` | the port of the DSH web shell |
| `embed_url` | `http://127.0.0.1:11434/api/embed` | the address of the embedding service |
| `llm_local_url` | `http://127.0.0.1:8080` | the address of the local model (any server with an OpenAI interface) without `/v1` at the end: `/v1` is added by itself |
| `llm_cloud_url` | empty | the address of the fallback cloud model without `/v1` at the end; it is turned on separately, in the intake settings |
| `dsh_patches` | empty list | extra DSH shell configuration files with models and providers: each one is passed to the shell with one more `--patch` after the main one |
| `env_names` | empty list | the names of the variables that the shell launcher takes from the file `env_file`; the rest of the file does not get into the environment |
| `dsh_llm_key_env` | empty | the name of the environment variable under which the shell expects the key of the local model (the key is read from the file `llm_key_file`); empty means the key is not passed to the shell |
| `embed_model` | `bge-m3` | the name of the embedding model; changing it requires rebuilding the index |
| `embed_dim` | `1024` | the dimension of the embeddings: it is built into the index table and changes only together with a rebuild of the index |
| `embed_gpu` | `false` | compute the embeddings on the GPU (otherwise on the CPU) |
| `llm_local_model` | empty | the name of the local model for the check and for the description |
| `llm_cloud_model` | empty | the name of the fallback cloud model; it is turned on separately, in the intake settings |
| `search_half_life_days` | `540` | the half-life of the freshness weight in search, in days: in this many days the freshness weight of a document falls by half |
| `search_nprobes` | `400` | how many partitions of the approximate index the search looks through: more means more exact and slower |
| `search_refine` | `30` | how many times more candidates the search re-checks against the original embeddings |
| `api_snippet_chars` | `420` | how many characters of text one result gets in the answer for a model |
| `api_max_k` | `20` | the largest number of results in one search call |
| `api_budget_chars` | `6000` | the common ceiling of text in one search answer, in characters |
| `mcp_timeout_s` | `300` | how many seconds the MCP adapter waits for the services to answer |
| `link_ttl_s` | `86400` | the lifetime of a signed link to a document, in seconds (one day) |
| `login_ttl_s` | `300` | the lifetime of a one-time login code, in seconds |
| `session_ttl_s` | `43200` | the lifetime of a session on the search page, in seconds |
| `llm_timeout_s` | `180` | how many seconds the model's answer is awaited in the check |
| `llm_max_chars` | `30000` | how many characters of a document the model sees in the check (the beginning and the end) |
| `llm_max_pages` | `20` | how many pages of a scan the model checks per document |
| `submit_max_mb` | `16` | the size limit of one document passed through MCP, in MB; the largest value is 17: a larger document would not pass through the request body of the gateway |
| `out_keep_hours` | `48` | how many hours the files created by the documents server are kept (charts, diagrams, Word, Excel, PowerPoint, PDF) |

### Samples `settings.example.json` and `sources.example.json`

Both files are in the root of the repository, and they are **samples of the format, not ready-made settings**. All the addresses and names in them are made up (`archive.example.com`, `llm.example.com`,
`cloud-model-example`). `settings.example.json` is built so that it does no harm when copied as it is: the keys that turn something on are empty in it, as they are by default:

- `gateway_bind` and `public_url` are empty: the gateway is not installed until you set both yourself (how to fill them in is in the section "Access from other machines": the address of this machine
  in the private network, from the range 100.64/10, and an external address of the form `http://<node name>:8780`, not `https`: the gateway speaks only HTTP); `flyarchive install --gateway auto` takes the missing
  values from tailscale;
- `llm_local_model` is empty: no local model is set, and `flyarchive init` and `flyarchive doctor` remind you to set one. Until you write the name of your model or turn the check off, every document waits
  for a decision in the review queue with the reason «модель не проверила: …» (the model did not check: …) (see "Local model");
- `dsh_patches` is empty: you name your own shell configuration files with models yourself;
- `llm_key_file`, `llm_cloud_key_file` and `pdf_font` point to files in a made-up directory (`~/flyarchive/secrets/llm-key`, `~/flyarchive/secrets/llm-cloud-key`,
  `~/flyarchive/fonts/DejaVuSans.ttf`): until you put your own files there, no key is found (a dummy key goes to the server) and the font for pdf is looked up among the usual ones.

The other keys in the sample are defaults or made-up values that turn nothing on by themselves (the fallback cloud model is turned on only separately: `flyarchive inbox set --cloud on`).
Take from the sample only the keys you need, and put in your own values. The rest works without the file too.

`sources.example.json` is a sample of the sources table; why it is needed and how to use it is said in the next section. The sample copied as it is is harmless: there are no roots with such names
in your corpus, and the walk skips them; but it gives nothing either.

### The sources table: your own exports, bypassing the inbox folder

The usual way to add to the archive is one: the inbox folder (see "The inbox folder and intake"). A file put into it goes through intake and gets into the corpus and the index by itself, and the sources table
is not needed for that. It is needed only by someone who puts ready-made exports into the corpus (mail, wiki pages, folders of files) by hand, bypassing the inbox folder and intake, and wants the documents from them
to get into the index under meaningful bases. Without it the archive works with one base, `входящие` (inbox).

The table is the file `sources.json` in the archive directory; the format of the keys is described at the beginning of `tools/sources.py`, a sample is `sources.example.json`, and its permissions are those of `settings.json`
(the file is yours, and writing by others is closed). It sets three things:

- `roots` — which `corpus` directories to walk and what to call the base of each: the kind of the directory (`mail` — mail, `pages` — pages with attachments, `files` — files), the default base,
  the rules "the base by a part of the path", and the place in removing duplicates;
- `labels` — the labels of the bases: they get into the description of the `search_archive` tool, which the model sees;
- `aliases` — under which former names the roots used to lie.

How to put your own files in and index them:

1. Put the export into the corpus directory: `<archive directory>/corpus/<root name>/…`.
2. Add this root to `sources.json`: the root name is the name of the directory from the first step, `kind` is the kind, `source` is the name of the base.
3. Run `python3 tools/index_more.py` (the embedding service must be running): it walks the roots from the table and the directory `входящие` (inbox), cuts the text into fragments, computes the embeddings and
   writes them into the index table. It can be resumed: a repeated run takes only what has not been passed yet.
4. Restart the search and MCP services: they read the table at startup, and the description of `search_archive` learns the new bases after a restart:
   `systemctl --user restart flyarchive-search flyarchive-mcp`.

The intake did not check such files: there are no rules, no receipts and no comparison with the known-files base for them, and you are responsible for their content.

## Access and tokens

Search, documents and the MCP adapter answer only a request with a token. The local DSH gets the service token by itself: the launcher takes it from `<archive directory>/secrets/local.token`.

```bash
flyarchive token add ноутбук                      # search and reading only (sample token names: "ноутбук" is "laptop", "планшет" is "tablet")
flyarchive token add планшет --level full --days 30
flyarchive token list
flyarchive token revoke ноутбук                   # takes effect from the next request
flyarchive journal -n 30                          # who asked what
flyarchive open                                   # a one-time link to the search page
```

The levels: `read` is search and reading documents, `full` also creates files and submits documents (`submit_document`); the service level `local` is the one DSH uses on the archive machine.
Deletion, intake decisions and the issuing of tokens are not exposed over MCP at all.

A token is shown once, when it is issued. The `secrets` directory holds only hashes (`tokens.json`), the link signing key (`link.key`) and the service token. The directory is closed with
permissions 700, the files with 600; with wider permissions the services do not start and name the reason in their log. A link from the search page is valid for as many seconds as the setting `login_ttl_s` says (300 by default, five
minutes: `flyarchive open` names this very term) and for one use, and the session after it lasts `session_ttl_s`; a signed link to a document lasts `link_ttl_s` (a day by default).

The access journal is `<archive directory>/logs/access.jsonl`, one JSON line per request. There are no token values in it.

The journal writes a refusal of a revoked or expired token under the name of that token: the line has its name, its level and the outcome «отказ: токен отозван <время>» (refusal: token
revoked <time>) or «отказ: токен просрочен <время>» (refusal: token expired <time>). The client gets the same as before, 401 and the general text «токен неверен, отозван или просрочен» (the token
is wrong, revoked or expired): the reason is not reported to it. If the token is both revoked and expired, «отозван» (revoked) is written. A value that is not in the store at all is written
as the client "-". A name can be issued again after a revocation: the old value keeps getting refusals under the same name with the mark «отозван» (revoked), and the new one works as usual.
A refusal does not update the token's «последний вызов» (last call).

```bash
flyarchive journal --client ноутбук -n 50         # refusals for a revoked token are visible under its name
```

In DSH it is the same: Settings → Archive → Access journal, with filtering by client.

A successful tool list (`tools/list`) is deliberately not written to the journal: the assistant client reconnects every half hour, and it would be noise. That the client is alive can be seen
from the `mcp:initialize` lines and from the lines of the services that run the tools (search, documents): they are written under the client's name.

MCP tools are annotated (`annotations` in the `tools/list` response, according to the MCP specification). The ones that read — `search_archive`, `read_document`, `read_document_rich` — are
marked "read only" (`readOnlyHint: true`). The ones that create — `make_landscape`, `make_diagram`, `make_chart`, `make_document`, `submit_document` — are marked as modifying
(`readOnlyHint: false`) but not destructive (`destructiveHint: false`): they create files and do not delete or overwrite anything. All of them have `openWorldHint: false`. A client that asks a
person about every tool without annotations lets the reading tools through by itself. The rule is guarded by the test `tests/test_mcp_annotations.py`.

A manual check (the services must be running):

```bash
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8765/api/search?q=test'      # 401
curl -s -H "Authorization: Bearer $(cat "${FLYARCHIVE_HOME:-$HOME/flyarchive}/secrets/local.token")" \
  'http://127.0.0.1:8765/api/search?q=test&k=1' | head -c 200                           # findings
```

### File permissions

```bash
flyarchive perms check            # the archive directory, its subdirectories, the files in secrets and logs
flyarchive perms check --deep     # every file
flyarchive perms fix [--deep]     # 700 on directories, 600 on files
```

The archive is closed to the other users of the machine. Everything that the command and the services create in the archive directory — the results directory `out`, the logs,
the index, the lists — is created for the owner only, with any process mask: the command and the services set the mask 077 at startup (services under systemd get the same
from `UMask=0077` in the service file), and directories and files are created at once with permissions 700 and 600. The directories that the command creates outside the
archive (the inbox folder, the return folder, the directory of service files) also get owner-only permissions. A shell in the sandbox (`flyarchive run`) gets the mask of
whoever started it: its files in the working directory are created with the same permissions as without the sandbox. Permissions do not protect against the owner's own
processes: the sandbox is for that (the next section). The command does not check permissions on the service code: it lies in the repository directory, not in the archive,
and closing it against writing by other users (`chmod go-w`) is up to you: whoever can change the code gets everything the
services can. The command says so in a second line after a successful check and after a fix, naming the directory it lies in. The installation does not create a separate service user for the services: that requires administrator rights.
## Connecting model shells

```bash
flyarchive connect claude            # Claude Code: a fragment of .mcp.json
flyarchive connect codex             # Codex: a fragment of ~/.codex/config.toml
flyarchive connect opencode          # OpenCode: a fragment of opencode.json
flyarchive connect dsh               # one more DSH: an overlay for the profile
flyarchive connect generic           # any other shell: the address, the header, a check with curl
flyarchive connect codex --remote    # a shell on another machine of the private network: the gateway address
```

The command prints three steps: issuing a token, configuring MCP and starting the shell. The configuration contains the name of an environment variable
(`FLYARCHIVE_TOKEN`, changed with the `--env` option), not the token itself. The address comes from the setting `mcp_url` (otherwise from `mcp_port`); with `--remote`
it comes from `public_url`, and the gateway must be installed for that.

### The shell of an external model in the sandbox

Tokens protect the network, not files: a shell that is simply started reads the corpus from disk, bypassing the tokens and the access journal. So start the shells of
external models as follows (`bwrap` is required):

```bash
flyarchive token add codex --days 90                 # the shell's token, shown once
export FLYARCHIVE_TOKEN=…                            # put it into the environment, not into files
flyarchive run --dir ~/projects/задача --name codex --env FLYARCHIVE_TOKEN --ro ~/.npm-global -- codex
```

| Option | What it does |
|---|---|
| `--dir` | the working directory of the shell, the only place where it writes |
| `--name` | the name of the shell: each one has its own home directory, `<sandbox_dir>/<name>` (by default `~/.local/share/flyarchive-sandbox/<name>`) |
| `--env <NAME>` | pass an environment variable; the value is taken from the environment and is not on the command line |
| `--ro <directory>` | show a directory as read-only, for example the one with the shell's program; its `bin` goes into the `PATH` |

Inside the sandbox there is no archive directory, no owner's home directory (ssh keys, the key of the local model), no Windows drives, no docker socket, no systemd bus,
no `sudo` and no foreign processes. The network is shared with the machine: MCP at `http://127.0.0.1:8767/mcp` answers by token.

A launch is refused before it starts if the working directory is inside the archive, above the archive, or the home directory itself, or if the service token
`FLYARCHIVE_LOCAL_TOKEN` is passed in `--env`. The directories listed in the file `<archive directory>/protected-paths` are protected in the same way as the archive
(one path per line; put there, for example, a copy of the corpus on another disk, which must not be handed over either). Every start is written to the access journal:
`flyarchive journal` shows the name of the shell, the working directory and the exit code.

What the sandbox does not do: it does not restrict the network. The shell can reach any service on the loopback interface and the internet. The archive is closed to it by
the token, but the embedding service and the model server do not belong to the archive: the archive's tokens do not protect them.

## Access from other machines

From another machine of the private network (tailnet), the archive is visible through the gateway — the only service that listens on an address of this network. You need
tailscale on both machines.

Two settings turn the gateway on: `gateway_bind` — the address of this machine in the private network (usually from the range 100.64/10; the gateway does not start on
`0.0.0.0`) and `public_url` — the external address of the gateway for links, of the form `http://<node name>:8780` (the gateway speaks only HTTP, so not `https`). The node
names under which the gateway accepts requests, other than the one taken from `public_url`, are listed in `public_hosts`. The easiest way is to let the installation take
everything from tailscale:

```bash
flyarchive install --gateway auto       # the address and the node name are taken from tailscale ip -4 and tailscale status; only the missing keys are appended
```

If the address or the name has changed, correct `settings.json` and restart `flyarchive-gateway`. The private network address does not appear right after the machine boots:
the gateway tries to take it for up to ten minutes and writes to its log that it is waiting.

What the gateway lets through from outside:

```
http://<node name>:8780/mcp         archive tools, by token
http://<node name>:8780/doc?…       links from answers: signed, no token, valid for link_ttl_s (one day)
http://<node name>:8780/file?…      created files: the same
```

Everything else gets the answer 404 from outside, and ports 8765, 8766 and 8767 cannot be reached from other machines. From outside, a token works only in MCP: a document is not
opened at a direct address even with a token, only by the signed link from an answer. Creating files needs a token of the `full` level. Deleting, intake decisions and
issuing tokens are possible only on the archive machine. The size of one submitted document is limited by the setting `submit_max_mb` (16 MB by default).

Connect a client:

```bash
flyarchive token add ноутбук --days 90      # on the archive machine; the token is shown once
flyarchive connect codex --remote           # a ready setting with the gateway address
```

On the client: the address `http://<node name>:8780/mcp` and the header `Authorization: Bearer <token>`. Check from the client:

```bash
curl -s -o /dev/null -w '%{http_code}' -X POST http://<node name>:8780/mcp    # 401
curl -s -X POST -H "Authorization: Bearer $FLYARCHIVE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://<node name>:8780/mcp
```

In the access journal such requests are marked «через tailnet» (via tailnet) and carry the address of the client's node.

Traffic between the nodes of the private network is encrypted by tailscale itself, so the gateway speaks HTTP. Do not turn on `tailscale funnel`: it would open the archive
to the internet. A machine outside the private network does not see the gateway; for a one-off check, forward a port over ssh and put the node name from `public_url` into
the `Host` header: `ssh -R 18780:<gateway_bind>:8780 <user>@<machine>` (on that side the address is `http://127.0.0.1:18780/mcp`). For permanent work, install tailscale on such
a machine.

## The DSH shell and plugin

DSH (DeepSeek Harness) is a shell with a web interface for talking to a model; the Archive section and tab appear in it. It is optional: the archive works without it (the command, the search
page, third-party shells through `flyarchive connect`). To install it, run `npm install -g @deepseek-ai/dsh` (the package is under the MIT license, the sources are at
`github.com/deepseek-ai/deepseek-harness`), then repeat `flyarchive install`.

The shell's profile (the directory from the setting `dsh_profile`, `~/.dsh/profiles/web` by default) appears after the first start of the shell: `tools/dsh-web-start`
starts it with a profile whose name is the last part of the path `dsh_profile` (`web` by default). While there is no profile directory, the installation skips the plugin and asks you to repeat the installation after the first start. The plugin
`flyarchive-dsh-plugin` (the version number is in `dsh-plugin/package.json`) is written for DSH on `@deepseek-ai/cordis` `~4.0.4`: if the shell has been updated and the plugin
no longer loads, the archive keeps working without it.

The installation has no models and no model providers: the shell configuration file from the repository connects only the archive's MCP server and the plugin. The setting
`dsh_patches` lists your own shell configuration files with models. Keys are not written into files: the launcher reads the local model's key from the file `llm_key_file` and
passes it to the shell under the name from `dsh_llm_key_env`; from the environment file `env_file` only the variables named in the list `env_names` are taken. The file does not
override a variable that is already set in the environment.

The login link to DSH carries a fresh token. The token changes every time the service comes up, so do not save the link as a bookmark:

```bash
tools/dsh-url                                      # print the current link (reads the service log)
systemctl --user restart flyarchive-dsh            # DSH reads plugins at start
```

Without systemd, start the shell by hand: `tools/dsh-web-start` (the link appears in the console). If the service holds the port, stop it first:

```bash
systemctl --user stop flyarchive-dsh               # otherwise the port dsh_port is busy
tools/dsh-web-start
systemctl --user start flyarchive-dsh              # bring the service back afterwards
```

Do not start the shell without `tools/dsh-web-start`: it would come up without the archive tools and without keys. The shell launcher connects them: it waits for the MCP adapter, reads the
service token and the keys (it calls the archive command through the command launcher `bin/flyarchive`, and when it is not there, through the file `tools/flyarchive`), and passes the shell the file `dsh.patch.yml` and the additional files from `dsh_patches`.

### The Archive tab and settings

The plugin gives DSH two places to work with the archive. The screen is in English; the names of buttons and sections below are given as they are on the screen; the messages of the
commands in the terminal stay Russian.

- **The Archive tab** in the right panel of a conversation, next to Files and Terminal. This is the everyday work: what has arrived, what waits for a decision, what happened
  earlier. The right panel exists only in an open conversation: in a new conversation, open the tab from the panel guide; on the start screen with no conversation
  there is no tab — use the section in the settings there.
- **Settings → Archive** — what changes rarely. The sections in order: Status, Folders, Intake, Tokens, Delete document, Access journal. The review queue and quarantine
  are not in the settings: they are on the tab.

The source of the plugin is the `dsh-plugin` directory of the repository; in DSH it lies in `<profile>/node_modules/flyarchive-dsh-plugin` and is enabled by a line in the shell
configuration file `<archive directory>/dsh.patch.yml` (the template is `tools/templates/dsh.patch.yml`).

#### The Archive tab

- **Status line.** How many files wait in the inbox folder ("Waiting in the inbox folder"), how often the intake pass runs ("Runs every N min" or "No timer: runs
  only on request"), the result of the last batch and the number of remarks in it, the number of documents that wait for a description ("Awaiting description"; zero is not shown).
  Next to it are the address of the inbox folder and "Copy path".
- **Run now** starts an intake pass through the service, like `flyarchive inbox kick`. While the pass runs, the button is inactive and the progress is shown under it: the name of
  the stage, "N of M" (or "N done" when the total is not known yet), a progress bar and "Last file". The stages are described in the section about the inbox folder.
- **Needs decision (N)** — the review queue and quarantine in one list. A row has: a checkbox, the name, where the file is from (`review` — the review queue, `quarantine` — quarantine),
  the score, the main rule, the time. A row from the review queue has the buttons Accept and Quarantine, a row from quarantine has Return and Delete. Delete erases permanently:
  it exists only on a single row and asks for confirmation ("Yes, delete forever").
- **Decisions for the marked rows.** "Select all" marks all the rows shown. "Accept selected" and "Quarantine selected" act on the marked rows of the review queue, "Return selected" acts on
  the marked rows of quarantine; a button that has nothing to act on is inactive. Before the action the tab asks for confirmation with the number of files. The decision goes out in one
  request (up to 200 paths, more than that in several requests); the answer comes for each path separately: the failure of one is shown at its row and does not interfere with the others.
  Any acceptance from the tab goes without waiting for indexing: the file is in the archive at once, and the next intake pass puts it into the index.
- **Batches** — the history of batches from the newest to the oldest, 30 at a time; "Load more" loads the next ones. A batch row has: the time (by the browser's clock), the number of
  files, the total by decisions ("accepted", "needs review", "quarantined" and so on), the number of remarks, the duration. An expanded batch shows the remarks of the pass and a
  table of files: File, Decision, Owner decision, Location, Score, Main rule. The buttons above the table filter the files by decision. An expanded file shows the findings, the reason,
  who checked it, the date of the document, the size, the SHA-256, the path in the archive or where the file was returned to. For an image or a scan, a line "Described by the model: N pages"
  or "Awaiting description" is added.
- **File preview.** An expanded row from "Needs decision" and a file in an expanded batch show the contents — how this works is in the section "File preview". While the file lies in the
  review queue, quarantine or the archive, it can be opened; for a returned or erased file, what remains is the information from the receipt.
- **Counter and message.** The title of the tab is "Archive (N)", where N is how many files wait for a decision; with none waiting, it is "Archive". When a batch has ended and it has files
  that need a decision or remarks, the message "Intake finished: …" pops up with the button "Open" (the button appears only when a conversation is open). The message is shown once for each
  batch and is not shown when the page loads.

The tab asks the server with one poll shared by all conversations: every 30 seconds while the page is visible, every 2 seconds while an intake pass runs. A hidden page does not poll.
The review queue and quarantine lists are re-read only when the counters or the number of the latest batch have changed. If there is no connection to DSH, the tab says so and keeps
trying. If the login has ended, the tab asks you to open DSH again by a fresh link (`tools/dsh-url`).

#### Settings → Archive

- **Status** — one line: how many files wait in the inbox folder, how many files wait for a decision, how many documents wait for a description, the result of the last batch, and a hint
  that decisions are made on the tab.
- **Folders.** "Inbox folder" is a field with the "Change" button. Before the change, DSH warns that the archive will take everything that lies in the new folder. The folder must already exist and lie
  outside the archive, otherwise the refusal is shown at the field. "Returns folder" and "Archive directory" are read-only, each has "Copy path".
- **Intake.** "Run every" (1, 5, 10, 30 or 60 minutes), "Score threshold", the unpacking limits ("Archive size, GB", "Files", "Compression, to 1", "Nesting depth"), "Check with the model",
  "Cloud fallback model" (with a warning that the text leaves the machine), "Describe images with the local model" and, with it, "Pages per run" and "Minutes per run". The changes are applied with the
  Save button. If you erase a number, the field stays empty; the name of an unfilled field is shown under the fields, and Save stays inactive.
- **Tokens.** A list without values: Name, Level, Issued, Expires, Last used, State (active, revoked, expired). To issue a token, give a name, the Level (read or full) and the term in days; the value
  is shown once. Revoke asks "Yes, close access". The service token `dsh-local` cannot be revoked here.
- **Delete document.** The path of the document, as in the search results, and a confirmation. The file is not erased but moved to `<archive directory>/удалённое/<day>/` (the name of the
  directory means "deleted").
- **Access journal.** The latest entries, with a filter by client; "no token" stands for requests without a token. Refusals to a revoked token are visible under its name.

All of this can also be done from the terminal: the settings section works through the `flyarchive` command. Issuing and revoking a token are written to the access journal wherever they are done from.

```bash
flyarchive token list --json
flyarchive token add ноутбук --level full --days 90 --json
flyarchive token revoke ноутбук --json
flyarchive journal --json -n 100 --client ноутбук
```

The plugin's addresses (`/api/flyarchive.*`) are closed by the DSH login: without a session cookie the answer is 401, with a foreign `Origin` or `Host` it is 403. The plugin takes the path to the
command from the shell configuration file (the line `command`, which the installation writes, so on an installed system the path is right), otherwise from the environment variable
`FLYARCHIVE_CLI`; if neither is set, it calls the command by the name `flyarchive` from the command search path (without a shell); if it is not found, the refusal hints at the setting that gives the path.

Important: models in DSH. DSH restricts only writing: the model's tools can read any file of the owner. A local model passes data to no one. A cloud model chosen in DSH can, through
the DSH tools, reach the archive files, bypassing MCP and the access journal. If the archive holds something that must not be handed to a cloud, do not choose a cloud model in a shell with the
DSH tools, and connect external shells through `flyarchive run` (see "The shell of an external model in the sandbox").

## The inbox folder and intake

Put a file, a folder or an archive file into the inbox folder — after one intake pass period (30 minutes by default) it is in the archive and in search. The default folder is
`<archive directory>/входящие` (the name of the directory means "inbox"); the return folder lies next to it and has the same name with the suffix `-возврат` (return): whatever the
archive did not take comes back here. Any other folder that lies outside the archive is set with `flyarchive inbox set --path`.

```bash
flyarchive inbox status                    # what is configured, what is waiting, how the last batch ended
flyarchive inbox run --wait                # run a pass now; --wait waits until a fresh file has settled
flyarchive inbox set --period 10           # a pass every 1, 5, 10, 30 or 60 minutes
flyarchive inbox set --path /srv/inbox     # another folder; not inside the archive
flyarchive inbox set --path /srv/inbox --must-exist   # the same, but the folder must already exist: nothing is created
flyarchive inbox set --llm off             # without the model check
flyarchive inbox set --cloud off           # without the fallback cloud model: no text leaves the machine
flyarchive inbox set --vision on           # describe images and scans with the local model (see "Image description")
flyarchive inbox batches                   # the history of batches, from the newest to the oldest (below)
```

One bad record does not stop the pass. An archive file that did not unpack goes to quarantine whole. A record on which intake itself failed is returned to the return folder; in the receipt it has
the decision `failed` and a reason, and in the command output there is a line «замечание» (remark). A file that could not be filed (no space, the name is taken) stays in the inbox folder
and is picked up by the next pass.

What happens to a file:

| Step | What is done |
|---|---|
| waiting | a file is taken when its size and modification time have not changed for 30 seconds |
| intake | type by content, unpacking of archive files, rules, check against the archive, model check |
| accepted | `<archive directory>/corpus/входящие/<batch>/…`, the index, the base «входящие» (inbox) |
| findings | `<archive directory>/очередь/<batch>/…` (the review queue) — waits for your decision |
| program, unusable archive file | `<archive directory>/карантин/<batch>/…` (quarantine) |
| verification | the sha256 of the copy equals the sha256 of the original file — the original file is removed from the inbox folder |
| receipt | `<archive directory>/квитанции/<batch>.jsonl` (receipts), one line per file; next to it `<batch>.meta.json` with the duration and the remarks |
| description | at the end of the pass an image or a scan without text is described by the local model and after that can be found by search; if the model is unavailable, a pending description entry «ждёт описания» (awaiting description) is left |

Nothing is lost: only what has an exact saved copy and what is not content is removed from the inbox folder. An exact copy exists when the file is accepted into the corpus, lies in the review
queue or in quarantine, or was already in the archive (an exact repeat). The files that are not content are empty files and system traces of operating systems: the directory `__MACOSX` and everything in it,
the files `.DS_Store`, `Thumbs.db`, `desktop.ini` and the files `._<name>` if they begin with the signature of the AppleDouble format (this is how a Mac puts metadata next to a file) or lie in
`__MACOSX`. Such a file gets the decision «пропущен» (skipped) with a reason in the receipt (for example, «пустой файл» (empty file)), does not go to the return folder and does not prevent
removing the folder or archive file where it lay: a zip from a Mac in which all documents are accepted is removed whole. The list of these names is one for everybody and cannot be configured;
an ordinary file of a person with a name that begins with `._` (it does not begin with the AppleDouble signature and lies outside `__MACOSX`) is not counted as a trace. Everything else that
the archive did not take is returned to the return folder: a file that is not a document (for example, a gif picture), an email that is already in the archive but with different bytes,
a file skipped by a name pattern (see "Intake parameters"). An archive file in which something like that was left is returned whole, and the traces inside it stay as they were.
Archive files of the types 7z and rar are unpacked by the `7z` program; without it they are not unpacked and go to quarantine.

Emails with garbled text (the finding `garbled`) are held in the review queue until you decide.

Without a model the check cannot give a verdict, and every document gets a HIGH finding «модель не проверила: …» (the model did not check: …): it waits for a decision in the review queue at any
threshold. While there is no model, turn the check off: `flyarchive inbox set --llm off` (see "Local model").

The review queue and quarantine lie outside the corpus and do not get into the index. Decisions on them are made on the Archive tab in DSH or with commands:

```bash
flyarchive queue list                                    # what waits for a decision: file, score, findings
flyarchive queue accept 'очередь/<batch>/<name>'         # accept: the corpus, the index, the known-files base
flyarchive queue quarantine 'очередь/<batch>/<name>'     # send to quarantine
flyarchive quarantine list
flyarchive quarantine return 'карантин/<batch>/<name>'   # return to the return folder; it does not get into the archive
flyarchive quarantine delete 'карантин/<batch>/<name>'   # erase, irreversibly
flyarchive doc delete 'входящие/<batch>/<name>' --yes    # remove the document from search and the corpus
```

You can accept only a file that has passed the check: its sha256 is compared with the receipt. A removed document is not erased: it moves to `<archive directory>/удалённое/<day>/…`.
To bring it back, put it into the inbox folder again.

Decisions can also be made in bulk: up to 200 paths in one command, and the paths go after `--` so that a file name is not taken for an option. With `--json` the answer is always
`{"results": [...]}`, with one record per path in the order of the request: `ok: true` and the usual fields of the answer, or `ok: false` and a message about the refusal. A bad path (no such
file, a file that is not from the review queue, a sha256 that does not match) does not stop the others, and the exit code is 0. More than 200 paths, an empty list or a path named twice is a refusal
before the first action. Without `--json`, several paths are printed one line per path, a failure goes to stderr as a line, and the exit code is 1.

```bash
flyarchive queue accept --json -- 'очередь/<batch>/<name 1>' 'очередь/<batch>/<name 2>'
flyarchive queue quarantine --json -- 'очередь/<batch>/<name 1>' 'очередь/<batch>/<name 2>'
flyarchive quarantine return --json -- 'карантин/<batch>/<name>'
```

Acceptance without waiting for indexing: the file moves into the corpus at once and is written to the known-files base, but the command does not wait for indexing. It writes a pending indexing entry and
starts an intake pass through the service; the answer has `indexed: false`, and the screen says «В индекс он попадёт следующим разбором» (it will get into the index with the next pass). This is how the
Archive tab accepts: a large document takes minutes to index, and a decision must not hang. If there is no timer service or a pass is already running, it is not an error: the entry is written, and the pass
that is running will pick it up at its end, or the next one will.

```bash
flyarchive queue accept --defer-index 'очередь/<batch>/<name>'
```

### Intake parameters

```bash
flyarchive inbox set --threshold 20        # up to what score a document is accepted automatically (0–100)
flyarchive inbox set --max-gb 2 --max-files 5000 --max-ratio 100 --depth 3   # limits for one archive file
flyarchive inbox kick                      # start a pass through the service and do not wait for the end
```

The values shown are the defaults. A program goes to quarantine, and what the model has not checked goes to the review queue at any threshold.

Service files. By default there are no names of "service" files: `README.md`, a `.jsonl` log and a `.csv` listing are accepted as ordinary documents, and in general any text file with any
name is too. If an export puts next to the documents files that are not needed in the archive (descriptions, listings, check reports), name them with file-name patterns (as in fnmatch,
case-insensitive). The key can be repeated; it sets the whole list, and an empty value clears it. A sample of how to write them is patterns for the companion files of an export that are not needed in the archive:

```bash
flyarchive inbox set --service-name '*.tmp' --service-name '*.bak'    # in any folder
flyarchive inbox set --service-root-name 'index.*'                    # only in the root of the inbox folder or of an archive file
flyarchive inbox set --service-fate delete                            # what happens to such files: return or delete
```

These parameters live in `inbox.json` in the archive directory: `service_names` is the patterns for any folder, `service_root_names` only for the root of the inbox folder or of an archive file,
`service_fate` is the fate of what was skipped. A file matched by a pattern gets the decision «пропущен» (skipped) with a reason in the receipt: «служебный файл выгрузки: перечень, а не документ»
(a service file of an export: a listing, not a document) (a pattern from `service_names`) or «служебный файл выгрузки: описание или отчёт о проверке» (a service file of an export: a description
or a check report) (from `service_root_names`). A description of an email is skipped in the same way: a `.json` file next to an email of the same name, `.eml` or `.msg`; no pattern is needed
for it. What happens to such files is decided by `service_fate`: `return` (the default) — the file moves to the return folder, and the folder or archive file where it lay is returned by the
general rule; `delete` — the file is removed and does not prevent returning the folder or archive file. A value that is not one of the two is refused with the message code
`inbox.bad_service_fate`, and the previous one stays. The patterns and the fate are visible in `flyarchive inbox status` and in the answer of `flyarchive inbox set`; the Intake section in DSH
does not show them and leaves them as they are when it saves. Empty files and system traces do not depend on `service_fate`: they are always removed.

Changing the inbox folder. With the option `--must-exist` the command creates nothing: if there is no such folder, it refuses, and the setting and the timer are not touched; the root and the home directory
are not accepted; the folder cannot lie inside the archive and cannot contain it. The change is written to the access journal: the service `cli`, the client «владелец» (owner), the old and the new address.
Without the option the command creates the missing folder. The "Change" button in Settings → Archive → Folders changes the folder in the same way. The return folder moves together with the inbox folder.
The archive takes everything that lies in the new folder, and what stayed in the old one will not be seen by a new pass.

The date of a document: the properties of the file first, then a date in the name, then the modification time, then the day of intake, with a mark. An email attachment gets the date of the email.

Another machine or a model with a token of the `full` level submits a document with the MCP tool `submit_document`. It lands in the same folder and goes through the same intake.

The pass on a schedule is run by the timer `flyarchive-inbox.timer`; the log is `<archive directory>/logs/inbox.log`.

```bash
systemctl --user list-timers flyarchive-inbox.timer
tail -20 "${FLYARCHIVE_HOME:-$HOME/flyarchive}/logs/inbox.log"
```

### A trial intake pass over a batch: `flyarchive check`

An intake pass into a folder (the intake directory): archive files are unpacked, each file goes through intake and is filed by the decision. The source is not changed, and nothing reaches the archive or the index.
This way you can see what intake would say without adding to the archive. The service-name patterns from `inbox.json` apply here too, and empty files and system traces get the decision
«пропущен» (skipped), as in an intake pass over the inbox folder.

```bash
flyarchive check ~/входящие/пачка.zip --into ~/flyarchive/staging/пачка-1
```

Before the pass you need the known-files base: it is how what already lies in the archive is recognized. The build reads only what is new or changed, so you can run it before every pass:

```bash
flyarchive known build
flyarchive known status
```

Emails are compared by `Message-ID`, other files by content. Without the base, `flyarchive check` refuses unless you give `--without-archive`: then duplicates are looked for only inside the batch.

In the intake directory:

| What | What it is for |
|---|---|
| `принято/` | passed intake |
| `на-утверждение/` | more findings than the threshold — you decide |
| `карантин/` | programs, scripts, password-protected archive files, archive bombs |
| `отчёт.md` | a summary: how many of what, what is in quarantine and why |
| `отчёт.jsonl` | one line per file: the decision, the score, the findings, where it was taken from |

The default limits for one archive file are: 2 GB unpacked, 5000 files, compression 100 to 1, nesting 3. For a large batch, set them with options:

```bash
flyarchive check mailarc.zip --into ~/flyarchive/staging/mailarc --max-gb 80 --max-files 400000 --jobs 16
```

The model check is turned on with the option `--llm`. The model reads only what has passed the rules and the check against the archive; it can add findings but cannot remove them.

```bash
flyarchive check пачка.zip --into ~/flyarchive/staging/пачка --llm               # the local model; the fallback cloud model if one is configured
flyarchive check пачка.zip --into ~/flyarchive/staging/пачка --llm --no-cloud    # the local model only
flyarchive check пачка.zip --into ~/flyarchive/staging/пачка --llm --llm-jobs 8  # read eight documents at a time
```

| What happened | What follows |
|---|---|
| the local model answered | the document is checked, and the report has the model's name |
| the local model is silent for `llm_timeout_s` seconds (180 by default) or the server reports that another model is loaded | the fallback cloud model checks the text, if one is configured — the text leaves the machine |
| there is no fallback model (`--no-cloud`, or `llm_cloud_url` and `llm_cloud_model` are not set) or it is silent too | the document waits for approval: «модель не проверила» (the model did not check) |
| the model answered with something that is not JSON, and a retry did not help | the document waits for approval; the text does not go to the cloud |
| an image or a scan | only the local model; without it the scan is recognized on the machine itself (`docling` is needed), and the text is what gets checked |

An intake directory must not be placed inside the corpus. Directories named `_карантин` are not walked by the indexer or by the known-files base.

The history of batches. The source is the receipts `<archive directory>/квитанции/<batch>.jsonl` and, next to them, `<batch>.meta.json`: when the pass ran, how many seconds it took and what remarks
it had. Old batches do not have this file: the duration is "—" and the remarks are 0.

```bash
flyarchive inbox batches                                  # the 30 latest batches, from the newest to the oldest
flyarchive inbox batches --limit 100                      # from 1 to 200
flyarchive inbox batches --before <batch>                 # only those older than the named one
flyarchive inbox batch <batch>                            # one batch, in detail
```

The batch number is `YYYYMMDD-HHMMSS`; if two batches started in the same second, `-2` is added to the second one. The list is a table with the columns «Пачка, Время (UTC), Секунд, Файлов, Замечаний, Итог» (Batch, Time (UTC),
Seconds, Files, Remarks, Result); if there are older batches, the last line suggests the command with `--before`. For one batch, the command prints one line per file: the intake decision, the score, where the file
is now, the owner's decision if there was one, and, for images and scans, the state of the description. Where the file is now: in the archive, in the review queue, in quarantine, returned to the
return folder, erased, «не хранится» (not stored: there was nothing to store — a duplicate, a skipped file, an unpacked archive file) and «нет на месте» (missing) — by the receipt the file should lie
in the archive, the review queue or quarantine, but it is not there; most likely it was removed by hand, and it is worth looking into. The same data is available with `--json`; the Batches block on the
Archive tab shows it.

Live intake progress. While a pass runs, the archive directory holds `inbox-progress.json`: the stage, how many are done out of how many, the last finished file, the batch number, the start time
and the time of the last update. When the pass has ended, including when it ended in a refusal, the file is gone. `flyarchive inbox status --json` gives it in the field `progress`, and if there is no
file, there is no process with the recorded process number or the record is older than ten minutes, the field is `null`. The Archive tab shows the progress under "Run now". The stages go in this order:

| Code | Name on the screen | What is going on |
|---|---|---|
| `intake` | Unpacking and rule checks | unpacking archive files, the rules, the check against the archive |
| `model` | Model check | the model check, if it is turned on |
| `settle` | Filing and de-duplication | filing by decisions and checking the copies |
| `index` | Indexing | indexing what was accepted and the pending indexing entries |
| `sources` | Cleaning up sources | removing the original files from the inbox folder |
| `vision` | Describing images | describing images and scans, see "Image description" |

The total number of files at the `intake` stage may be unknown: archive files are opened in rounds, and the number grows. While one document is being indexed or described by the model, the progress
record refreshes itself every 20 seconds, so a live pass does not look abandoned. A pass with no new files but with pending description entries runs without a batch and starts straight at the `vision` stage.

If the embeddings could not be computed (the embedding service does not answer), the document stays in the corpus, and the pending entry stays in `<archive directory>/index/pending.jsonl`. A pass works
off the pending entries at the start and once more at the end: an entry written while the pass was running (an acceptance without waiting for indexing) is indexed by the same pass. All changes of the
pending entries file go under the lock `index/pending.lock`: acceptances and a pass can run at the same time, and no entry is lost or doubled. How many pending entries there are is shown by
`flyarchive inbox status` («Ждёт индексации», awaiting indexing). The second kind of pending entry is «ждёт описания» (awaiting description) for images and scans without text, see "Image description".
## File preview

The Archive tab shows the contents of a file from the review queue, quarantine or the archive. The server draws the file, and only an image and plain text go to the browser: the browser
never receives the file itself. `bwrap` is required, and the system must allow it to create a sandbox (see "Troubleshooting common failures").

The worker process runs with the real interpreter with which the command was started (for a virtual environment, this is its base interpreter). If it lies in the system directories, the
sandbox sees it by itself; if it lies outside them (your own Python in the home directory, pyenv, conda), the directory of its installation is bound to the sandbox read-only. If it cannot be
given safely, the preview answers with the refusal `preview.python_outside`; the line `preview_python` in `flyarchive doctor` names this interpreter in advance.

| Kind of file | How it is shown | Limit |
|---|---|---|
| PDF, SVG | pages as images | 20 pages, at most 4 MP per page |
| PNG, JPG, GIF, BMP, TIFF, WEBP | an image | 2,000 pixels on the longer side |
| text, code, json, xml, sql, yaml, log, md | plain text | the first 20,000 characters |
| emails (eml and msg) | the header, the text and a list of attachments; an attachment is opened by the same preview, and so is an attachment inside an attachment | text of 20,000 characters; attachments nested deeper than two levels are not opened |
| archive files: zip, 7z, rar, tar and compressed ones | the contents: names and sizes, nothing is unpacked | 500 lines |
| calendar (ics) | the events: when, subject, place, number of attendees | 20,000 characters |
| Word, Excel, PowerPoint, OpenDocument, RTF, CSV, Visio, EMF, WMF, HTML | converted to PDF in a container and shown as pages | a file of up to 100 MB, a conversion of up to 120 seconds |
| programs, unknown types | only information about the file: name, type, size, sha256, batch, where it came from | — |

BPMN, draw.io, video and audio are not shown yet: for them the tab says "A converter is needed".

How this is protected, in plain words. A file in the review queue or in quarantine is unchecked and may be malicious, so the preview command does not parse it itself: it only computes
the checksum as a stream. The file is read by a worker process in a sandbox without a network: it sees its input file and nothing else, and its memory, its time and the size of what
it writes are limited before it has loaded any libraries. Office files and HTML are opened by a container with no network, read-only, without privileges, and with limits on memory,
cores and time. What the worker process or the container returned is checked as data (a regular file, the size, the beginning and the end of the PNG or PDF, a description against a
schema) before it is written to the cache. Only PNG and plain text go out: markup and scripts from the file do not get into the response, an SVG with a script gives an image, and the
page of the tab makes requests to nothing except DSH itself.

The same works from the terminal. The scope (`--area`) is `queue`, `quarantine` or `corpus`, and the path is as in the lists; paths for `corpus` are counted from the corpus root.
`--member N` opens an email attachment (up to two such options).

```bash
flyarchive preview show --area queue -- 'очередь/<batch>/<name>'              # what kind of file it is, the text or the number of pages
flyarchive preview show --area queue --json -- 'очередь/<batch>/<name>'       # the same, for other programs to read
flyarchive preview show --area queue --member 0 -- 'очередь/<batch>/<email>.eml'   # an email attachment
flyarchive preview page --area queue -- 'очередь/<batch>/<name>' 1 > страница.png   # page 1 as PNG, to stdout
```

One-time container setup. Without it, PDF, images, text, emails, archive files and the calendar are shown, but Office, HTML and metafiles are not. You need docker. The image with
LibreOffice is downloaded once, by a separate command; only that command needs the network, and the preview itself downloads nothing.

```bash
flyarchive preview setup                    # download the gotenberg/gotenberg:8 image and record its digest
flyarchive preview setup --image <image>    # the image is already downloaded: record the digest without the network
```

The digest (`<name>@sha256:…`) is written to `<archive directory>/preview.json`. The container is started only by the recorded digest: a tag without a digest is not accepted, and if the
machine has no image with that digest, the preview refuses with a hint and does not download the image itself. Access to docker equals the rights of the machine's administrator: the
command runs one pinned image with a fixed set of options, and the file name does not get into the options.

Limits. Worker process: 2 GB of memory, 60 seconds of CPU time, 90 seconds of total time, at most two processes at once (the third waits). Container: 2 GB of memory, 2 cores, 256
processes, 120 seconds for a conversion; the time limit is set inside the container, so the container ends even if the command was killed. One conversion runs at a time. The first
preview of a large Office file takes a long time; a repeat is taken from the cache at once.

The cache lies in `<archive directory>/cache/preview/<sha256>/` and is entirely derived: everything in it is recreated at the next preview. The key is the sha256 of the bytes that are
drawn, so an email attachment has its own cache, and the same attachment from different emails is drawn once. The cache is cleaned by itself when its size is exceeded, and on command:

```bash
flyarchive preview clean                  # remove what is older than 7 days and everything over 2 GB
```

The command prints «Убрано: N, освобождено байт: X, в кэше осталось байт: Y» (removed: N, bytes freed: X, bytes left in the cache: Y). Any `flyarchive preview …` command removes forgotten
containers `flyarchive-preview-…` older than five minutes at startup.

What the owner sees on a refusal. The preview does not fail: only the information about the file is shown, with the reason as text under it. On the tab this is "No preview is available for this
file" and an explanation; in the terminal the same text in Russian. Typical reasons: the file is protected by a password, it is a program or a script, the image is larger than the limit in
megapixels, the worker process ran out of memory or time, the file is larger than 100 MB for the container, the type is not supported, there is no `bwrap` sandbox (or there is one, but the system does not let it be created), the container is not set
up. If the file is being drawn by another request, the tab shows "Rendering…", asks again every 2 seconds, and after three minutes gives up and offers the "Try again" button.

## Image description

Images and scans without a text layer are accepted into the archive, but the index has no text for them, and search did not find them. A local model with vision describes them: what kind of
diagram it is, all the inscriptions word for word, what is connected to what. The description goes into the index as fragments of the same document, and search leads to the original file.
The description of each page begins with the words «Описание изображения, сделанное моделью (страница N):» (Image description made by the model (page N):), so it can always be told apart
from the real text of the document. The request to the model and the description are in Russian.

What is described: png, jpg, tiff and svg files accepted into the archive, and PDF pages without a text layer. Images of other formats (gif, bmp, webp) are not accepted at intake: by content it
recognizes only png, jpg and tiff among images, counts the rest as "not a document" and returns it to the return folder (and if such an image lies in an archive file, the whole archive file is returned). An image smaller than 120 pixels on a side is not described.
For a document, the first 20 pages are described. A PDF with a text layer does not need a description: its text goes into the index in the usual way.

What you need: a local model with vision (it accepts images in a `chat/completions` request) and `bwrap`; the model settings are described in the section "Local model". Images never go to
the cloud: this step has no fallback cloud path, and the setting of the fallback cloud model has no effect on it. The file for the model is prepared by the preview, that is, by a worker
process in the sandbox; the model receives a ready image of at most 4 MP.

To turn it on and off:

```bash
flyarchive inbox set --vision on                        # describe
flyarchive inbox set --vision off                       # do not describe: images are accepted and wait
flyarchive inbox set --vision-pages 60 --vision-minutes 10   # the limits of one pass
```

The same can be done with the checkbox "Describe images with the local model" and the fields "Pages per run" and "Minutes per run" in Settings → Archive → Intake. Until you have turned
the description on or off yourself, it follows the model check (`--llm`): if the check is on, the description is on too.

Limits of a pass. In one pass the step describes at most 60 pages (1–1000) and runs for at most 10 minutes (1–240); the rest stays pending. Requests go one at a time, one page per request;
the answer is at most 1,500 tokens long, and a request gets `llm_timeout_s` seconds (180 by default). Before each request it is checked that the model is ready: if the server reports a list
of loaded models, the needed model must be in it, and the step does not unload another model from the GPU.

Pending description entries («ждёт описания», waiting for description). An image or a scan is accepted as usual, and a pending entry is recorded for it in `<archive directory>/index/vision-pending.jsonl`.
Pending entries are worked off at the end of every pass, after the receipts (the stage Describing images; a pass with no new files but with pending entries also runs it), and by the command
`vision run`. If the model does not answer, is not loaded, is busy with another model, or the description is turned off, this is not a failure: the document stays in the archive, the entry
waits, and the batch remarks get one entry with the reason. The failure of one page does not lose the others. A page that failed in three passes is skipped, and the document is closed.

```bash
flyarchive vision status                          # whether it is on, how many wait for description, how many are described
flyarchive vision run                             # work off the pending entries now, within the pages and minutes from the settings
flyarchive vision run --limit 20                  # at most 20 documents at a time
flyarchive vision backfill --limit 200 --dry-run  # old documents without text in the index: show what was found
flyarchive vision backfill --limit 200            # record them as pending entries; `vision run` or a pass describes them
flyarchive vision backfill --limit 200 --source входящие   # only one base of the index (входящие is the inbox base)
```

`vision run` does not check the "describe" setting: the command is called by the owner. Old documents of the archive that have no text are described only on command and in parts:
`vision backfill` without `--limit` refuses, and it does not call the model itself, it only records pending entries. `--limit` is an integer from 1 to 100,000.

Where the descriptions are kept: `<archive directory>/index/vision/<sha256>.json` (the model, the date, the pages). The file is named by the sha256 of the document, so there is neither a
document name nor the text of the description in the path. The description is kept separately from the index so that the index can be rebuilt without the model: re-indexing takes the ready
descriptions, and a description of the same content is not made again. `flyarchive doc delete` removes the description file and the document's pending entries, unless the same content lies in
the archive under a different path.

The description is data, not instructions. Control characters are removed from it, its length is limited, and the model's key is erased. If an instruction for the model is written on an
image, it goes into the description as an inscription and changes nothing. The description goes through the same rules as text at intake: a page with a HIGH or CRITICAL finding does not go
into the index; in the file, only the page number and the name of the rule remain from it, and the batch remarks get the entry «описание страницы N не попало в индекс: правила нашли …»
(the description of page N did not get into the index: the rules found …).

Where it is visible: `flyarchive inbox status` («Ждёт описания», waiting for description), `flyarchive inbox batch <batch>` («описан моделью, страниц: N», described by the model, pages: N,
or «ждёт описания», waiting for description), Status and the Archive tab ("Awaiting description"), the Batches block. A batch receipt is written before the description and does not change
afterwards, so the live state is taken from the description file and the pending entries.

## Local model

Two steps need the local model: the check of documents at intake and the description of images. **Any server with an OpenAI interface** (`<address>/v1/chat/completions`) can serve as the
model, as long as you have given it an address and a model name; for image description, the model must accept images in the request. The requests to the model are written in Russian, so it
must read Russian.

```json
{
  "llm_local_url": "http://127.0.0.1:8080",
  "llm_local_model": "имя-модели-на-сервере"
}
```

This is the content of `settings.json`. The value "имя-модели-на-сервере" in the example is a placeholder that means "the model name on the server". The address `llm_local_url` is written without
`/v1` at the end (it is appended automatically), and the name `llm_local_model` is the one under which the server knows the model. The key, if the server requires one, goes in the environment
variable `FLYARCHIVE_LLM_KEY` or in a file whose path is set by `llm_key_file`; the settings do not contain the key itself.

What the server must be able to do, and what is optional:

- Required: an answer to `chat/completions`. The request goes without tools, with temperature 0 and with extra fields: `response_format` (JSON), `chat_template_kwargs` with reasoning turned
  off, and for the cloud endpoint, `reasoning_effort`. A server that answers them with code 400 gets the same request once more with only the main fields (the model, the messages, the
  temperature, the number of tokens); if that succeeds, from then on only these fields are sent to that endpoint in that process.
- Optional: the list of loaded models, `GET <llm_local_url>/running`, which not all servers support. A server that answers it with code 404, 405 or 501 does not report what is loaded, and
  the model on it is considered ready. If the list exists, the needed model must be in it, and while another model holds the GPU, the step waits: the check waits for up to `llm_timeout_s`
  seconds, then goes to the fallback model or is postponed; image description does not unload the other model and is postponed. Any other failure of the list request means that the state is
  unknown, and the step stops with a reason.

**What happens without a model.** The name is not set (`llm_local_model` is empty): there is no local model, and no requests are made. The intake check then gives no verdict: every document
gets the finding «модель не проверила: …» (the model did not check: …) and waits in the review queue for a decision; image description follows the check and accumulates pending entries
«ждёт описания» (waiting for description). This does not go unnoticed: `flyarchive init` and `flyarchive doctor` remind you to set a model. To work without one, turn off the check and the
description:

```bash
flyarchive inbox set --llm off --cloud off
```

Then documents on which the rules found nothing are accepted by themselves, and those where the rules gave a score above the threshold (`--threshold`) wait in the review queue for a decision.
You can turn the model check on later by setting `llm_local_model` and running `flyarchive inbox set --llm on`.

**Fallback cloud model.** It is off by default. To turn it on, set `llm_cloud_url` and `llm_cloud_model` and run `flyarchive inbox set --cloud on`: then, if the local model is silent for longer
than `llm_timeout_s` seconds, the cloud model checks the text of the document, and the text leaves the machine. The key of the cloud endpoint is set the same way as the local one's: by the variable
`FLYARCHIVE_LLM_CLOUD_KEY` or by a file whose path is set by `llm_cloud_key_file`; if there is neither, the endpoint receives the dummy key `ollama`, as servers without a key do. It also receives
the field `reasoning_effort`, as with ollama-compatible cloud models. The mark «текст уходил с машины» (the text left the machine) is set by the endpoint that answered, not by the name of the
model: in the intake report it stands when a document was checked by the fallback cloud endpoint. Images and scans never go to the cloud.

**Limits of the check.** The model sees the beginning and the end of a document up to `llm_max_chars` characters (30,000 by default) and up to `llm_max_pages` pages of a scan (20 by default).
The model only adds findings: it cannot take away the score of the rules, and it does not send anything to quarantine by itself. For the model, a document is data between random markers, not
instructions; if the answer is not JSON after one retry, the document goes to the review queue, and it does not go to the cloud.

**One GPU.** If the local model takes almost all of the video memory, the embedding model must not share the GPU with it: by default, embeddings and the index rebuild are computed on the CPU
(`embed_gpu` is off). For scheduled work, while the large model is unloaded, you can turn the GPU on explicitly: for embeddings with the setting `embed_gpu` (`FLYARCHIVE_EMBED_GPU=1`), for
building the index with the option `--gpu` of `flyarchive index optimize`. Image description runs on the local model itself, not on a separate one, and does not unload another model from the
GPU: until the needed model is loaded, documents wait for description.

## Index maintenance

The index is the table `docs` in LanceDB in the directory `<archive directory>/index/lance`; for how search and the table are built, see `docs/en/architecture.md`. A freshly created table has
only a full-text index: there is no vector index, and search by embeddings goes by brute force; rows added after the indexes were built also lie outside them and are searched by brute force.
While there are tens or hundreds of documents, this is not noticeable. When tens of thousands have accumulated, the search indexes need to be built: `flyarchive index optimize`.

```bash
flyarchive index optimize               # build the search indexes (on the CPU); deletes nothing
flyarchive index optimize --compact     # the same, and also compact the table: merge fragments, remove old versions
flyarchive known build                  # known-files base: sha256, Message-ID and the fingerprint of emails; reads only what is new
flyarchive known status                 # the state of this base
flyarchive index dedupe                 # count the duplicates of emails and mail files in the index (without the option it changes nothing)
flyarchive index dedupe --apply         # remove them
flyarchive index dates                  # count how many dates will change
flyarchive index dates --apply          # set the real dates on emails and attachments (and rebuild the indexes)
python3 tools/index_more.py             # top up the index for a corpus placed in the directory corpus by hand
```

**Search indexes.** `flyarchive index optimize` builds the indexes on top of the table: the full-text one anew (the replacement is atomic: if it fails, the previous one stays) and an approximate
vector index, IVF-PQ (cosine distance; the number of partitions is the square root of the number of rows, kept between 64 and 4096). On a table of fewer than 20,000 rows the vector index is
not built: brute force there is faster than an index, and the command says so; this is not a failure. The computation runs on the CPU; the GPU is turned on only by the option `--gpu` (if the
GPU turns out unsuitable, the computation runs on the CPU instead of being refused). Without `--compact` nothing is deleted. With `--compact` the command also merges the fragments of the table,
brings the indexes up to cover all rows, and removes all versions of the table except the last: every write to the table creates a version, and old versions take up space. Do not compact while
a write is in progress; while an intake pass is running, the command refuses, and the intake pass timer skips its pass while the command works. Run it after a large load and when search over a
large index has slowed down. If there is no table, it refuses: run `flyarchive init` first.

**Removing duplicates.** An email that comes from several exports of a mailbox should be in the index once. Without the option, `index dedupe` only counts; with the option `--apply` it deletes the
fragments of the extra copies and saves the list of removed paths in `index/dedupe-dropped-<time>.txt`. The corpus files are not touched. Before cleaning you need a fresh known-files base
(`flyarchive known build`) and a copy of the directory `index/lance`.

**Real dates.** Without the option, `index dates` counts how many dates will change. With the option `--apply` it rewrites the index with the real dates (for an email, from the header; for an
attachment, the date of the email, task or page), builds the vector and full-text indexes on the CPU when the table has at least 10,000 rows, and swaps the directories. If there is nothing to
change, the command prints «Менять нечего» (nothing to change) and builds nothing. The previous index stays in `index/lance.before-dates-<time>`: you can delete it once you are satisfied with
how search works.

**Topping up the index.** `python3 tools/index_more.py` walks the corpus roots named in the sources table (see "The sources table: your own exports, bypassing the inbox folder") and the directory
`входящие`, cuts the text into fragments of up to 4,000 characters and writes them with embeddings. It can be resumed: the files already passed are recorded in `index/ingested.txt`.
A file that could not be read because a format library is missing (docx, xlsx, pptx, msg, pdf) is not recorded there: the reason is named once per library (the `lib.missing` message),
the number of skipped files is in the final line, and after the library is installed a second run indexes the file. Directories `_карантин` are never walked. For documents accepted through the inbox folder it is not needed: intake indexes them itself.

Embeddings and the index are computed on the CPU; for scheduled work, the GPU is turned on explicitly (see "Local model").

## Logs

```
<archive directory>/logs/search.log      search
<archive directory>/logs/office.log      documents and graphics
<archive directory>/logs/mcp.log         MCP adapter
<archive directory>/logs/gateway.log     gateway
<archive directory>/logs/dsh-web.log     DeepSeek Harness
<archive directory>/logs/inbox.log       intake pass over the inbox folder
<archive directory>/logs/access.jsonl    access journal (read by the command flyarchive journal)
```

Control bytes get into the DSH log, so read it with `grep -a`: otherwise grep prints "binary file matches" instead of the line. The state of the services is shown by
`systemctl --user status flyarchive-search` and so on.

## Troubleshooting common failures

**Search answers 502 or 503 and states a reason.** 502 means «служба векторов не отвечает» (the embedding service does not answer): check `embed_url` and that the service is running
(`flyarchive doctor`, the line about the embedding service). If the ollama log says `model "bge-m3" not found`, the service is running as a different user and looks into that user's model
store: download the model there (`ollama pull bge-m3` as the right user) or point the service at the right folder with `OLLAMA_MODELS`. 503 means that the library `lancedb` or the index table
is missing: the text of the answer says what to install or which command to run (`flyarchive init`).

**The embedding service is not installed or not running.** Search answers 502, and `flyarchive doctor` writes in a `НЕТ` line:
«служба векторов не отвечает: <адрес> (<причина>). Запусти её и скачай модель: ollama pull bge-m3; другой адрес — настройка embed_url»
(the embedding service does not answer: the address (the reason). Start it and download the model: ollama pull bge-m3; for another address, use the setting embed_url), and at the end:
«Не хватает обязательного: служба векторов» (a required item is missing: the embedding service). The same summary ends `flyarchive init` and
`flyarchive install`. What to do, in order:

1. Check that ollama is installed: `ollama --version`. If there is no such command, install ollama from the download page (see "Embedding service").
2. Start the service: under WSL2 without systemd, run `ollama serve` in a second terminal and leave it open; where the system starts it, check that the system service is running.
3. Download the model: `ollama pull bge-m3`.
4. Repeat `flyarchive doctor`: the line «служба векторов отвечает» (the embedding service answers) should appear. If the service listens on another address, set it in the setting `embed_url`.

If `doctor` writes «служба векторов не ответила за N с: первый запрос может поднимать модель — повтори проверку» instead (the embedding service did not answer within N s: the first request may load the model, so repeat the check),
the service exists but the first request waited while the model loaded from disk: repeat the check.

**`flyarchive: command not found` after installation.** The directory that holds the link (`bin_dir`, `~/.local/bin` by default) is not in `PATH`. The installation printed the line for the profile of
your command-line shell; until you add it, call `python3 tools/flyarchive …`.

**`flyarchive` answers «нет библиотеки …» (no library …).** The command that the installation set up (the link `<bin_dir>/flyarchive` pointing to the launcher) runs with the installation's
interpreter, and it does not need the environment to be activated: such an answer means that the library is missing in this very interpreter. A different Python runs the command when
you call `python3 tools/flyarchive …` from the clone directory in a shell where the virtual environment is not activated. The answer looks like this:
«нет библиотеки lancedb: без неё нельзя завести таблицу индекса и искать по архиву. Поставить: python3 -m pip install -r requirements.txt»
(no library lancedb: without it you cannot create the index table and search the archive. To install: python3 -m pip install -r requirements.txt). Activate the environment (`. .venv/bin/activate` from the
clone directory) and repeat. If the library really is missing,
the command from the answer installs it. What exactly is installed and what is missing is shown by `flyarchive doctor`.

**«FlyArchive работает на Linux; под Windows — в WSL2» (FlyArchive runs on Linux; on Windows, in WSL2).** This is how every subcommand except the help answers when the command is not run on Linux: for example,
with Python from Windows, or on macOS. The exit code is 2. Open a WSL2 terminal and run the command from there; on another system the archive will not start.

**Services do not start.** Look at the service log in `<archive directory>/logs/` and at `systemctl --user status flyarchive-<name>`. Typical causes: the permissions on the directory `secrets`
or on the token store are wider than 700 and 600 (`flyarchive perms check`, `flyarchive perms fix`); a setting with an invalid value (the refusal names the key and the source, and the value is
not in it); a port that is already taken.

**The gateway says it is waiting for tailscale.** The address `gateway_bind` has not appeared on the machine: check `tailscale ip -4`. The gateway tries for up to ten minutes and then exits, and
systemd restarts it. An address that the machine does not have and will not have (for example, one copied from someone else's setting) will never be bound.

**All documents go to the review queue with the finding «модель не проверила: …» (the model did not check: …).** The local model is not set or is unavailable (see "Local model"). Set
`llm_local_model` or turn the check off: `flyarchive inbox set --llm off`.

**7z and rar archive files go to quarantine and are not shown.** The program `7z` is not installed (it comes from the package p7zip or 7zip); zip and tar are read without it.

**`no credential for provider route "<name>"` in DSH.** The shell does not see the model key: the service does not read `.bashrc`. Set `llm_key_file` and `dsh_llm_key_env` (the launcher reads
the file and passes the key under this name) and make sure that the file is in place and that your shell configuration file from `dsh_patches` names the same variable. Restart `flyarchive-dsh`.

**`EADDRINUSE: address already in use 127.0.0.1:3080`.** DSH is already started by the service. Stop the service or just open the existing link (`tools/dsh-url`).

**The model repeats the same call and does not answer.** Look at the access journal: `flyarchive journal -n 30` shows the calls that reached the archive. If the repeats are not there and the model
has still looped, the call is getting lost between the model and the archive, on the shell's side. A short call helps: for diagrams, use `make_landscape`, and keep the source for `make_diagram` under three thousand
characters; you can also limit the number of calls in the system prompt of the shell. If the calls arrive but the answer is empty, see the next case.

**Empty results with a working index.** First check that the request reached the archive: `flyarchive journal -n 20` should show a search line under the client's name. If the line is not there, the model
is calling not `search_archive` but another tool of the shell: name `search_archive` directly in the system prompt. If the line is there and there are no results, loosen the query: drop `source`,
`space` and `since`, which narrow the search; the same query from the terminal, `flyarchive search "запрос"`, gives what the model sees.

**Intake does not find the files that are in the inbox folder.** `flyarchive inbox status` names the inbox folder in its first line: check that it is the one you put files into.
`flyarchive inbox set --path` without `--must-exist` creates a missing folder, so a typo or a Windows path instead of a WSL2 path (`/mnt/c/…`) gives a new empty folder, not a refusal,
and intake honestly answers «Разбирать нечего: …» (nothing to take: …). Fix the path, preferably with `--must-exist`. Check against the archive itself: `flyarchive known status` gives the number of files and emails in the base.

**The preview answers «страница ещё рисуется другим запросом: повтори позже» (the page is still being drawn by another request: try again later).** On the tab this is "Rendering…", after three
minutes "The preview is still not ready" and the button "Try again"; in this case `flyarchive preview show` prints «Файл ещё обрабатывается другим запросом: повтори позже» (the file is still being
processed by another request: try again later). Office conversion goes one at a time, and there are at most two worker processes; a large xlsx or pptx takes up to 120 seconds. Wait and press
"Try again" (in the terminal, repeat the command): what is ready is taken from the cache. If the answer does not change for longer than several minutes, run `flyarchive preview clean`: any
`preview` command removes the containers `flyarchive-preview-…` older than five minutes at startup, and the time limit inside the container itself is 120 seconds, so a hung one does not live long.

**Office, HTML or a metafile is not shown: «образ для просмотра не записан: выполни flyarchive preview setup» (the image for the preview is not recorded: run flyarchive preview setup).** The
container is not set up. The same signs:
«образа <образ> … нет: выполни flyarchive preview setup» (the image … is not on the machine: run flyarchive preview setup),
«docker не установлен: Office, HTML и метафайлы без него не показываются» (docker is not installed: Office, HTML and metafiles are not shown without it),
«docker не ответил за N с: проверь, что служба docker работает» (docker did not answer within N s: check that the docker service is running).
Install or start docker and run `flyarchive preview setup` (or `flyarchive preview setup --image <image>` if the image is already downloaded). PDF, images, text, emails and archive files do not
depend on this. For BPMN, draw.io, video and audio the preview shows «нужен преобразователь: файлы этого типа (…) пока не показываются»
(a converter is needed: files of this type (…) are not shown yet): they are not implemented.

**`flyarchive doctor` writes «программа bwrap найдена, но песочницу создать не может» (the program bwrap is found, but it cannot create a sandbox).** The program is installed, but the system does not
let it create a sandbox, so the preview, image description and the shells of external models do not work (a file is never parsed without a sandbox); intake, the index and search work. In
parentheses stands the reason: the return code of `bwrap` and the first line of its answer (or, if it did not start or did not finish in time, the kind of failure or the time limit). The usual reason is a ban on unprivileged namespaces: on a plain Ubuntu 24.04 outside WSL2 the system may set it, and
in a container the container itself does. To find how to allow the sandbox on your system, look for the kernel parameter `kernel.apparmor_restrict_unprivileged_userns` and for an AppArmor profile
for `bwrap`; this is a parameter of the system, not of FlyArchive, and it is you who decides whether to change it. Check the result with the same `flyarchive doctor`.

**The preview answers «просмотр работает с Python из системных каталогов: … лежит вне них» (the preview works with a Python from the system directories: … lies outside them).** The worker process
of the preview runs with the interpreter with which the command was started, and the directory of its installation has to be given to the sandbox; for an interpreter outside the system
directories (your own Python, pyenv, conda) this is possible as long as the installation directory is safe. The preview answers with the refusal `preview.python_outside` when the interpreter
lies not in its own installation directory (an environment with a copy of the interpreter), when the installation directory is of the first level, is the owner's home directory or the directory
above it, or coincides with the archive directory, lies in it or above it. The same refusal is named by the line `preview_python` in `flyarchive doctor`. What to do: install the libraries into an
environment created by the system Python (`python3 -m venv`) and run the installation again from it.

**The documents server answers «нет шрифта с кириллицей для pdf: …» (there is no font with Cyrillic for pdf: …).** This is a refusal (code 503) to a `make_document` request with the kind pdf and
Russian text: there is no font with Cyrillic in the file from `pdf_font` or among the usual fonts of the system, and the standard pdf fonts do not know it. Install the package named in the answer
(on Debian and Ubuntu, `sudo apt install fonts-dejavu-core`), or point `pdf_font` at a `.ttf` file. After the package, a repeat works at once; after changing `pdf_font`, restart the documents
server: `systemctl --user restart flyarchive-office`.

**Documents wait for description and are not described.** The "Awaiting description" number in Status and on the tab does not go down, and `flyarchive vision status` shows «Ждёт описания: N»
(waiting for description: N). The reason is named in the batch remarks and, for the file, in the batch history:
«локальная модель не задана (настройка llm_local_model)» (the local model is not set: the setting llm_local_model),
«локальная модель не загружена: документы ждут описания» (the local model is not loaded: documents wait for description),
«локальная модель не отвечает (…)» (the local model does not answer),
«локальная модель не ответила за N с» (the local model did not answer within N s),
«видеокарта занята другой моделью (…)» (the GPU is busy with another model) or
«ждёт описания: описание изображений выключено (включить: flyarchive inbox set --vision on)» (waiting for description: image description is turned off; to turn it on: flyarchive inbox set --vision on).
This is not a breakage: the files are already in the archive, only their description is waiting, and until it is done, search does not find them: they have no text for the index. Set and load the
model (if the server loads it on the first request, it is enough to ask it anything once) and run `flyarchive vision run`; otherwise the next pass will work them off. If another model is on the GPU,
wait: the step does not unload it. If the description is turned off, turn it on: `flyarchive inbox set --vision on`.

**An intake pass runs for a long time, and the tab shows a stage.** This is not always a failure. Look at the stage: `Unpacking and rule checks` on a large archive file takes minutes, and the number
of files grows while the archive files are being unpacked; `Model check` takes seconds per document; `Indexing` takes minutes for a large document; `Describing images` takes up to ten minutes per
pass, and the rest stays pending. A pass is alive while the progress record is being updated: `flyarchive inbox status --json`, the field `progress`, and in it `updated`. If the progress disappeared
in the middle of a pass (`progress` became `null`, there is no new batch, the files lie in the inbox folder), the pass most likely died: there is no process, or the record is older than ten minutes.
The next pass will remove what is left of the record and continue; you do not have to wait for the timer: "Run now" or `flyarchive inbox run`. The answer
«Разбор входящих уже идёт — этот запуск пропущен.» (an intake pass is already running; this run was skipped) is not an error: another pass is running, and two at once are not needed.

**A token was revoked, and the client keeps trying to connect.** The access journal has lines under the client name with the outcome
«отказ: токен отозван <время>» (refusal: token revoked, then the time) or «токен просрочен <время>» (token expired, then the time). The client itself sees only 401 and a generic text; the reason is not reported to it, so it will not understand what to do.
To look: `flyarchive journal --client <name> -n 50` or Settings → Archive → Access journal with a filter by client. If the client is needed, issue a new token (after a revocation the name is free) and
update it on the client; if not, remove the MCP server from its settings. Lines with the client "-" show a value that is not in the store at all: a typo or someone else's token.

## Tests

The tests do not touch the live archive, the index, the services or the model: the server tests run in temporary directories, on a stub model and a stub embedding service; a real GPU,
ollama and the network are not needed for them. The libraries from the dependency files are needed, and the tests run in the same virtual environment where those libraries are installed
(`. .venv/bin/activate`):

```bash
python3 -m pip install -r requirements.txt -r requirements-optional.txt -r requirements-dev.txt
```

With only the required libraries (`requirements.txt` and `requirements-dev.txt`), the suite also runs with no failures: a test that needs a format library or a program that is not there is
skipped with a reason that names it. The more libraries are installed, the more tests run.

**Do not change the repository files while the full run is going.** One of the tests runs the plugin tests from the repository directory, and editing files at that moment can turn it red through no fault
of the code. If the suite turned red while you were editing something, run it again.

**1. Python.**

```bash
python3 -m pytest                                  # all tests; takes about half an hour
python3 -m pytest tests/test_inbox.py              # one file
```

There are several thousand tests, and many of them start the archive command as a separate process, so a full run takes about half an hour (longer on a weak machine). `pytest.ini` sets `-q`: the output is short,
and the summary line (`N passed in …`) stays; a second `-q` on the command line removes it as well, and then you look at the return code and at the `FAILED` lines. The plugin tests (`node --test`) run inside this suite. Tests that need `bwrap`, docker
with an image, node, `7z`, graphviz or a format library are skipped without them, with the reason named; a skip is not green, so read the summary lines (`-rs`). A test with a real sandbox is also skipped where `bwrap` is installed but the system does not let a sandbox be created (a plain Ubuntu 24.04
outside WSL2, a container): the reason names what exactly does not work — there is no program, it does not start or a namespace cannot be created, and what `bwrap` itself said. On Python 3.10 one test is also
skipped (a fragment of the Codex setting is read through `tomllib`, which is in the standard library since 3.11).

**2. The plugin tests separately** (node 18.15 or newer is needed, 19.6 or newer on the 19 line; with an older version the tests in the suite are skipped with the reason named; checked on version 22):

```bash
cd dsh-plugin && node --test test/*.test.mjs
```

**3. End-to-end checks through the interface.** A real DSH with the plugin from the working directory and a real Chromium: the tests drive the browser through the Archive tab and the settings and
check the result from outside, by an MCP request and by a command, and not only by what the page shows. They cover issuing and revoking a token (a revoked token gets 401, and both attempts are
visible in the access journal under the client name), the "read" and "full" levels, an expired validity period, the token value not remaining either on the page or in the browser storage, changing the
folder, the period and the model check, the setting for image description, "Run now" and the intake progress, decisions one at a time and by batch, the preview of an email with attachments, and
deleting a document.

```bash
bash dsh-plugin/e2e/run.sh                  # the whole suite; takes minutes
bash dsh-plugin/e2e/run.sh -g "токен"       # only the tests whose name contains the word (test names are in Russian: "токен" means "token")
```

What you need on the machine (the suite downloads and installs nothing): Linux (WSL will do), node 20 or newer, Playwright and `@playwright/test` installed globally, Chromium for Playwright, the
command `dsh`, a `bwrap` that is able to start a sandbox, python3 with `lancedb`, `pyarrow` and `pymupdf`. If something is missing, the suite prints "e2e suite skipped" with the reason and exits
with code 0. This is a skip, not a passed check: read the output. Otherwise the return code is the result of the tests.

The suite runs on a throwaway test bench: its own `HOME`, `DSH_HOME` and archive directory, a stub `systemctl` and embedding service, no model, free ports on the loopback. It does not touch the
live archive, the DSH profile, the services and the timers; before its first click every test checks that the address and the directory belong to the test bench, and otherwise fails without
clicking anything. After a run, the suite searches its own report for client tokens and sign-in links and exits with code 1 if it finds any. Environment switches: `BA_E2E_TOOLS` (where to take
the server-side code from: `worktree` by default, `head`, or a directory), `BA_E2E_PLUGIN`, `BA_E2E_OUT`, `BA_E2E_KEEP=1` (do not delete the directory of the test bench; needed only for debugging,
and the tokens of the test bench lie in it, so delete the directory after debugging).

**4. Documents.** The test `tests/test_docs_for_reader.py` checks `docs/operations.md` and `docs/architecture.md` (the Russian originals) against the code: every named path, command, option,
setting and service must exist, and there must be no traces of another machine or links to private documents.

**5. The publication gate.** For those who publish their own fork. The guard checks every tracked file before the code is made public, and names the file and the line: home paths, forbidden
words, email addresses that are not examples, network addresses and node names, anything that looks like tokens and keys, the working name of the project, tracked files in private directories.

```bash
python3 tests/publication_gate.py --words <word list file>
python3 tests/publication_gate.py --root <snapshot directory> --words <word list file>   # a clean snapshot, not a repository
python3 tests/publication_gate.py --words <word list file> --json                        # findings and a summary for other programs
```

The list of forbidden words lies outside the repository: it holds exactly those names of organizations, surnames and machine names that must not be in a public repository, and inside the
repository it would itself become a leak. The path to it is the option `--words` or the environment variable `FLYARCHIVE_GATE_WORDS`. The contents of the list are never printed; only the place
where something was found is printed. Without the list the gate is not considered passed: return code 2. The codes: 0 means clean, 1 means there are findings, 2 means the gate could not work (no
list, no git, the directory did not open). Exceptions are recorded in `tests/publication_allow.txt` with a reason, and the allowed nodes in `tests/publication_hosts.txt`.
