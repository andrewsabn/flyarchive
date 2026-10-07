/*
 * Английский словарь сообщений системы для вкладки «Archive» (FR-80).
 *
 * Сообщение приходит от сервера как {code, args, text}: text — русский, code и args — для словаря. Этот файл
 * превращает известный код в английскую строку, а неизвестный оставляет текстом сервера. Здесь же названия того,
 * что известно уже сейчас: этапы хода разбора, решения приёмки, решения владельца, где лежит файл, уровни находок
 * и правила проверок.
 * TEMPLATES — по английскому шаблону на каждый код каталога сервера (tools/messages.py), с теми же именами параметров;
 * WORDS — английские слова системных видов (messages.py --words). Тест client-messages.test.mjs сверяет оба словаря
 * с каталогом сервера и называет код, который разошёлся.
 *
 * Язык шаблона: {имя} — параметр; {?…?} — необязательная часть, которая показывается, только когда все её параметры
 * не пусты (не null и не пустая строка). Так шаблон сам решает, как выглядит сообщение с пустым параметром,
 * и русский text на экран из-за пустого параметра не попадает.
 *
 * Подключается из client.js через require.async("./client.messages.js"): хост DSH отдаёт только client.js
 * и соседей вида client.<имя>.js, а синхронный require соседнего файла запрещён. Файл самодостаточен.
 */
