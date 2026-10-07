# FlyArchive

Russian (original): [README.ru.md](README.ru.md)

Your own document archive with search, on your own machine. You put files into the inbox folder; the archive checks them, accepts them and
indexes them. You can search it yourself, or give a language model (local or external) access to it over MCP (Model Context Protocol, the way a model
gets the archive's tools) with a token that you issue.

- **Search by meaning and by words.** Embeddings and a full-text index in one table; everything is computed locally.
- **Adding documents with strict intake.** The file type is detected from its content, not from its extension. Programs and anything suspicious
  go to quarantine, anything doubtful waits for your decision, and duplicates are not accepted. Nothing is lost: every file gets a receipt, and what the archive did not take is returned to the return folder (only saved exact copies and what is not content are
  removed: empty files and system traces such as `__MACOSX` and `.DS_Store`; and when the fate of service files is set to "delete", the files matched by the service name patterns are removed too, while by default they are returned).
- **Models work with the archive through tools.** Search, reading whole documents, diagrams, charts, building Word, Excel, PowerPoint and PDF files.
  Access is by token with the "read" or the "full" level, and every request is written to the access journal.
- **One door to the outside.** From other machines the archive is visible only through the gateway in the private network: MCP with a token and
  signed links to documents. Deleting, decisions on the review queue and issuing tokens are possible only on the archive machine.

Version 0.1. The command interface and messages are in Russian; the plugin screen for the web shell (a client program that talks to a model, such as DSH) is in English.

## What you need

| What | Why | Required |
|---|---|---|
| Linux (on Windows, WSL2) | file locks, sandbox, user services | yes |
| Python 3.10 or newer and the libraries from `requirements.txt` | archive, intake, search | yes |
| Embedding service with an Ollama interface (simplest: Ollama itself) and the `bge-m3` model, about a gigabyte on disk | embeddings for the index and for queries | yes |
| Libraries from `requirements-optional.txt` | Office and email formats, charts, PDF building, parsing documents with tables | depends on the format |
| systemd (user services) | running all the time: search, documents, MCP, scheduled intake | no: everything can also be started by hand |
| bubblewrap (`bwrap`) | preview of unchecked files, shells of external models in a sandbox; on a plain Ubuntu 24.04 outside WSL2 a system setting may forbid the sandbox, and `flyarchive doctor` will show it | no |
| docker | preview of Office and HTML files | no |
| 7z, graphviz | 7z and rar archive files in the inbox folder; diagrams | no |
| Local model on a server with an OpenAI interface | model check of documents, description of images and scans | no |
| DeepSeek Harness shell (`dsh`) | talking to a model in the browser and the "Archive" section | no |
| Tailscale | access to the archive from your other machines | no |

`flyarchive doctor` tells you what is missing on your machine and what will not work without it.

## First five minutes

You need: Linux (on Windows, WSL2), Python 3.10 or newer (on Debian and Ubuntu, together with the `python3-venv` package), git, and the ollama embedding service
with the bge-m3 model. Programs such as bwrap, 7z, docker, systemd and the DSH shell are optional: search works without them.

Install the embedding service separately, before the block below: the block does not install it, it only downloads the model for it. The service is the Ollama program.
Download it from the download page on the Ollama project's site: [ollama.com/download](https://ollama.com/download). Take the install command from that page and read it
before you run it: it is not given here. Ollama has to be running for as long as the archive is. Where the system starts it, it is already running; under WSL2 without
systemd, run `ollama serve` in a second terminal and leave it open. The line `ollama pull bge-m3` in the block downloads the model: about a gigabyte on disk. To see
whether the service answers, run `flyarchive doctor` (the line "служба векторов отвечает", meaning "the embedding service answers"); if it does not, see the case
"The embedding service is not installed or not running" in the troubleshooting section of [docs/en/operations.md](docs/en/operations.md).

The block below takes you from cloning the repository to finding a document: a virtual environment, the environment check, creating the archive, three made-up documents
from `examples/`, an intake pass, a search. No services and no shell are involved: the command does everything. The virtual environment is created first: on Ubuntu 24.04 and Debian 12
the system `pip` refuses to install libraries outside an environment (`externally-managed-environment`), while inside one (the `.venv` folder next to the code)
the system Python stays untouched. You need to activate the environment in every new terminal session: `. .venv/bin/activate` from the clone directory.

The intake timer is not installed (`--no-timer`): after the block there are no service files and no enabled timer on the system, and intake passes are started by hand with
`inbox run`. To get intake on a schedule, run `flyarchive install`, or run `flyarchive inbox set` without `--no-timer`. An intake pass takes a file only once
it has stopped changing (30 seconds by default), so `inbox run --wait` waits about half a minute before it answers. The model check is off
(`--llm off`): without a local model, everything unchecked would wait for your decision in the review queue; you can set a model later, with the `llm_local_model` setting.
At the end, `init` and `doctor` ask the embedding service, and the first request may have to load the model from disk: `init` sometimes waits up to a minute, and the command has not hung.

<!-- first-five-minutes -->
```bash
git clone https://github.com/andrewsabn/flyarchive.git flyarchive && cd flyarchive
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r requirements.txt
ollama pull bge-m3                                # the ollama service must already be running; the model is about a gigabyte
python3 tools/flyarchive doctor                   # what is missing; the "embedding service" is required, the rest is optional
python3 tools/flyarchive init
python3 tools/flyarchive inbox set --path ~/flyarchive-inbox --llm off --cloud off --no-timer
cp examples/* ~/flyarchive-inbox/
python3 tools/flyarchive inbox run --wait
python3 tools/flyarchive search "перенос релиза"
python3 tools/flyarchive search "tally file format"   # the same in English: the samples come in both languages
```

`doctor` names what is missing (libraries for the formats: `python3 -m pip install -r requirements-optional.txt`). The `flyarchive` command
without `python3 tools/` appears after the permanent installation (the section below): `flyarchive install` puts a link to it in `~/.local/bin`
and installs the services.

## What you should see

At the end of `inbox run` you get the batch number (`YYYYMMDD-HHMMSS`, the time of the run) and the count by decision; the three samples are accepted, and their receipts lie in the archive directory:

```
Пачка ГГГГММДД-ЧЧММСС:
  Принято: 3
Квитанции: <каталог архива>/квитанции/ГГГГММДД-ЧЧММСС.jsonl
```

The program prints in Russian, so the lines above stay in Russian. "Пачка" means "Batch", "Принято: 3" means "Accepted: 3", and "Квитанции" means "Receipts"
(the path of the file with the receipts, one line per file). In the sample, placeholders stand for the date and the time. Search prints the results in order; the beginning of the output of `search "перенос релиза"`:

```
 1. [0.0297] ГГГГ-ММ-ДД  ГГГГММДД-ЧЧММСС  protokol-vstrechi.md
    входящие/ГГГГММДД-ЧЧММСС/protokol-vstrechi.md
    # Протокол встречи команды «Лодочный журнал» Встреча выдуманной команды, которая делает приложение для записи лодочных прогулок. Любое сходство с …
```

The first line of a result holds the number, the score in square brackets, the document date, the section, and the title. For a file accepted through the inbox folder, the section is the batch number,
and the date is the file's modification time: the samples have no date of their own. The second line is the path in the archive (`входящие` (inbox) is the base of what was accepted through the inbox folder),
and the third is the beginning of the text (up to 220 characters; it is cut short here). The score is for comparing results with each other: your numbers will differ, and what matters is which document comes
first. The query `search "tally file format"` puts `file-format.txt` first. If nothing is found, the command prints «ничего не найдено» (nothing found).

If the output of `inbox run` has lines `замечание:` (remark) about the embedding service, the documents are accepted but not yet indexed, and search will not find them for now: for the way out, see
the case "The embedding service is not installed or not running" in the troubleshooting section of [docs/en/operations.md](docs/en/operations.md).

## Permanent installation

```bash
. .venv/bin/activate                           # the environment from the clone directory: the services and the launcher remember this Python, not the system one without libraries
python3 tools/flyarchive install --dry-run     # what would be written and started; touches nothing
python3 tools/flyarchive install               # the archive, the services and the intake timer, the command in ~/.local/bin, and start-up
```

The services listen on the loopback interface only: the search service, the documents server and the MCP adapter. Data, logs and secrets are kept in the archive directory
(`~/flyarchive` or the `FLYARCHIVE_HOME` variable); the code stays where the installation was started from. The `flyarchive` command is installed as the launcher `bin/flyarchive` in the archive directory and a link to it in `~/.local/bin`:
it runs with the Python that the installation was started with, so the environment does not need to be activated in a new shell for it. The `dsh` shell and its plugin are installed
if `dsh` is present on the machine; the gateway into the private network is installed if its addresses are set or with the `--gateway auto` option.
Under WSL2, the user services need systemd to be enabled: the line `systemd=true` in the `[boot]` section of the file `/etc/wsl.conf`, then a restart of WSL
(`wsl --shutdown` in Windows). The model check is on by default: until a local model is set, every document waits for your decision in the review queue;
set a model (the `llm_local_model` setting) or turn the check off: `flyarchive inbox set --llm off`.
Details, settings and updating are in [docs/en/operations.md](docs/en/operations.md).

## Connect a model

```bash
flyarchive token add ассистент --level read    # the token is shown once
flyarchive connect claude                      # ready-made MCP setting for a shell; also available: codex, opencode, dsh, generic
flyarchive journal -n 20                       # who asked what
```

The model works with the archive through the MCP adapter, and the adapter has to be running. After the "first five minutes" there are no services: install them with the permanent installation (the section above) or
start the three services by hand, `tools/webui.py`, `tools/office_server.py` and `tools/mcp_server.py` (the section "Without services and without a shell" in
[docs/en/operations.md](docs/en/operations.md)).

The model gets the archive's tools and works only through them. How it should use them is described in
[docs/en/for-llm.md](docs/en/for-llm.md): you can hand this document to the model whole, as an instruction.

## Add documents to the archive

```bash
flyarchive inbox status          # what is set up and what is waiting
flyarchive inbox run --wait      # run an intake pass on the inbox folder now
flyarchive queue list            # what is waiting for your decision
```

Put a file, a folder or an archive file into the inbox folder. An intake pass takes the files that have stopped changing, checks them and
sorts them: what is accepted goes to the corpus and the index, what is doubtful goes to the review queue, what is dangerous goes to quarantine, and the rest goes to the return folder; empty files and system traces (`.DS_Store`, `__MACOSX`) are removed.
While no local model is set and the model check is on, everything counts as doubtful: documents wait in the review queue (`flyarchive queue list`) until you set a model or turn the check off
(the section "Permanent installation").

## Documentation

| File | What it covers |
|---|---|
| [docs/en/operations.md](docs/en/operations.md) | Installation, settings, access and tokens, the inbox folder, the shell, troubleshooting |
| [docs/en/architecture.md](docs/en/architecture.md) | How it is built: services, access, intake, the index and search |
| [docs/en/for-llm.md](docs/en/for-llm.md) | How a language model should work with the archive: tools, order of work, specifics of search |
| [docs/en/requirements.md](docs/en/requirements.md) | Requirements as cards: what the system does and what it must be like |

## What is in the repository

```
tools/                  the flyarchive command, services, intake, search, installation; tools/templates/ — service templates
dsh-plugin/             plugin for DeepSeek Harness: the tab and the "Archive" section
examples/               made-up documents for a first try
tests/                  tests; the plugin's JavaScript tests are part of the full run
docs/                   documentation
requirements*.txt       dependencies: required, per format, for tests
settings.example.json   sample of the settings file settings.json (a sample of the format, not ready-made settings)
sources.example.json    sample of the sources table sources.json (needed only if you put ready-made exports into the corpus yourself)
CHANGELOG.md            what changed between versions
```

## Tests

```bash
. .venv/bin/activate                                 # the same environment as at installation; in a new terminal session you need to activate it again
python3 -m pip install -r requirements.txt -r requirements-optional.txt -r requirements-dev.txt
python3 -m pytest
```

The tests run in the same virtual environment where the libraries are installed: outside it the system will not let you install them. A full run takes about half an hour: there are several thousand tests.
The tests touch neither your archive nor your services: each test has its own temporary directory and stub programs. On a fresh clone the suite shows no failures even with
only the required libraries (`requirements.txt` and `requirements-dev.txt`): a test that needs a library for some format, or a program such as node, bwrap,
docker, 7z or graphviz, is skipped with a reason that names it. A test that needs a real sandbox is skipped also where `bwrap` is installed but the system does not let it create a sandbox. A skip is not a pass: read the summary lines (`-rs`).

## Limitations

- It runs on Linux; on Windows, in WSL2. It has not been tested on macOS and will not start there: it needs Linux file locks and the Linux sandbox.
- The command, its messages and the names of the directories in the archive (`входящие` (inbox), `очередь` (review queue), `карантин` (quarantine), `квитанции` (receipts))
  are in Russian. Image descriptions and the instructions to the model during the model check are in Russian too.
- The archive is designed for one owner on one machine. There is no separation of rights between users.
- The embedding service must understand the Ollama request (`/api/embed`); the dimension of the embeddings is set when the archive is created.
- Tested on Ubuntu 24.04 (WSL2), Python 3.12, and the library versions from the dependency files.

## Licenses

| What | License |
|---|---|
| Core: everything except the `dsh-plugin/` directory | GNU AGPL-3.0, text in [LICENSE](LICENSE) |
| Plugin `dsh-plugin/` | MIT, text in [dsh-plugin/LICENSE](dsh-plugin/LICENSE) |

Required libraries: lancedb and pyarrow are Apache-2.0, PyMuPDF is AGPL-3.0. Of the per-format libraries, extract-msg
is distributed under GPL-3.0, and the others use permissive licenses (MIT, BSD, HPND, the matplotlib license).

Copyright © 2026 Andrey Sabynin.
