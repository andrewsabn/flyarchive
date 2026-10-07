/*
 * Раздел «Archive» в настройках DeepSeek Harness и вкладка «Archive» в правой панели — половина для браузера
 * (FR-41, FR-50, FR-52, FR-53, FR-54, FR-80, FR-81, FR-82, FR-83, FR-84, FR-85, FR-86, FR-94).
 *
 * Раздел настроек (то, что меняется редко): Status, Folders, Intake, Tokens, Delete document, Access journal. Состояние он берёт
 * из того же общего хранилища, что вкладка, и сам сервер не опрашивает. Счётчик файлов на решение — в заголовке вкладки, а когда
 * пачка кончилась с файлами на решение или замечаниями — всплывающее сообщение (Toast из примитивов DSH в собственном корне React).
 * Вкладка: строка состояния, живой ход разбора, решения по файлам по одному и для отмеченных, история пачек,
 * просмотр содержимого файла в раскрытой строке (страницы, текст, письмо с вложениями, список архива).
 * Весь экран английский:
 * подписи в словаре locale (namespace flyarchive), названия и сообщения системы — в соседнем client.messages.js.
 * Все данные приходят от серверной половины по адресам под api/ — туда пускает сам DSH по cookie сеанса.
 * Написано без сборки: обычный сценарий в формате загрузчика модулей DSH.
 */
