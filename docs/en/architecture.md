# Architecture

Russian (original): [docs/architecture.md](../architecture.md)

How FlyArchive is built: which services it consists of, where the code and the data live, how a document gets into the archive, how it is searched, and how a person
opens it. Everything here is checked against the code. Default values are taken from the settings schema (`tools/settings.py`; `python3 tools/settings.py --reference`
prints the reference), not from anyone's installation. For how to install and run FlyArchive, see `docs/en/operations.md`. For the requirements, card by card, see
`docs/en/requirements.md`.

## Overall diagram

```
  archive machine                              other machines of the private network
  ───────────────                              ─────────────────────────────────────
  browser ── DSH :3080 ─┐                      model shell + token
  shell in the sandbox ─┤                               │
                        ▼                               ▼
                   MCP :8767  ◄──────────  gateway  <private network address>:8780
                    │      │               (/mcp by token, /doc and /file
                    ▼      ▼                by signed link)
           search :8765  documents :8766
           LanceDB       Docling, matplotlib, graphviz,
           + embedding   accepting a document into the inbox folder
           service
                │              │
             index         corpus ◄── inbox folder ── intake ── timer
```

DSH, the gateway and the sandbox are optional. The core — the inbox folder, intake, the index and search — works through commands even without any services (see "Installation").

The DSH plugin is the owner's path, not the model's: it does not go through MCP but calls the `flyarchive` command. It has two halves. The server half lives in the DSH
process and runs the command. The browser half draws the settings section and the tab.

```
  browser: the Archive tab, Settings → Archive
        │  fetch /api/flyarchive.*   (DSH's own login: session cookie, Host and Origin check)
        ▼
  DSH :3080 ── plugin, server half (dsh-plugin/lib/index.js)
        │  request check, then execFile without a shell: arguments as a list, paths after "--"
        ▼
  flyarchive … --json   (tools/flyarchive)
        │
        ├─ inbox folder, batches, intake progress, decisions   inbox.py, batches.py, progress.py, review.py
        ├─ file preview                                        preview.py, preview_worker.py, preview_container.py
        ├─ image description                                   vision.py
        └─ tokens, access journal, document deletion           tokens.py, journal.py, review.py
```

The archive services listen on the loopback interface only; the listen address is not configurable in them. The only door to the outside is the gateway, on the machine's
address in the private network. Under WSL the loopback interface is visible to Windows programs too. The archive services are protected by a token, but the embedding service and
the local model server do not belong to the archive. Their addresses are set by the settings `embed_url` and `llm_local_url`, and whoever installed them must protect them
from other callers.

## Services and ports

| Default address | What | File |
|---|---|---|
| `127.0.0.1:8765` (`search_port`) | search: the page, `/api/search`, `/doc`, `/openapi.json` | `tools/webui.py` |
| `127.0.0.1:8766` (`office_port`) | documents and graphics, accepting a document over MCP, `/openapi.json` | `tools/office_server.py` |
| `127.0.0.1:8767` (`mcp_port`) | the MCP adapter on top of the first two | `tools/mcp_server.py` |
| `<gateway_bind>:8780` (`gateway_port`) | the gateway into the private network: MCP and signed links; does not start without `gateway_bind` | `tools/gateway.py` |
| `127.0.0.1:3080` (`dsh_port`) | DeepSeek Harness with the plugin: the Archive section in the settings and the Archive tab in the right panel | `tools/dsh-web-start`, `dsh-plugin/` |
| — | an intake pass over the inbox folder on a timer (every 30 minutes by default); at the end of a pass, image description | `tools/inbox.py`, `flyarchive-inbox.timer` |
| `127.0.0.1:11434` (`embed_url`) | embedding service with the `/api/embed` interface (ollama): `bge-m3` embeddings; not part of the archive | installed by the owner |
| `127.0.0.1:8080` (`llm_local_url`) | local model server with an OpenAI-compatible interface: the model check at intake and image description; not part of the archive | installed by the owner |

The search and documents servers are separate on purpose: parsing a hundred-page pdf takes seconds and, in a shared process, would hold up search results. The MCP adapter
relays requests to these two servers; it does not repeat their work. The embedding service can be replaced by any service that answers the same way. The embedding model and
its dimension are the settings `embed_model` and `embed_dim`.

One address of the search and documents services is named in the table without explanation. `/openapi.json` exists on both services: it is a description of their tools in the OpenAPI
format, for a shell that takes its tools over OpenAPI rather than over MCP; it is given only with a token (the "read" level or higher) and only on the loopback interface. The gateway lets only
`/mcp`, `/doc` and `/file` out, so `/openapi.json` is not available from other machines.

### The DSH plugin

The `flyarchive-dsh-plugin` plugin has two halves that talk to each other only through the addresses `/api/flyarchive.*`. It does nothing on its own: it does not read the
token store, the access journal or intake, and it does not know their format. Everything goes through the `flyarchive` command.

