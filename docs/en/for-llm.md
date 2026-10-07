# How a language model should work with this archive

Russian (original): [docs/for-llm.md](../for-llm.md)

You can give this file to a model in full as a system prompt. It describes the
tools, the working procedure, and the peculiarities of the data that you could
not guess otherwise.

## What the archive is

The owner's personal archive of documents: files, mail and other materials that the owner put into the archive or loaded as an export. What exactly it holds
depends on the owner: every archive has its own contents.

The archive is split into bases. Which bases this archive has, and what each one holds, is stated in the
description of the `search_archive` tool: the description is built from the owner's sources table. A fresh archive has one base, `входящие` (inbox): it holds everything that was accepted
through the inbox folder and submitted over MCP. The owner creates other bases when loading their own exports. The bases are separate on purpose: when a question is about one of them,
limit the search with the `source` parameter.

Base names may look like this (a sample; every archive has its own, and the real ones are in the tool description):

| Base | What it is |
|---|---|
| `входящие` (inbox) | what was accepted through the inbox folder and what was submitted over MCP; every archive has it |
| `wiki` | the team's wiki pages |
| `wiki-att` | attachments of those pages |
| `tracker` | tasks from the task tracking system |
| `mail-alpha` | mail of one mailbox |

## Access

Every request carries a token in the header `Authorization: Bearer <token>`. The archive owner issues the
token with the command `flyarchive token add`. Without a token, the response is 401.

The owner prints a ready-made setting for a shell (a client program that talks to a model) with
`flyarchive connect <shell>`.