window.__ModuleLoader__.load({
  id: "flyarchive-dsh-plugin",
  chunk: "client.messages.js",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    // этапы хода разбора, в порядке прохода (inbox-progress.json, поле stage)
    const STAGES = {
      intake: "Unpacking and rule checks",
      model: "Model check",
      settle: "Filing and de-duplication",
      index: "Indexing",
      sources: "Cleaning up sources",
      vision: "Describing images"
    };
    // решения приёмки в порядке показа в итоге пачки
    const DECISIONS = {
      accept: "accepted",
      review: "needs review",
      quarantine: "quarantined",
      duplicate: "duplicates",
      skip: "skipped",
      unpacked: "archives unpacked",
      failed: "failed"
    };
    // решение владельца о файле пачки (поле decided в ответе flyarchive inbox batch)
    const OWNER_DECISIONS = {
      accept: "accepted",
      quarantine: "quarantined",
      "return": "returned",
      "delete": "deleted"
    };
    // где файл пачки лежит сейчас (поле location там же)
    const LOCATIONS = {
      corpus: "In the archive",
      queue: "Awaiting review",
      quarantine: "In quarantine",
      returned: "In the returns folder",
      deleted: "Deleted",
      missing: "Missing"
    };
    const LEVELS = { CRITICAL: "Critical", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };
    // правила, которые выдают tools/gate.py, tools/llm_check.py и tools/intake.py
    const RULES = {
      executable: "Executable file",
      macros: "Macros",
      active_content: "Active content",
      hidden_text: "Hidden text",
      garbled: "Garbled text",
      prompt_injection: "Prompt injection",
      secret: "Secret or credential",
      encrypted: "Encrypted file",
      needs_vision: "Needs an image check",
      attachment: "Risky attachment",
      mismatch: "Type mismatch",
      unreadable: "Unreadable file",
      archive: "Archive problem",
      llm_unchecked: "Not checked by the model",
      llm_partial: "Partly checked by the model",
      llm_malicious: "Flagged as malicious by the model",
      llm_other: "Flagged by the model"
    };
    // код сообщения → английский шаблон; {имя} — параметр из args
    const TEMPLATES = {
      "generic.text": "{text}",

      // отказы входящей папки
      "inbox.busy": "An intake run is already in progress",
      "inbox.folder_missing": "The inbox folder does not exist: {path}",
      "inbox.bad_period": "The intake period must be one of: {periods} minutes",
      "inbox.bad_switch": "The llm and cloud settings accept only on or off",
      "inbox.bad_int": "{key} must be a whole number from {low} to {high}",
      "inbox.bad_number": "{key} must be a number from {low} to {high}",
      "inbox.folder_inside_archive": "The inbox folder cannot be inside the archive or contain it ({archive}): nothing reaches the archive before intake",
      "inbox.folder_too_wide": "The inbox folder cannot be the root or the home directory: {path}",

      // база известного
      "known.not_built": "The database for checking against the archive has not been built, so duplicates would reach the archive. Run: flyarchive known build (for a new archive: flyarchive init)",
      "known.mail_not_read": "Letters without check keys: {count}. The library {package} is missing, so repeats of these letters are not recognized. Install it and repeat: flyarchive known build",
      "known.db_missing": "The known-files database is missing: {path}. Build it with: flyarchive known build",

      // таблица индекса (FR-103): на чистом архиве её нет, заводит её только flyarchive init
      "index.no_table": "The index table does not exist: run flyarchive init",

      // замечания прохода
      "problem.index_still": "Index: {rel} is still not indexed ({why})",
      "problem.settle_aborted": "Filing was interrupted ({error}). What was done is recorded in the receipts; the rest stayed in the inbox folder",
      "problem.intake_failed_returned": "{name}: intake failed ({why}); the record was moved to the returns folder",
      "problem.intake_failed_kept": "{name}: intake failed ({why}) and the record could not be returned ({os_error}); it stayed in the inbox folder",
      "problem.meta_not_written": "The file with problems and duration was not written ({error}); the receipts were written",
      "problem.copy_mismatch": "{name}: the SHA-256 of the copy does not match the source; the source was left in the inbox folder",
      "problem.not_placed": "{name}: not filed ({error}); the source was left in the inbox folder",
      "problem.known_rejected": "The known-files database did not accept the batch ({error}); the accepted documents were left in the inbox folder",
      "problem.index_failed": "Index: {rel} is in the corpus but was not indexed ({why}); it will be retried",
      "problem.source_not_removed": "{name}: the source was not removed ({os_error}); it stayed in the inbox folder",
      "problem.pending_failed": "The indexing backlog was not handed over ({error}); the documents are in the corpus and will be indexed on the next run",
      "problem.index_no_table": "Index: {rel} is in the corpus, but the index table does not exist; the document stays in the indexing backlog. Run: flyarchive init",

      // описание картинок и сканов локальной моделью со зрением (FR-92): почему документ ещё не описан, отказы команды, замечания прохода
      "vision.waiting": "Waiting for a description: the document has no text, and the local vision model will describe it",
      "vision.off": "Waiting for a description: image description is turned off (turn it on with: flyarchive inbox set --vision on)",
      "vision.model_down": "The local model is not responding ({why}): documents wait for a description",
      "vision.model_timeout": "The local model did not answer within {seconds} s: documents wait for a description",
      "vision.model_not_loaded": "The local model is not loaded: documents wait for a description",
      "vision.model_busy": "The GPU is busy with another model ({loaded}): documents wait for a description",
      "vision.model_not_configured": "The local model is not set (the llm_local_model setting): documents wait for a description",
      "vision.bad_answer": "Page {page}: the model's answer was unusable (empty or not text)",
      "vision.failed": "Could not be described: {why}",
      "vision.blocked": "{rel}: the description of page {page} was kept out of the index: the rules found {rule}",
      "vision.changed": "{rel}: the file in the corpus no longer matches the recorded SHA-256, so no description was saved",
      "vision.limit_needed": "Old documents are described only on command and only N at a time: the --limit N option is required",
      "vision.bad_limit": "--limit must be a whole number from 1 to {max}",
      "problem.vision_failed": "Image description: {pages} pages in {docs} documents could not be described (last reason: {why}); it will be retried on the next runs",
      "problem.vision_aborted": "Image description was interrupted ({error}): documents wait for a description and it will be retried on the next run",

      // решения владельца
      "review.path_needed": "A path from the list is required",
      "review.bad_path": "The path must look like {area}/<batch>/<name>",
      "review.no_file": "No such file: {path}",
      "review.target_exists": "A file already exists at the destination: {path}",
      "review.no_receipt": "There is no intake receipt for {path}, so it cannot be accepted",
      "review.sha_mismatch": "The file's SHA-256 does not match the receipt: {path} was changed after the check",
      "review.return_exists": "A file with that name is already in the returns folder: {path}",
      "review.doc_path_needed": "A document path is required, as shown in the search results",
      "review.doc_path_outside": "The path must lead inside the corpus: {path}",
      "review.no_doc": "No such document in the corpus: {path}",
      "review.index_unavailable": "The index is unavailable ({error_type}); the document was not touched",
      "review.move_failed": "The rows were removed from the index, but the file could not be moved ({error_type}): {path}",
      "review.paths_none": "At least one path is required",
      "review.paths_too_many": "At most {max} paths at a time, but {count} were given",
      "review.path_repeated": "The path is named twice: {path}",

      // приёмка пачки
      "intake.no_source": "The source does not exist: {path}",
      "intake.into_corpus": "The intake directory cannot be placed inside the corpus ({corpus}): nothing reaches the index before a person decides",
      "intake.into_source": "The intake directory cannot be placed inside the source",
      "intake.into_not_empty": "The intake directory is not empty: {path}",

      // история пачек
      "batch.bad_id": "A batch ID must look like YYYYMMDD-HHMMSS, with -N at the end when needed: {value}",
      "batch.bad_limit": "--limit must be a whole number from 1 to {max}",
      "batch.no_batch": "No such batch: {batch}",

      // токены
      "token.store_open": "Permissions on {path} are wider than 600: the token store is open to others, so it is not used",
      "token.store_broken": "The token store {path} is corrupted: {error}",
      "token.bad_name": "A client name must be 1 to 64 letters, digits, or the characters . _ -",
      "token.bad_level": "The level must be one of: {levels}",
      "token.name_taken": "The name “{name}” is taken by an active token: revoke it or choose another",
      "token.no_active": "There is no active token named “{name}”",

      // вход
      "auth.secrets_dir_open": "Permissions on the directory {path} are wider than 700: the secrets are open to others",
      "auth.link_key_open": "Permissions on {path} are wider than 600: the link-signing key is open to others",
      "auth.link_key_broken": "The link-signing key {path} is corrupted",

      // сама команда flyarchive
      "cli.local_token_open": "Permissions on {path} are wider than 600: the service token is open to others",
      "cli.known_for_dedupe": "The known-files database is required to recognize repeats. Run: flyarchive known build",
      "cli.known_for_dates": "The known-files database is required: it holds the email dates. Run: flyarchive known build",
      "cli.confirm_needed": "Removing a document from the archive and the index must be confirmed with --yes",
      "cli.bad_switch": "--{name} accepts only on or off",
      "cli.bad_shell_name": "A shell name may contain letters, digits, hyphens, and underscores, up to 40 characters",
      "cli.kick_no_systemd": "The intake run was not started: systemd is unavailable ({error_type})",
      "cli.kick_failed": "The intake run was not started: {why}",
      "cli.kick_failed_silent": "The intake run was not started: systemctl returned an error",

      // flyarchive init (FR-103): что сделано по шагам и что осталось владельцу
      "init.dir_created": "Created the directory {path}",
      "init.dir_exists": "The directory already exists: {path}",
      "init.token_created": "The service token was issued and saved to {path}",
      "init.token_exists": "The service token is already in place: {path}",
      "init.known_created": "The database for checking against the archive was created: {path}",
      "init.known_exists": "The database for checking against the archive already exists: {path}",
      "init.table_created": "The index table docs was created: {path} (vector size {dim}); the full-text index on the text was built",
      "init.table_exists": "The index table docs already exists and was not touched: {path}",
      "init.todo_preview": "Set up the preview of Office and HTML files: flyarchive preview setup",
      "init.todo_model": "Set the local model for checking and describing: the llm_local_model setting (in settings.json or the FLYARCHIVE_LLM_LOCAL_MODEL environment variable), or turn the model check off: flyarchive inbox set --llm off (without a model, documents wait for a person's decision in the queue)",
      "init.todo_inbox": "Choose the inbox folder: flyarchive inbox set --path FOLDER",
      "init.step_failed": "The step \"{what}\" failed: {why}",

      // проверка окружения: flyarchive doctor (FR-107)
      "cli.not_linux": "FlyArchive runs on Linux; on Windows use WSL2",
      "doctor.title": "FlyArchive environment check: nothing is changed or created",
      "doctor.system_ok": "System: {system}",
      "doctor.python_ok": "Python {version}: fine ({need} or newer is required)",
      "doctor.python_old": "Python {version} is too old: {need} or newer is required",
      "doctor.lib_ok": "Library {package} {version}",
      "lib.missing": "The library {package} is missing: without it {use}. Install it: python3 -m pip install -r {file}",
      "lib.broken": "The library {package} is installed but does not load ({error}). Install it again: python3 -m pip install --force-reinstall -r {file}",
      // песочница и интерпретатор просмотра: проверка окружения и отказ просмотра
      "doctor.sandbox_blocked": "The program {name} is found but cannot create the sandbox ({why}): without it {use}. The usual cause is a ban on unprivileged user namespaces: on Ubuntu 24.04 look for the kernel parameter kernel.apparmor_restrict_unprivileged_userns and the AppArmor profile for bwrap; in a container the ban comes from the container itself",
      "doctor.preview_python_ok": "Preview: the worker process is started with the interpreter {python}",
      "preview.python_outside": "Preview works with a Python from the system directories: {python} lies outside them, and the directory it is installed in cannot be safely given to the sandbox. Install the libraries into an environment created by the system Python (python3 -m venv)",
      "doctor.tool_ok": "Program {name} found",
      "doctor.tool_missing": "The program {name} is missing: without it {use}",
      "doctor.embed_ok": "The vector service answers: {url}, model {model}, vector size {dim}",
      "embed.down": "The vector service does not answer: {url} ({why}). Start it and pull the model: ollama pull {model}; for another address use the embed_url setting",
      "embed.slow": "The vector service did not answer within {seconds} s: the first request may be loading the model — check again",
      "doctor.embed_waiting": "Waiting for the vector service, up to {seconds} s: the first request may be loading the model, the command has not hung",
      "doctor.embed_dim": "The vector service answers, but its vector size is {got} and the embed_dim setting is {want}: change the model (embed_model) or the setting while the index table is not created yet",
      "doctor.archive_ok": "The archive is set up: {path}",
      "doctor.archive_missing": "The archive is not set up: {path}. Set it up: flyarchive init",
      "doctor.table_ok": "The index table docs exists",
      "doctor.table_failed": "The index table cannot be opened ({error_type}): check the index directory",
      "doctor.model_ok": "The local model is set: {model}",
      "doctor.summary_ok": "The required parts are in place: FlyArchive can be started.",
      "doctor.summary_missing": "Required parts are missing: {names}. What to install is listed above; then check again: flyarchive doctor",
      "doctor.tail_ok": "Environment: the required parts are in place (details: flyarchive doctor)",
      "doctor.tail_missing": "Environment: required parts are missing: {names}. What to do: flyarchive doctor",
      // a font with Cyrillic for PDF files, and the file-name patterns of intake
      "doctor.font_ok": "A font with Cyrillic for PDF files: {path}",
      "doctor.font_missing": "There is no font with Cyrillic for PDF files: without it Russian text cannot be put into a PDF. Install the package {package} (Debian and Ubuntu: sudo apt install {package}) or point the pdf_font setting at a .ttf file",
      "inbox.bad_service_names": "{key} must be a list of file-name patterns, at most {max}, each a non-empty string up to {length} characters without \"/\" and \"\\\"",
      "inbox.bad_service_fate": "service_fate must be one of: {values}",
      "reason.system_trace": "A system trace left by an operating system, not content",

      // таймер входящих
      "inbox.timer_no_systemd": "systemd is unavailable ({error_type}): the timer was saved but not enabled",
      "inbox.timer_failed": "systemctl {command}: {why}",
      "inbox.timer_skipped": "The timer was left alone: no service files were written and systemctl was not called. Run intake by hand with flyarchive inbox run; flyarchive install or flyarchive inbox set without --no-timer schedules it",

      // обслуживание индекса flyarchive index optimize (FR-109)
      "optimize.busy": "An intake run is in progress: the table is not compacted while data is being written. Try again when it finishes (flyarchive inbox status)",
      "optimize.compact_warning": "Compaction removes old versions of the table: do not run it while data is being written (intake, accepting from the queue, describing images)",
      "optimize.before": "Before: {rows} rows, index folder {mb} MB",
      "optimize.after": "After: {rows} rows, index folder {mb} MB",
      "optimize.fts_built": "The full-text index on the text was rebuilt in {seconds} s",
      "optimize.ann_built": "The approximate vector index was built on the CPU in {seconds} s: {partitions} partitions, {sub_vectors} sub-vectors",
      "optimize.ann_built_gpu": "The approximate vector index was built on the GPU in {seconds} s: {partitions} partitions, {sub_vectors} sub-vectors",
      "optimize.ann_small": "The approximate index is not needed: the table has {rows} rows, and below {min} a full scan is faster than an index",
      "optimize.gpu_failed": "The GPU did not work out ({why}): the approximate index is built on the CPU",
      "optimize.kept": "Old versions of the table were left alone ({versions} versions); to compact the table and remove them run: flyarchive index optimize --compact",
      "optimize.compacted": "The table was compacted: fragments {fragments_before} before, {fragments_after} after; versions {versions_before} before, {versions_after} left",
      "optimize.fts_failed": "The full-text index was not built ({why}); the previous one, if there was one, is untouched; the remaining steps did not run",
      "optimize.ann_failed": "The approximate vector index was not built ({why}); the full-text index is built, compaction did not run",
      "optimize.compact_failed": "Compaction failed ({why}); the indexes are built",

      // находки правил и приёмки: что нашлось. Где у сообщения quoted: true, за ним на экране идёт цитата документа в кавычках
      "finding.hidden_tags": "Hidden text encoded in invisible tag characters",
      "finding.hidden_zero_width": "Invisible zero-width characters: {count}",
      "finding.hidden_bidi": "Characters that change the text direction",
      "finding.garbled": "Looks like an encoding error: {count} ideograph characters that actually read as “{decoded}”",
      "finding.prompt_injection": "{kind}",
      "finding.secret": "{kind}",
      "finding.hidden_html": "Text hidden by styling",
      "finding.hidden_word": "Hidden Word text",
      "finding.hidden_white": "White text",
      "finding.active_markup": "Markup with a script: the browser will run it when the document is viewed",
      "finding.active_pdf": "PDF with a script or a program launch: {marker}",
      "finding.pdf_embedded": "Another file is embedded in the PDF",
      "finding.pdf_encrypted": "The PDF is password-protected: its contents cannot be checked",
      "finding.needs_vision_pdf": "The PDF has no text layer: the model will check the contents from the image",
      "finding.needs_vision_image": "Image: the model will check the contents",
      "finding.attachment_executable": "An attachment is a program or a script: {names}",
      "finding.active_rtf": "The RTF contains an embedded object, a common way to deliver malicious code",
      "finding.executable": "{kind}",
      "finding.executable_renamed": "{kind}; the .{ext} extension does not match the content",
      "finding.mismatch": "The extension is .{ext}, but by content this is {detected}",
      "finding.macros": "The document contains macros",
      "finding.broken_document": "The document is damaged: the file is truncated or corrupted and cannot be opened",
      "finding.unreadable_unpacked_size": "Parts of the document unpack to more than {mb} MB",
      "finding.unreadable_no_main": "The document has no main part",
      "finding.unreadable_dtd": "The document declares a DTD, which Office formats never contain",
      "finding.unreadable_main_broken": "The main part of the document is damaged: {error}",
      "finding.unreadable_open": "The document cannot be opened: {error}",
      "finding.unreadable_pdf_open": "The PDF cannot be opened: {error_type}",
      "finding.unreadable_pdf_read": "The PDF cannot be read: {error_type}",
      "finding.unreadable_mail_parse": "The email cannot be parsed: {error_type}",
      "finding.unreadable_mail_read": "The email cannot be read: {error_type}",
      "finding.unreadable_crash": "Cannot be read: {error_type}",
      "finding.check_crashed": "The check failed: {error_type}: {error}",
      "finding.llm_unchecked": "The model did not check this: {reason}",
      "finding.llm_unchecked_failed": "The model did not check this: the check failed ({error_type})",
      "finding.llm_partial": "The model checked only the first {checked} of {total} pages",
      // находка модели: единственный код без области; слова модели бывают пустыми. Страницу показывает where_msg, здесь её нет
      llm_finding: "The model {model} reported{?: {why}?}",
      "llm.not_configured": "The local model is not set: set llm_local_model or turn the model check off (flyarchive inbox set --llm off)",

      // где нашлось
      "where.line": "Line {line}",
      "where.file": "Whole file",
      "where.file_name": "File name",
      "where.mail_attachments": "Email attachments",
      "where.byte": "Byte {offset}",
      "where.object": "Object {number}",
      "where.html_markup": "HTML markup",
      "where.pdf_attachment": "PDF attachment",
      "where.archive": "Whole archive",
      "where.part": "{part}",
      "where.recognized_line": "Recognized text: line {line}",
      "where.llm": "According to the model {model}",
      "where.llm_page": "According to the model {model}, page {page}",
      "where.llm_no_quote": "According to the model {model}; that quote is not in the text",
      "where.llm_page_no_quote": "According to the model {model}, page {page}; that quote is not in the text",

      // причины решения по файлу
      "reason.duplicate_exact": "The same file already exists: the content matches byte for byte",
      "reason.executable": "Not a document: a program or a script",
      "reason.empty_file": "Empty file",
      "reason.broken_file": "Damaged file",
      "reason.macos_sidecar": "A macOS system file stored next to the real file",
      "reason.iwork": "Apple iWork document: there is nothing to read it with",
      "reason.not_document": "Not a document: the type is not supported",
      "reason.archive": "Archive: its contents are being checked",
      "reason.archive_rejected": "The archive was rejected as a whole",
      "reason.archive_unpacked": "Files unpacked: {count}",
      "reason.not_a_file": "A link or not a regular file: it cannot be read",
      "reason.service_listing": "An export service file: a listing, not a document",
      "reason.service_readme": "An export service file: a description or a check report",
      "reason.service_mail_description": "A service file: a description of the email stored next to it",
      "reason.duplicate_in_archive": "Already in the archive: {path}",
      "reason.duplicate_in_batch": "Already in this batch: the same email or the same file",
      "reason.held": "Held for the owner's decision. Finding: {rule}",
      "reason.intake_failed": "Intake failed: {why}",

      // отказ распаковки
      "unpack.traversal_absolute": "Absolute path in the archive: {name}",
      "unpack.traversal_dots": "Path with “..” in the archive: {name}",
      "unpack.traversal_escape": "The path escaped the destination directory",
      "unpack.link_device": "The archive contains a link or a device file: {name}",
      "unpack.link": "The archive contains a link: {name}",
      "unpack.encrypted": "Password-protected archive: its contents cannot be checked",
      "unpack.encrypted_error": "Password-protected archive: {error}",
      "unpack.bomb_files": "More files than the limit of {limit}",
      "unpack.bomb_bytes": "The unpacked size is over the limit of {mb} MB",
      "unpack.bomb_ratio": "The archive is compressed more than {ratio} to 1: it looks like an archive bomb",
      "unpack.broken": "The archive is damaged: {error}",
      "unpack.broken_zip_toc": "The archive is damaged: the zip table of contents cannot be read",
      "unpack.broken_timeout": "Unpacking did not finish within the allowed time",
      "unpack.broken_format": "The archive is damaged or its format is not supported: {detail}",
      "unpack.broken_extract": "Unpacking failed: {detail}",
      "unpack.broken_os": "The archive could not be unpacked: {error_type}: {error}",
      "unpack.broken_crash": "Unpacking failed: {error_type}: {error}",
      "unpack.unsupported_no_7z": "The 7z program is missing: 7z and rar archives cannot be unpacked",
      "unpack.unsupported_not_archive": "This is not an archive: {family} {detected}",
      "unpack.dest_not_empty": "The destination directory is not empty: {path}",
      "unpack.depth": "Archives are nested more than {limit} levels deep: not unpacked",

      // примечания к архивам
      "note.archive_not_copied": "The source archive was not copied to quarantine",
      "note.executable_inside": "An executable file was inside: {name}",

      // просмотр файла: отказы команды
      "preview.bad_area": "The preview area must be one of: {areas}",
      "preview.bad_member": "--member must be a whole number from 0, with at most {max} keys",
      "preview.bad_page": "The page number must be a whole number from 1",
      "preview.no_page": "The file has no page {page}: {shown} pages are shown",
      "preview.no_pages": "This file has no pages: it is shown as text or as details only",
      "preview.still_rendering": "The page is still being drawn by another request: try again later",
      // просмотр: пояснения, почему содержимое не показано
      "preview.no_sandbox": "Preview is unavailable: the bwrap sandbox is missing, and an unchecked file is never opened without it",
      "preview.worker_failed": "The preview worker failed ({why})",
      "preview.timeout": "The preview worker did not finish within {seconds} s and was stopped",
      "preview.bad_answer": "The preview worker's answer was rejected ({what})",
      "preview.memory": "The file needed more memory than a preview may use",
      "preview.image_too_large": "The image is too large: {megapixels} MP against a limit of {limit} MP",
      "preview.encrypted": "The file is password-protected: its contents cannot be shown",
      "preview.broken": "The file cannot be opened: {error_type}",
      "preview.empty": "The file is empty: there is nothing to show",
      "preview.program": "A program or a script: its contents are not shown",
      "preview.unsupported": "Files of this type cannot be previewed: {type}",
      "preview.needs_converter": "A converter is needed: files of this type ({type}) cannot be previewed yet",
      "preview.cache_failed": "The preview cache could not be written ({error_type})",
      // просмотр писем, архивов и календаря
      "preview.no_member": "There is no attachment {member}: the file has {count} attachments",
      "preview.needs_extractor": "The 7z program is needed to list this archive ({type}), and it is not available",
      "preview.too_big": "The file is larger than {limit} MB: a message or an attachment this large is not opened",
      "preview.no_events": "The calendar has no events",
      // просмотр Office, HTML и метафайлов через контейнер: отказы преобразования и настройка образа
      "preview.convert_too_big": "The file is larger than {limit} MB: files this large are not converted for preview",
      "preview.no_docker": "Docker is not installed: Office, HTML and metafile previews need it",
      "preview.docker_silent": "Docker did not answer within {seconds} s: check that the Docker service is running",
      "preview.docker_failed": "Docker is not working ({why})",
      "preview.no_image": "No preview image is recorded yet: run flyarchive preview setup",
      "preview.image_missing": "The image {image} is not on this machine: run flyarchive preview setup",
      "preview.convert_timeout": "The conversion did not finish within {seconds} s and was stopped",
      "preview.convert_failed": "The conversion container failed ({why})",
      "preview.convert_memory": "The container ran out of memory ({gb} GB): the file is too complex to preview",
      "preview.convert_no_pdf": "The container left no PDF: the file could not be converted",
      "preview.convert_not_pdf": "What the container returned is not a PDF, so it was rejected",
      "preview.convert_not_a_file": "The container returned a link or something that is not a regular file, so it was rejected",
      "preview.convert_output_big": "The PDF from the container is larger than {limit} MB, so it was rejected",
      "preview.convert_several": "The container left more than one file, so the result was rejected",
      "preview.not_converted": "The file is not converted yet: pages come from the finished PDF, and only opening the file summary starts a conversion",
      "preview.input_changed": "The file changed during preview: the copy given to the container did not match the original sha256",
      "preview.setup_bad_image": "Not a valid image name: {image}",
      "preview.setup_pull_failed": "The image could not be downloaded ({why})",
      "preview.setup_no_image": "The image {image} is not on this machine: flyarchive preview setup without --image downloads it",
      "preview.setup_no_digest": "The image {image} has no repository digest (was it built on this machine?), so it cannot be pinned",

      // единый модуль настроек (FR-95): значение настройки в сообщение не попадает, только ключ, источник и что ожидалось
      "settings.unknown_key": "The settings file has an unknown setting: {key}",
      "settings.home_in_file": "The settings file {path} cannot contain the key home: the archive directory is the one the file lives in",
      "settings.file_open": "The settings file {path} belongs to another user or is writable by others, so it is not used",
      "settings.file_unreadable": "The settings file {path} cannot be read ({error_type})",
      "settings.bad_json": "The settings file {path} is not valid JSON (line {line}, column {column})",
      "settings.not_object": "The settings file {path} must contain a JSON object",
      "settings.bad_int": "{key} ({source}) must be a whole number from {low} to {high}",
      "settings.bad_number": "{key} ({source}) must be a number from {low} to {high}",
      "settings.bad_choice": "{key} ({source}) must be one of: {allowed}",
      "settings.bad_switch": "{key} ({source}) must be true or false (in an environment variable: 1, 0, true, false, on, off, yes or no)",
      "settings.bad_text": "{key} ({source}) must be a string without control characters",
      "settings.bad_list": "{key} ({source}) must be a list of strings without control characters (in an environment variable: items separated by commas)",
      "settings.bad_path": "{key} ({source}) must be an absolute path without control characters (a leading ~ and the archive directory placeholder are expanded before the check)",
      "settings.bad_url": "{key} ({source}) must be an http or https address with a host name, without a user name or password",
      "settings.bad_env_name": "{key} ({source}) must be an environment variable name: capital Latin letters, digits and underscores, not starting with a digit",

      // таблица источников (FR-99): значение из файла в сообщение не попадает, только место (корень, номер правила с единицы, ключ) и что ожидалось
      "sources.file_open": "The sources table {path} belongs to another user or is writable by others, so it is not used",
      "sources.file_unreadable": "The sources table {path} cannot be read ({error_type})",
      "sources.bad_json": "The sources table {path} is not valid JSON (line {line}, column {column})",
      "sources.duplicate_key": "A key is repeated in the sources table: {where}",
      "sources.unknown_key": "The sources table has an unknown key: {where}",
      "sources.missing_key": "{where}: the key is required",
      "sources.key_not_for_kind": "{where}: the key does not apply to a root of kind {kind}",
      "sources.not_object": "{where} must be a JSON object",
      "sources.not_list": "{where} must be a non-empty list",
      "sources.not_text": "{where} must be a non-empty string without control characters, at most {high} characters long",
      "sources.bad_kind": "{where}: the kind of a root is mail, pages or files",
      "sources.bad_name": "{where} must be a single directory name: no separators, not . or .., no control characters",
      "sources.reserved_root": "{where}: the inbox root is built in and cannot be set in the table",
      "sources.reserved_base": "{where}: this base belongs to the inbox intake and cannot be set in the table",
      "sources.bad_rule": "{where}: a rule has exactly one feature (contains or first_part_prefix) and a base (source)",
      "sources.rule_no_source": "{where}: the rule has no base (source)",
      "sources.not_lowercase": "{where}: the substring must be lowercase because the path is compared in lowercase",
      "sources.bad_prefix": "{where}: the prefix must be a non-empty string without separators or control characters",
      "sources.bad_title": "{where}: the title can only be folder",
      "sources.bad_rank": "{where}: the rank is a whole number from 0 to {high}",
      "sources.alias_target": "{where}: the alias points to a root that is not in the table",
      "sources.alias_clash": "{where}: the old name is the name of a root, so the alias would hide a real root"
    };
    // английские слова системных видов: код сообщения → параметр → код вида → слово. Значение параметра с видом при показе
    // заменяется словом; вид, которого здесь нет, показывается как пришёл. Перечень видов — messages.py --words
    const WORDS = {
      "finding.executable": {
        kind: {
          pe: "Windows program",
          elf: "Linux program",
          "macho-or-class": "macOS or Java program",
          lnk: "Windows shortcut",
          jar: "Java program",
          apk: "Android app",
          msi: "Windows installer",
          script: "Script",
          "by-extension": "Executable file",
          other: "Program"
        }
      },
      "finding.prompt_injection": {
        kind: {
          cancel_instructions: "Attempt to cancel earlier instructions",
          role_change: "Attempt to change the model's role",
          chat_markers: "Chat control markers",
          addresses_model: "Text addressed to the model",
          hide_instruction: "Request to hide an instruction",
          extract_prompt: "Attempt to extract the system prompt",
          exfiltrate: "Attempt to send data out"
        }
      },
      "finding.secret": {
        kind: {
          private_key: "Private key",
          aws_key: "AWS key",
          github_token: "GitHub token",
          api_key: "API key",
          slack_token: "Slack token",
          archive_token: "Archive token",
          password: "Password"
        }
      }
    };
    // что не работает без библиотеки или программы (проверка окружения): слова идут после «without it»
    WORDS["lib.missing"] = {
      use: {
        index: "the index table cannot be created and the archive cannot be searched",
        schema: "the index table cannot be created: its columns have no description",
        pdf: "pdf files are not checked on intake and their text does not reach the index",
        images: "images are not shown and scan pages go to the model without being scaled down",
        docx: "Word documents (.docx) do not reach the index and the document server cannot read them",
        xlsx: "Excel tables (.xlsx) do not reach the index and the document server cannot read them",
        pptx: "presentations (.pptx) do not reach the index and the document server cannot read them",
        msg: "Outlook letters (.msg) are not parsed on intake and do not reach the index",
        ole: "old Office documents (.doc, .xls, .ppt) and .msg letters are not recognized and are not accepted",
        docling: "there is no layout-aware document parsing and no on-machine scan recognition",
        charts: "the document server cannot draw charts",
        pdf_out: "the document server cannot create pdf files"
      }
    };
    WORDS["doctor.tool_missing"] = {
      use: {
        bwrap: "unchecked files cannot be previewed and external model shells cannot run in the sandbox",
        "7z": "7z and rar archives are neither unpacked nor listed (zip and tar work anyway)",
        dot: "the document server cannot draw diagrams",
        docker: "Office, HTML and metafile files are not shown in the preview",
        systemctl: "services and the intake timer cannot be installed: the archive works by hand, with flyarchive commands",
        dsh: "there is no DSH web shell with the Archive section; the archive works without it: the command, the search page, third-party shells",
        tailscale: "the gateway to the private network cannot be installed: the archive is reachable from this machine only"
      }
    };
    WORDS["doctor.sandbox_blocked"] = WORDS["doctor.tool_missing"];
    WORDS["finding.executable_renamed"] = WORDS["finding.executable"];
    // названия по имени параметра: параметр rule любого кода (сейчас reason.held) хранит код правила, на экране — его название
    const PARAM_WORDS = { rule: RULES };

    const own = (table, key) => typeof key === "string" && Object.prototype.hasOwnProperty.call(table, key);
    /** Название из словаря; чужое слово остаётся как есть, пустое — пустой строкой. */
    const named = (table) => (key) => (own(table, key) ? table[key] : key === null || key === undefined ? "" : String(key));
    const simple = (value) => ["string", "number", "boolean"].includes(typeof value);
    // за один проход: необязательная часть {?…?} или параметр {имя}; подставленные значения заново не разбираются
    const PIECE = /\{\?([\s\S]*?)\?\}|\{(\w+)\}/g;

    /**
     * Сообщение системы в английскую строку.
     * Неизвестный код, недостающий или негодный параметр — исходный text сервера: лучше русская строка, чем дыра в английской.
     * Пустой параметр (null или пустая строка) в необязательной части {?…?} — не порча: часть опускается.
     * Параметр null там, где шаблон его требует, — сообщение сломано, text.
     * @param message - {code, args, text}; строка тоже принимается и возвращается как есть.
     * @param templates - таблица шаблонов; нужна тестам, по умолчанию общая.
     * @param words - таблица слов видов; нужна тестам, по умолчанию общая.
     * @param byParam - названия по имени параметра (PARAM_WORDS): у кого бы ни стоял параметр с этим именем; нужна тестам, по умолчанию общая.
     */
    function format(message, templates, words, byParam) {
      if (typeof message === "string") return message;
      if (message === null || typeof message !== "object") return "";
      const text = typeof message.text === "string" ? message.text : "";
      const table = templates === undefined ? TEMPLATES : templates;
      if (!own(table, message.code)) return text;
      const args = message.args !== null && typeof message.args === "object" ? message.args : {};
      const wordTable = words === undefined ? WORDS : words;
      const kinds = own(wordTable, message.code) ? wordTable[message.code] : {};
      const common = byParam === undefined ? PARAM_WORDS : byParam;
      // слово вида у кода сильнее названия по имени параметра; чужое значение остаётся как пришло
      const shown = (name) => {
        if (own(kinds, name) && own(kinds[name], args[name])) return kinds[name][args[name]];
        if (own(common, name) && own(common[name], args[name])) return common[name][args[name]];
        return String(args[name]);
      };
      let complete = true;
      const fill = (template, optional) => {
        let empty = false;
        const line = template.replace(PIECE, (_whole, inner, name) => {
          if (inner !== undefined) return fill(inner, true);
          if (!own(args, name)) {
            complete = false;
            return "";
          }
          if (optional && (args[name] === null || args[name] === "")) {
            empty = true;
            return "";
          }
          if (!simple(args[name])) {
            complete = false;
            return "";
          }
          return shown(name);
        });
        return empty ? "" : line;
      };
      const line = fill(table[message.code], false);
      return complete ? line : text;
    }

    exports.format = format;
    exports.stageName = named(STAGES);
    exports.decisionName = named(DECISIONS);
    exports.ownerDecisionName = named(OWNER_DECISIONS);
    exports.locationName = named(LOCATIONS);
    exports.levelName = named(LEVELS);
    exports.ruleName = named(RULES);
    exports.STAGES = STAGES;
    exports.DECISIONS = DECISIONS;
    exports.OWNER_DECISIONS = OWNER_DECISIONS;
    exports.LOCATIONS = LOCATIONS;
    exports.LEVELS = LEVELS;
    exports.RULES = RULES;
    exports.TEMPLATES = TEMPLATES;
    exports.WORDS = WORDS;
    exports.PARAM_WORDS = PARAM_WORDS;
    return module.exports;
  }
});