window.__ModuleLoader__.load({
  id: "flyarchive-dsh-plugin",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;
    // модули основы платформы DSH (таблица PLATFORM_MODULES оболочки): отдельно объявлять их в package.json не нужно.
    // Нужны только всплывающему сообщению; нет любого из них — плагин работает без сообщения.
    const optional = (id) => {
      try {
        return require(id);
      } catch (_error) {
        return null;
      }
    };
    const platform = { primitives: optional("@deepseek-ai/dsh-client-ui-primitives"), dom: optional("react-dom/client") };
    const { useCallback, useEffect, useState, useSyncExternalStore } = React;

    // адреса относительные: DSH может быть открыт не из корня сайта
    const ROUTES = {
      tokens: "api/flyarchive.tokens", revoke: "api/flyarchive.tokens.revoke", journal: "api/flyarchive.journal",
      inbox: "api/flyarchive.inbox", run: "api/flyarchive.inbox.run", queue: "api/flyarchive.queue",
      quarantine: "api/flyarchive.quarantine", remove: "api/flyarchive.doc.delete",
      batches: "api/flyarchive.batches", batch: "api/flyarchive.batch",
      preview: "api/flyarchive.preview", previewPage: "api/flyarchive.preview.page"
    };
    const JOURNAL_ROWS = 100;
    const PERIODS = [1, 5, 10, 30, 60];
    // числовые поля Intake в порядке на экране: ключ → подпись словаря. Поле показывает набранный текст, число из него делается при сохранении
    const INTAKE_NUMBERS = {
      threshold: "intake.threshold", vision_pages: "intake.visionPages", vision_minutes: "intake.visionMinutes",
      max_gb: "intake.maxGb", max_files: "intake.maxFiles", max_ratio: "intake.maxRatio", depth: "intake.depth"
    };
    const VISION_NUMBERS = ["vision_pages", "vision_minutes"]; // доступны только при включённом описании изображений
    /** Число из набранного текста: пробелы по краям не в счёт, запятая как точка; пусто и не число — NaN. */
    const countOf = (typed) => (typeof typed === "string" && typed.trim() !== "" ? Number(typed.replace(",", ".")) : NaN);

    const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

    /**
     * Отказ сервера как сообщение {code, args, text}: прежний вид — строка, новый — объект с text.
     * Всё прочее (нет текста, не тот тип) отказом с сообщением не считается: null.
     */
    function refusalOf(value) {
      if (typeof value === "string" && value !== "") return { code: null, args: {}, text: value };
      if (isObject(value) && typeof value.text === "string" && value.text !== "") {
        return { code: typeof value.code === "string" ? value.code : null, args: isObject(value.args) ? value.args : {}, text: value.text };
      }
      return null;
    }

    /** Ошибка обращения: kind — offline (нет связи), auth (вход истёк), refused (сервер отказал сообщением), failed (прочее). */
    const failure = (kind, message, extra) => Object.assign(new Error(message), { kind }, extra);

    /** Обращения к серверной половине; отказ превращается в ошибку с её же текстом и видом (kind). */
    function createClient(doFetch) {
      const reach = async (route, init) => {
        try {
          return await doFetch(route, init);
        } catch (_error) {
          throw failure("offline", "No connection to DSH");
        }
      };
      const readJson = async (response) => {
        try {
          return await response.json();
        } catch (_error) {
          return null;
        }
      };
      /** Отказ сервера как ошибка: с его сообщением, «вход истёк» или общая. */
      const refusedBy = (response, data) => {
        const refusal = refusalOf(data && data.error);
        if (refusal) return failure("refused", refusal.text, { status: response.status, refusal });
        if (response.status === 401) return failure("auth", "Your DSH sign-in has expired: open DSH again from a fresh link", { status: 401 });
        return failure("failed", "The archive request failed, code " + response.status, { status: response.status });
      };
      const ask = async (route, init) => {
        const response = await reach(route, init);
        const data = await readJson(response);
        if (!response.ok) throw refusedBy(response, data);
        return data;
      };
      /** Ответ-картинка читается blob-ом, не JSON-ом; отказ у неё — обычный JSON с сообщением. */
      const askPicture = async (route, init) => {
        const response = await reach(route, init);
        if (!response.ok) throw refusedBy(response, await readJson(response));
        try {
          return await response.blob();
        } catch (_error) {
          throw failure("offline", "No connection to DSH");
        }
      };
      const postInit = (body) => ({
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body)
      });
      const post = (route, body) => ask(route, postInit(body));
      return {
        tokens: async () => (await ask(ROUTES.tokens)).tokens,
        issue: (name, level, days) => post(ROUTES.tokens, { name, level, days }),
        revoke: (name) => post(ROUTES.revoke, { name }),
        journal: async (client, n) => {
          const query = new URLSearchParams({ n: String(n) });
          if (client) query.set("client", client);
          return (await ask(ROUTES.journal + "?" + query)).records;
        },
        inbox: () => ask(ROUTES.inbox),
        setInbox: (patch) => post(ROUTES.inbox, patch),
        // смена входящей папки — отдельный запрос и только с подтверждением; ответ — новое состояние
        setInboxPath: (path) => post(ROUTES.inbox, { path, confirm: true }),
        runInbox: () => post(ROUTES.run, {}),
        queue: async () => (await ask(ROUTES.queue)).items,
        decideQueue: (path, action) => post(ROUTES.queue, { path, action }),
        quarantine: async () => (await ask(ROUTES.quarantine)).items,
        decideQuarantine: (path, action) => post(ROUTES.quarantine, { path, action }),
        // решение над списком путей (до 200 за запрос): ответ {"results": [...]} отдаётся как есть
        decide: (area, action, paths) => post(ROUTES[area], { paths, action }),
        deleteDoc: (path) => post(ROUTES.remove, { path, confirm: true }),
        // история пачек: страница от новой к старой (before — номер последней уже показанной) и подробности одной пачки
        batches: (limit, before) => {
          const query = new URLSearchParams({ limit: String(limit) });
          if (before) query.set("before", before);
          return ask(ROUTES.batches + "?" + query);
        },
        batch: (id) => ask(ROUTES.batch + "?" + new URLSearchParams({ id })),
        // просмотр: ссылка на файл — {area, path, member}; путь уходит в теле, а не в адресе. Страница — blob с PNG
        preview: (ref) => post(ROUTES.preview, { area: ref.area, path: ref.path, member: ref.member }),
        previewPage: (ref, page) => askPicture(ROUTES.previewPage, postInit({ area: ref.area, path: ref.path, member: ref.member, page }))
      };
    }

    /** Кого предлагать в отборе журнала: владельцев токенов и всех, кто встречается в записях. */
    function clientsOf(records, tokens) {
      const names = new Set();
      for (const token of tokens || []) names.add(token.name);
      for (const record of records || []) names.add(record.client);
      return [...names].sort();
    }

    /** Время записи в поясе браузера; пустое или негодное значение — словом. dateOnly — без часов. */
    function formatTime(ts, empty, dateOnly) {
      const date = ts ? new Date(ts) : null;
      if (!date || Number.isNaN(date.getTime())) return empty === undefined ? "—" : empty;
      const two = (n) => String(n).padStart(2, "0");
      const day = two(date.getDate()) + "." + two(date.getMonth() + 1) + "." + date.getFullYear();
      return dateOnly ? day : day + " " + two(date.getHours()) + ":" + two(date.getMinutes());
    }

    // дата письма: ГГГГ-ММ-ДД, затем необязательно время (T или пробел), секунды, доли секунды и пояс
    const MAIL_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d+)?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?)?$/;

    /**
     * Дата письма из строки ISO 8601 в том же виде, что время пачек (ДД.ММ.ГГГГ ЧЧ:ММ), но время суток — как в самой строке:
     * письмо показывает время отправителя, и его пояс (+03:00, нулевой — UTC) стоит рядом, а не теряется пересчётом в пояс браузера.
     * Нет пояса — нет и приписки. Негодная строка (в том числе несуществующий день или час) — как есть; пустое и не строка — «—».
     */
    function formatMailDate(value) {
      if (typeof value !== "string" || value === "") return "—";
      const found = MAIL_DATE.exec(value);
      if (found === null) return value;
      const [, year, month, day, hours, minutes, seconds, zone] = found;
      const probe = new Date(0);
      probe.setUTCFullYear(Number(year), Number(month) - 1, Number(day));
      if (probe.getUTCMonth() !== Number(month) - 1) return value; // нет такого месяца или дня: счёт перелился в соседний месяц
      const date = day + "." + month + "." + year;
      if (hours === undefined) return date;
      if (Number(hours) > 23 || Number(minutes) > 59 || (seconds !== undefined && Number(seconds) > 59)) return value;
      let offset = "";
      if (zone !== undefined && zone !== "Z") {
        const digits = zone.slice(1).replace(":", "");
        const [offsetHours, offsetMinutes] = [digits.slice(0, 2), digits.slice(2) || "00"];
        if (Number(offsetHours) > 23 || Number(offsetMinutes) > 59) return value;
        offset = Number(offsetHours) + Number(offsetMinutes) === 0 ? "UTC" : zone[0] + offsetHours + ":" + offsetMinutes;
      } else if (zone === "Z") offset = "UTC";
      return date + " " + hours + ":" + minutes + (offset === "" ? "" : " " + offset);
    }

    const CSS = [
      ".ba-section{display:flex;flex-direction:column;gap:12px;font-size:13px;line-height:20px}",
      ".ba-section h2{font-size:18px;line-height:26px;margin:0}",
      ".ba-section h3{font-size:14px;line-height:20px;margin:12px 0 0}",
      ".ba-note{color:var(--dsw-alias-label-secondary,#888);margin:0}",
      ".ba-wrap{overflow-x:auto}",
      ".ba-table{border-collapse:collapse;width:100%}",
      ".ba-table th,.ba-table td{text-align:left;padding:6px 8px 6px 0;border-bottom:.5px solid var(--dsw-alias-border-l2,#8884);vertical-align:top;white-space:nowrap}",
      ".ba-table th{color:var(--dsw-alias-label-secondary,#888);font-weight:400}",
      ".ba-table td.ba-wide{white-space:normal;word-break:break-word;min-width:180px}",
      ".ba-dim{color:var(--dsw-alias-label-secondary,#888)}",
      ".ba-bad{color:var(--dsw-alias-label-danger,#d33)}",
      ".ba-row{display:flex;flex-wrap:wrap;gap:8px;align-items:end}",
      ".ba-row label{display:flex;flex-direction:column;gap:2px;color:var(--dsw-alias-label-secondary,#888)}",
      ".ba-row label.ba-check{flex-direction:row;align-items:center;gap:6px;color:inherit}",
      ".ba-section input,.ba-section select,.ba-section button{font:inherit;padding:4px 8px;border-radius:6px;border:.5px solid var(--dsw-alias-border-l2,#8884);background:transparent;color:inherit}",
      ".ba-section input[type=checkbox]{padding:0}",
      ".ba-section button{cursor:pointer}",
      ".ba-section button:disabled{opacity:.5;cursor:default}",
      ".ba-box{border:.5px solid var(--dsw-alias-border-l2,#8884);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}",
      ".ba-token{font-family:ui-monospace,monospace;word-break:break-all;user-select:all}",
      ".ba-error{color:var(--dsw-alias-label-danger,#d33)}",
      ".ba-part{display:flex;flex-direction:column;gap:12px}",
      // вкладка правой панели
      ".ba-tab{padding:12px;box-sizing:border-box;height:100%;overflow:auto}",
      ".ba-tab-title{display:inline-flex;align-items:center;gap:6px}",
      ".ba-bar{height:6px;border-radius:3px;background:var(--dsw-alias-border-l2,#8884);overflow:hidden}",
      ".ba-bar-fill{height:100%;width:0;border-radius:3px;background:var(--dsw-alias-label-primary,currentColor)}",
      ".ba-bar-busy .ba-bar-fill{width:30%;animation:ba-slide 1.2s ease-in-out infinite alternate}",
      "@keyframes ba-slide{from{margin-left:0}to{margin-left:70%}}",
      ".ba-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}",
      ".ba-item{display:grid;grid-template-columns:auto 1fr;column-gap:8px;padding:8px 0;border-bottom:.5px solid var(--dsw-alias-border-l2,#8884)}",
      ".ba-item-main{display:flex;flex-direction:column;gap:2px;min-width:0}",
      ".ba-item-name{word-break:break-word}",
      ".ba-item-meta{display:flex;flex-wrap:wrap;gap:4px 10px}",
      ".ba-confirm{display:flex;flex-wrap:wrap;gap:8px;align-items:center}",
      // история пачек
      ".ba-batch{display:flex;flex-direction:column;gap:6px;padding:8px 0;border-bottom:.5px solid var(--dsw-alias-border-l2,#8884)}",
      ".ba-section button.ba-batch-head{display:flex;flex-wrap:wrap;gap:2px 10px;width:100%;text-align:left;border:0;padding:0;border-radius:0}",
      ".ba-batch-body{display:flex;flex-direction:column;gap:8px}",
      ".ba-section button.ba-link{border:0;padding:0;text-align:left;text-decoration:underline;text-underline-offset:2px;word-break:break-word}",
      ".ba-section button[aria-pressed=true]{font-weight:600}",
      ".ba-finding,.ba-field{display:flex;flex-wrap:wrap;gap:2px 8px}",
      // просмотр содержимого
      ".ba-expander{cursor:pointer;text-decoration:underline;text-underline-offset:2px}",
      ".ba-preview{display:flex;flex-direction:column;gap:8px;margin-top:8px;min-width:0}",
      ".ba-preview-body,.ba-preview-pages{display:flex;flex-direction:column;gap:8px;min-width:0}",
      ".ba-preview-meta{display:flex;flex-direction:column;gap:2px}",
      ".ba-preview-text{font-family:ui-monospace,monospace;white-space:pre-wrap;word-break:break-word;margin:0;max-height:480px;overflow:auto}",
      ".ba-preview-picture{overflow:auto;max-width:100%}",
      ".ba-preview-img{display:block}",
      // запись вкладки в путеводителе панели: тот же вид, что у встроенных карточек
      ".ba-guide{box-sizing:border-box;border:.5px solid var(--dsw-alias-border-l3);border-radius:var(--dsl-guide-entry-radius);background:var(--dsw-alias-bg-layer-1);align-items:center;gap:14px;width:100%;min-height:56px;padding:14px 20px;display:flex;position:relative;overflow:hidden}",
      ".ba-guide-main{position:absolute;inset:0;width:100%;height:100%;border:0;border-radius:0;background:transparent;cursor:pointer}",
      ".ba-guide-main:focus-visible{outline-offset:-2px}",
      ".ba-guide-icon{width:26px;height:26px;color:var(--dsw-alias-label-secondary);pointer-events:none;flex:none;justify-content:center;align-items:center;display:flex;position:relative}",
      ".ba-guide-text{pointer-events:none;flex-direction:column;flex:1;gap:3px;min-width:0;display:flex;position:relative}",
      ".ba-guide-title{color:var(--dsw-alias-label-primary);white-space:nowrap;text-overflow:ellipsis;font-size:14px;line-height:1.4;overflow:hidden}",
      ".ba-guide-desc{color:var(--dsw-alias-label-tertiary);white-space:nowrap;text-overflow:ellipsis;font-size:11px;line-height:1.4;overflow:hidden}"
    ].join("");
    function ensureStyles() {
      if (typeof document === "undefined" || !document.head || typeof document.createElement !== "function") return; // не браузер
      const tagId = "flyarchive-dsh-plugin/section.css";
      if (document.querySelector("style[data-plugin-css=" + JSON.stringify(tagId) + "]") !== null) return;
      const tag = document.createElement("style");
      tag.dataset.plugin = "flyarchive-dsh-plugin";
      tag.dataset.pluginCss = tagId;
      tag.textContent = CSS;
      document.head.appendChild(tag);
    }

    // ── вкладка «Archive» в правой панели (FR-80, FR-81) ────────────────────────
    const NAMESPACE = "flyarchive";
    const TAB_ID = "flyarchive-dsh-plugin"; // идентичность реализации в системе вкладок; под ним же тело, заголовок и запись путеводителя
    const TAB_KIND = "flyarchive-archive";
    const POLL_IDLE_MS = 30000; // страница видна, разбора нет
    const POLL_RUN_MS = 2000; // идёт разбор
    const AFTER_RUN_POLLS = 5; // после «Run now» ход появляется не сразу: столько опросов подряд остаются частыми
    const BATCH_SIZE = 200; // путей в одном запросе решения
    const BULK_ACTIONS = { queue: ["accept", "quarantine"], quarantine: ["return"] }; // массово стирать нельзя: только у строки
    const LEVEL_RANK = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };
    const HISTORY_PAGE = 30; // пачек на страницу истории
    const FILES_SHOWN = 200; // файлов в таблице пачки за раз
    const FILE_COLUMNS = ["file", "decision", "owner", "location", "score", "rule"];
    const PREVIEW_AREAS = ["queue", "quarantine", "corpus"]; // где файл можно смотреть: очередь, карантин, корпус
    const IMAGE_KINDS = ["pages", "image", "media"]; // виды просмотра с картинкой: страница документа, картинка, кадр
    const RENDER_RETRY_MS = 2000; // пока файл рисуется, описание переспрашивается так часто
    const RENDER_MAX_RETRIES = 90; // 90 повторов по 2 с — три минуты, дальше «Try again»
    const MAX_MEMBER_DEPTH = 2; // вложение письма и вложение во вложении; глубже вложения не раскрываются
    const ZOOMS = [["fit", "preview.zoom.fit"], ["100", "preview.zoom.100"], ["150", "preview.zoom.150"]];
    const ZOOM_FACTOR = { "100": 1, "150": 1.5 };

    /** Подписи вкладки. Экран английский; словарь zh повторяет en (DSH требует оба набора с одинаковыми ключами). */
    const EN = {
      title: "Archive",
      "guide.description": "Intake status and decisions on incoming files",
      loading: "Loading…",
      "status.waiting": "Waiting in the inbox folder: {n}",
      "status.timer": "Runs every {n} min",
      "status.noTimer": "No timer: runs only on request",
      "status.lastBatch": "Last batch {batch}: {summary}",
      "status.lastBatchEmpty": "Last batch {batch}: no files",
      "status.noBatches": "No batches yet",
      "status.problems": "Problems in the last batch: {n}",
      "status.vision": "Awaiting description: {n}",
      run: "Run now",
      "inbox.folder": "Inbox folder:",
      copy: "Copy path",
      copied: "Copied",
      "copy.failed": "Could not copy: select the path and copy it by hand.",
      "progress.label": "Intake progress",
      "progress.count": "{done} of {total}",
      "progress.countOnly": "{done} done",
      "progress.current": "Last file: {name}",
      "decision.heading": "Needs decision",
      "decision.title": "Needs decision ({n})",
      "decision.empty": "Nothing is waiting for a decision.",
      "origin.queue": "review",
      "origin.quarantine": "quarantine",
      "row.select": "Select {name}",
      "row.score": "Score {n}",
      "row.noFindings": "No findings",
      "row.failed": "This file could not be processed.",
      selectAll: "Select all",
      clearSelection: "Clear selection",
      acceptSelected: "Accept selected",
      quarantineSelected: "Quarantine selected",
      returnSelected: "Return selected",
      accept: "Accept",
      quarantine: "Quarantine",
      "return": "Return",
      "delete": "Delete",
      cancel: "Cancel",
      "confirm.accept.one": "Accept {n} file into the archive?",
      "confirm.accept.many": "Accept {n} files into the archive?",
      "confirm.quarantine.one": "Move {n} file to quarantine?",
      "confirm.quarantine.many": "Move {n} files to quarantine?",
      "confirm.return.one": "Return {n} file to the returns folder?",
      "confirm.return.many": "Return {n} files to the returns folder?",
      "confirm.delete": "Delete permanently?",
      "yes.accept": "Yes, accept {n}",
      "yes.quarantine": "Yes, quarantine {n}",
      "yes.return": "Yes, return {n}",
      "yes.delete": "Yes, delete forever",
      "error.offline": "No connection to DSH. The tab keeps trying.",
      "error.signedOut": "Your DSH sign-in has expired. Open DSH again from a fresh link.",
      "error.failed": "The archive request failed (code {status}).",
      "error.unknown": "Something went wrong while reading the archive.",
      "batches.heading": "Batches",
      "batches.empty": "No batches yet",
      "batches.more": "Load more",
      "batch.files.one": "{n} file",
      "batch.files.many": "{n} files",
      "batch.problems.one": "{n} problem",
      "batch.problems.many": "{n} problems",
      "batch.took": "Took {time}",
      "batch.problemsHeading": "Problems in this run",
      "batch.noFiles": "No files in this batch",
      "batch.filter.all": "All ({n})",
      "batch.filter.one": "{name} ({n})",
      "batch.col.file": "File",
      "batch.col.decision": "Decision",
      "batch.col.owner": "Owner decision",
      "batch.col.location": "Location",
      "batch.col.score": "Score",
      "batch.col.rule": "Main rule",
      "batch.notStored": "Not stored",
      "batch.vision.described": "Described by the model",
      "batch.vision.described.one": "Described by the model: {n} page",
      "batch.vision.described.many": "Described by the model: {n} pages",
      "batch.vision.truncated": "first {n} of {total}",
      "batch.vision.waiting": "Awaiting description",
      "batch.andMore": "… and {n} more",
      "batch.showMore": "Show more",
      "file.reason": "Reason:",
      "file.notes": "Notes:",
      "file.checkedBy": "Checked by:",
      "file.date": "Document date:",
      "file.size": "Size:",
      "file.sha": "SHA-256:",
      "file.path": "Path:",
      "file.returned": "Returned to:",
      "file.decidedAt": "Owner decision made:",
      "preview.rendering": "Rendering…",
      "preview.late": "The preview is still not ready.",
      "preview.retry": "Try again",
      "preview.none": "No preview is available for this file.",
      "preview.noPages": "There are no pages to show.",
      "preview.page": "Page {n} of {total}",
      "preview.previous": "Previous",
      "preview.next": "Next",
      "preview.zoom.fit": "Fit width",
      "preview.zoom.100": "100%",
      "preview.zoom.150": "150%",
      "preview.firstPages": "First {n} pages shown",
      "preview.pageAlt": "Page {n}",
      "preview.imageAlt": "File preview",
      "preview.duration": "Duration: {time}",
      "preview.frameSize": "Frame: {width} × {height} px",
      "preview.truncated": "The text is truncated: only the beginning is shown.",
      "preview.listTruncated": "The list is truncated: only the first {n} entries are shown.",
      "preview.col.name": "Name",
      "preview.col.type": "Type",
      "preview.col.size": "Size",
      "preview.meta.name": "Name:",
      "preview.meta.type": "Type:",
      "preview.meta.size": "Size:",
      "preview.meta.sha": "SHA-256:",
      "preview.meta.batch": "Batch:",
      "preview.meta.archive": "Archive:",
      "preview.meta.inner": "Path inside the archive:",
      "preview.mail.from": "From:",
      "preview.mail.to": "To:",
      "preview.mail.cc": "Cc:",
      "preview.mail.date": "Date:",
      "preview.mail.subject": "Subject:",
      "preview.mail.attachments": "Attachments",
      "preview.mail.depth": "Attachments at this level are only listed: they cannot be opened.",
      "preview.mail.back": "Back to message",
      "preview.mail.truncated": "The message is shown in part: the text or the attachment list is cut.",
      "preview.mail.program": "program",
      "preview.mail.inside": "Attachment of: {name}",
      "title.count": "Archive ({n})",
      refresh: "Refresh",
      save: "Save",
      "settings.intro": "Settings that rarely change: folders, intake rules, access tokens.",
      "settings.status": "Status",
      "settings.folders": "Folders",
      "settings.intake": "Intake",
      "settings.tokens": "Tokens",
      "settings.delete": "Delete document",
      "settings.journal": "Access journal",
      "settings.status.attention": "Awaiting a decision: {n}",
      "settings.status.vision": "Awaiting description: {n}",
      "settings.status.hint": "Decisions on incoming files are made on the Archive tab in the right panel of a conversation.",
      "settings.saved": "Intake settings saved.",
      "settings.deleted": "Document removed from the archive: {rows} index rows, file moved to {moved}.",
      "folders.inbox": "Inbox folder",
      "folders.change": "Change",
      "folders.warning": "The archive will take in everything that lies in the new folder. Check that it holds only files meant for the archive.",
      "folders.confirm": "Yes, change the inbox folder",
      "folders.changed": "Inbox folder changed to {path}.",
      "folders.returned": "Returns folder:",
      "folders.home": "Archive directory:",
      "intake.period": "Run every",
      "intake.minutes": "{n} min",
      "intake.threshold": "Score threshold",
      "intake.llm": "Check with the model",
      "intake.cloud": "Cloud fallback model",
      "intake.cloud.warning": "If the local model stays silent for 180 seconds, the cloud model checks the document text: it leaves this machine.",
      "intake.maxGb": "Archive size, GB",
      "intake.maxFiles": "Files",
      "intake.maxRatio": "Compression, to 1",
      "intake.depth": "Nesting depth",
      "intake.vision": "Describe images with the local model",
      "intake.vision.hint": "Images never leave this machine. Needs a local model with vision.",
      "intake.visionPages": "Pages per run",
      "intake.visionMinutes": "Minutes per run",
      "intake.fillIn": "Enter a number for: {fields}",
      "tokens.none": "No tokens.",
      "tokens.noExpiry": "no expiry",
      "tokens.neverUsed": "never used",
      "tokens.col.name": "Name",
      "tokens.col.level": "Level",
      "tokens.col.created": "Issued",
      "tokens.col.expires": "Expires",
      "tokens.col.lastUsed": "Last used",
      "tokens.col.state": "State",
      "token.level.read": "read",
      "token.level.full": "full",
      "token.level.local": "service",
      "token.state.active": "active",
      "token.state.revoked": "revoked",
      "token.state.expired": "expired",
      "tokens.revoke": "Revoke",
      "tokens.revoke.confirm": "Yes, close access",
      "tokens.issue": "Issue a token",
      "tokens.name": "Client name",
      "tokens.placeholder": "laptop",
      "tokens.level": "Level",
      "tokens.option.read": "read: search and documents",
      "tokens.option.full": "full: also create and submit files",
      "tokens.days": "Days (empty: no expiry)",
      "tokens.issueButton": "Issue",
      "tokens.issued": "Token for client \u201c{name}\u201d, level {level}. Save it now: it is not shown a second time.",
      "tokens.copy": "Copy",
      "tokens.dismiss": "Dismiss",
      "delete.note": "The document leaves search and the corpus. The file is not erased: it moves to the archive's deleted folder.",
      "delete.path": "Document path, as shown in search results",
      "delete.placeholder": "folder/file.pdf",
      "delete.button": "Remove from archive",
      "delete.confirm": "Yes, remove from archive and index",
      "journal.client": "Client",
      "journal.all": "All",
      "journal.noToken": "no token",
      "journal.none": "No records.",
      "journal.col.time": "Time",
      "journal.col.client": "Client",
      "journal.col.service": "Service",
      "journal.col.tool": "Tool",
      "journal.col.outcome": "Outcome",
      "journal.col.from": "From",
      "journal.col.params": "Parameters",
      "toast.text": "Intake finished: {parts}.",
      "toast.attention.one": "{n} file needs a decision",
      "toast.attention.many": "{n} files need a decision",
      "toast.problems.one": "{n} problem",
      "toast.problems.many": "{n} problems",
      "toast.open": "Open",
      "toast.close": "\u00d7"
    };

    const has = (table, key) => Object.prototype.hasOwnProperty.call(table, key);
    /** Подстановка {имя} в шаблон словаря: то же, что делает t от службы языка. */
    const fillTemplate = (template, params) => String(template).replace(/\{(\w+)\}/g, (whole, name) => (params && has(params, name) ? String(params[name]) : whole));

    /**
     * Названия и сообщения из соседнего client.messages.js. Пока он не загрузился (или не загрузится совсем) —
     * text сервера для сообщений и сырые слова для названий: экран работает, только не по-английски.
     */
    function formatterOf(messages) {
      const dictionary = messages || {};
      const raw = (key) => (key === null || key === undefined ? "" : String(key));
      const pick = (name, fallback) => (typeof dictionary[name] === "function" ? dictionary[name] : fallback);
      return {
        message: pick("format", (value) => (typeof value === "string" ? value : value && typeof value.text === "string" ? value.text : "")),
        stage: pick("stageName", raw),
        decision: pick("decisionName", raw),
        owner: pick("ownerDecisionName", raw),
        location: pick("locationName", raw),
        level: pick("levelName", raw),
        rule: pick("ruleName", raw),
        decisions: () => (isObject(dictionary.DECISIONS) ? Object.keys(dictionary.DECISIONS) : [])
      };
    }

    const failureOf = (error) => ({
      kind: (error && error.kind) || "failed",
      message: (error && error.message) || "",
      refusal: (error && error.refusal) || null,
      status: (error && error.status) || null
    });

    /** Отказ по одному файлу: сообщение {code, args, text}; строка (старый вид) и пустота тоже приводятся к нему. */
    const rowRefusal = (value) => refusalOf(value) || { code: null, args: {}, text: "" };

    // ── просмотр: ключи и общие расчёты (FR-83) ──────────────────
    /** Ключ записи просмотра в хранилище: область и путь файла. Один файл — одна запись, где бы его ни раскрыли. */
    const previewKey = (target) => target.area + ":" + target.path;
    const validTarget = (target) => isObject(target) && PREVIEW_AREAS.includes(target.area) && typeof target.path === "string" && target.path !== "";

    /** Сколько страниц можно листать: показанные (shown), но не больше, чем в документе (pages); нет сведений — 0. */
    function shownPages(data) {
      const count = (value) => (Number.isInteger(value) && value >= 1 ? value : 0);
      const pages = count(data.pages);
      const shown = count(data.shown);
      if (shown === 0) return pages;
      return pages === 0 ? shown : Math.min(shown, pages);
    }

    /**
     * Общее хранилище вкладки: одно на плагин. Только оно опрашивает сервер — раз в 30 с, пока страница видна,
     * раз в 2 с, пока идёт разбор; скрытая страница не опрашивает. Списки очереди и карантина перечитываются, когда
     * изменились счётчики или номер последней пачки (и после каждого решения). Снимок getSnapshot() неизменяем и
     * меняется только вместе с уведомлением подписчиков — на нём стоит useSyncExternalStore.
     * История пачек (FR-82) — часть того же хранилища: history = {items, more, loading, error, open, details}. Первая страница
     * перечитывается, когда в состоянии сменился номер последней пачки; подробности открытых пачек — когда изменились
     * счётчики очереди или карантина (и после решения на вкладке). Сбой истории остаётся в history.error и не трогает error.
     * Просмотры файлов (FR-83) — тоже часть хранилища: previews[ключ файла] = {target, stack}; запись живёт, пока открыта хоть одна
     * область просмотра этого файла (openPreview/closePreview считают их). Адреса объектов (URL.createObjectURL) создаёт и освобождает
     * только хранилище; таймеры повтора rendering живут, пока есть ожидающий просмотр, постоянных таймеров просмотр не заводит.
     * @param deps - client (createClient), timers ({setTimeout, clearTimeout}), page ({isVisible, subscribe}).
     */
    function createStore({ client, timers, page }) {
      let state = {
        status: null, queue: null, quarantine: null, error: null, rowErrors: {}, busy: false, messages: null, notice: null,
        history: { items: null, more: false, loading: false, error: null, open: [], details: {} },
        previews: {}
      };
      const listeners = new Set();
      let started = false;
      let timer = null;
      let unwatch = null;
      let grace = 0; // сколько ещё опросов подряд оставаться частыми после «Run now»
      let listKey = null; // под каким состоянием счётчиков списки прочитаны в последний раз
      let working = 0;
      let chain = Promise.resolve(); // опросы идут по одному, в порядке заказа
      let seenBatch; // номер последней пачки, под которым первая страница истории прочитана в последний раз (пока не читали — undefined)
      let seenCounts = null; // счётчики очереди и карантина при прошлом опросе
      let polled = false; // был ли уже удавшийся опрос состояния: первый после загрузки событий не даёт, он только запоминает пачку
      const knownBatches = new Set(); // пачки, которые уже были последними при каком-то опросе: об одной пачке сообщение не больше одного раза
      let noticeSeq = 0;
      let pagesWorking = 0; // сколько запросов страниц истории в работе или в очереди
      let pagesChain = Promise.resolve(); // страницы истории читаются по одной: «до» считается по списку, каким он стал к своей очереди
      let detailCalls = 0;
      const detailSeq = new Map(); // номер пачки → номер её последнего запроса подробностей: ответ на более ранний отбрасывается

      const set = (patch) => {
        state = { ...state, ...patch };
        for (const listener of [...listeners]) listener();
      };
      const work = (delta) => {
        working += delta;
        if (state.busy !== working > 0) set({ busy: working > 0 });
      };
      // сбой у строки показывается, пока файл на экране; ушёл из списков — сбой уходит с ним
      const keepListed = (errors, lists = state) => Object.fromEntries(Object.entries(errors).filter(([path]) =>
        [lists.queue, lists.quarantine].some((list) => Array.isArray(list) && list.some((entry) => entry.path === path))));

      // ── событие «пачка кончилась» ──────────────────────────────
      /**
       * Номер последней пачки сменился по сравнению с прошлыми удавшимися опросами (первый после загрузки только запоминает пачку) — пачка
       * кончилась. Если в ней есть файлы на решение (attention) или замечания (problems), в снимок кладётся notice: сообщение для ToastHost.
       * Оба нуля — сообщения нет. Пачка, уже бывшая последней, новой не считается: возврат к ней (дрожание списка) сообщения не даёт.
       */
      function noteBatch(status) {
        const last = isObject(status.last_batch) && typeof status.last_batch.batch === "string" ? status.last_batch.batch : null;
        const ended = polled && last !== null && !knownBatches.has(last);
        polled = true;
        if (last === null) return;
        knownBatches.add(last);
        if (!ended) return;
        const attention = attentionOf(status);
        const problems = Number.isInteger(status.problems) && status.problems > 0 ? status.problems : 0;
        if (attention + problems > 0) set({ notice: { id: ++noticeSeq, batch: last, attention, problems } });
      }
      /** Сообщение погасло или закрыто: убирается только то, что на экране (устаревший вызов о прежнем сообщении ничего не трогает). */
      function dismissNotice(id) {
        if (state.notice !== null && state.notice.id === id) set({ notice: null });
      }

      // ── история пачек ──────────────────────────────────────────
      const setHistory = (patch) => set({ history: { ...state.history, ...patch } });
      const setDetail = (id, entry) => setHistory({ details: { ...state.history.details, [id]: entry } });
      const detailEntry = (id) => (has(state.history.details, id) ? state.history.details[id] : null);
      const readPage = (data) => {
        if (!isObject(data) || !Array.isArray(data.batches)) throw new Error("the batch list is not a list");
        return { batches: data.batches.filter((batch) => isObject(batch) && typeof batch.id === "string"), more: data.more === true };
      };
      const readDetail = (data) => {
        if (!isObject(data) || !Array.isArray(data.files)) throw new Error("the batch details are malformed");
        return data;
      };
      /** Запрос страницы списка в очередь: задача сама ловит свои сбои; loading держится, пока в очереди что-то есть. */
      const queuePage = (task) => {
        pagesWorking += 1;
        if (!state.history.loading) setHistory({ loading: true });
        const run = pagesChain.then(task).catch(() => {}).then(() => {
          pagesWorking -= 1;
          if (pagesWorking === 0) setHistory({ loading: false });
        });
        pagesChain = run;
        return run;
      };
      /**
       * Первая страница. Что загружено ниже (по «Load more»), остаётся, если свежая страница стыкуется со списком:
       * её последняя пачка уже есть в нём. Иначе (пачек прибыло больше страницы) между ними могла бы быть дыра — тогда остаётся одна свежая.
       */
      const loadFirstPage = (last) => queuePage(async () => {
        try {
          const page = readPage(await client.batches(HISTORY_PAGE, null));
          seenBatch = last;
          const old = state.history.items;
          const joint = Array.isArray(old) && page.more && page.batches.length > 0 ? old.findIndex((batch) => batch.id === page.batches[page.batches.length - 1].id) : -1;
          const kept = joint >= 0 ? old.slice(joint + 1) : [];
          setHistory({ items: [...page.batches, ...kept], more: kept.length > 0 ? state.history.more : page.more, error: null });
        } catch (error) {
          setHistory({ error: failureOf(error) });
        }
      });
      /** Следующая страница: «до» — последняя из показанных к тому времени, как очередь дошла до этого запроса. */
      function loadMoreBatches() {
        const { items, more, loading } = state.history;
        if (!more || loading || items === null || items.length === 0) return Promise.resolve();
        return queuePage(async () => {
          const shown = state.history.items;
          try {
            const page = readPage(await client.batches(HISTORY_PAGE, shown[shown.length - 1].id));
            const have = new Set(state.history.items.map((batch) => batch.id));
            setHistory({ items: [...state.history.items, ...page.batches.filter((batch) => !have.has(batch.id))], more: page.more, error: null });
          } catch (error) {
            setHistory({ error: failureOf(error) });
          }
        });
      }
      /** Подробности пачки. Прежние данные остаются на экране, пока идёт перечитывание; ответ на устаревший запрос отбрасывается. */
      function loadDetail(id) {
        const mine = ++detailCalls;
        detailSeq.set(id, mine);
        const before = detailEntry(id);
        setDetail(id, { data: before !== null ? before.data : null, error: null, loading: true });
        return client.batch(id).then(readDetail).then((data) => {
          if (detailSeq.get(id) === mine) setDetail(id, { data, error: null, loading: false });
        }, (error) => {
          if (detailSeq.get(id) !== mine) return;
          const kept = detailEntry(id);
          setDetail(id, { data: kept !== null ? kept.data : null, error: failureOf(error), loading: false });
        });
      }
      /** Раскрыть или закрыть пачку. Готовые подробности из памяти заново не спрашиваются; неудавшиеся — спрашиваются. */
      function toggleBatch(id) {
        const { open } = state.history;
        if (open.includes(id)) {
          setHistory({ open: open.filter((other) => other !== id) });
          return Promise.resolve();
        }
        setHistory({ open: [...open, id] });
        const entry = detailEntry(id);
        return entry === null || (!entry.loading && entry.error !== null) ? loadDetail(id) : Promise.resolve();
      }
      /** Решения владельца меняют decided и location: открытые пачки перечитываются, закрытые теряют устаревшее и спросят заново при раскрытии. */
      function refreshDetails() {
        const { open, details } = state.history;
        for (const id of Object.keys(details)) if (!open.includes(id)) detailSeq.delete(id);
        setHistory({ details: Object.fromEntries(open.filter((id) => has(details, id)).map((id) => [id, details[id]])) });
        for (const id of open) loadDetail(id);
      }
      /** Что перечитать по свежему состоянию. Запросы идут сами по себе: сбой или медлительность истории опрос не задерживают. */
      function syncHistory(status, force) {
        const last = isObject(status.last_batch) && typeof status.last_batch.batch === "string" ? status.last_batch.batch : null;
        const counts = JSON.stringify([status.queue, status.quarantine]);
        const moved = seenCounts !== null && counts !== seenCounts;
        seenCounts = counts;
        if (last !== seenBatch && pagesWorking === 0) loadFirstPage(last); // и первое чтение, и повтор после сбоя
        if (moved || force) {
          refreshDetails();
          return;
        }
        for (const id of state.history.open) {
          const entry = detailEntry(id);
          if (entry !== null && !entry.loading && entry.error !== null) loadDetail(id); // открытая, а подробностей нет: пробуем снова
        }
      }

      // ── просмотр содержимого (FR-83) ───────────────────────────
      // Вид = {id, member, phase, data, error, page, image}. phase: loading, rendering (файл рисуется, повтор по таймеру),
      // late (рисуется дольше трёх минут), ready, failed. image: null или {token, page, phase: loading|ready|failed, url, error}.
      // Первый вид в стопке — сам файл, следующие — вложения письма (member [i], потом [i, j]); показан последний.
      const viewers = new Map(); // ключ файла → сколько областей просмотра его сейчас показывают
      const retries = new Map(); // номер вида → сколько повторов rendering уже заказано
      const renderTimers = new Map(); // номер вида → таймер ближайшего повтора
      let previewSeq = 0; // номера видов и картинок: ответ на ушедший вид или страницу отбрасывается по номеру

      const entryOf = (key) => (has(state.previews, key) ? state.previews[key] : null);
      const topOf = (entry) => entry.stack[entry.stack.length - 1];
      const findView = (key, id) => {
        const entry = entryOf(key);
        return entry === null ? null : entry.stack.find((view) => view.id === id) || null;
      };
      const putEntry = (key, entry) => set({ previews: { ...state.previews, [key]: entry } });
      const patchView = (key, id, patch) => {
        const entry = entryOf(key);
        if (entry === null || !entry.stack.some((view) => view.id === id)) return;
        putEntry(key, { ...entry, stack: entry.stack.map((view) => (view.id === id ? { ...view, ...patch } : view)) });
      };
      const freshView = (member) => ({ id: ++previewSeq, member, phase: "loading", data: null, error: null, page: 1, image: null });
      const revokeImage = (image) => {
        if (image === null || image === undefined || typeof image.url !== "string") return;
        try {
          URL.revokeObjectURL(image.url);
        } catch (_error) {
          // адрес мог уйти вместе со страницей: освобождать нечего
        }
      };
      /** Вид уходит: его адрес освобождается, таймер повтора гаснет. */
      const releaseView = (view) => {
        revokeImage(view.image);
        retries.delete(view.id);
        if (renderTimers.has(view.id)) {
          timers.clearTimeout(renderTimers.get(view.id));
          renderTimers.delete(view.id);
        }
      };
      const readPreview = (data) => {
        if (!isObject(data) || typeof data.kind !== "string") throw new Error("the preview description is malformed");
        return data;
      };

      /** Картинка показанной страницы вида. Прежний адрес освобождается сразу; ответ на ушедшую страницу отбрасывается и адреса не получает. */
      async function fetchImage(key, id) {
        const entry = entryOf(key);
        const view = findView(key, id);
        if (entry === null || view === null) return;
        revokeImage(view.image);
        const token = ++previewSeq;
        const shown = view.page;
        patchView(key, id, { image: { token, page: shown, phase: "loading", url: null, error: null } });
        const current = () => {
          const now = findView(key, id);
          return now !== null && now.image !== null && now.image.token === token;
        };
        const fail = (error) => patchView(key, id, { image: { token, page: shown, phase: "failed", url: null, error: failureOf(error) } });
        let blob;
        try {
          blob = await client.previewPage({ area: entry.target.area, path: entry.target.path, member: view.member }, shown);
        } catch (error) {
          if (current()) fail(error);
          return;
        }
        if (!current()) return; // страница сменилась или просмотр закрыт: адрес даже не создаётся
        let url;
        try {
          url = URL.createObjectURL(blob);
        } catch (error) {
          fail(error);
          return;
        }
        patchView(key, id, { image: { token, page: shown, phase: "ready", url, error: null } });
      }

      /** Описание вида. quiet — повтор после rendering: вид остаётся в rendering, а не мигает загрузкой. */
      async function fetchView(key, id, quiet) {
        const entry = entryOf(key);
        const view = findView(key, id);
        if (entry === null || view === null) return;
        if (!quiet && view.phase !== "loading") patchView(key, id, { phase: "loading", error: null });
        let data;
        try {
          data = readPreview(await client.preview({ area: entry.target.area, path: entry.target.path, member: view.member }));
        } catch (error) {
          if (findView(key, id) !== null) patchView(key, id, { phase: "failed", data: null, error: failureOf(error) });
          return;
        }
        if (findView(key, id) === null) return; // закрыли или вернулись к письму, пока ждали
        if (data.kind === "rendering") {
          const done = retries.get(id) || 0;
          if (done >= RENDER_MAX_RETRIES) {
            patchView(key, id, { phase: "late", data: null, error: null });
            return;
          }
          retries.set(id, done + 1);
          patchView(key, id, { phase: "rendering", data: null, error: null });
          renderTimers.set(id, timers.setTimeout(() => {
            renderTimers.delete(id);
            fetchView(key, id, true);
          }, RENDER_RETRY_MS));
          return;
        }
        patchView(key, id, { phase: "ready", data, error: null });
        if (IMAGE_KINDS.includes(data.kind)) fetchImage(key, id); // не ждём: описание уже готово
      }

      /** Область просмотра файла появилась: запись создаётся и описание подгружается при первом показе; следующие показы делят её. */
      function openPreview(target) {
        if (!validTarget(target)) return Promise.resolve();
        const key = previewKey(target);
        viewers.set(key, (viewers.get(key) || 0) + 1);
        if (entryOf(key) !== null) return Promise.resolve();
        const view = freshView([]);
        putEntry(key, { target: { area: target.area, path: target.path }, stack: [view] });
        return fetchView(key, view.id, false);
      }
      /** Область просмотра ушла: когда последняя, запись исчезает, адреса освобождаются, таймеры гаснут. */
      function closePreview(target) {
        if (!validTarget(target)) return;
        const key = previewKey(target);
        const left = (viewers.get(key) || 0) - 1;
        if (left > 0) {
          viewers.set(key, left);
          return;
        }
        viewers.delete(key);
        const entry = entryOf(key);
        if (entry === null) return;
        entry.stack.forEach(releaseView);
        const rest = { ...state.previews };
        delete rest[key];
        set({ previews: rest });
      }
      /** Вложение письма тем же просмотром: member — номер из записи вложения, к цепочке показанного вида. Глубже двух уровней — нет. */
      function openAttachment(target, index) {
        if (!validTarget(target) || !Number.isSafeInteger(index) || index < 0) return Promise.resolve();
        const key = previewKey(target);
        const entry = entryOf(key);
        if (entry === null) return Promise.resolve();
        const view = topOf(entry);
        if (view.phase !== "ready" || view.data.kind !== "mail" || view.member.length >= MAX_MEMBER_DEPTH) return Promise.resolve();
        const next = freshView([...view.member, index]); // письмо картинки не держит: освобождать нечего
        putEntry(key, { ...entry, stack: [...entry.stack, next] });
        return fetchView(key, next.id, false);
      }
      /** «Back to message»: уровень выше без нового запроса; вложение уходит вместе со своим адресом и таймером. */
      function backToMessage(target) {
        if (!validTarget(target)) return;
        const key = previewKey(target);
        const entry = entryOf(key);
        if (entry === null || entry.stack.length < 2) return;
        releaseView(topOf(entry));
        putEntry(key, { ...entry, stack: entry.stack.slice(0, -1) });
      }
      /** Страница документа: только из показанных, и только та, что запрошена сейчас. */
      function showPage(target, pageNumber) {
        if (!validTarget(target)) return Promise.resolve();
        const key = previewKey(target);
        const entry = entryOf(key);
        if (entry === null) return Promise.resolve();
        const view = topOf(entry);
        if (view.phase !== "ready" || view.data.kind !== "pages") return Promise.resolve();
        if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > shownPages(view.data) || pageNumber === view.page) return Promise.resolve();
        patchView(key, view.id, { page: pageNumber });
        return fetchImage(key, view.id);
      }
      /** «Try again»: описание, которое не вышло или не успело, либо страница, которая не вышла. */
      function retryPreview(target) {
        if (!validTarget(target)) return Promise.resolve();
        const key = previewKey(target);
        const entry = entryOf(key);
        if (entry === null) return Promise.resolve();
        const view = topOf(entry);
        if (view.phase === "failed" || view.phase === "late") {
          retries.delete(view.id);
          return fetchView(key, view.id, false);
        }
        if (view.phase === "ready" && view.image !== null && view.image.phase === "failed") return fetchImage(key, view.id);
        return Promise.resolve();
      }
      /** Плагин снят: все адреса освобождены, все таймеры просмотров погашены. */
      function releasePreviews() {
        for (const entry of Object.values(state.previews)) entry.stack.forEach(releaseView);
        viewers.clear();
        if (Object.keys(state.previews).length > 0) set({ previews: {} });
      }

      const stopTimer = () => {
        if (timer !== null) {
          timers.clearTimeout(timer);
          timer = null;
        }
      };
      const schedule = () => {
        stopTimer();
        if (!started || !page.isVisible()) return;
        const running = state.status !== null && state.status.progress !== null && state.status.progress !== undefined;
        timer = timers.setTimeout(() => {
          timer = null;
          refresh();
        }, running || grace > 0 ? POLL_RUN_MS : POLL_IDLE_MS);
      };

      async function poll(force) {
        try {
          const status = await client.inbox();
          if (!isObject(status)) throw new Error("the inbox state is not an object");
          if (status.progress !== null && status.progress !== undefined) grace = 0;
          else if (grace > 0) grace -= 1;
          set({ status });
          noteBatch(status);
          syncHistory(status, force);
          const key = JSON.stringify([status.queue, status.quarantine, isObject(status.last_batch) ? status.last_batch.batch : null]);
          if (force || key !== listKey) {
            const [queue, quarantine] = await Promise.all([client.queue(), client.quarantine()]);
            if (!Array.isArray(queue) || !Array.isArray(quarantine)) throw new Error("the list is not a list");
            listKey = key;
            set({ queue, quarantine, rowErrors: keepListed(state.rowErrors, { queue, quarantine }) });
          }
          set({ error: null });
        } catch (error) {
          set({ error: failureOf(error) });
        } finally {
          schedule();
        }
      }
      /** Один опрос состояния; списки — если изменились счётчики или нужно force. Не бросает: сбой уходит в снимок. */
      function refresh(options = {}) {
        const run = chain.then(() => poll(options.force === true));
        chain = run;
        return run;
      }

      function start() {
        if (started) return;
        started = true;
        unwatch = page.subscribe(() => {
          if (!started) return;
          if (page.isVisible()) refresh();
          else stopTimer();
        });
        if (page.isVisible()) refresh();
      }
      function stop() {
        started = false;
        stopTimer();
        if (unwatch !== null) {
          unwatch();
          unwatch = null;
        }
        releasePreviews();
      }

      /** Куда показать сбой запроса: отказ по одному файлу — у его строки, остальное — над списком. */
      function place(error, paths, rowFailures) {
        if (error.kind === "refused" && error.refusal && paths.length === 1) rowFailures[paths[0]] = { kind: "refused", refusal: error.refusal };
        else set({ error });
      }

      async function runNow() {
        work(+1);
        try {
          set({ error: null });
          let error = null;
          try {
            await client.runInbox();
            grace = AFTER_RUN_POLLS;
          } catch (failed) {
            error = failureOf(failed);
          }
          await refresh();
          if (error !== null) set({ error });
        } finally {
          work(-1);
        }
      }

      /** Решение над файлами одной области: до 200 путей в запросе, больше — несколькими, по порядку. */
      async function decide(area, action, paths) {
        if (!has(BULK_ACTIONS, area) || !BULK_ACTIONS[area].includes(action)) throw new Error("unsupported action: " + area + " " + action);
        const list = [...new Set(paths)];
        if (list.length === 0) return;
        work(+1);
        try {
          set({ error: null, rowErrors: Object.fromEntries(Object.entries(state.rowErrors).filter(([path]) => !list.includes(path))) });
          const rowFailures = {};
          let error = null;
          let sent = [];
          for (let at = 0; at < list.length && error === null; at += BATCH_SIZE) {
            sent = list.slice(at, at + BATCH_SIZE);
            try {
              const data = await client.decide(area, action, sent);
              for (const result of isObject(data) && Array.isArray(data.results) ? data.results : []) {
                if (isObject(result) && result.ok === false && typeof result.path === "string") {
                  rowFailures[result.path] = { kind: "refused", refusal: rowRefusal(result.error) };
                }
              }
            } catch (failed) {
              error = failureOf(failed);
            }
          }
          await refresh({ force: true });
          if (error !== null) place(error, sent, rowFailures);
          set({ rowErrors: keepListed({ ...state.rowErrors, ...rowFailures }) });
        } finally {
          work(-1);
        }
      }

      /** Стирание из карантина: один файл, прежним видом запроса; подтверждает вкладка. */
      async function remove(path) {
        work(+1);
        try {
          set({ error: null, rowErrors: Object.fromEntries(Object.entries(state.rowErrors).filter(([key]) => key !== path)) });
          const rowFailures = {};
          let error = null;
          try {
            await client.decideQuarantine(path, "delete");
          } catch (failed) {
            error = failureOf(failed);
          }
          await refresh({ force: true });
          if (error !== null) place(error, [path], rowFailures);
          set({ rowErrors: keepListed({ ...state.rowErrors, ...rowFailures }) });
        } finally {
          work(-1);
        }
      }

      return {
        getSnapshot: () => state,
        subscribe(listener) {
          listeners.add(listener);
          return () => listeners.delete(listener);
        },
        start,
        stop,
        refresh,
        runNow,
        decide,
        remove,
        loadMoreBatches,
        toggleBatch,
        openPreview,
        closePreview,
        openAttachment,
        backToMessage,
        showPage,
        retryPreview,
        dismissNotice,
        setMessages: (messages) => set({ messages })
      };
    }

    /** Главная находка строки: самая тяжёлая, при равенстве — первая. */
    function mainFinding(findings) {
      let best = null;
      let bestRank = -1;
      for (const finding of Array.isArray(findings) ? findings : []) {
        if (!isObject(finding)) continue;
        const rank = has(LEVEL_RANK, finding.level) ? LEVEL_RANK[finding.level] : 0;
        if (rank > bestRank) {
          best = finding;
          bestRank = rank;
        }
      }
      return best;
    }

    /** Сбой запроса словами для экрана: отказ — по его сообщению, прочее — отдельными английскими текстами. */
    function describe(failed, fmt, t) {
      if (failed.kind === "refused" && failed.refusal) return fmt.message(failed.refusal) || failed.message;
      if (failed.kind === "offline") return t("error.offline");
      if (failed.kind === "auth") return t("error.signedOut");
      if (failed.status) return t("error.failed", { status: failed.status });
      return t("error.unknown"); // чужое исключение: его текст не английский и пользователю ничего не скажет
    }

    /** Ход разбора: этап, done из total (или только done), полоса, последний готовый файл. */
    function ProgressView({ progress, fmt, t }) {
      const done = Number.isInteger(progress.done) && progress.done >= 0 ? progress.done : 0;
      const known = Number.isInteger(progress.total) && progress.total >= 0;
      const total = known ? Math.max(progress.total, done) : null; // total растёт по ходу: меньше done он быть не может
      const bar = known
        ? h("div", { className: "ba-bar", role: "progressbar", "aria-label": t("progress.label"), "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": done },
          h("div", { className: "ba-bar-fill", style: { width: (total === 0 ? 0 : Math.round((done / total) * 100)) + "%" } }))
        : h("div", { className: "ba-bar ba-bar-busy", role: "progressbar", "aria-label": t("progress.label"), "aria-valuemin": 0 },
          h("div", { className: "ba-bar-fill" }));
      return h("div", { className: "ba-item-main" },
        h("div", null, fmt.stage(progress.stage)),
        h("div", null, known ? t("progress.count", { done, total }) : t("progress.countOnly", { done })),
        bar,
        typeof progress.current === "string" && progress.current !== "" ? h("div", { className: "ba-dim" }, t("progress.current", { name: progress.current })) : null);
    }

    /** Строка состояния: сколько ждёт, сколько ждёт описания (когда есть), таймер, итог последней пачки, замечания, «Run now», ход, адрес входящей папки. */
    function StatusBox({ status, busy, fmt, t, onRun }) {
      const [copied, setCopied] = useState(null);
      if (status === null) {
        return h("div", { className: "ba-box" },
          h("div", { className: "ba-dim" }, t("loading")),
          h("div", { className: "ba-row" }, h("button", { type: "button", disabled: true }, t("run"))));
      }
      const lastLine = lastBatchLine(status, fmt, t);
      const running = status.progress !== null && status.progress !== undefined;
      const copyPath = () => {
        const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
        if (!clipboard || typeof clipboard.writeText !== "function") return setCopied("failed");
        Promise.resolve().then(() => clipboard.writeText(status.inbox)).then(() => setCopied("done"), () => setCopied("failed"));
      };
      return h("div", { className: "ba-box" },
        h("div", null, t("status.waiting", { n: status.waiting })),
        awaitingOf(status) > 0 ? h("div", null, t("status.vision", { n: awaitingOf(status) })) : null,
        h("div", null, status.timer ? t("status.timer", { n: status.period }) : t("status.noTimer")),
        h("div", { className: "ba-dim" }, lastLine),
        typeof status.problems === "number"
          ? h("div", { className: status.problems > 0 ? "ba-bad" : "ba-dim" }, t("status.problems", { n: status.problems }))
          : null,
        running ? h(ProgressView, { progress: status.progress, fmt, t }) : null,
        h("div", { className: "ba-row" }, h("button", { type: "button", disabled: busy || running, onClick: onRun }, t("run"))),
        typeof status.inbox === "string"
          ? h("div", { className: "ba-row" },
            h("span", { className: "ba-dim" }, t("inbox.folder")),
            h("span", { className: "ba-token" }, status.inbox),
            h("button", { type: "button", onClick: copyPath }, copied === "done" ? t("copied") : t("copy")))
          : null,
        copied === "failed" ? h("div", { className: "ba-dim" }, t("copy.failed")) : null);
    }

    const BULK = {
      accept: { area: "queue", action: "accept", label: "acceptSelected" },
      quarantine: { area: "queue", action: "quarantine", label: "quarantineSelected" },
      "return": { area: "quarantine", action: "return", label: "returnSelected" }
    };

    /** «Needs decision»: очередь и карантин одним списком, галочки, кнопки у строки и для отмеченных. */
    function DecisionList({ snap, fmt, t, store }) {
      const [selected, setSelected] = useState(() => new Set());
      const [confirming, setConfirming] = useState(null); // какое действие над отмеченными ждёт «да»
      const [deleting, setDeleting] = useState(null); // у какой строки карантина ждёт «да» стирание
      const [expanded, setExpanded] = useState(() => new Set()); // пути раскрытых строк: находки, сведения и просмотр
      const { queue, quarantine, status, busy, rowErrors } = snap;
      const rows = [
        ...(queue || []).map((entry) => ({ ...entry, area: "queue" })),
        ...(quarantine || []).map((entry) => ({ ...entry, area: "quarantine" }))
      ];
      const loaded = queue !== null && quarantine !== null;
      const count = loaded ? rows.length : status !== null ? (status.queue || 0) + (status.quarantine || 0) : null;
      // отмеченное, чего уже нет в списках, не считается: число в вопросе — то, что уйдёт на сервер
      const chosen = rows.filter((row) => selected.has(row.path));
      const chosenIn = (area) => chosen.filter((row) => row.area === area);
      const toggle = (path, on) => setSelected((before) => {
        const next = new Set(before);
        if (on) next.add(path);
        else next.delete(path);
        return next;
      });

      const pending = confirming !== null ? BULK[confirming] : null;
      const targets = pending !== null ? chosenIn(pending.area) : [];
      const prompt = pending !== null && targets.length > 0
        ? h("div", { className: "ba-confirm", role: "group" },
          h("span", null, t("confirm." + confirming + (targets.length === 1 ? ".one" : ".many"), { n: targets.length })),
          h("button", {
            type: "button",
            disabled: busy,
            onClick: () => {
              setConfirming(null);
              store.decide(pending.area, pending.action, targets.map((row) => row.path));
            }
          }, t("yes." + confirming, { n: targets.length })),
          h("button", { type: "button", onClick: () => setConfirming(null) }, t("cancel")))
        : null;

      const rowButtons = (row) => {
        const act = (action) => () => store.decide(row.area, action, [row.path]);
        const button = (key, onClick, className) => h("button", { key, type: "button", className, disabled: busy, onClick }, t(key));
        if (row.area === "queue") return [button("accept", act("accept")), button("quarantine", act("quarantine"))];
        if (deleting === row.path) {
          return [
            h("span", { key: "ask" }, t("confirm.delete")),
            h("button", { key: "yes", type: "button", className: "ba-bad", disabled: busy, onClick: () => { setDeleting(null); store.remove(row.path); } }, t("yes.delete")),
            h("button", { key: "no", type: "button", onClick: () => setDeleting(null) }, t("cancel"))
          ];
        }
        return [button("return", act("return")), button("delete", () => { setConfirming(null); setDeleting(row.path); }, "ba-bad")];
      };

      const flip = (path) => setExpanded((before) => {
        const next = new Set(before);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });

      const rowView = (row) => {
        const finding = mainFinding(row.findings);
        const rule = finding !== null ? fmt.rule(finding.rule) + " · " + fmt.level(finding.level) : t("row.noFindings");
        const failed = has(rowErrors, row.path) ? rowErrors[row.path] : null;
        const isOpen = expanded.has(row.path);
        // не <button>: у строки кнопки только решения (Accept, Quarantine, Return, Delete), и они не должны путаться с раскрытием
        return h("li", { key: row.path, className: "ba-item", "data-path": row.path },
          h("input", { type: "checkbox", checked: selected.has(row.path), "aria-label": t("row.select", { name: row.name }), onChange: (e) => toggle(row.path, e.target.checked) }),
          h("div", { className: "ba-item-main" },
            h("div", {
              className: "ba-item-name ba-expander", role: "button", tabIndex: 0, "aria-expanded": isOpen, title: row.path,
              onClick: () => flip(row.path),
              onKeyDown: (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  flip(row.path);
                }
              }
            }, row.name),
            h("div", { className: "ba-dim ba-item-meta" },
              h("span", null, t("origin." + row.area)),
              h("span", null, t("row.score", { n: typeof row.score === "number" ? row.score : "—" })),
              h("span", null, rule),
              h("span", null, formatTime(row.time))),
            h("div", { className: "ba-row" }, rowButtons(row)),
            failed !== null ? h("div", { className: "ba-error" }, fmt.message(failed.refusal) || t("row.failed")) : null,
            isOpen ? h("div", { className: "ba-batch-body" },
              h(FileInfo, { file: row, fmt, t }),
              h(PreviewArea, { target: { area: row.area, path: row.path }, previews: snap.previews, fmt, t, store })) : null));
      };

      return h("div", { className: "ba-box" },
        h("h3", null, count === null ? t("decision.heading") : t("decision.title", { n: count })),
        h("div", { className: "ba-row" },
          h("button", { type: "button", disabled: busy || rows.length === 0, onClick: () => setSelected(new Set(rows.map((row) => row.path))) }, t("selectAll")),
          h("button", { type: "button", disabled: chosen.length === 0, onClick: () => { setSelected(new Set()); setConfirming(null); } }, t("clearSelection")),
          Object.keys(BULK).map((key) => h("button", {
            key,
            type: "button",
            disabled: busy || chosenIn(BULK[key].area).length === 0,
            onClick: () => { setDeleting(null); setConfirming(key); }
          }, t(BULK[key].label)))),
        prompt,
        !loaded ? h("div", { className: "ba-dim" }, t("loading"))
          : rows.length === 0 ? h("div", { className: "ba-dim" }, t("decision.empty"))
            : h("ul", { className: "ba-list" }, rows.map(rowView)));
    }

    // ── история пачек на экране (FR-82) ─────────────────────────
    /** Длительность словами: секунды, минуты, часы; негодное значение — null. */
    function formatDuration(seconds) {
      if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null;
      const total = Math.round(seconds);
      if (total < 60) return total + " s";
      const minutes = Math.floor(total / 60);
      if (minutes < 60) return minutes + " min" + (total % 60 > 0 ? " " + (total % 60) + " s" : "");
      return Math.floor(minutes / 60) + " h" + (minutes % 60 > 0 ? " " + (minutes % 60) + " min" : "");
    }

    /** Размер файла английскими единицами. */
    function formatBytes(bytes) {
      if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "—";
      if (bytes < 1024) return bytes + " B";
      if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
      if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
      return (bytes / 1024 / 1024 / 1024).toFixed(1) + " GB";
    }

    /** Дата документа ГГГГ-ММ-ДД как ДД.ММ.ГГГГ: это день, а не момент времени, поэтому часовой пояс браузера её не двигает. */
    function formatDay(value) {
      if (typeof value !== "string" || value === "") return "—";
      const parts = /^(\d{4})-(\d{2})-(\d{2})(?!\d)/.exec(value);
      return parts ? parts[3] + "." + parts[2] + "." + parts[1] : value;
    }

    /** Итог по решениям: порядок из словаря, нулевые пропущены, незнакомое решение названо как есть. */
    function decisionSummary(counts, fmt) {
      if (!isObject(counts)) return "";
      const known = fmt.decisions();
      const keys = [...known.filter((key) => has(counts, key)), ...Object.keys(counts).filter((key) => !known.includes(key))];
      return keys.filter((key) => Number.isInteger(counts[key]) && counts[key] > 0).map((key) => fmt.decision(key) + " " + counts[key]).join(" · ");
    }

    /** Текст сообщения, если оно есть и даёт строку; иначе прежняя строка как есть. */
    function said(message, legacy, fmt) {
      const line = isObject(message) ? fmt.message(message) : "";
      if (line !== "") return line;
      return typeof legacy === "string" ? legacy : "";
    }

    /**
     * Пояснение к находке: сообщение по словарю (неизвестный код — его text); цитата документа — только когда сообщение
     * говорит, что это цитата (args.quoted), иначе русское описание дублировало бы английское. В кавычках — выдержка
     * из документа (args.excerpt), если сервер её прислал: в quote перед цитатой стоит русский ярлык; нет выдержки — quote.
     * Находка без сообщения — прежняя цитата.
     */
    function explanation(finding, fmt) {
      const note = isObject(finding.msg) ? fmt.message(finding.msg) : "";
      const quote = typeof finding.quote === "string" ? finding.quote : "";
      if (note === "") return quote;
      const args = isObject(finding.msg.args) ? finding.msg.args : {};
      if (args.quoted !== true) return note;
      const cited = typeof args.excerpt === "string" ? args.excerpt : quote;
      return cited !== "" ? note + " “" + cited + "”" : note;
    }

    /** Примечания к архиву: сообщения из notes_msg по словарю, в порядке notes; нет notes_msg (старая квитанция) — прежние notes. */
    function noteLines(file, fmt) {
      const legacy = Array.isArray(file.notes) ? file.notes : [];
      const messages = Array.isArray(file.notes_msg) ? file.notes_msg : [];
      return Array.from({ length: Math.max(legacy.length, messages.length) }, (_, i) => said(messages[i], legacy[i], fmt)).filter((line) => line !== "");
    }

    /** Раскрытый файл пачки: находки, причина, кто проверил, дата документа, размер, sha256, путь или куда возвращён. */
    function FileInfo({ file, fmt, t }) {
      const findings = Array.isArray(file.findings) ? file.findings.filter(isObject) : [];
      const reason = said(file.reason_msg, file.reason, fmt);
      const notes = noteLines(file, fmt);
      const filled = (value) => (typeof value === "string" && value !== "" ? value : "—");
      const field = (label, value, className) => h("div", { className: "ba-field" },
        h("span", { className: "ba-dim" }, t(label)), " ", h("span", { className }, value));
      const returned = typeof file.returned === "string" && file.returned !== "";
      return h("div", { className: "ba-batch-body" },
        findings.length === 0 ? h("div", { className: "ba-dim" }, t("row.noFindings"))
          : findings.map((finding, i) => {
            const note = explanation(finding, fmt);
            const where = isObject(finding.where_msg) ? fmt.message(finding.where_msg) : "";
            return h("div", { key: i, className: "ba-finding" },
              h("span", null, fmt.level(finding.level) + " · " + fmt.rule(finding.rule)),
              note !== "" ? h("span", { className: "ba-dim" }, note) : null,
              where !== "" ? h("span", { className: "ba-dim" }, where) : null);
          }),
        reason !== "" ? field("file.reason", reason) : null,
        notes.length > 0 ? field("file.notes", notes.join(" · ")) : null,
        field("file.checkedBy", filled(file.checked_by)),
        field("file.date", formatDay(file.date)),
        field("file.size", formatBytes(file.size)),
        field("file.sha", filled(file.sha256), "ba-token"),
        returned ? field("file.returned", file.returned, "ba-token") : field("file.path", filled(file.path), "ba-token"),
        typeof file.decided_at === "string" && file.decided_at !== "" ? field("file.decidedAt", formatTime(file.decided_at)) : null);
    }

    // ── просмотр содержимого на экране (FR-83) ──────────────────
    // Всё, что пришло из содержимого файла (текст, имена, строки архива, поля письма, пояснения), попадает на экран только
    // текстовыми узлами: ни разметки, ни ссылок с адресами из содержимого. Единственный src — адрес объекта, который создало хранилище.

    /** Куда смотреть файл пачки: область и путь по тому, где он лежит сейчас. Возвращённым, удалённым, пропавшим и не хранившимся просмотра нет. */
    function previewTargetOf(file, batchId) {
      const name = typeof file.name === "string" ? file.name : "";
      if (name === "" || typeof batchId !== "string" || batchId === "") return null;
      if (file.location === "queue") return { area: "queue", path: "очередь/" + batchId + "/" + name };
      if (file.location === "quarantine") return { area: "quarantine", path: "карантин/" + batchId + "/" + name };
      if (file.location === "corpus") {
        // принятый владельцем файл в квитанции всё ещё назван очередью; в корпусе он лежит там же, где и принятый приёмкой
        const own = typeof file.path === "string" && file.path.startsWith("входящие/") ? file.path : null;
        return { area: "corpus", path: own !== null ? own : "входящие/" + batchId + "/" + name };
      }
      return null;
    }

    /** Сведения о файле: имя, тип по содержимому, размер, sha256, пачка — чего нет, то прочерк; архив и путь внутри — только когда они есть. */
    function PreviewMeta({ meta, t }) {
      const info = isObject(meta) ? meta : {};
      const origin = isObject(info.origin) ? info.origin : {};
      const present = (value) => typeof value === "string" && value !== "";
      const filled = (value) => (present(value) ? value : "—");
      const field = (label, value, className) => h("div", { className: "ba-field" },
        h("span", { className: "ba-dim" }, t(label)), " ", h("span", { className }, value));
      return h("div", { className: "ba-preview-meta" },
        field("preview.meta.name", filled(info.name)),
        field("preview.meta.type", filled(info.type)),
        field("preview.meta.size", formatBytes(info.size)),
        field("preview.meta.sha", filled(info.sha256), "ba-token"),
        field("preview.meta.batch", filled(info.batch)),
        present(origin.archive) ? field("preview.meta.archive", origin.archive) : null,
        present(origin.inner) ? field("preview.meta.inner", origin.inner) : null);
    }

    /** Место картинки: загрузка, отказ с «Try again» или сама картинка по адресу объекта. */
    function PictureSlot({ view, alt, style, onLoad, fmt, t, store, target }) {
      const image = view.image;
      if (image === null || image.phase === "loading") return h("div", { className: "ba-dim" }, t("loading"));
      if (image.phase === "failed") {
        return h("div", { className: "ba-item-main" },
          h("div", { className: "ba-error", role: "alert" }, describe(image.error, fmt, t)),
          h("div", { className: "ba-row" }, h("button", { type: "button", onClick: () => store.retryPreview(target) }, t("preview.retry"))));
      }
      return h("div", { className: "ba-preview-picture" }, h("img", { className: "ba-preview-img", src: image.url, alt, style, onLoad }));
    }

    /** Ширина картинки: по ширине области, либо натуральная (100%) или полуторная (150%), когда натуральная ширина уже известна. */
    function zoomStyle(zoom, natural) {
      if (zoom === "fit" || !(natural > 0)) return { maxWidth: "100%", height: "auto" };
      return { maxWidth: "none", width: Math.round(natural * ZOOM_FACTOR[zoom]) + "px", height: "auto" };
    }

    /** Документ страницами: «Page N of M», «Previous» и «Next», масштаб; запрашивается только показанная страница. */
    function PagesView({ view, fmt, t, store, target }) {
      const [zoom, setZoom] = useState("fit");
      const [natural, setNatural] = useState(null);
      const data = view.data;
      const total = shownPages(data);
      if (total < 1) return h("div", { className: "ba-dim" }, t("preview.noPages"));
      const pages = Number.isInteger(data.pages) ? data.pages : total;
      const go = (page) => () => store.showPage(target, page);
      return h("div", { className: "ba-preview-pages" },
        h("div", { className: "ba-row", role: "group" },
          h("button", { type: "button", disabled: view.page <= 1, onClick: go(view.page - 1) }, t("preview.previous")),
          h("span", null, t("preview.page", { n: view.page, total })),
          h("button", { type: "button", disabled: view.page >= total, onClick: go(view.page + 1) }, t("preview.next")),
          ZOOMS.map(([key, label]) => h("button", { key, type: "button", "aria-pressed": zoom === key, onClick: () => setZoom(key) }, t(label)))),
        total < pages ? h("div", { className: "ba-dim" }, t("preview.firstPages", { n: total })) : null,
        h(PictureSlot, {
          view, fmt, t, store, target, alt: t("preview.pageAlt", { n: view.page }), style: zoomStyle(zoom, natural),
          onLoad: (event) => setNatural(event && event.target ? event.target.naturalWidth : null)
        }));
    }

    /** Картинка или кадр видео, при кадре — длительность и размер кадра. */
    function PictureView({ view, fmt, t, store, target }) {
      const media = view.data.kind === "media" ? (isObject(view.data.media) ? view.data.media : {}) : null;
      const took = media !== null ? formatDuration(media.seconds) : null;
      const sized = media !== null && Number.isInteger(media.width) && Number.isInteger(media.height) && media.width > 0 && media.height > 0;
      return h("div", { className: "ba-preview-pages" },
        took !== null ? h("div", null, t("preview.duration", { time: took })) : null,
        sized ? h("div", null, t("preview.frameSize", { width: media.width, height: media.height })) : null,
        h(PictureSlot, { view, fmt, t, store, target, alt: t("preview.imageAlt"), style: { maxWidth: "100%", height: "auto" } }));
    }

    /** Текст как есть: моноширинный, с переводами строк; признак обрезки — отдельной строкой. */
    function TextView({ data, t }) {
      return h("div", { className: "ba-preview-pages" },
        h("pre", { className: "ba-preview-text" }, typeof data.text === "string" ? data.text : ""),
        data.truncated === true ? h("div", { className: "ba-dim" }, t("preview.truncated")) : null);
    }

    /** Письмо: шапка, текст, вложения. Вложение открывается щелчком (нужен целый member от 0), но только на первых двух уровнях. */
    function MailView({ view, t, store, target }) {
      const mail = isObject(view.data.mail) ? view.data.mail : {};
      const filled = (value) => (typeof value === "string" && value !== "" ? value : "—");
      const people = (value) => {
        const list = Array.isArray(value) ? value.filter((one) => typeof one === "string" && one !== "") : typeof value === "string" && value !== "" ? [value] : [];
        return list.length > 0 ? list.join(", ") : "—";
      };
      const field = (label, value) => h("div", { className: "ba-field" }, h("span", { className: "ba-dim" }, t(label)), " ", h("span", null, value));
      const attachments = Array.isArray(mail.attachments) ? mail.attachments.filter(isObject) : [];
      const deep = view.member.length >= MAX_MEMBER_DEPTH;
      return h("div", { className: "ba-preview-pages" },
        h("div", { className: "ba-preview-meta" },
          field("preview.mail.from", people(mail.from)),
          field("preview.mail.to", people(mail.to)),
          field("preview.mail.cc", people(mail.cc)),
          field("preview.mail.date", formatMailDate(mail.date)),
          field("preview.mail.subject", filled(mail.subject))),
        typeof mail.text === "string" && mail.text !== "" ? h("pre", { className: "ba-preview-text" }, mail.text) : null,
        mail.truncated === true ? h("div", { className: "ba-dim" }, t("preview.mail.truncated")) : null,
        attachments.length === 0 ? null : h("div", { className: "ba-preview-pages" },
          h("div", { className: "ba-dim" }, t("preview.mail.attachments")),
          deep ? h("div", { className: "ba-dim" }, t("preview.mail.depth")) : null,
          h("div", { className: "ba-wrap" }, h("table", { className: "ba-table" },
            h("thead", null, h("tr", null, ["name", "type", "size"].map((column) => h("th", { key: column }, t("preview.col." + column))))),
            h("tbody", null, attachments.map((attachment, i) => {
              const name = typeof attachment.name === "string" ? attachment.name : "";
              const canOpen = !deep && Number.isSafeInteger(attachment.member) && attachment.member >= 0;
              const program = attachment.executable === true; // «программа»: это видно в списке, а вложение открывается по-прежнему
              return h("tr", { key: i, "data-attachment": i, "data-program": program ? "true" : undefined },
                h("td", { className: "ba-wide" }, canOpen
                  ? h("button", { type: "button", className: "ba-link", onClick: () => store.openAttachment(target, attachment.member) }, name)
                  : name),
                h("td", null, filled(attachment.type), program ? " " : null, program ? h("span", { className: "ba-bad" }, t("preview.mail.program")) : null),
                h("td", null, formatBytes(attachment.size)));
            }))))));
    }

    /** Содержимое архива: таблица имён и размеров, признак обрезки. */
    function ListingView({ data, t }) {
      const rows = Array.isArray(data.listing) ? data.listing.filter(isObject) : [];
      return h("div", { className: "ba-preview-pages" },
        h("div", { className: "ba-wrap" }, h("table", { className: "ba-table" },
          h("thead", null, h("tr", null, ["name", "size"].map((column) => h("th", { key: column }, t("preview.col." + column))))),
          h("tbody", null, rows.map((row, i) => h("tr", { key: i },
            h("td", { className: "ba-wide" }, typeof row.name === "string" ? row.name : ""),
            h("td", null, formatBytes(row.size))))))),
        data.truncated === true ? h("div", { className: "ba-dim" }, t("preview.listTruncated", { n: rows.length })) : null);
    }

    /** Готовое описание: тело по виду и сведения о файле; у вложения над сведениями — письмо, которому оно принадлежит. Неизвестный вид — как «none». */
    function PreviewReady({ view, outer = null, fmt, t, store, target }) {
      const data = view.data;
      let body;
      if (data.kind === "pages") body = h(PagesView, { view, fmt, t, store, target });
      else if (data.kind === "image" || data.kind === "media") body = h(PictureView, { view, fmt, t, store, target });
      else if (data.kind === "text") body = h(TextView, { data, t });
      else if (data.kind === "mail") body = h(MailView, { view, t, store, target });
      else if (data.kind === "listing") body = h(ListingView, { data, t });
      else {
        const note = isObject(data.note) ? fmt.message(data.note) : "";
        body = h("div", { className: "ba-dim" }, note !== "" ? note : t("preview.none"));
      }
      // имя письма — из его описания, уже лежащего в стопке видов: на сервер за ним не ходят; только текстом
      const outerName = outer !== null && isObject(outer.data) && isObject(outer.data.meta) ? outer.data.meta.name : null;
      const inside = outer === null ? null
        : h("div", { className: "ba-dim" }, t("preview.mail.inside", { name: typeof outerName === "string" && outerName !== "" ? outerName : "—" }));
      return h("div", { className: "ba-preview-body" }, body, inside, h(PreviewMeta, { meta: data.meta, t }));
    }

    /**
     * Область просмотра файла внутри раскрытой строки. Жизнь просмотра — жизнь области: пока она на экране, запись в хранилище
     * есть и держит адреса; ушла (строка свёрнута, файл решён, пачка закрыта) — запись, адреса и таймеры уходят вместе с ней.
     */
    function PreviewArea({ target, previews, fmt, t, store }) {
      useEffect(() => {
        store.openPreview(target);
        return () => store.closePreview(target);
      }, [store, target.area, target.path]);
      const key = previewKey(target);
      const entry = has(previews, key) ? previews[key] : null;
      const view = entry === null ? null : entry.stack[entry.stack.length - 1];
      const retry = h("div", { className: "ba-row" }, h("button", { type: "button", onClick: () => store.retryPreview(target) }, t("preview.retry")));
      let content;
      if (view === null || view.phase === "loading") content = h("div", { className: "ba-dim" }, t("loading"));
      else if (view.phase === "rendering") content = h("div", { className: "ba-dim" }, t("preview.rendering"));
      else if (view.phase === "late") content = h("div", { className: "ba-item-main" }, h("div", { className: "ba-dim" }, t("preview.late")), retry);
      else if (view.phase === "failed") {
        content = h("div", { className: "ba-item-main" }, h("div", { className: "ba-error", role: "alert" }, describe(view.error, fmt, t)), retry);
      } else content = h(PreviewReady, { key: view.id, view, outer: entry.stack.length > 1 ? entry.stack[entry.stack.length - 2] : null, fmt, t, store, target });
      return h("div", { className: "ba-preview", "data-preview": target.path },
        entry !== null && entry.stack.length > 1
          ? h("div", { className: "ba-row" }, h("button", { type: "button", onClick: () => store.backToMessage(target) }, t("preview.mail.back")))
          : null,
        content);
    }

    /**
     * Описание изображений у файла пачки (FR-94): под именем файла — состояние («Described by the model: N pages» и, если описаны
     * не все, «first N of TOTAL»; либо «Awaiting description» и причина), затем замечания о страницах. Отдельного столбца нет:
     * таблица на узкой вкладке не должна расти. Нет поля vision (или оно не объект), нечего сказать — null: строка как прежде.
     * Причина и замечания идут по словарю сообщений, а без него — их text; все значения из ответа попадают на экран только текстом.
     */
    function visionLines(file, fmt, t) {
      const vision = file.vision;
      if (!isObject(vision)) return null;
      const count = (value) => Number.isSafeInteger(value) && value >= 0;
      const lines = [];
      if (vision.state === "described") {
        const head = count(vision.pages) ? t("batch.vision.described." + (vision.pages === 1 ? "one" : "many"), { n: vision.pages }) : t("batch.vision.described");
        const partial = vision.truncated === true && count(vision.pages) && count(vision.total) ? t("batch.vision.truncated", { n: vision.pages, total: vision.total }) : "";
        lines.push(h("div", { key: "state", className: "ba-dim", "data-vision": "described" }, partial === "" ? head : head + " · " + partial));
      } else if (vision.state === "waiting") {
        lines.push(h("div", { key: "state", className: "ba-dim", "data-vision": "waiting" }, t("batch.vision.waiting")));
        const reason = said(vision.reason_msg, vision.reason, fmt);
        if (reason !== "") lines.push(h("div", { key: "reason", className: "ba-dim" }, reason));
      }
      for (const [i, note] of (Array.isArray(vision.notes) ? vision.notes : []).entries()) {
        const line = fmt.message(note);
        if (line !== "") lines.push(h("div", { key: "note:" + i, className: "ba-dim" }, line));
      }
      return lines.length === 0 ? null : lines;
    }

    /** Подробности раскрытой пачки: замечания прохода, отбор по решению, таблица файлов (по 200 за раз). */
    function BatchDetail({ entry, previews, fmt, t, store }) {
      const [filter, setFilter] = useState(null); // решение, по которому отобрано; null — все
      const [shown, setShown] = useState(FILES_SHOWN);
      const [opened, setOpened] = useState(() => new Set()); // имена раскрытых файлов
      const data = entry !== null && entry !== undefined ? entry.data : null;
      const failed = entry && entry.error !== null ? h("div", { className: "ba-error", role: "alert" }, describe(entry.error, fmt, t)) : null;
      if (data === null) return h("div", { className: "ba-batch-body" }, failed !== null ? failed : h("div", { className: "ba-dim" }, t("loading")));

      const files = data.files.filter(isObject);
      const counts = new Map();
      for (const file of files) if (typeof file.decision === "string") counts.set(file.decision, (counts.get(file.decision) || 0) + 1);
      const known = fmt.decisions();
      const kinds = [...known.filter((kind) => counts.has(kind)), ...[...counts.keys()].filter((kind) => !known.includes(kind))];
      const active = filter !== null && counts.has(filter) ? filter : null;
      const chosen = active === null ? files : files.filter((file) => file.decision === active);
      const visible = chosen.slice(0, shown);
      const rest = chosen.length - visible.length;
      const problems = (Array.isArray(data.problems) ? data.problems : []).map((problem) => fmt.message(problem)).filter((line) => line !== "");
      const choose = (kind) => () => {
        setFilter(kind);
        setShown(FILES_SHOWN);
      };
      const toggleFile = (name) => setOpened((before) => {
        const next = new Set(before);
        if (next.has(name)) next.delete(name);
        else next.add(name);
        return next;
      });
      const rows = [];
      for (const file of visible) {
        const name = typeof file.name === "string" ? file.name : "";
        const finding = mainFinding(file.findings);
        const isOpen = opened.has(name);
        rows.push(h("tr", { key: "row:" + name, "data-file": name },
          h("td", { className: "ba-wide" },
            h("button", { type: "button", className: "ba-link", "aria-expanded": isOpen, onClick: () => toggleFile(name) }, name),
            visionLines(file, fmt, t)),
          h("td", null, fmt.decision(file.decision) || "—"),
          h("td", null, typeof file.decided === "string" ? fmt.owner(file.decided) : "—"),
          h("td", null, typeof file.location === "string" ? fmt.location(file.location) : t("batch.notStored")),
          h("td", null, typeof file.score === "number" ? String(file.score) : "—"),
          h("td", null, finding !== null ? fmt.rule(finding.rule) + " · " + fmt.level(finding.level) : "—")));
        if (isOpen) {
          const target = previewTargetOf(file, data.id); // просмотр — пока файл лежит в корпусе, очереди или карантине
          rows.push(h("tr", { key: "info:" + name, "data-file-info": name },
            h("td", { colSpan: FILE_COLUMNS.length, className: "ba-wide" },
              h(FileInfo, { file, fmt, t }),
              target !== null ? h(PreviewArea, { target, previews, fmt, t, store }) : null)));
        }
      }
      if (rest > 0) {
        rows.push(h("tr", { key: "more" },
          h("td", { colSpan: FILE_COLUMNS.length, className: "ba-wide" },
            t("batch.andMore", { n: rest }), " ",
            h("button", { type: "button", onClick: () => setShown(shown + FILES_SHOWN) }, t("batch.showMore")))));
      }
      return h("div", { className: "ba-batch-body" },
        failed,
        problems.length > 0
          ? h("div", { className: "ba-item-main" },
            h("div", { className: "ba-dim" }, t("batch.problemsHeading")),
            problems.map((line, i) => h("div", { key: i }, line)))
          : null,
        files.length === 0 ? h("div", { className: "ba-dim" }, t("batch.noFiles"))
          : h("div", { className: "ba-row", role: "group" },
            h("button", { type: "button", "aria-pressed": active === null, onClick: choose(null) }, t("batch.filter.all", { n: files.length })),
            kinds.map((kind) => h("button", { key: kind, type: "button", "aria-pressed": active === kind, onClick: choose(kind) },
              t("batch.filter.one", { name: fmt.decision(kind), n: counts.get(kind) })))),
        files.length === 0 ? null
          : h("div", { className: "ba-wrap" }, h("table", { className: "ba-table" },
            h("thead", null, h("tr", null, FILE_COLUMNS.map((column) => h("th", { key: column }, t("batch.col." + column))))),
            h("tbody", null, rows))));
    }

    /** Строка пачки: время, файлы, итог по решениям, замечания, длительность; нажатие раскрывает подробности. */
    function BatchRow({ batch, entry, previews, open, fmt, t, store }) {
      const whole = (value) => Number.isInteger(value) && value >= 0;
      const summary = decisionSummary(batch.counts, fmt);
      const took = formatDuration(batch.seconds);
      const plural = (key, n) => t(key + (n === 1 ? ".one" : ".many"), { n });
      return h("li", { className: "ba-batch", "data-batch": batch.id },
        h("button", { type: "button", className: "ba-batch-head", "aria-expanded": open, title: batch.id, onClick: () => store.toggleBatch(batch.id) },
          h("span", null, formatTime(batch.time)),
          whole(batch.files) ? h("span", null, plural("batch.files", batch.files)) : null,
          summary !== "" ? h("span", null, summary) : null,
          whole(batch.problems) ? h("span", { className: batch.problems > 0 ? "ba-bad" : "ba-dim" }, plural("batch.problems", batch.problems)) : null,
          took !== null ? h("span", { className: "ba-dim" }, t("batch.took", { time: took })) : null),
        open ? h(BatchDetail, { entry, previews, fmt, t, store }) : null);
    }

    /** «Batches»: история пачек разбора от новой к старой, по 30. Свои сбои показывает у себя, остальную вкладку не трогает. */
    function BatchesSection({ history, previews, fmt, t, store }) {
      const { items, more, loading, error, open, details } = history;
      return h("div", { className: "ba-box", "data-section": "batches" },
        h("h3", null, t("batches.heading")),
        error !== null ? h("div", { className: "ba-error", role: "alert" }, describe(error, fmt, t)) : null,
        items === null ? (error === null ? h("div", { className: "ba-dim" }, t("loading")) : null)
          : items.length === 0 ? h("div", { className: "ba-dim" }, t("batches.empty"))
            : h("ul", { className: "ba-list" }, items.map((batch) => h(BatchRow, {
              key: batch.id, batch, entry: has(details, batch.id) ? details[batch.id] : null, previews, open: open.includes(batch.id), fmt, t, store
            }))),
        items !== null && more
          ? h("div", { className: "ba-row" }, h("button", { type: "button", disabled: loading, onClick: () => store.loadMoreBatches() }, t("batches.more")))
          : null);
    }

    /** Тело вкладки: ошибка, строка состояния, список решений, история пачек. Данные берёт из общего хранилища. */
    function ArchiveTab({ store, t }) {
      const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
      useEffect(() => {
        ensureStyles();
      }, []);
      const fmt = formatterOf(snap.messages);
      return h("div", { className: "ba-section ba-tab" },
        snap.error !== null ? h("div", { className: "ba-error", role: "alert" }, describe(snap.error, fmt, t)) : null,
        h(StatusBox, { status: snap.status, busy: snap.busy, fmt, t, onRun: () => store.runNow() }),
        h(DecisionList, { snap, fmt, t, store }),
        h(BatchesSection, { history: snap.history, previews: snap.previews, fmt, t, store }));
    }

    /** Значок вкладки: коробка архива, линиями в цвет текста. */
    function ArchiveIcon({ size }) {
      return h("svg", { width: size || 16, height: size || 16, viewBox: "0 0 16 16", fill: "none", "aria-hidden": "true" },
        h("path", { d: "M2 3.5h12v2.5H2z", stroke: "currentColor" }),
        h("path", { d: "M3 6v6.5h10V6", stroke: "currentColor" }),
        h("path", { d: "M6.5 9h3", stroke: "currentColor" }));
    }

    /** Сколько файлов ждёт решения: поле attention состояния (очередь плюс карантин); у старого сервера без него — сумма счётчиков. Негодное — 0. */
    function attentionOf(status) {
      if (!isObject(status)) return 0;
      const whole = (value) => Number.isInteger(value) && value >= 0;
      if (whole(status.attention)) return status.attention;
      return (whole(status.queue) ? status.queue : 0) + (whole(status.quarantine) ? status.quarantine : 0);
    }

    /** Сколько документов ждёт описания изображений: целое больше нуля из vision_pending; ноль, нет ключа (старый архив) и негодное — 0. */
    function awaitingOf(status) {
      return isObject(status) && Number.isSafeInteger(status.vision_pending) && status.vision_pending > 0 ? status.vision_pending : 0;
    }

    /** Заголовок вкладки в панели: значок и название из словаря; «Archive (N)», когда есть файлы на решение. Число — из общего хранилища. */
    function ArchiveTitle({ store, t }) {
      const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
      const waiting = attentionOf(snap.status);
      return h("span", { className: "ba-tab-title" }, h(ArchiveIcon, { size: 16 }), waiting > 0 ? t("title.count", { n: waiting }) : t("title"));
    }

    /** Запись вкладки в путеводителе панели: карточка, нажатие открывает вкладку на месте путеводителя. */
    function ArchiveGuide({ kind, title, description, useTabInfo }) {
      const { tab } = useTabInfo();
      return h("div", { className: "ba-guide", "data-sidebar-right-guide-entry": kind },
        h("button", {
          type: "button",
          className: "ba-guide-main",
          "aria-label": description === undefined ? title : title + " " + description,
          onClick: () => tab.actions.openTab(kind, { replaceTab: true })
        }),
        h("span", { className: "ba-guide-icon", "aria-hidden": "true" }, h(ArchiveIcon, { size: 22 })),
        h("span", { className: "ba-guide-text" },
          h("span", { className: "ba-guide-title", "aria-hidden": "true" }, title),
          description === undefined ? null : h("span", { className: "ba-guide-desc", "aria-hidden": "true" }, description)));
    }

    // ── раздел «Archive» в настройках (FR-84) ───────────────────────────────────
    // Состав и порядок: Status, Folders, Intake, Tokens, Delete document, Access journal. Очереди и карантина здесь нет: они на вкладке.
    // Состояние (строка Status, папки, параметры разбора) раздел берёт из общего хранилища вкладки и сам сервер не опрашивает;
    // свои обращения у него только за токенами и журналом и за действиями (сохранить, сменить папку, выпустить, удалить).
    const TOKEN_STATES = { "действует": "active", "отозван": "revoked", "просрочен": "expired" }; // слова состояния, как их отдаёт сервер (данные) → ключи словаря
    const TOKEN_LEVELS = ["read", "full", "local"];

    /** Токены клиентов. Отозвать можно действующий токен клиента; служебный токен DSH — нельзя. */
    function TokensTable({ tokens, onRevoke, busy, t }) {
      const [confirming, setConfirming] = useState(null);
      if (tokens === null) return h("div", { className: "ba-dim" }, t("loading"));
      if (tokens.length === 0) return h("div", { className: "ba-dim" }, t("tokens.none"));
      const stateKey = (token) => (has(TOKEN_STATES, token.state) ? TOKEN_STATES[token.state] : null);
      const action = (token) => {
        if (stateKey(token) !== "active" || token.level === "local") return null;
        if (confirming !== token.name) {
          return h("button", { type: "button", disabled: busy, onClick: () => setConfirming(token.name) }, t("tokens.revoke"));
        }
        return h("span", { className: "ba-row" },
          h("button", { type: "button", className: "ba-bad", disabled: busy, onClick: () => { setConfirming(null); onRevoke(token.name); } }, t("tokens.revoke.confirm")),
          h("button", { type: "button", disabled: busy, onClick: () => setConfirming(null) }, t("cancel")));
      };
      const columns = ["name", "level", "created", "expires", "lastUsed", "state"];
      return h("div", { className: "ba-wrap" }, h("table", { className: "ba-table" },
        h("thead", null, h("tr", null, [...columns.map((column) => t("tokens.col." + column)), ""].map((title, i) => h("th", { key: i }, title)))),
        h("tbody", null, tokens.map((token, i) => h("tr", { key: token.name + ":" + i },
          h("td", null, token.name),
          h("td", null, TOKEN_LEVELS.includes(token.level) ? t("token.level." + token.level) : token.level),
          h("td", null, formatTime(token.created, "—", true)),
          h("td", null, formatTime(token.expires, t("tokens.noExpiry"), true)),
          h("td", null, formatTime(token.last_used, t("tokens.neverUsed"))),
          h("td", { className: stateKey(token) === "active" ? undefined : "ba-dim" }, stateKey(token) !== null ? t("token.state." + stateKey(token)) : token.state),
          h("td", null, action(token)))))));
    }

    /** Форма выпуска. Проверяет сервер; здесь только сбор значений. */
    function IssueForm({ onIssue, busy, t }) {
      const [name, setName] = useState("");
      const [level, setLevel] = useState("read");
      const [days, setDays] = useState("90");
      const submit = (event) => {
        event.preventDefault();
        const trimmed = days.trim();
        onIssue(name.trim(), level, trimmed === "" ? null : Number(trimmed));
        setName("");
      };
      return h("form", { className: "ba-row", onSubmit: submit },
        h("label", null, t("tokens.name"),
          h("input", { name: "name", value: name, placeholder: t("tokens.placeholder"), maxLength: 64, autoComplete: "off", onChange: (e) => setName(e.target.value) })),
        h("label", null, t("tokens.level"),
          h("select", { name: "level", value: level, onChange: (e) => setLevel(e.target.value) },
            h("option", { value: "read" }, t("tokens.option.read")),
            h("option", { value: "full" }, t("tokens.option.full")))),
        h("label", null, t("tokens.days"),
          h("input", { name: "days", value: days, inputMode: "numeric", size: 6, autoComplete: "off", onChange: (e) => setDays(e.target.value) })),
        h("button", { type: "submit", disabled: busy || name.trim() === "" }, t("tokens.issueButton")));
    }

    /** Только что выпущенный токен. Значение живёт в памяти страницы до нажатия «Dismiss». */
    function IssuedToken({ issued, onDismiss, t }) {
      const [copied, setCopied] = useState(false);
      const copy = () => {
        if (typeof navigator !== "undefined" && navigator.clipboard) {
          navigator.clipboard.writeText(issued.token).then(() => setCopied(true), () => setCopied(false));
        }
      };
      const level = TOKEN_LEVELS.includes(issued.level) ? t("token.level." + issued.level) : issued.level;
      return h("div", { className: "ba-box", role: "status" },
        h("div", null, t("tokens.issued", { name: issued.name, level })),
        h("div", { className: "ba-token" }, issued.token),
        h("div", { className: "ba-row" },
          h("button", { type: "button", onClick: copy }, copied ? t("copied") : t("tokens.copy")),
          h("button", { type: "button", onClick: onDismiss }, t("tokens.dismiss"))));
    }

    /** Журнал обращений, свежие записи сверху. */
    function JournalTable({ records, t }) {
      if (records === null) return h("div", { className: "ba-dim" }, t("loading"));
      if (records.length === 0) return h("div", { className: "ba-dim" }, t("journal.none"));
      const params = (record) => Object.entries(record.params || {}).map(([key, value]) => key + "=" + value).join(" ");
      const from = (record) => [record.via, record.ip].filter(Boolean).join(" ");
      return h("div", { className: "ba-wrap" }, h("table", { className: "ba-table" },
        h("thead", null, h("tr", null, ["time", "client", "service", "tool", "outcome", "from", "params"].map((column) => h("th", { key: column }, t("journal.col." + column))))),
        h("tbody", null, [...records].reverse().map((record, i) => h("tr", { key: i },
          h("td", null, formatTime(record.ts)),
          h("td", null, record.client),
          h("td", null, record.server),
          h("td", null, record.tool),
          h("td", { className: record.outcome === "ok" ? undefined : "ba-bad" }, record.status + " " + record.outcome),
          h("td", { className: "ba-dim" }, from(record)),
          h("td", { className: "ba-wide" }, params(record)))))));
    }

    /** Удаление документа из архива и индекса: путь из выдачи поиска и подтверждение вторым нажатием. */
    function DeleteDoc({ onDelete, busy, t }) {
      const [path, setPath] = useState("");
      const [confirming, setConfirming] = useState(false);
      const ready = path.trim() !== "";
      return h("div", { className: "ba-row" },
        h("label", null, t("delete.path"),
          h("input", { name: "path", value: path, size: 48, autoComplete: "off", placeholder: t("delete.placeholder"),
            onChange: (e) => { setPath(e.target.value); setConfirming(false); } })),
        confirming
          ? h("span", { className: "ba-row" },
            h("button", { type: "button", className: "ba-bad", disabled: busy || !ready,
              onClick: () => { setConfirming(false); onDelete(path.trim()); setPath(""); } }, t("delete.confirm")),
            h("button", { type: "button", disabled: busy, onClick: () => setConfirming(false) }, t("cancel")))
          : h("button", { type: "button", disabled: busy || !ready, onClick: () => setConfirming(true) }, t("delete.button")));
    }

    /** Итог последней пачки одной строкой: «Last batch N: accepted 11, needs review 2» (нулевые решения не пересказываются). */
    function lastBatchLine(status, fmt, t) {
      const last = isObject(status.last_batch) ? status.last_batch : null;
      if (last === null) return t("status.noBatches");
      const known = fmt.decisions();
      const order = known.length > 0 ? known : Object.keys(last).filter((key) => key !== "batch" && key !== "time");
      const summary = order.filter((key) => Number.isInteger(last[key]) && last[key] > 0).map((key) => fmt.decision(key) + " " + last[key]).join(", ");
      return summary !== "" ? t("status.lastBatch", { batch: last.batch, summary }) : t("status.lastBatchEmpty", { batch: last.batch });
    }

    /**
     * Status: одна строка — сколько ждёт во входящей папке, сколько файлов ждёт решения, сколько документов ждёт описания (когда есть),
     * итог последней пачки — и подсказка про вкладку.
     */
    function SettingsStatus({ snap, fmt, t }) {
      const { status, error } = snap;
      const failed = error !== null ? h("div", { className: "ba-error", role: "alert" }, describe(error, fmt, t)) : null;
      if (status === null) return h("div", { className: "ba-box", "data-section": "status" }, h("div", { className: "ba-dim" }, t("loading")), failed);
      const awaiting = awaitingOf(status) > 0 ? [t("settings.status.vision", { n: awaitingOf(status) })] : []; // ноль и негодное не показываются
      const line = [t("status.waiting", { n: status.waiting }), t("settings.status.attention", { n: attentionOf(status) }), ...awaiting, lastBatchLine(status, fmt, t)].join(" · ");
      return h("div", { className: "ba-box", "data-section": "status" },
        h("div", null, line),
        h("div", { className: "ba-dim" }, t("settings.status.hint")),
        failed);
    }

    /** Папка только для чтения: подпись, адрес и «Copy path». */
    function PathRow({ label, value, t }) {
      const [copied, setCopied] = useState(null);
      const copy = () => {
        const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
        if (!clipboard || typeof clipboard.writeText !== "function") return setCopied("failed");
        Promise.resolve().then(() => clipboard.writeText(value)).then(() => setCopied("done"), () => setCopied("failed"));
      };
      return h("div", { className: "ba-item-main" },
        h("div", { className: "ba-row" },
          h("span", { className: "ba-dim" }, t(label)),
          h("span", { className: "ba-token" }, value),
          h("button", { type: "button", onClick: copy }, copied === "done" ? t("copied") : t("copy"))),
        copied === "failed" ? h("div", { className: "ba-dim" }, t("copy.failed")) : null);
    }

    /**
     * Folders: входящая папка — поле и «Change». Перед сменой — вопрос с предупреждением, что архив заберёт всё, что лежит в новой папке;
     * правка адреса вопрос закрывает, чтобы подтверждался тот адрес, о котором предупредили. Отказ команды показан у поля.
     * Папка возврата и каталог архива — только чтение.
     * @param onChange - смена папки: обещание, которое отказывает ошибкой запроса.
     */
    function FoldersPanel({ status, onChange, busy, fmt, t }) {
      const [draft, setDraft] = useState(null); // правка адреса; null — не трогали
      const [asking, setAsking] = useState(false);
      const [failed, setFailed] = useState(null);
      if (status === null) return h("div", { className: "ba-box", "data-section": "folders" }, h("div", { className: "ba-dim" }, t("loading")));
      const current = typeof status.inbox === "string" ? status.inbox : "";
      const shown = draft === null ? current : draft;
      const path = shown.trim();
      const edit = (value) => {
        setDraft(value);
        setAsking(false);
        setFailed(null);
      };
      const proceed = () => {
        setAsking(false);
        setFailed(null);
        Promise.resolve(onChange(path)).then(() => setDraft(null), (error) => setFailed(failureOf(error)));
      };
      return h("div", { className: "ba-box", "data-section": "folders" },
        h("div", { className: "ba-row" },
          h("label", null, t("folders.inbox"),
            h("input", { name: "inbox", value: shown, size: 48, autoComplete: "off", onChange: (e) => edit(e.target.value) })),
          asking ? null : h("button", { type: "button", disabled: busy || path === "" || path === current, onClick: () => setAsking(true) }, t("folders.change"))),
        asking
          ? h("div", { className: "ba-item-main", role: "group" },
            h("div", null, t("folders.warning")),
            h("div", { className: "ba-row" },
              h("button", { type: "button", className: "ba-bad", disabled: busy, onClick: proceed }, t("folders.confirm")),
              h("button", { type: "button", onClick: () => setAsking(false) }, t("cancel"))))
          : null,
        failed !== null ? h("div", { className: "ba-error", role: "alert" }, describe(failed, fmt, t)) : null,
        typeof status.returned === "string" && status.returned !== "" ? h(PathRow, { label: "folders.returned", value: status.returned, t }) : null,
        typeof status.home === "string" && status.home !== "" ? h(PathRow, { label: "folders.home", value: status.home, t }) : null);
    }

    /**
     * Intake: период таймера, порог баллов, пределы распаковки, проверка моделью, запасная облачная модель и описание изображений
     * локальной моделью (флажок и два числа, доступные только при включённом флажке) — с «Save».
     */
    function IntakePanel({ status, onSave, busy, t }) {
      const [draft, setDraft] = useState(null);
      if (status === null) return h("div", { className: "ba-box", "data-section": "intake" }, h("div", { className: "ba-dim" }, t("loading")));
      const value = (key) => (draft && key in draft ? draft[key] : status[key]);
      const change = (key, v) => setDraft({ ...(draft || {}), [key]: v });
      const visionOn = value("vision") === true;
      const isNumber = (key) => has(INTAKE_NUMBERS, key);
      // Числовое поле в черновике — набранный текст, число из него делается при сохранении: стёртое поле остаётся пустым, дописать можно.
      // Пустое или не число поле называется под полями, а Save не нажимается: негодное значение на сервер не уходит.
      // Поле описания изображений при выключенном флажке править нечем: если оно не заполнено, то ни Save не держит, ни на сервер не идёт.
      const idle = (key) => VISION_NUMBERS.includes(key) && !visionOn;
      const edited = draft === null ? [] : Object.keys(draft).filter((key) => !(isNumber(key) && idle(key) && !Number.isFinite(countOf(draft[key]))));
      const unfilled = Object.keys(INTAKE_NUMBERS).filter((key) => edited.includes(key) && !Number.isFinite(countOf(draft[key])));
      // чего нет в состоянии (старый архив) или что не число, поле показывает пустым, а не словом «undefined» или «NaN»
      const number = (key, size, { disabled = false, integer = false } = {}) => h("label", null, t(INTAKE_NUMBERS[key]),
        h("input", { name: key, value: draft !== null && typeof draft[key] === "string" ? draft[key] : Number.isFinite(status[key]) ? String(status[key]) : "",
          inputMode: integer ? "numeric" : "decimal", size, autoComplete: "off", disabled,
          "aria-invalid": unfilled.includes(key) ? "true" : undefined, onChange: (e) => change(key, e.target.value) }));
      // выбранное сохраняется; отказ сервера оставляет введённое на месте
      const save = () => Promise.resolve(onSave(Object.fromEntries(edited.map((key) => [key, isNumber(key) ? countOf(draft[key]) : draft[key]]))))
        .then((saved) => { if (saved !== false) setDraft(null); });
      return h("div", { className: "ba-box", "data-section": "intake" },
        status.timer ? null : h("div", { className: "ba-bad" }, t("status.noTimer")),
        h("div", { className: "ba-row" },
          h("label", null, t("intake.period"),
            h("select", { name: "period", value: String(value("period")), onChange: (e) => change("period", Number(e.target.value)) },
              PERIODS.map((p) => h("option", { key: p, value: String(p) }, t("intake.minutes", { n: p }))))),
          number("threshold", 4),
          h("label", { className: "ba-check" },
            h("input", { type: "checkbox", name: "llm", checked: value("llm") === true, onChange: (e) => change("llm", e.target.checked) }),
            t("intake.llm")),
          h("label", { className: "ba-check" },
            h("input", { type: "checkbox", name: "cloud", checked: value("cloud") === true, disabled: value("llm") !== true,
              onChange: (e) => change("cloud", e.target.checked) }),
            t("intake.cloud"))),
        value("llm") === true && value("cloud") === true ? h("div", { className: "ba-dim" }, t("intake.cloud.warning")) : null,
        h("div", { className: "ba-row" },
          h("label", { className: "ba-check" },
            h("input", { type: "checkbox", name: "vision", checked: visionOn, onChange: (e) => change("vision", e.target.checked) }),
            t("intake.vision")),
          number("vision_pages", 5, { disabled: !visionOn, integer: true }),
          number("vision_minutes", 5, { disabled: !visionOn, integer: true })),
        visionOn ? h("div", { className: "ba-dim" }, t("intake.vision.hint")) : null,
        h("div", { className: "ba-row" },
          number("max_gb", 5), number("max_files", 7), number("max_ratio", 5), number("depth", 3)),
        unfilled.length > 0
          ? h("div", { className: "ba-error", role: "alert" }, t("intake.fillIn", { fields: unfilled.map((key) => t(INTAKE_NUMBERS[key])).join("; ") }))
          : null,
        h("div", { className: "ba-row" },
          h("button", { type: "button", disabled: busy || edited.length === 0 || unfilled.length > 0, onClick: save }, t("save"))));
    }

    /**
     * Раздел настроек целиком. Состояние входящих — из общего хранилища (store); своих запросов за ним нет: после действия раздел
     * просит хранилище обновиться (store.refresh), а не читает сам.
     */
    function ArchiveSection({ client, store, t }) {
      const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
      const fmt = formatterOf(snap.messages);
      const [tokens, setTokens] = useState(null);
      const [records, setRecords] = useState(null);
      const [filter, setFilter] = useState("");
      const [error, setError] = useState(null);
      const [notice, setNotice] = useState(null);
      const [issued, setIssued] = useState(null);
      const [pending, setPending] = useState(0);
      const guarded = useCallback(async (work) => {
        setPending((n) => n + 1);
        setError(null);
        try {
          await work();
          return true;
        } catch (failed) {
          setError(failureOf(failed));
          return false;
        } finally {
          setPending((n) => n - 1);
        }
      }, []);
      const loadTokens = useCallback(() => guarded(async () => setTokens(await client.tokens())), [client, guarded]);
      const loadJournal = useCallback(() => guarded(async () => setRecords(await client.journal(filter, JOURNAL_ROWS))), [client, guarded, filter]);
      useEffect(() => {
        ensureStyles();
        loadTokens();
      }, [loadTokens]);
      useEffect(() => {
        loadJournal();
      }, [loadJournal]);
      const issue = (name, level, days) => guarded(async () => {
        setIssued(await client.issue(name, level, days));
        setTokens(await client.tokens());
      });
      const revoke = (name) => guarded(async () => {
        await client.revoke(name);
        setIssued((shown) => (shown && shown.name === name ? null : shown));   // отозванный токен на экране ни к чему
        setTokens(await client.tokens());
        setRecords(await client.journal(filter, JOURNAL_ROWS));
      });
      const saveInbox = (patch) => guarded(async () => {
        await client.setInbox(patch);
        await store.refresh();
        setNotice(t("settings.saved"));
      });
      // смена папки: отказ идёт к полю (его показывает FoldersPanel), а не наверх раздела
      const changeInbox = async (path) => {
        setPending((n) => n + 1);
        setError(null);
        setNotice(null);
        try {
          await client.setInboxPath(path);
          await store.refresh();
          setNotice(t("folders.changed", { path }));
        } finally {
          setPending((n) => n - 1);
        }
        loadJournal(); // смена пишется в журнал обращений; сбой чтения журнала к смене папки не относится
      };
      const deleteDoc = (path) => guarded(async () => {
        const result = await client.deleteDoc(path);
        setNotice(t("settings.deleted", { rows: result.rows, moved: result.moved_to }));
        await store.refresh();
        setRecords(await client.journal(filter, JOURNAL_ROWS));
      });
      const busy = pending > 0;
      return h("div", { className: "ba-section" },
        h("h2", null, t("title")),
        h("p", { className: "ba-note" }, t("settings.intro")),
        error !== null ? h("div", { className: "ba-error", role: "alert" }, describe(error, fmt, t)) : null,
        notice ? h("div", { className: "ba-dim", role: "status" }, notice) : null,
        h("div", { className: "ba-row" },
          h("button", { type: "button", disabled: busy, onClick: () => { setNotice(null); loadTokens(); loadJournal(); store.refresh(); } }, t("refresh"))),
        h("h3", null, t("settings.status")),
        h(SettingsStatus, { snap, fmt, t }),
        h("h3", null, t("settings.folders")),
        h(FoldersPanel, { status: snap.status, onChange: changeInbox, busy, fmt, t }),
        h("h3", null, t("settings.intake")),
        h(IntakePanel, { status: snap.status, onSave: saveInbox, busy, t }),
        h("h3", null, t("settings.tokens")),
        h("div", { className: "ba-part", "data-section": "tokens" },
          issued ? h(IssuedToken, { issued, onDismiss: () => setIssued(null), t }) : null,
          h(TokensTable, { tokens, onRevoke: revoke, busy, t }),
          h("div", { className: "ba-dim" }, t("tokens.issue")),
          h(IssueForm, { onIssue: issue, busy, t })),
        h("h3", null, t("settings.delete")),
        h("div", { className: "ba-part", "data-section": "delete" },
          h("p", { className: "ba-note" }, t("delete.note")),
          h(DeleteDoc, { onDelete: deleteDoc, busy, t })),
        h("h3", null, t("settings.journal")),
        h("div", { className: "ba-part", "data-section": "journal" },
          h("div", { className: "ba-row" },
            h("label", null, t("journal.client"),
              h("select", { name: "client", value: filter, onChange: (e) => setFilter(e.target.value) },
                h("option", { value: "" }, t("journal.all")),
                clientsOf(records, tokens).map((name) => h("option", { key: name, value: name }, name === "-" ? t("journal.noToken") : name))))),
          h(JournalTable, { records, t })));
    }

    // ── сообщение об окончании пачки (FR-86) ────────────────────────────────────
    const TOAST_HOLD_MS = 8000; // сколько сообщение держится в полную силу; после него Toast ещё гаснет и сам сообщает onDone

    /**
     * Сообщение в собственном корне React плагина: Toast из примитивов DSH по событию общего хранилища (store.notice).
     * panel — что плагин знает о правой панели: есть ли сеанс на экране (подписка и чтение) и как открыть вкладку.
     */
    function ToastHost({ store, t, Toast, panel }) {
      const snap = useSyncExternalStore(store.subscribe, store.getSnapshot);
      const sessionShown = useSyncExternalStore(panel.subscribe, panel.available);
      const notice = snap.notice;
      if (notice === null) return null;
      const done = () => store.dismissNotice(notice.id);
      const open = () => {
        try {
          panel.open();
        } catch (error) {
          console.error("flyarchive: could not open the Archive tab", error);
        }
        done();
      };
      const parts = [];
      if (notice.attention > 0) parts.push(t("toast.attention." + (notice.attention === 1 ? "one" : "many"), { n: notice.attention }));
      if (notice.problems > 0) parts.push(t("toast.problems." + (notice.problems === 1 ? "one" : "many"), { n: notice.problems }));
      const actions = [];
      if (sessionShown) actions.push({ label: t("toast.open"), onClick: open }); // без сеанса вкладку открывать негде
      actions.push({ label: t("toast.close"), onClick: done });
      return h(Toast, { key: notice.id, text: t("toast.text", { parts: parts.join(", ") }), holdMs: TOAST_HOLD_MS, actions, onDone: done });
    }

    /**
     * Поднимает собственный корень React на элементе в document.body и рисует в нём ToastHost. Нет Toast, createRoot или document —
     * плагин работает без сообщения. Возвращает снятие: корень и элемент уходят вместе с плагином.
     */
    function mountToast({ platform, store, t, panel }) {
      const Toast = platform.primitives ? platform.primitives.Toast : null;
      const createRoot = platform.dom ? platform.dom.createRoot : null;
      if (typeof Toast !== "function" || typeof createRoot !== "function") return () => {};
      if (typeof document === "undefined" || !document.body || typeof document.createElement !== "function") return () => {};
      let container = null;
      let root = null;
      const dispose = (report) => {
        try {
          if (root !== null) root.unmount();
        } catch (error) {
          if (report) console.error("flyarchive: could not remove the message root", error);
        }
        if (container !== null && container.parentNode) container.parentNode.removeChild(container);
      };
      try {
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        root.render(h(ToastHost, { store, t, Toast, panel }));
      } catch (error) {
        console.error("flyarchive: the Archive messages are unavailable", error);
        dispose(false); // корень, который не смог нарисовать, вряд ли снимется: одна запись в консоли достаточно
        return () => {};
      }
      return () => dispose(true);
    }

    /** Что плагин знает о правой панели для сообщения: сеанс на экране и открытие вкладки. Служб панели нет — сеанса «нет». */
    function panelOf(ctx) {
      const side = ctx.sidebarRight;
      const mounted = side && side.mounted ? side.mounted : null;
      return {
        subscribe: (listener) => (mounted !== null && typeof mounted.subscribe === "function" ? mounted.subscribe(listener) : () => {}),
        available: () => {
          try {
            return mounted !== null && mounted.getSnapshot() !== undefined;
          } catch (_error) {
            return false;
          }
        },
        open: () => side.openTab(TAB_KIND)
      };
    }

    /**
     * Регистрирует вкладку так же, как DSH регистрирует Files и Terminal: тип и три места панели.
     * Хранилище одно и принадлежит плагину; его опрос, словарь сообщений и сообщение — корневые эффекты (install), а не часть вкладки.
     */
    function registerTab(ctx, store, t) {
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: TAB_ID,
        kind: TAB_KIND,
        multiple: false,
        keepMounted: true,
        title: () => t("title"),
        guide: [{ id: "open", order: 40, title: () => t("title"), description: () => t("guide.description"), icon: ArchiveIcon }]
      }), "flyarchive.type");
      // t приходит от DSH (следит за сменой языка); без него — связанный словарь
      const seat = (name, component) => ctx.effect(() => ctx.slots.inject(name, () => ctx.slots.register({
        name,
        key: TAB_ID,
        locale: NAMESPACE
      }, component)), "flyarchive." + name);
      seat("sidebar.right.tab.guide.entry", (props) => h(ArchiveGuide, props));
      seat("sidebar.right.pane.tab", (props) => h(ArchiveTab, { store, t: props.t || t }));
      seat("sidebar.right.pane.tab.title", (props) => h(ArchiveTitle, { store, t: props.t || t }));
    }

    /** Службы DSH, нужные странице: места интерфейса, язык и правая панель (она же реестр типов вкладок). */
    const inject = ["slots", "locale", "sidebarRight", "sidebarRightTabs"];

    /**
     * Ставит раздел «Архив» в настройки, как только оболочка настроек объявит место для разделов,
     * и вкладку «Archive» в правую панель, если у DSH есть её службы; без них остаётся раздел настроек.
     * @param ctx - контекст плагина в браузере.
     * @param env - fetch, таймеры и видимость страницы; подменяются в тестах.
     */
    function install(ctx, env) {
      ensureStyles(); // сразу: карточка в путеводителе панели рисуется раньше, чем открыто тело вкладки
      const client = createClient(env.fetch);
      const store = createStore({ client, timers: env.timers, page: env.page });
      // без службы языка подписи берутся из того же словаря напрямую: раздел остаётся английским
      const t = ctx.locale ? ctx.locale.bind(NAMESPACE) : (key, params) => fillTemplate(has(EN, key) ? EN[key] : key, params);
      if (ctx.locale && typeof ctx.effect === "function") ctx.effect(() => ctx.locale.register(NAMESPACE, { zh: { ...EN }, en: { ...EN } }), "flyarchive.copy");
      ctx.slots.inject("settings.section", () => ctx.slots.register({
        name: "settings.section",
        id: "flyarchive",
        order: 30,
        label: () => t("title")
      }, () => h(ArchiveSection, { client, store, t })));
      if (typeof ctx.effect === "function") {
        // опрос один и идёт, пока плагин жив: он нужен и вкладке, и заголовку, и сообщению, и разделу настроек, а вкладки может и не быть
        ctx.effect(() => {
          store.start();
          return () => store.stop();
        }, "flyarchive.poll");
        ctx.effect(() => {
          let alive = true;
          require.async("./client.messages.js").then((module) => {
            if (alive) store.setMessages(module);
          }, (error) => {
            console.error("flyarchive: the message dictionary client.messages.js did not load; the screen will show the texts the server sent", error);
          });
          return () => {
            alive = false;
          };
        }, "flyarchive.messages");
        ctx.effect(() => mountToast({ platform, store, t, panel: panelOf(ctx) }), "flyarchive.toast");
      }
      if (ctx.locale && ctx.sidebarRight && ctx.sidebarRightTabs) registerTab(ctx, store, t);
    }

    /** Видимость страницы по document.hidden; без document (не браузер) страница считается видимой. */
    function pageOf(doc) {
      return {
        isVisible: () => !doc || !doc.hidden,
        subscribe(listener) {
          if (!doc) return () => {};
          doc.addEventListener("visibilitychange", listener);
          return () => doc.removeEventListener("visibilitychange", listener);
        }
      };
    }

    function apply(ctx) {
      install(ctx, {
        fetch: (route, init) => fetch(route, init),
        timers: { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (id) => clearTimeout(id) },
        page: pageOf(typeof document === "undefined" ? undefined : document)
      });
    }

    exports.apply = apply;
    exports.inject = inject;
    exports.parts = { createClient, clientsOf, formatTime, DeleteDoc, TokensTable, IssueForm, IssuedToken, JournalTable, SettingsStatus, FoldersPanel,
      IntakePanel, ArchiveSection, createStore, ArchiveTab, ArchiveTitle, ToastHost, install, EN };
    return module.exports;
  }
});
