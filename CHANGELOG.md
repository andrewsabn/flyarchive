# Changelog

Russian (original): [CHANGELOG.ru.md](CHANGELOG.ru.md)

## 0.1.0 — first public release

- **Archive.** An archive directory with a corpus and an index; search by meaning and by words from the command line, from a web page and over the network.
- **Access.** Tokens with the "read" and "full" levels, an access journal, a gateway into a private network, signed links to documents.
- **Intake.** An inbox folder, file type detection by content, security rules, a check against what is already accepted, a check by
  a local model, a review queue, quarantine, a return folder, a receipt for every file. Nothing is lost: what was not accepted is returned to the return folder, and only exact copies of what
  was saved, empty files and system traces (`__MACOSX`, `.DS_Store`) are removed; the fate of service files matched by name patterns is set by `service_fate`.
- **Models.** The MCP protocol and eight tools: search, reading documents, diagrams, charts, building files, handing a document over
  to the archive. The local model is any server with an OpenAI-compatible interface; the fallback cloud model takes its key from `FLYARCHIVE_LLM_CLOUD_KEY` or from a file. Shells of external
  models run in a sandbox. A PDF with Russian text is built with a font with Cyrillic (it can be set with the setting `pdf_font`).
- **Preview and description.** Document pages as images (the worker process runs with the real interpreter of the parent); description of images and scans by a local vision model.
- **Shell.** A plugin for DeepSeek Harness: the "Archive" tab and settings section.
- **Installation.** One command from templates; the installed command runs with the installation's interpreter through a launcher. An environment check that also tries the sandbox, dependency files,
  samples and the "first five minutes" walkthrough.