- **The server half** (`dsh-plugin/lib/index.js`) registers the addresses in the DSH connection. DSH itself decides who is let in: it checks `Host` and `Origin` and a signed
  session cookie, so only a person who is logged in to DSH on the archive machine can work with the archive. The plugin checks each request before it calls the command: names
  and levels, number bounds, the shape of a path (non-empty, no control characters, not looking like an option), a list of at most 200 paths without repeats, the form of a
  batch number, the preview scope, the nesting depth of attachments, the page number. Then it runs `flyarchive … --json` through `execFile`. There is no shell, the arguments
  are passed as a list, paths go after `--`, and a file named `--yes` will not turn into an option. A refusal by the command (the last line of stderr is JSON `{"error": …}`)
  reaches the client as 409 with the message. The command's exit code 2 (argument parsing) and anything else become 502 with a generic text: raw stderr never goes out, and
  anything that looks like a token is erased. A token value gets into a response only when the token is issued. The wait limit for the command is 20 seconds for an ordinary
  call, 15 minutes for accepting and deleting (indexing runs on the CPU) and 300 seconds for preview. The plugin takes the path to the command from its own setting
  (`command` in the shell configuration file: the installation writes into it the command launcher `<archive directory>/bin/flyarchive`), otherwise from the environment variable `FLYARCHIVE_CLI`, otherwise it takes
  the name `flyarchive` from the command search path (still launched without a shell); if it is not found, the refusal hints at the setting that gives the path.
- **The browser half** (`dsh-plugin/lib/client.js` and the message dictionary `client.messages.js`) is written without a bundler, in the format of the DSH module loader. It
  puts a section into the settings and a tab type into the right panel (the tab is single and stays mounted), as well as the tab title and an entry in the panel guide. The
  message dictionary is loaded by a separate request (`require.async`): the DSH host serves only `client.js` and its neighbors of the form `client.<name>.js`.