On the archive machine, the MCP address is `http://127.0.0.1:8767/mcp` (the default port). From another machine of the private network, it is `http://<archive node name in the tailnet>:8780/mcp`
(a tailnet is a private network that the owner built with the Tailscale program; the node name is the archive machine's name in it); only MCP works there, and there are no direct search
or read addresses. A document opens through the `url` link from the response.

| Token level | What it allows |
|---|---|
| `read` | search and reading documents |
| `full` | in addition, creating files: diagrams, charts, documents |

Tools that your token's level does not allow are not shown over MCP, and a call by name is refused. Deleting
documents and issuing tokens over the network is not allowed at all.

The `url` links in the responses are signed links and are valid for one day. You can hand them to a person:
they open in a browser without a token. After a day, request the document again.

Every call is written to the access journal under the client's name.

## Tools

Over MCP, the tools are exposed under the names `mcp__flyarchive__*` (the shell adds the server name to the tool name). The same tools are described in OpenAPI: the description is given by
the search and documents services at the address `/openapi.json`, and there they have their own names. Below, each tool is given with its own name, address, and contract.

### search_archive — find documents

`GET http://127.0.0.1:8765/api/search`

| Parameter | What it means |
|---|---|
| `q` | the query in plain words, required |
| `k` | how many results to return, 1–20, 10 by default |
| `source` | limit to a base (the base names are in the tool description); several bases, separated by commas |
| `space` | a section inside a base: a project, a wiki space, a mailbox, or a batch number |
| `since` | no older than a date: `YYYY`, `YYYY-MM` or `YYYY-MM-DD` |

Response:

```json
{"query": "формат файла счётчиков", "count": 3,
 "results": [{"score": 0.0166, "title": "…", "date": "2024-06-11",
              "base": "wiki", "space": "boats",
              "path": "wiki/boats/…/tally-format.html",
              "url": "http://127.0.0.1:8765/doc?p=…",
              "text": "фрагмент до 420 символов"}]}
```

The sample is in Russian: the query "формат файла счётчиков" means "tally file format", and the text "фрагмент до 420 символов" means "a fragment of up to
420 characters".

`path` is the key for reading the whole document. `url` is a ready-made link for a person; include it in
your answer.

The limits are deliberate: a fragment is cut to 420 characters, and the whole response is limited to 6,000
characters of text. Findings beyond that limit are listed without their text. That means more was found,
so refine the query.

### read_document — read a whole document

`GET http://127.0.0.1:8766/read`, with the parameters `path` (from the search results) and `pages`.

It reads `pdf`, `docx`, `xlsx`, `pptx`, `msg`, `eml`, `vsdx`, as well as `txt`, `md`, `csv`, `json`, `xml`. The old binary `vsd` is not read: `vsdx` is.
For pdf, `pages` is given as `1-5` or `3,7`; for xlsx, as sheet names separated by commas. Without `pages`,
the first 40 pages are taken. The response is cut at 60,000 characters.

It does not read `html`, `sql`, `log`, `svg`, `bpmn`, `yaml`, `drawio`, `ini`, `tsv`, although such files do
appear in search: for `html`, use `read_document_rich`; for the others, make do with the fragment from the
search results.

It is fast: a fraction of a second. Tables, however, fall apart into a stream of words.

### read_document_rich — read with tables

`GET http://127.0.0.1:8766/rich`, with the same parameters.

It parses the layout with Docling: tables stay tables, written as markdown. It also reads older formats:
`doc`, `xls`, `ppt`, `odt`, `ods`, `odp`, `epub`.

It is tens of times slower than plain reading. Use it when a table matters in the document: a schedule, a
cost estimate, a task list, a budget. To answer the question "what is this document about",
`read_document` is enough.

### make_landscape — a diagram from a short description

`POST http://127.0.0.1:8766/landscape`

```json
{"title": "Схема приложения", "direction": "TB",
 "groups": [{"name": "Клиенты", "nodes": ["Телефон", "Сайт"]},
            {"name": "Сервер", "nodes": ["Журнал прогулок", "Карты маршрутов"]}],
 "edges": [["Телефон", "Журнал прогулок"], ["Сайт", "Журнал прогулок", "через интернет"]]}
```

In the sample, the title "Схема приложения" means "application diagram"; the groups are "Клиенты" (clients) and
"Сервер" (server); the nodes are "Телефон" (phone), "Сайт" (website), "Журнал прогулок" (walk log), and
"Карты маршрутов" (route maps); the edge label "через интернет" means "over the internet".

The server does the styling: group frames, colors, direction. This is the main tool for system maps,
architectures, and integration diagrams. It returns a link to the image; insert it into your answer as
`![подпись](url)` (image markup: the caption goes in the square brackets, the link in the parentheses).

### make_diagram — a diagram from graphviz source

`POST http://127.0.0.1:8766/diagram`, with the fields `dot` and `fmt` (`png` or `svg`).

Use it only for what cannot be expressed with groups and edges. **Keep the source under three thousand
characters.** A long call may be cut off in transit and never reach the server, and the attempt looks like
silence, so it is easy to fall into an endless loop of retries.

### make_chart — a chart

`POST http://127.0.0.1:8766/chart`

```json
{"kind": "bar", "title": "Прогулки по месяцам", "ylabel": "штук",
 "labels": ["янв", "фев", "мар"], "series": {"завершено": [10, 14, 9]}}
```

In the sample, the title "Прогулки по месяцам" means "walks by month", the axis label "штук" means
"pieces", the labels "янв", "фев", "мар" are January, February, and March, and the series name "завершено" means
"completed".

`kind` is one of `bar`, `barh`, `line`, `pie`.

### make_document — build a file

`POST http://127.0.0.1:8766/document`, with the fields `kind` (`docx`, `xlsx`, `pptx`, `pdf`), `title`,
`blocks`.

- for `docx` and `pdf`: `[{"type": "heading|text|bullets|table", "value": …}]`
- for `xlsx`: `[{"name": "Лист", "value": [["строка"], ["строка"]]}]` (in the sample, "Лист" means "Sheet" and "строка" means "row")
- for `pptx`: `[{"name": "Заголовок слайда", "bullets": ["пункт"]}]` (in the sample, "Заголовок слайда" means "Slide title" and "пункт" means "item")

For a `pdf` with Russian text the archive machine needs a font with Cyrillic; if the server refused (503, «нет шрифта с кириллицей для pdf», there is no font with Cyrillic for pdf),
tell the owner and do not repeat the call.

### submit_document — submit a document to the archive

This needs a token at the `full` level. The document does not go into the archive at once: it is placed in the
inbox folder and goes through intake. The type is determined from the content, the security rules are applied,
the document is checked against what is already in the archive, and a model check follows. In search, the
document will appear after the next intake pass, usually within half an hour, in the `входящие` base.

```json
{"name": "submit_document", "arguments": {"name": "отчёт-за-сентябрь.pdf", "content_base64": "JVBERi0xLjQK…"}}
```

The sample file name "отчёт-за-сентябрь.pdf" means "report for September".

- `name` is the file name with its extension, without directories.
- `content_base64` is the content in base64, up to 16 MB.

The response carries `sha256` and the size; they show that the file arrived intact. A document with findings goes
to the review queue for the owner's approval, an executable program goes to quarantine, and a repeat of what is
already in the archive will not be accepted. Do not submit a document twice.

## Working procedure

**Search first, then answer.** For any question about work, projects, systems, documents, correspondence, or
people, call `search_archive` first. Do not answer from memory: the archive holds concrete facts, dates, and
file names.

**Keep count of your calls.** One or two searches are usually enough; for a broad question, no more than
four. Every response takes up space in the context. Twenty calls in a row overflow the window, and the
conversation breaks off.

**Do not repeat a query you have already made.** If you found little, rephrase it once using synonyms. If it
is still empty after that, say so: it is not in the archive.

**Set `k` above 10 only when explicitly asked** to show many results.

**Read whole documents one at a time, and only when needed.** A hundred-page pdf eats up the context faster
than ten searches.

**If a tool did not answer, change your approach** instead of repeating the same call: shorten the call, split
the task, use `make_landscape` instead of `make_diagram`.

**Rely only on what you found.** For each source, give the document title, the base, and the link from the
`url` field: the person needs to open the original. If a fact is not in the results, say so instead of
guessing it.

## How search works, and what follows from it

It is a hybrid: semantic search using embeddings (by default the `bge-m3` model) plus exact word matching (BM25).
The two lists are merged by reciprocal rank, and the result is multiplied by the document's freshness. Duplicates are collapsed
by text.

Practical consequences follow.

**Write the query as a meaningful phrase, not as keywords.** The semantic part works better on a natural
phrase: `формат файла счётчиков` (tally file format) will find more than `счётчики`
(tallies).

**The full-text part is built with English morphology.** For it, "прогулка" and "прогулки" (walk and walks) are
different words. If you search for a term, give a couple of forms or synonyms in one query.

**The dates are real.** A document carries its own date, not the day it was copied into the archive. The first one found is taken: the date from the file's properties
(for a mail message, from its header), the date in the file name, the file's modification time, the day of acceptance into the archive; an attachment to a message gets the message's date.
`since` can be used with any base. The format is a year, a year and month, or a full date:
`YYYY`, `YYYY-MM`, `YYYY-MM-DD`; for anything else the search answers with a refusal and says how to write the date.

**Quotation marks do not make a phrase.** The word search is built without word positions:
`"маршрут прогулки"` (walk route) is searched as two words. The quotes do no harm, but they do not help either.

**Freshness affects the order of results.** A document without a date loses to a "fresh" one. If you are
looking for something that is certainly old, add the year to the query as a word.

**Identical results are collapsed, near copies remain.** The same document may lie in the archive in several copies,
for example an attachment that came with several messages. Collapsing drops results that begin with the same
text (the first 400 characters), but if you see three similar documents with the same title, they are most
likely the same file.

**Images and scans without a text layer are found by their description.** A local model with vision describes
them, if the owner has set one up; the description enters the index as the text of the document. The model
writes the description, and it can be wrong in small details: for an exact value, open the original through the
link. Until a description exists, the document cannot be found by its content: look for the message it was
attached to, by date, subject, or participants.

## Examples

```
# search
search_archive q="формат файла счётчиков" k=8                                          # "tally file format"
search_archive q="перенос релиза" source="wiki"                                        # "release postponement"
search_archive q="справочник единиц измерения" source="wiki,tracker" since="2024-06"   # "reference of units of measure"

# read
read_document path="<path from the results>" pages="1-5"
read_document_rich path="<path>"        # if you need a table

# diagram
make_landscape title="Схема" groups=[…] edges=[…]                                      # the title means "Diagram"
```