A shared state store and a single poll. The tabs of the right panel live inside a conversation session, and there can be many sessions. Having each of them poll the server
would be wasteful. So the state is one shared store for the settings section, the tab body, its title and the pop-up message, and only the plugin's root effect polls the
server: every 30 seconds while the page is visible, every 2 seconds while an intake pass runs, and not at all while the page is hidden. The review queue and quarantine lists are
re-read only when the counters or the number of the latest batch change. The title "Archive (N)" and the message about the end of a batch (`Toast` from the DSH primitives, in
the plugin's own React root) read the same store. The tab, language and `Toast` services are internal to DSH and may change with its updates. If they are missing, the plugin
does not fail: the settings section works as before, and the message is just not shown.

## Code and data

Code and data are separate. **The code** is the `tools` directory of the repository (and `dsh-plugin` for the plugin); the services are started from the directory the
installation was run from. **The data, the logs and the secrets** are the archive directory: `~/flyarchive` or `FLYARCHIVE_HOME` (the setting `home`, the only one that is
not in `settings.json`, because that file lies in the directory itself). There is no code in the archive directory: only the command launcher `bin/flyarchive` lies there, a short sh file written by the installation. Each service receives the archive directory in one
environment variable. The service files contain no addresses, ports or node names: the services read the settings at startup.

Settings are built from three layers: the default in the code (not tied to a machine), the file `settings.json` in the archive directory, and the environment variable
`FLYARCHIVE_<KEY IN CAPITALS>`. Each setting has one variable, and the last layer is the strongest. The keys of the models never get into the
settings: the local model's key is taken from the variable `FLYARCHIVE_LLM_KEY` or from a file whose path is set by `llm_key_file`, the key of the fallback cloud model from the variable
`FLYARCHIVE_LLM_CLOUD_KEY` or from a file whose path is set by `llm_cloud_key_file`. The intake settings (the inbox folder, the intake pass period,
the model check, the fallback cloud model, the threshold, the unpacking limits, image description) are not part of the common schema. They live in `inbox.json` in the archive
directory, and they are changed by `flyarchive inbox set` and by the Intake section in DSH. Each setting lives in one place: a value saved from DSH will not be overridden by
the environment or by `settings.json`. The sources table (`sources.json`, optional) lies in the same place: it names the corpus roots and the index bases. Samples of both
files are `settings.example.json` and `sources.example.json`.

The settings file and the sources table are read only if they belong to the owner and are not writable by anyone else. The token store and the `secrets` directory are protected
by permissions 700 and 600. There are no secrets in the settings, and setting values do not get into error messages.

## Installation

`flyarchive install` (`tools/install.py`) writes everything from the templates in `tools/templates` according to the settings and keeps nothing "in its head". The templates
contain no addresses, no node names and no paths of someone else's machine, and a substitution with an unknown name is an error, not a blank. Step by step:

1. **Archive.** The same code as `flyarchive init`: directories with the owner's permissions, the service token, an empty known-files base, an empty index table with a
   full-text index. What already exists is not recreated.
2. **Services and timer.** `flyarchive-search`, `flyarchive-office`, `flyarchive-mcp` (one template for a Python service), `flyarchive-gateway` (only if `gateway_bind` and
   `public_url` are set), `flyarchive-dsh` (only if `dsh` is on the search path and `--no-dsh` is not given) and the pair `flyarchive-inbox.service` with
   `flyarchive-inbox.timer`. The same code and the same templates rewrite the intake pass service and timer on `flyarchive inbox set` (with the option `--no-timer` the command
   only saves the settings). The path to the code, the interpreter and the archive directory go into the service file. So a path with a space or with one of the characters
   `%` `"` `'` `\` `$` stops the installation before anything is written.
3. **Plugin.** The folder `dsh-plugin` (`package.json` and `lib`) is put into `<dsh_profile>/node_modules/flyarchive-dsh-plugin`. The previous folder is replaced as a whole,
   and the neighboring profile directories are not touched. If there is no profile, the step is skipped.
4. **The shell configuration file** `<archive directory>/dsh.patch.yml`, made from `tools/templates/dsh.patch.yml`: the connection of the archive's MCP server and of the
   plugin. The service token and keys are not written into it: the shell launcher reads them at startup.
5. **The command on the search path.** The command launcher `<archive directory>/bin/flyarchive` (sh, permissions 0700: `exec` of the command file with the interpreter the installation was run with)
   and a link `<bin_dir>/flyarchive` to it; someone else's file with the same name is not touched. The command is called through the launcher by
   the plugin (the line `command` of the file `dsh.patch.yml`) and by `tools/dsh-web-start`: the first line of the command file is `#!/usr/bin/env python3`, while the libraries are installed in the
   installation's Python.
6. **Start.** `systemctl --user daemon-reload`, `enable --now` for the services in the order of their dependencies, `enable --now` and `restart` for the timer.

The gateway is installed only when both keys, `gateway_bind` and `public_url`, are set; the installation does not overwrite values that are already set. With `--gateway auto`
the address and the node name are taken from tailscale (`tailscale ip -4`, `tailscale status --json`), the address must be from the private network range (100.64/10), and only
the missing keys are appended to `settings.json`. `--dry-run` touches nothing and prints every file (the launcher too) and every systemctl command. If there is no `systemctl` or no profile
directory, the step is skipped rather than failing. Running the installation again is safe.

## Access

Every request carries a token. Only the hashes of tokens are stored. The levels are `read`, `full`, `local`.

| Who | How they get in | What they can do |
|---|---|---|
| DSH on the archive machine | the service token of level `local` from a closed file | everything that is in MCP |
| the shell of an external model on the archive machine | its own token; started in the sandbox with `flyarchive run` | by the token level; it does not see the archive files |
| another machine of the private network | its own token, only through the gateway and only MCP | by the token level |
| a browser, by a link from an answer | a signature in the link, lifetime `link_ttl_s` (one day by default) | open one document |
| the owner | the `flyarchive` command, the Archive section in the DSH settings and the Archive tab | tokens, review queue, quarantine, deletion, parameters, file preview |

Deletion, decisions on the review queue and quarantine, and the issuing of tokens are not exposed over MCP at all. Every access is written to the access journal `logs/access.jsonl`
under the client's name; there are no token values in it.

The access journal names a revoked token as well. The token check (`tools/tokens.py`) can answer not only "does it fit" but also "whose invalid token is this and why". The hash is
compared with all the rows of the store in constant time, and nothing is written on the failure path, including "last access". The common login code
(`tools/auth.py`, one for search, documents and MCP) answers the client with the same 401 as before and a generic text «токен неверен, отозван или просрочен» (the token is
wrong, revoked or expired). The state of the token is not revealed to the outside. In the journal it writes the name and the level of the token and the outcome
«отказ: токен отозван <время>» (refused: token revoked <time>) or «токен просрочен <время>» (token expired <time>). When both conditions hold, «отозван» (revoked) is written.
A value that has no row in the store is written as the client "-". A successful tool list is deliberately not written to the journal: the assistant client reconnects every
half hour, and it would be noise. Tool calls are written by the service that runs them, under the client's name, and the MCP adapter writes only the connection.

The MCP tools are annotated according to the specification (`annotations` in `tools/mcp_server.py`): the three that read are marked "read only", the five that create are marked
"not destructive", and all of them are marked "closed world". Clients that ask a person about tools without annotations pass the reading tools through on their own. The internal
fields `route` and `need` are not sent out. That an annotation matches the token level is guarded by `tests/test_mcp_annotations.py`.

The sandbox (`tools/sandbox.py`, bwrap): the system is read-only, the working directory is writable, the home directory is separate, and the network is shared. Inside it there
is no archive, no owner's home directory, no Windows drives, no docker socket and no foreign processes. The sandbox protects files, not the network; the network is closed by
tokens.

## Adding documents: the inbox folder

```
inbox folder ──► waits 30 s with no changes
        │
        ▼
   intake (tools/gate.py, intake.py)
     type by content · unpacking archive files with limits · rules and scores
     check against the archive: an email by Message-ID, a file by sha256
     model check, if it is enabled: the local model, and if it is silent for llm_timeout_s, the fallback model, if one is configured
        │
        ├─ accepted ──────► corpus/входящие/<batch>/ ──► index, base «входящие» (inbox)
        ├─ findings ──────► очередь/<batch>/                  review queue: waits for the owner's decision
        ├─ program ───────► карантин/<batch>/                 quarantine
        ├─ not content ───► removed: an empty file, a system trace (in the receipt: skipped)
        └─ not taken ─────► <inbox folder>-возврат/<batch>/   return folder
        ▼
   sha256 check of the copy against the original file ──► original file removed ──► квитанции/<batch>.jsonl + <batch>.meta.json (receipts)
        ▼
   pending indexing entries (again) ──► description of images and scans, stage vision ──► index
```

While a pass runs, the stage and the count are written to `inbox-progress.json`: the tab reads it.

The default inbox folder is `<archive directory>/входящие` (here "входящие" means inbox). The return folder lies next to it and has the same name with the suffix `-возврат`
("return"). A configured folder must lie outside the archive and must not contain it.

**What is removed and what is returned.** An original file is removed from the inbox folder only when its exact copy is saved (in the corpus, the review queue or quarantine, or it was already in
the archive as an exact repeat) or when it is not content: an empty file and a system trace of an operating system (the directory `__MACOSX` and everything in it, `.DS_Store`, `Thumbs.db`,
`desktop.ini`, `._<name>` with the AppleDouble signature or inside `__MACOSX`). The list of traces is set in the code (`intake.py`, the function `trace_reason`), nothing from outside changes it,
and it looks at the file name (for `._<name>`, also at the signature at the start of the file); a program under such a name still goes to quarantine. Such a file gets the decision «пропущен» (skipped) with a reason in the receipt (in the intake report it carries
the mark `skipped_as` with the value `trace`), does not prevent removing the folder or archive file where it lay, and does not go to the return folder. Everything else that the archive did not
take is returned, and an archive file with such a member is returned whole. Service files matched by name patterns (`service_names` — in any folder, `service_root_names` — in the root of the
inbox folder or of an archive file; empty by default, so a text file with any name is a document) and the description of an email (`.json` next to an email of the same name) are marked by
intake with `skipped_as` with the value `service`, and the fate of a marked one is decided by the parameter `service_fate` from `inbox.json`: `return` (the default) — return, `delete` — the file
is removed, like a trace. This is decided by the pass over the inbox folder (`inbox.py`): intake itself does not change the source.

At intake the model only adds findings: it cannot take away the score given by the rules, and it does not send anything to quarantine by itself. For the model, a document is
data between random markers. Only the local model checks images and scans. If there is no model or it is unavailable, the check gives no verdict, and the document gets the
finding «модель не проверила: …» (the model did not check: …) of level HIGH. Such a document waits for the owner's decision at any threshold. An owner who has no model turns
the check off with `flyarchive inbox set --llm off`.

The review queue, quarantine, receipts and `удалённое/` (deleted) lie in the archive directory but outside the corpus: they do not get into the index.

**A batch and its history.** A pass in which there were files gets the number `YYYYMMDD-HHMMSS`. A second batch in the same second gets `-2` added, and so on. Receipts
`квитанции/<batch>.jsonl` ("квитанции" means receipts) hold one record per intake file. After them the rows with the owner's decisions are appended (the field `by`: accept,
quarantine, return, erase). Next to each receipts file lies `<batch>.meta.json` (permissions 0600): the start and end times, the duration in seconds, and the remarks of the pass
as messages with codes. Older readers of receipts pick only `.jsonl` files and do not see the remarks file. The history (`tools/batches.py`)
is assembled from these files: a list of batches from the newest to the oldest, with a count by intake decisions (the owner's rows are not counted), and the details of a batch:
one record per file, plus the owner's latest decision and `location`, where the file lies now. Three locations (`corpus`, `queue`, `quarantine`) are checked by whether the file is
present there. `returned` and `deleted` follow from the records: the file was returned by intake or by the owner; the owner erased it from quarantine, or removed the document from
the archive, and it is found in `удалённое/`. `missing` means that by the receipt the file should lie in one of the three locations, but it is not there; `null` means that there
was nothing to keep. The batch number is checked against its form before a file name is built from it; a receipt row that cannot be parsed is skipped. A receipt is written once,
before the image description, and does not change afterwards: only the owner appends to it. The live state of the description is taken when a batch is shown, by sha256, from
the description file and the pending entries.

**Intake progress.** While a pass holds the lock `inbox.lock` (only one runs at a time), it writes `inbox-progress.json`: the stage, how many are done out of how many, the last
finished file, the batch number, `pid`. The write goes through a temporary file and `os.replace`, with permissions 0600, at most four times a second. A stage change and the
final record of a stage are always written. While a single document is being indexed or described, a separate thread rewrites the record if it has not been touched for 20 seconds,
so that a live pass does not look abandoned. The stages are `intake` (unpacking and rules), `model` (the model check), `settle` (placing files and checking copies), `index`
(indexing), `sources` (cleaning up the original files) and `vision` (image description); the total number of files may be unknown at `intake`. The status
(`flyarchive inbox status`) returns the record as `progress`, or `null` if there is no file, no process with such a `pid`, or the record is older than ten minutes. A file left
by a killed pass belongs to no one: the next pass removes it while holding the lock. A failure to write the progress does not stop the intake pass.

**Pending entries.** Two files of pending entries share one lock `index/pending.lock`. The first is `index/pending.jsonl`: the document lies in the corpus but is not in the index
(indexing failed, or the acceptance ran without waiting). The second is `index/vision-pending.jsonl`: an image or a scan without text is waiting to be described. Reading,
changing and writing (through a temporary file) are one action under the lock, so the owner's acceptance and a pass can run at the same time, and entries are neither lost nor
duplicated. A pass works off the pending indexing entries at the start and once more at the end: an entry written while the pass was running is indexed by the same pass. The
pending description entries are worked off once at the end of a pass and by the command `flyarchive vision run`. The step itself holds its own lock `index/vision.lock`, so that a
pass and the command do not collide, and a busy step is skipped rather than waited for.

**Batch decisions and acceptance without waiting.** `queue accept`, `queue quarantine` and `quarantine return` take up to 200 paths and answer with one record per path; a bad
path does not stop the others (`review.decide_many`). `queue accept --defer-index` moves the file into the corpus and the known-files base, writes a pending indexing entry and
starts the intake pass service (`systemctl --user start --no-block`), but does not wait for indexing: this is how the tab accepts. The inbox folder can be changed with the
command `inbox set --path … --must-exist`: the directory must exist, lie outside the archive and not contain it. The change is written to the access journal.

### How the archive is filled

The open part of the project has only one way to add documents: the inbox folder. A file, a folder or an archive file put into it goes through intake and, if accepted, lands in
the corpus and in the index. A document passed over MCP (`submit_document`) follows the same path. There is no bulk initial load in the open part: exports of mail, pages and file folders, and their
processing, are the owner's work outside it. If such exports are already in `corpus`, they are indexed by the top-up indexer `tools/index_more.py`. It walks the roots named in the
sources table (`sources.json`) and the directory `входящие` (inbox), cuts the text into fragments of 4,000 characters (with an overlap of 200; the first 400,000 characters are
taken from a file), computes the embeddings and writes them to the table. It can be resumed — the files already passed are recorded in `index/ingested.txt` — and it does not
enter directories named `_карантин` (_quarantine). What was accepted through the inbox folder is included in the full walk, so rebuilding the index does not lose it.

Text extraction: `PyMuPDF` for pdf, `python-docx`, `openpyxl`, `python-pptx`, `extract-msg` for .msg emails, the standard library for .eml, markup parsing for `bpmn`, `svg`,
`drawio`, `xml`, `html`; plain text formats are read as they are. Images and scans without a text layer give the indexer no text: the local model describes them (see "Image
description"). The path from the index to a corpus file is parsed by a single module, `corpus_path.py`: both the search server and the documents server use it.

## Preview

The Archive tab shows the contents of a file from the review queue, quarantine or the corpus, and such a file is unchecked and may be malicious. There is one rule: the bytes
of such a file are read only by a process in a sandbox without a network or by a container without a network, and only PNG and plain text go to the browser.

```
  tab ── POST (body: scope, path, attachments, page) ───────────► plugin: request check, execFile, 300 s limit
                                                                  │
                                                                  ▼
                                      flyarchive preview show | page      parent process (tools/preview.py)
                                      reference checked by names · sha256 as a stream · cache · locks
                              ┌───────────────────────┴───────────────────────┐
                              ▼                                               ▼
                 worker process in bwrap, no network          docker container, no network (Office, HTML, metafiles)
                 PDF, images, text, emails, archive files     soffice → PDF; then the worker process draws it
                              └───────────────────────┬───────────────────────┘
                                                      ▼
                         checking the answer as data (regular file, size, PNG, schema) → cache by sha256
                                                      ▼
                         to the browser: PNG (image/png, nosniff, sandbox) and plain text
```

A reference to a file is a scope (`queue`, `quarantine`, `corpus`), a path and up to two attachment numbers. The parent process (`preview.py`) checks it by names: the path has no
`..`, is not absolute, contains no symbolic links and lies inside the named scope (the same parsing as for the owner's decisions and for `corpus_path`). Only after that is the file
opened, and the parent reads it as a stream to compute the sha256. The type and the contents are determined by the worker process (`preview_worker.py`). It goes into `bwrap`
with `--unshare-all` and no network, and it sees its input file, the code directory and the Python libraries (all read-only) and an empty output directory. The interpreter is the real one, with which the parent process was started (for a virtual environment, its base interpreter):
if it lies outside the system directories, the directory of its installation is bound read-only, and if it cannot be given safely, the preview answers with the refusal
`preview.python_outside`. The sandbox may be forbidden by the system itself, so `flyarchive doctor` tries a found `bwrap` with a short launch (`bwrap --unshare-all --ro-bind / / true`) and names the
reason for a refusal. The limits on
memory, CPU time and the size of what it writes are set inside the process before the libraries are imported. The worker process describes the file (kind, pages, text,
an email with its list of attachments, the contents of an archive file) and draws a page as a PNG. It only extracts an email attachment into the output directory, and the
attachment is opened by a new worker process under its own sha256.

The container is needed for Office files, HTML and metafiles. The parent process puts into its own directory a copy of the bytes from the already opened file (its sha256 is
checked) and runs `docker run` on a pinned image with a fixed set of options: `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, 256 processes, 2 GB of memory,
2 cores, the input read-only, a separate output directory, `--pull never` (no image means a refusal, not a download). The image is recorded by the command
`flyarchive preview setup` together with its digest (`preview.json` in the archive directory): the container is started by digest only. The time limit for the conversion is set
inside the container (`timeout -s KILL 120`), so the container ends even if the parent is killed. When the time is up, the parent also stops and removes the container by the name
`flyarchive-preview-<uuid>`. In addition, every `preview` command at startup removes, in a separate process, such containers older than five minutes. From the output directory the
parent accepts exactly one regular file that begins with `%PDF-` and puts it into the cache as `base.pdf`. From there it is an ordinary PDF, and the same worker process in `bwrap`
draws it.

What the worker process or the container returned is checked as data before it is written to the cache: a regular file (not a link), the size, the beginning and the end of the
PNG, the description against a schema with limits. If the check fails, the answer is `kind: "none"` with a message, no exception goes out, and foreign data does not get into the
cache. A `none` result is not written to the cache.

**Cache and locks.** The cache is `cache/preview/<sha256>/` (directories 0700, files 0600, written through a temporary file and `os.replace`): `desc.json`, `page-N.png`, and for
Office files also `base.pdf`. The key is the sha256 of the bytes that are drawn. An email attachment has its own key, and the same attachment from different emails is drawn once.
The locks (`flock`) are: one on the description of an object, one on each page, two slots for worker processes (the third waits) and a common render lock `.render.lock`, so that
only one conversion in a container runs at a time. A request that finds a lock busy does not wait but gets `kind: "rendering"`, and the tab asks again every 2 seconds for up to
three minutes. `preview page` never starts a conversion: if there is no ready `base.pdf`, it refuses. Two requests for the same page draw it once. The cache is cleaned by age
(7 days) and size (2 GB) with the command `flyarchive preview clean`, and by itself when the size is exceeded.

**What goes to the browser.** The server half of the plugin returns the description as it is, and a page as PNG bytes with `Content-Type: image/png`,
`X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`; a response that does not begin with the PNG signature is replaced by a 502 refusal. A page request is
sent in the body; the file path does not get into the URL. The email text, the attachment names, the rows of an archive file and the text of a document are output by
the browser half only as text: no markup from the contents gets onto the page. The preview types are: text, PDF, SVG, images, emails with attachments, the contents of archive
files, calendar and Office files, HTML, and metafiles through the container. BPMN, draw.io, video and audio are not implemented: for them the answer is `kind: "none"` with the
note «нужен преобразователь» (a converter is needed).

## Image description

Images and scans without a text layer are accepted, but they do not get into the index on their own: the indexer has no text for them. A local model with vision describes them,
and the description goes into the index.

```
pass:    intake ─► placement ─► receipts ─► pending indexing entries ─► description step (stage vision) ─► writing the progress and the batch remarks
              │
              └ an image or scan without text: the entry "waits for description" → index/vision-pending.jsonl

step:    entry ─► preview gives the page PNG (up to 4 MP) ─► local model, one page per request
              ─► intake rules on the text of the description ─► index/vision/<sha256>.json ─► fragments in the index under the path of the original document
```

The step's place in a pass is at the end, after the receipts and after the pending indexing entries are worked off for the second time. Therefore a receipt names only the reason for
waiting, and the live state (described, waiting, how many pages, which model, how many seconds) is returned by `inbox.vision_state` by sha256. A pass with no new files but with
pending description entries runs without a batch and starts straight from the `vision` stage. Everything that indexes can add pending description entries: a pass, the working off
of pending indexing entries, and acceptance from the review queue.

The image for the model is prepared by the preview (`preview.page`), so an unchecked file is still read only by the worker process in the sandbox. The step itself
(`tools/vision.py`) does not open or parse the file: it takes the number of pages from `preview.show`, and the image size from the PNG header that the preview returned. The
first 20 pages of a document are described; a page smaller than 120 pixels on a side is not described.

Only the local model is used. The request goes to the local endpoint from `llm_check` (the address, key and name come from the settings `llm_local_url`, `llm_key_file` and
`llm_local_model`). A fallback cloud path is not built for this step at all, even if one is configured. The request has no tools, the temperature is 0, reasoning is off, the
answer is up to 1,500 tokens, and the time limit is `llm_timeout_s` (180 seconds by default). Requests go one at a time, and before each one it is checked that the model is
ready: a server that reports a list of loaded models (`swap_running`) must name the needed model in it, and a server without such a list is considered ready. The request and the
description are in Russian. A server that rejects the extra request fields with code 400 gets the same request once more without them.

The description is data. Control characters are removed, the length is limited, the model's key is erased, and the text goes through `gate.scan_text`. A page with a HIGH or
CRITICAL finding does not go into the index or into the description file; only the page number and the name of the rule remain. An instruction written on an image gets into the
description as an inscription and changes nothing.

**The description file** is `index/vision/<sha256>.json` (0600, directory 0700, written through a temporary file and `os.replace`). The name is only the sha256: there is neither
a document name nor any text in the path. The file is filled up page by page: the failure of one page or a stop of the model does not lose what is already done, and the
description of the same content is not made a second time. It is separate from the index on purpose: the index can be rebuilt without the model. The description goes into the
index as fragments of the same document (the same `path`, base and date), and the description of each page begins with the label
«Описание изображения, сделанное моделью (страница N):» (Image description made by the model (page N):). Search finds such a fragment like any other and leads to the original
document.

**Pending entries under a lock.** The pending description entries lie in `index/vision-pending.jsonl` under the same lock `index/pending.lock` as the pending indexing entries,
but in a separate file: a pending description entry has its own budget, its own reason for waiting and its own failure counter. Only one description step at a time holds
`index/vision.lock`; a busy step is skipped, it does not wait. In one pass the step describes at most a set number of pages and runs for at most a set number of minutes
(60 and 10 by default); the rest stays pending. There are two kinds of failure. If the model does not answer, is not loaded, is busy, or the time limit expires, the whole step stops, the
pending entries stay intact, and there is one entry in the batch remarks. If a page did not work out (a bad answer, a refusal by the preview), that is a page failure, and the
other pages and documents go on. The preview's answer «ещё рисуется» (still rendering) is waiting, not a failure: the attempt counter grows only from real refusals, and a page
that has failed in three passes is skipped. A description that could not be indexed stays in the file: the retry goes without the model.

Old documents in the archive that have no text in the index are described only on command. `flyarchive vision backfill --limit N` writes them as pending entries (it does not
call the model), and `flyarchive vision run` or a pass describes them. `flyarchive doc delete` removes the description file and the document's pending entries, unless the same
content lies in the archive under a different path.

## Coded messages

Everything the system says to a person has a code and parameters; the Russian text stays as it was. The plugin screen is English, the terminal is Russian, and the bridge between
them is the code.

**The catalog** is `tools/messages.py`: a code maps to a Russian template and the parameter names (more than two hundred codes). The codes have the form `область.что_случилось`
("area.what_happened"; for example `inbox.folder_missing`, `preview.no_image`, `vision.model_busy`, `finding.secret`), and the parameter names are in Latin letters. A message is
built by the function `messages.make(код, **параметры)` ("код" is the code, "параметры" are the parameters) and behaves like the former plain string: comparison, substring
search and printing give the Russian text. Besides the text it carries `code` and `args`, and in JSON it goes out as `{"code", "args", "text"}`. An unknown code, an extra or a
missing parameter, or a value that is not a string, a number, a boolean or null is a build error: the tests catch it, not the owner. System words (the kind of program, the kind of
instruction injection, the kind of secret) do not live in parameters. The parameter holds a kind code, the Russian word comes from the `WORDS` table, and the plugin substitutes
the English one.

**Where it is used.** Command refusals (`InboxError`, `ReviewError`, `IntakeError`, `KnownError`, `BatchError`, `PreviewError` and others) inherit from `CodedError`. A command
with `--json` writes, on a refusal, `{"error": {"code", "args", "text"}}` as the last line of stderr and exits with code 1; without `--json` it prints «ошибка: текст» (error: text). The
exit code 2 means argument parsing; it has no message object. The remarks of a pass are messages from the catalog, and in `квитанции/<batch>.meta.json` each one has a code.
Intake findings have `msg` (what was found) and `where_msg` (where), receipt records have `reason_msg` and `notes_msg`; the model's finding is the only code without an area,
`llm_finding`. The fields `rule`, `level`, `where`, `quote`, `reason` did not change in meaning or in text. When `quote` is a quotation from the document, `args` holds
`quoted: true`: the plugin shows the quotation only then.

**The English dictionary.** `dsh-plugin/lib/client.messages.js`: an English template for every code, with the same parameter names, and the English words for the kinds. In a
template, `{имя}` is a parameter ("имя" is the name), and `{?…?}` is an optional part that is shown only when all its parameters are non-empty. A file name, a quotation from a
document and the model's words are inserted as they are. If the plugin receives an unknown code, it shows `text`, the server's Russian text: the screen works, just not in
English.

**Cross-check.** The dictionary and the catalog must not drift apart, and the tests enforce this. `dsh-plugin/test/client-messages.test.mjs` runs `python3 tools/messages.py --json`,
`--words` and `--rules` and compares the codes, the parameter names, the kind words and the list of rules with the dictionary; a mismatch names the code. `tests/test_messages.py`
checks the catalog from the other side: every reference to a code in `tools/` names an existing code, and every code of the catalog is used by something. The Russian texts of the
codes are checked against samples "as they were before the catalog".

## Index and search

LanceDB, the table `docs` in `<archive directory>/index/lance`. It is created by `flyarchive init` (the schema is in `tools/ingest.py`), and the rows are written by intake
(`ingest.index_documents`) and by the top-up indexer.

| Column | What |
|---|---|
| `path` | the path relative to the corpus, the key for reading a document |
| `source` | the base: for documents accepted through the inbox folder it is `входящие`; the other names are set by the sources table |
| `space` | the section of the base: a first-level directory, a mailbox or a batch number |
| `title` | the title: the subject of an email, a file name, a task key |
| `updated` | the date of the document, `YYYY-MM-DD` |
| `url` | a link to the original, if there was one |
| `chunk` | the number of the fragment inside the document |
| `text` | the text of the fragment itself, up to 4,000 characters |
| `vector` | the embedding from the model `embed_model` (`bge-m3` by default), of dimension `embed_dim` (1024 by default) |

There are two indexes: a full-text one on `text` (created by `init`) and a vector one, IVF-PQ. A fresh table has no vector index: the embedding search goes by brute force, which
is not noticeable on a small archive. Rows added after the indexes were built are searched the same way. The vector index is built by `flyarchive index optimize`
(`tools/index_optimize.py`: IVF-PQ with cosine distance, on a table of 20,000 rows or more, on the CPU; with the option `--compact` it also compacts the table). When the
table is rewritten with real dates, `flyarchive index dates --apply` is used (from 10,000 rows). Alongside lies the known-files base `index/known.sqlite`: the sha256 of every
corpus file, and the `Message-ID`, the fingerprint and the date of every email. Intake uses it to recognize what already lies in the archive.

### How search results are ranked

1. Two lists of candidates: a semantic one by embeddings and an exact one by BM25 (full-text).
2. Merging by reciprocal rank: `1.0/(60+ранг)` for embeddings, `0.8/(60+ранг)` for BM25 ("ранг" is the rank).
3. Multiplying by freshness: `0.55 + 0.45 × затухание` ("затухание" is the decay). The half-life is the setting `search_half_life_days` (540 days by default); for a document without a
   date the age is taken as ten years.
4. Collapsing repeats by the first 400 characters of the text.

A vector search over the index examines `search_nprobes` partitions (400 by default) and refines `search_refine` (30 by default) times more candidates against the original
vectors. More partitions mean a more accurate and slower search. The defaults were chosen by measuring on a large index, where small values lost a noticeable share of the true
nearest neighbors. The query embedding is computed on the CPU until the setting `embed_gpu` is turned on. The result filters are `since` (a year, a month or a day),
`source` (one base or several separated by commas) and `space`. The size of the answer for the model is limited by `api_snippet_chars`, `api_max_k` and `api_budget_chars`. The same
search is available from the terminal as `flyarchive search` (and `python3 tools/search.py`), on the search page and through `/api/search`: the shared code is `tools/search.py`.

The date of a document is the real one. For an email it comes from the header; for an attachment it is the date of its email, task or page. For a document accepted through the
inbox folder it is taken in this order: the file properties, the date in the name, the modification time, the day of intake.

## Archive directory

The archive directory is `~/flyarchive` or `FLYARCHIVE_HOME`; there is no code in it, only the command launcher `bin/flyarchive`. The names of the working directories (`входящие` (inbox), `очередь` (review queue),
`карантин` (quarantine), `квитанции` (receipts), `удалённое` (deleted)) are Russian.

```
<archive directory>/
  corpus/
    входящие/<batch>/   inbox: accepted through the inbox folder and over MCP
    <root>/             exports laid out by hand; the root names are in the sources table
    _карантин/          quarantine: directories with this name in the corpus are skipped by the walk, so they do not go into the index
  index/                lance/ — the index, known.sqlite — the known-files base, pending.jsonl — pending indexing entries,
                        vision-pending.jsonl — pending description entries, vision/<sha256>.json — image descriptions,
                        ingested.txt — files already passed by the top-up indexer
  входящие/             inbox: the default inbox folder (a configured one lies outside the archive)
  входящие-возврат/     return folder next to the inbox folder: what the archive did not take
  очередь/<batch>/      review queue: documents with findings, waiting for a decision
  карантин/<batch>/     quarantine: programs and unusable archive files
  квитанции/            receipts: <batch>.jsonl — intake records and the owner's decisions, <batch>.meta.json — duration and remarks
  удалённое/<day>/      deleted: documents removed from the archive
  cache/preview/        preview cache: derived, cleaned by age and size
  settings.json         the settings (optional file)
  sources.json          the sources table (optional file)
  inbox.json            the intake settings
  preview.json          the preview container image: name and digest
  protected-paths       directories that are not given to the sandbox (optional file)
  dsh.patch.yml         the shell configuration file: written by the installation
  bin/flyarchive        the command launcher (sh): written by the installation, the link in the directory of commands points to it
  inbox-state.json      what the previous pass saw in the inbox folder: by it, a file that has stopped changing is considered ready for the intake pass
  inbox-progress.json   intake progress: present only while a pass runs
  secrets/              token hashes, the link signing key, the service token
  logs/                 the access journal and the service logs
```

The directory is closed to the other users of the machine: 700 on directories, 600 on files (`flyarchive perms check`).

## Requirements and the tested configuration

Required: Linux (Ubuntu 24.04, including under WSL2; the code does not run on other systems), Python 3.10 or newer, the libraries `lancedb`, `pyarrow` and `pymupdf`, an embedding
service with the `/api/embed` interface and the model `bge-m3`. Everything else is optional: bubblewrap, `7z`, graphviz, docker, systemd, `dsh` with node, tailscale, a local model
with an OpenAI-compatible interface. Which parts work without which others is described in `docs/en/operations.md`, in the section "What you need and the environment check". You can check
the environment with the command `flyarchive doctor`.

Tested on: Ubuntu 24.04, Python 3.12, `lancedb` 0.38, `pyarrow` 25, `pymupdf` 1.28 (the lower bounds in `requirements.txt` match these versions), node 22 for the plugin; the plugin
is built against `@deepseek-ai/cordis` `~4.0.4`. It was checked by a test suite on stub services (embeddings, the local model, `systemctl`, docker) and by the end-to-end suite
`dsh-plugin/e2e/run.sh` with a real DSH and Chromium. How to run the tests is described in `docs/en/operations.md`. The Python test suite was also run on `lancedb` 0.40, the newest on the release day. What the suites do not prove: that FlyArchive works with
newer library versions than the ones named, with other local model servers (a stub server with an OpenAI-compatible interface, `tests/test_local_server.py`, stands in for them) or
on other distributions. It is worth checking these on your own installation (`flyarchive doctor`, then `flyarchive init` and one document through the inbox folder).
