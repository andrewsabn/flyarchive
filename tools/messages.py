"""Каталог сообщений системы (FR-73а): код, русский шаблон и имена параметров каждого сообщения.

    messages.make("review.no_file", path=путь) -> Message

Сообщение — строка с русским текстом, поэтому сравнение, `in`, `startswith`, печать и `str()` дают этот текст, как раньше.
Кроме текста оно несёт `code` и `args`; `to_json()` отдаёт `{"code", "args", "text"}` — по коду плагин показывает свою английскую фразу.

Неизвестный код, лишний или недостающий параметр, значение не из строки, числа, булева и null — `MessageError` при сборке.
Код — латиницей, вида `область.что_случилось`; имена параметров — латиницей.

Отказ команды — `CodedError` (от него наследуют InboxError, ReviewError и прочие): `str(e)` — русский текст, `e.message` — сообщение.
`of(e)` — сообщение любого исключения: своё, если оно из каталога, иначе общее `generic.text` с текстом исключения как есть.

    python3 tools/messages.py --json     {код: [имена параметров]} — по нему плагин сверяет свой английский словарь
    python3 tools/messages.py --words    {код: {параметр: {код вида: русское слово}}} — слова системных видов
    python3 tools/messages.py --rules    [правило, …] — все правила находок; причины и находки — коды `reason.*`, `finding.*` из --json

Системные слова (вид программы, вид внедрения указаний, вид секрета) в параметрах не живут: параметр хранит код вида
(`kind: "pe"`), русское слово для текста сообщения берётся из таблицы WORDS, а английское подставляет плагин. Всё, что
взято из документа, ошибки библиотеки или слов модели, идёт параметром как есть.

Находки (FR-73б): `finding.*` — что нашлось, `where.*` — где, `reason.*` — причина решения по файлу, `unpack.*` — отказ
распаковки, `note.*` — примечание к архиву. Находка модели — единственный код без области, `llm_finding`.
Если quote находки — цитата документа, в args стоит `quoted: true` и `excerpt` — та же цитата без слов перед ней (quote — «слова: цитата»);
у описания их нет, а текст сообщения — это quote.
"""
import math
import sys

# код -> (русский шаблон для str.format, имена параметров по порядку). Шаблон берёт только объявленные параметры.
CATALOG = {
    "generic.text": ("{text}", ("text",)),

    # отказы входящей папки
    "inbox.busy": ("разбор входящих уже идёт", ()),
    "inbox.folder_missing": ("входящей папки нет: {path}", ("path",)),
    "inbox.bad_period": ("период разбора: {periods} минут", ("periods",)),
    "inbox.bad_switch": ("llm и cloud: on или off", ()),
    "inbox.bad_int": ("{key}: целое число от {low} до {high}", ("key", "low", "high")),
    "inbox.bad_number": ("{key}: число от {low} до {high}", ("key", "low", "high")),
    "inbox.folder_inside_archive": ("входящая папка не может лежать внутри архива или содержать его ({archive}): "
                                    "до приёмки в архив ничего не попадает", ("archive",)),
    "inbox.folder_too_wide": ("входящей папкой не может быть корень или домашний каталог: {path}", ("path",)),

    # база известного
    "known.not_built": ("база сверки с архивом не построена — без неё в архив пойдут дубликаты. "
                        "Выполни: flyarchive known build (для нового архива — flyarchive init)", ()),
    "known.db_missing": ("базы известного нет: {path}. Собери её: flyarchive known build", ("path",)),
    # письма, ключи которых не прочитаны из-за отсутствующей библиотеки: повторы таких писем не узнаются, пока её нет
    "known.mail_not_read": ("писем без ключей сверки: {count} — нет библиотеки {package}, повторы этих писем не узнаются. "
                            "Поставь её и повтори: flyarchive known build", ("count", "package")),

    # таблица индекса (FR-103): её нет на чистом архиве, заводит её только flyarchive init
    "index.no_table": ("таблицы индекса нет: выполни flyarchive init", ()),

    # замечания прохода
    "problem.index_still": ("индекс: {rel} всё ещё не проиндексирован ({why})", ("rel", "why")),
    "problem.settle_aborted": ("раскладка прервана ({error}) — сделанное записано в квитанции, "
                               "остальное осталось во входящей папке", ("error",)),
    "problem.intake_failed_returned": ("{name}: приёмка упала ({why}) — запись возвращена в папку возврата", ("name", "why")),
    "problem.intake_failed_kept": ("{name}: приёмка упала ({why}), вернуть запись не удалось ({os_error}) — "
                                   "она осталась во входящей папке", ("name", "why", "os_error")),
    "problem.meta_not_written": ("файл замечаний и длительности не записан ({error}) — квитанции записаны", ("error",)),
    "problem.copy_mismatch": ("{name}: sha256 копии не совпал с исходником — исходник оставлен во входящей папке", ("name",)),
    "problem.not_placed": ("{name}: не разложен ({error}) — исходник оставлен во входящей папке", ("name", "error")),
    "problem.known_rejected": ("база известного не приняла пачку ({error}) — принятые документы оставлены "
                               "во входящей папке", ("error",)),
    "problem.index_failed": ("индекс: {rel} лежит в корпусе, но не проиндексирован ({why}); будет повтор", ("rel", "why")),
    "problem.source_not_removed": ("{name}: исходник не убран ({os_error}) — он остался во входящей папке", ("name", "os_error")),
    "problem.pending_failed": ("долги индексации не отданы ({error}) — документы лежат в корпусе, индексация будет в следующий проход",
                               ("error",)),
    "problem.index_no_table": ("индекс: {rel} лежит в корпусе, но таблицы индекса нет — документ остаётся в долгах индексации. "
                               "Выполни: flyarchive init", ("rel",)),

    # решения владельца
    "review.path_needed": ("нужен путь из списка", ()),
    "review.bad_path": ("путь должен быть вида {area}/<пачка>/<имя>", ("area",)),
    "review.no_file": ("нет такого файла: {path}", ("path",)),
    "review.target_exists": ("на месте назначения уже есть файл: {path}", ("path",)),
    "review.no_receipt": ("нет квитанции приёмки для {path}: принять нельзя", ("path",)),
    "review.sha_mismatch": ("sha256 файла не совпадает с квитанцией: {path} изменён после проверки", ("path",)),
    "review.return_exists": ("в папке возврата уже есть такой файл: {path}", ("path",)),
    "review.doc_path_needed": ("нужен путь документа, как в выдаче поиска", ()),
    "review.doc_path_outside": ("путь должен вести внутрь корпуса: {path}", ("path",)),
    "review.no_doc": ("нет такого документа в корпусе: {path}", ("path",)),
    "review.index_unavailable": ("индекс недоступен ({error_type}): документ не тронут", ("error_type",)),
    "review.move_failed": ("строки из индекса убраны, но файл перенести не удалось ({error_type}): {path}", ("error_type", "path")),
    "review.paths_none": ("нужен хотя бы один путь", ()),
    "review.paths_too_many": ("путей не больше {max} за раз, а их {count}", ("max", "count")),
    "review.path_repeated": ("путь назван дважды: {path}", ("path",)),

    # приёмка пачки
    "intake.no_source": ("источника нет: {path}", ("path",)),
    "intake.into_corpus": ("каталог разбора нельзя класть внутрь корпуса ({corpus}): до решения человека в индекс "
                           "ничего не попадает", ("corpus",)),
    "intake.into_source": ("каталог разбора нельзя класть внутрь источника", ()),
    "intake.into_not_empty": ("каталог разбора не пуст: {path}", ("path",)),

    # история пачек; value — номер пачки так, как его видит человек (repr)
    "batch.bad_id": ("номер пачки должен быть вида ГГГГММДД-ЧЧММСС, при совпадении с -N на конце: {value}", ("value",)),
    "batch.bad_limit": ("--limit: целое число от 1 до {max}", ("max",)),
    "batch.no_batch": ("нет такой пачки: {batch}", ("batch",)),

    # токены
    "token.store_open": ("права на {path} шире 600 — хранилище токенов открыто чужим, не использую", ("path",)),
    "token.store_broken": ("хранилище токенов {path} испорчено: {error}", ("path", "error")),
    "token.bad_name": ("имя клиента: от 1 до 64 букв, цифр и знаков . _ -", ()),
    "token.bad_level": ("уровень должен быть одним из: {levels}", ("levels",)),
    "token.name_taken": ("имя «{name}» занято действующим токеном — отзови его или выбери другое", ("name",)),
    "token.no_active": ("действующего токена с именем «{name}» нет", ("name",)),

    # вход
    "auth.secrets_dir_open": ("права на каталог {path} шире 700 — секреты открыты чужим", ("path",)),
    "auth.link_key_open": ("права на {path} шире 600 — ключ подписи ссылок открыт чужим", ("path",)),
    "auth.link_key_broken": ("ключ подписи ссылок {path} испорчен", ("path",)),

    # сама команда flyarchive
    "cli.local_token_open": ("права на {path} шире 600 — служебный токен открыт чужим", ("path",)),
    "cli.known_for_dedupe": ("нужна база известного: по ней узнаются повторы. Выполни: flyarchive known build", ()),
    "cli.known_for_dates": ("нужна база известного: в ней даты писем. Выполни: flyarchive known build", ()),
    "cli.confirm_needed": ("удаление документа из архива и индекса нужно подтвердить ключом --yes", ()),
    "cli.bad_switch": ("--{name}: on или off", ("name",)),
    "cli.bad_shell_name": ("имя оболочки: буквы, цифры, дефис и подчёркивание, до 40 знаков", ()),
    "cli.kick_no_systemd": ("разбор не запущен: systemd недоступен ({error_type})", ("error_type",)),
    "cli.kick_failed": ("разбор не запущен: {why}", ("why",)),
    "cli.kick_failed_silent": ("разбор не запущен: systemctl вернул ошибку", ()),

    # flyarchive init (FR-103): что сделано по шагам и что осталось владельцу
    "init.dir_created": ("создан каталог {path}", ("path",)),
    "init.dir_exists": ("каталог уже есть: {path}", ("path",)),
    "init.token_created": ("служебный токен выпущен и записан в {path}", ("path",)),
    "init.token_exists": ("служебный токен уже на месте: {path}", ("path",)),
    "init.known_created": ("база сверки с архивом создана: {path}", ("path",)),
    "init.known_exists": ("база сверки с архивом уже есть: {path}", ("path",)),
    "init.table_created": ("таблица индекса docs создана: {path} (размерность векторов {dim}), полнотекстовый индекс по тексту построен",
                           ("path", "dim")),
    "init.table_exists": ("таблица индекса docs уже есть, не тронута: {path}", ("path",)),
    "init.todo_preview": ("настроить просмотр Office и HTML: flyarchive preview setup", ()),
    "init.todo_model": ("задать локальную модель для проверки и описания: настройка llm_local_model (файл settings.json или переменная "
                        "FLYARCHIVE_LLM_LOCAL_MODEL) — или выключить проверку моделью: flyarchive inbox set --llm off "
                        "(без модели документы ждут решения человека в очереди)", ()),
    "init.todo_inbox": ("указать входящую папку: flyarchive inbox set --path ПАПКА", ()),
    "init.step_failed": ("шаг «{what}» не выполнен: {why}", ("what", "why")),

    # таймер входящих
    "inbox.timer_no_systemd": ("systemd недоступен ({error_type}): таймер записан, но не включён", ("error_type",)),
    "inbox.timer_failed": ("systemctl {command}: {why}", ("command", "why")),
    "inbox.timer_skipped": ("таймер не трогали: файлы служб не записаны, systemctl не вызывался. Разбор — вручную: flyarchive inbox run; "
                            "по расписанию его запускает flyarchive install или flyarchive inbox set без ключа --no-timer", ()),

    # обслуживание индекса flyarchive index optimize (FR-109): ход по шагам и отказы
    "optimize.busy": ("идёт разбор входящих: уплотнение при идущей записи не делается. Повтори, когда разбор закончится (flyarchive inbox status)", ()),
    "optimize.compact_warning": ("уплотнение убирает старые версии таблицы: не делай его при идущей записи — разборе входящих, приёме из очереди, "
                                 "описании картинок", ()),
    "optimize.before": ("до: строк {rows}, каталог индекса {mb} МБ", ("rows", "mb")),
    "optimize.after": ("после: строк {rows}, каталог индекса {mb} МБ", ("rows", "mb")),
    "optimize.fts_built": ("полнотекстовый индекс по тексту построен заново за {seconds} с", ("seconds",)),
    "optimize.ann_built": ("приближённый индекс по векторам построен на процессоре за {seconds} с: разбиений {partitions}, подвекторов {sub_vectors}",
                           ("seconds", "partitions", "sub_vectors")),
    "optimize.ann_built_gpu": ("приближённый индекс по векторам построен на видеокарте за {seconds} с: разбиений {partitions}, подвекторов {sub_vectors}",
                               ("seconds", "partitions", "sub_vectors")),
    "optimize.ann_small": ("приближённый индекс не нужен: в таблице строк {rows}, а пока их меньше {min}, перебор быстрее индекса", ("rows", "min")),
    "optimize.gpu_failed": ("видеокарта не подошла ({why}): приближённый индекс строится на процессоре", ("why",)),
    "optimize.kept": ("старые версии таблицы не тронуты (версий {versions}); уплотнить таблицу и убрать их: flyarchive index optimize --compact",
                      ("versions",)),
    "optimize.compacted": ("таблица уплотнена: фрагментов было {fragments_before}, стало {fragments_after}; "
                           "версий было {versions_before}, осталось {versions_after}",
                           ("fragments_before", "fragments_after", "versions_before", "versions_after")),
    "optimize.fts_failed": ("полнотекстовый индекс не построен ({why}); прежний, если он был, не тронут; остальные шаги не выполнялись", ("why",)),
    "optimize.ann_failed": ("приближённый индекс по векторам не построен ({why}); полнотекстовый построен, уплотнение не выполнялось", ("why",)),
    "optimize.compact_failed": ("уплотнение не удалось ({why}); индексы построены", ("why",)),

    # проверка окружения flyarchive doctor (FR-107). Слова «что без неё не работает» — в WORDS (параметр use хранит код вида);
    # модель не задана и таблицы индекса нет — сообщения init.todo_model и index.no_table, тех же слов здесь нет.
    # lib.missing и embed.down зовут и другие: поиск (нет lancedb, не отвечает служба векторов), приёмка (нет PyMuPDF, olefile, extract_msg)
    "cli.not_linux": ("FlyArchive работает на Linux; под Windows — в WSL2", ()),
    "doctor.title": ("Проверка окружения FlyArchive: ничего не меняется и не создаётся", ()),
    "doctor.system_ok": ("система: {system}", ("system",)),
    "doctor.python_ok": ("Python {version}: подходит (нужен {need} или новее)", ("version", "need")),
    "doctor.python_old": ("Python {version} слишком старый: нужен {need} или новее", ("version", "need")),
    "doctor.lib_ok": ("библиотека {package} {version}", ("package", "version")),
    "lib.missing": ("нет библиотеки {package}: без неё {use}. Поставить: python3 -m pip install -r {file}", ("package", "use", "file")),
    # обязательная библиотека на месте, но не загружается (сломанная установка); error — вид исключения и первая строка его текста
    "lib.broken": ("библиотека {package} стоит, но не загружается ({error}). Поставить заново: python3 -m pip install --force-reinstall -r {file}",
                   ("package", "error", "file")),
    # песочница и интерпретатор просмотра. Программа bwrap найдена, а пробный запуск не удался: причина — первая строка вывода или код возврата (why),
    # слова «что без неё не работает» — те же, что у отсутствующей программы (WORDS, параметр use хранит код вида). Просмотр отвечает отказом
    # preview.python_outside, когда интерпретатор лежит вне системных каталогов и отдать каталог его установки песочнице нельзя безопасно;
    # тот же отказ называет строка preview_python проверки окружения
    "doctor.sandbox_blocked": ("программа {name} найдена, но песочницу создать не может ({why}): без неё {use}. Обычная причина — запрет "
                               "непривилегированных пространств имён: на Ubuntu 24.04 поищи параметр ядра kernel.apparmor_restrict_unprivileged_userns "
                               "и профиль AppArmor для bwrap; в контейнере такой запрет ставит сам контейнер", ("name", "why", "use")),
    "doctor.preview_python_ok": ("просмотр: рабочий процесс запускается интерпретатором {python}", ("python",)),
    "preview.python_outside": ("просмотр работает с Python из системных каталогов: {python} лежит вне них, а каталог его установки нельзя безопасно "
                               "отдать песочнице. Поставь библиотеки в окружение, созданное системным Python (python3 -m venv)", ("python",)),
    "doctor.tool_ok": ("программа {name} найдена", ("name",)),
    "doctor.tool_missing": ("нет программы {name}: без неё {use}", ("name", "use")),
    "doctor.embed_ok": ("служба векторов отвечает: {url}, модель {model}, размерность {dim}", ("url", "model", "dim")),
    "embed.down": ("служба векторов не отвечает: {url} ({why}). Запусти её и скачай модель: ollama pull {model}; "
                          "другой адрес — настройка embed_url", ("url", "why", "model")),
    "embed.slow": ("служба векторов не ответила за {seconds} с: первый запрос может поднимать модель — повтори проверку", ("seconds",)),
    "doctor.embed_waiting": ("ждём службу векторов, до {seconds} с: первый запрос может поднимать модель, команда не зависла", ("seconds",)),
    "doctor.embed_dim": ("служба векторов отвечает, но размерность векторов {got}, а в настройке embed_dim {want}: поменяй модель (embed_model) "
                         "или настройку, пока таблица индекса не заведена", ("got", "want")),
    "doctor.archive_ok": ("архив заведён: {path}", ("path",)),
    "doctor.archive_missing": ("архив не заведён: {path}. Заведи его: flyarchive init", ("path",)),
    "doctor.table_ok": ("таблица индекса docs есть", ()),
    "doctor.table_failed": ("таблица индекса не открывается ({error_type}): проверь каталог индекса", ("error_type",)),
    "doctor.model_ok": ("локальная модель задана: {model}", ("model",)),
    "doctor.summary_ok": ("Обязательное на месте: FlyArchive можно запускать.", ()),
    "doctor.summary_missing": ("Не хватает обязательного: {names}. Что поставить — выше; потом повтори: flyarchive doctor", ("names",)),
    "doctor.tail_ok": ("Окружение: обязательное на месте (подробно: flyarchive doctor)", ()),
    "doctor.tail_missing": ("Окружение: не хватает обязательного: {names}. Что делать: flyarchive doctor", ("names",)),
    # шрифт с кириллицей для pdf сервера документов (необязательный: без него pdf с русским текстом не собрать, латинский собирается) и шаблоны имён
    # служебных файлов приёмки (настройка inbox.json: негодный список или судьба — отказ `inbox set`; reason.system_trace — причина «пропущен»
    # у следа операционной системы: он не содержимое и возврату исходника не мешает)
    "doctor.font_ok": ("шрифт с кириллицей для pdf: {path}", ("path",)),
    "doctor.font_missing": ("нет шрифта с кириллицей для pdf: без него русский текст в pdf не собрать. Поставить пакет {package} "
                            "(Debian и Ubuntu: sudo apt install {package}) или указать файл .ttf настройкой pdf_font", ("package",)),
    "inbox.bad_service_names": ("{key}: нужен список шаблонов имён файлов, не больше {max}, каждый — непустая строка до {length} знаков без «/» и «\\»",
                                ("key", "max", "length")),
    "inbox.bad_service_fate": ("service_fate: нужно одно из значений: {values}", ("values",)),
    "reason.system_trace": ("служебный след операционной системы, а не содержимое", ()),

    # находки правил и приёмки: что нашлось. У описания текст — прежняя цитата (quote), у цитаты документа в args стоит quoted
    # и текст — слова перед цитатой: prefix + ": " + цитата.
    "finding.hidden_tags": ("невидимые символы-метки, в них спрятано", ("quoted", "excerpt")),
    "finding.hidden_zero_width": ("невидимые символы нулевой ширины: {count} шт.", ("count",)),
    "finding.hidden_bidi": ("символы смены направления письма", ("quoted", "excerpt")),
    "finding.garbled": ("похоже на сбой кодировки: {count} знаков-иероглифов, на деле это «{decoded}»", ("count", "decoded")),
    "finding.prompt_injection": ("{kind}", ("kind", "quoted", "excerpt")),
    "finding.secret": ("{kind}", ("kind", "quoted", "excerpt")),
    "finding.hidden_html": ("текст, скрытый оформлением", ("quoted", "excerpt")),
    "finding.hidden_word": ("скрытый текст Word", ("quoted", "excerpt")),
    "finding.hidden_white": ("белый текст", ("quoted", "excerpt")),
    "finding.active_markup": ("разметка со сценарием, браузер выполнит его при просмотре", ("quoted", "excerpt")),
    "finding.active_pdf": ("PDF со сценарием или запуском программы: {marker}", ("marker",)),
    "finding.pdf_embedded": ("в PDF вложен другой файл", ()),
    "finding.pdf_encrypted": ("PDF закрыт паролем: содержимое проверить нельзя", ()),
    "finding.needs_vision_pdf": ("в PDF нет текстового слоя: содержимое проверит модель по изображению", ()),
    "finding.needs_vision_image": ("изображение: содержимое проверит модель", ()),
    "finding.attachment_executable": ("во вложении программа или скрипт: {names}", ("names",)),
    "finding.active_rtf": ("в RTF встроен объект — частый способ доставки вредоносного кода", ()),
    "finding.executable": ("{kind}", ("kind",)),
    "finding.executable_renamed": ("{kind}; расширение .{ext} не соответствует содержимому", ("kind", "ext")),
    "finding.mismatch": ("расширение .{ext}, а по содержимому это {detected}", ("ext", "detected")),
    "finding.macros": ("в документе макросы", ()),
    "finding.broken_document": ("документ повреждён: файл оборван или испорчен, открыть нельзя", ()),
    "finding.unreadable_unpacked_size": ("части документа распаковываются больше чем в {mb} МБ", ("mb",)),
    "finding.unreadable_no_main": ("в документе нет основной части", ()),
    "finding.unreadable_dtd": ("в документе объявления DTD, которых в формате Office не бывает", ()),
    "finding.unreadable_main_broken": ("основная часть документа повреждена: {error}", ("error",)),
    "finding.unreadable_open": ("документ не открывается: {error}", ("error",)),
    "finding.unreadable_pdf_open": ("PDF не открывается: {error_type}", ("error_type",)),
    "finding.unreadable_pdf_read": ("PDF не читается: {error_type}", ("error_type",)),
    "finding.unreadable_mail_parse": ("письмо не разбирается: {error_type}", ("error_type",)),
    "finding.unreadable_mail_read": ("письмо не читается: {error_type}", ("error_type",)),
    "finding.unreadable_crash": ("не читается: {error_type}", ("error_type",)),
    "finding.check_crashed": ("сбой проверки: {error_type}: {error}", ("error_type", "error")),
    "finding.llm_unchecked": ("модель не проверила: {reason}", ("reason",)),
    "finding.llm_unchecked_failed": ("модель не проверила: сбой проверки ({error_type})", ("error_type",)),
    "finding.llm_partial": ("моделью проверены первые {checked} страниц из {total}", ("checked", "total")),
    # находка модели: единственный код без области — так его знает английский словарь плагина
    "llm_finding": ("модель {model}: {why}", ("model", "page", "why", "quoted", "excerpt")),
    # локальная модель не задана в настройках (FR-97): запросов с пустым именем нет, причина идёт в «модель не проверила: …» и в вывод команды
    "llm.not_configured": ("локальная модель не задана: задай llm_local_model или выключи проверку моделью (flyarchive inbox set --llm off)", ()),

    # где нашлось
    "where.line": ("строка {line}", ("line",)),
    "where.file": ("весь файл", ()),
    "where.file_name": ("имя файла", ()),
    "where.mail_attachments": ("вложения письма", ()),
    "where.byte": ("байт {offset}", ("offset",)),
    "where.object": ("объект {number}", ("number",)),
    "where.html_markup": ("разметка HTML", ()),
    "where.pdf_attachment": ("вложение PDF", ()),
    "where.archive": ("весь архив", ()),
    "where.part": ("{part}", ("part",)),
    "where.recognized_line": ("распознанный текст: строка {line}", ("line",)),
    "where.llm": ("по оценке модели {model}", ("model",)),
    "where.llm_page": ("по оценке модели {model}, страница {page}", ("model", "page")),
    "where.llm_no_quote": ("по оценке модели {model}, такой цитаты нет в тексте", ("model",)),
    "where.llm_page_no_quote": ("по оценке модели {model}, страница {page}, такой цитаты нет в тексте", ("model", "page")),

    # причины решения по файлу (поле reason записи отчёта и квитанции)
    "reason.duplicate_exact": ("такой файл уже есть: содержимое совпадает до байта", ()),
    "reason.executable": ("не документ: программа или скрипт", ()),
    "reason.empty_file": ("пустой файл", ()),
    "reason.broken_file": ("повреждённый файл", ()),
    "reason.macos_sidecar": ("служебный файл macOS рядом с настоящим файлом", ()),
    "reason.iwork": ("документ Apple iWork: читать нечем", ()),
    "reason.not_document": ("не документ: тип не поддерживается", ()),
    "reason.archive": ("архив: проверяется содержимое", ()),
    "reason.archive_rejected": ("архив не принят целиком", ()),
    "reason.archive_unpacked": ("распаковано файлов: {count}", ("count",)),
    "reason.not_a_file": ("ссылка или не файл: не читается", ()),
    "reason.service_listing": ("служебный файл выгрузки: перечень, а не документ", ()),
    "reason.service_readme": ("служебный файл выгрузки: описание или отчёт о проверке", ()),
    "reason.service_mail_description": ("служебный файл: описание письма, которое лежит рядом", ()),
    "reason.duplicate_in_archive": ("уже лежит в архиве: {path}", ("path",)),
    "reason.duplicate_in_batch": ("уже есть в этой пачке: то же письмо или тот же файл", ()),
    "reason.held": ("задержан до решения владельца: находка {rule}", ("rule",)),
    "reason.intake_failed": ("приёмка упала: {why}", ("why",)),

    # отказ распаковки: после «unpack.» идёт вид отказа (UnpackError.reason), потом уточнение
    "unpack.traversal_absolute": ("абсолютный путь в архиве: {name}", ("name",)),
    "unpack.traversal_dots": ("путь с «..» в архиве: {name}", ("name",)),
    "unpack.traversal_escape": ("путь вышел за каталог назначения", ()),
    "unpack.link_device": ("в архиве ссылка или устройство: {name}", ("name",)),
    "unpack.link": ("в архиве ссылка: {name}", ("name",)),
    "unpack.encrypted": ("архив с паролем: проверить содержимое нельзя", ()),
    "unpack.encrypted_error": ("архив с паролем: {error}", ("error",)),
    "unpack.bomb_files": ("файлов больше предела {limit}", ("limit",)),
    "unpack.bomb_bytes": ("распакованный объём больше предела {mb} МБ", ("mb",)),
    "unpack.bomb_ratio": ("архив сжат сильнее, чем {ratio} к 1 — похоже на архивную бомбу", ("ratio",)),
    "unpack.broken": ("архив повреждён: {error}", ("error",)),
    "unpack.broken_zip_toc": ("архив повреждён: оглавление zip не читается", ()),
    "unpack.broken_timeout": ("распаковка не уложилась в отведённое время", ()),
    "unpack.broken_format": ("архив повреждён или формат не поддержан: {detail}", ("detail",)),
    "unpack.broken_extract": ("распаковка не удалась: {detail}", ("detail",)),
    "unpack.broken_os": ("архив не распаковался: {error_type}: {error}", ("error_type", "error")),
    "unpack.broken_crash": ("распаковка не удалась: {error_type}: {error}", ("error_type", "error")),
    "unpack.unsupported_no_7z": ("нет программы 7z: архивы 7z и rar распаковать нечем", ()),
    "unpack.unsupported_not_archive": ("это не архив: {family} {detected}", ("family", "detected")),
    "unpack.dest_not_empty": ("каталог назначения не пуст: {path}", ("path",)),
    "unpack.depth": ("вложенность архивов больше {limit}: не распаковывается", ("limit",)),

    # примечания к архивам (поле notes записи отчёта, дублируется в notes_msg)
    "note.archive_not_copied": ("исходный архив не копировался в карантин", ()),
    "note.executable_inside": ("внутри был исполняемый файл: {name}", ("name",)),

    # просмотр файла без контейнера (FR-76): отказы команды preview
    "preview.bad_area": ("область просмотра: {areas}", ("areas",)),
    "preview.bad_member": ("--member: целое число от 0, не больше {max} ключей", ("max",)),
    "preview.bad_page": ("номер страницы: целое число от 1", ()),
    "preview.no_page": ("в файле нет страницы {page}: показано страниц {shown}", ("page", "shown")),
    "preview.no_pages": ("у этого файла нет страниц: он показывается текстом или только сведениями", ()),
    "preview.still_rendering": ("страница ещё рисуется другим запросом: повтори позже", ()),
    # просмотр: пояснения к ответу kind none; те же слова — в отказе страницы
    "preview.no_sandbox": ("просмотр недоступен: нет песочницы bwrap, а непроверенный файл без неё не разбирается", ()),
    "preview.worker_failed": ("рабочий процесс просмотра не отработал ({why})", ("why",)),
    "preview.timeout": ("рабочий процесс просмотра не уложился в {seconds} с и остановлен", ("seconds",)),
    "preview.bad_answer": ("ответ рабочего процесса просмотра не принят ({what})", ("what",)),
    "preview.memory": ("файлу не хватило памяти, отведённой на просмотр", ()),
    "preview.image_too_large": ("изображение слишком велико: {megapixels} Мп при пределе {limit} Мп", ("megapixels", "limit")),
    "preview.encrypted": ("файл закрыт паролем: содержимое показать нельзя", ()),
    "preview.broken": ("файл не открывается: {error_type}", ("error_type",)),
    "preview.empty": ("пустой файл: показывать нечего", ()),
    "preview.program": ("программа или скрипт: содержимое не показывается", ()),
    "preview.unsupported": ("просмотр файлов этого типа не поддерживается: {type}", ("type",)),
    "preview.needs_converter": ("нужен преобразователь: файлы этого типа ({type}) пока не показываются", ("type",)),
    "preview.cache_failed": ("кэш просмотра не записан ({error_type})", ("error_type",)),
    # просмотр писем, архивов и календаря (FR-77)
    "preview.no_member": ("вложения с номером {member} нет: в файле вложений {count}", ("member", "count")),
    "preview.needs_extractor": ("нужен распаковщик 7z: состав архива ({type}) без него не показывается", ("type",)),
    "preview.too_big": ("файл больше {limit} МБ: письмо или вложение такого размера не открывается", ("limit",)),
    "preview.no_events": ("в календаре нет событий", ()),
    # просмотр через контейнер (FR-78): отказы преобразования Office, HTML и метафайлов и настройка образа
    "preview.convert_too_big": ("файл больше {limit} МБ: через контейнер такие файлы не преобразуются", ("limit",)),
    "preview.no_docker": ("docker не установлен: Office, HTML и метафайлы без него не показываются", ()),
    "preview.docker_silent": ("docker не ответил за {seconds} с: проверь, что служба docker работает", ("seconds",)),
    "preview.docker_failed": ("docker не работает ({why})", ("why",)),
    "preview.no_image": ("образ для просмотра не записан: выполни flyarchive preview setup", ()),
    "preview.image_missing": ("образа {image} на этой машине нет: выполни flyarchive preview setup", ("image",)),
    "preview.convert_timeout": ("преобразование не уложилось в {seconds} с и остановлено", ("seconds",)),
    "preview.convert_failed": ("контейнер завершился с ошибкой ({why})", ("why",)),
    "preview.convert_memory": ("контейнеру не хватило памяти ({gb} ГБ): файл слишком сложный для просмотра", ("gb",)),
    "preview.convert_no_pdf": ("контейнер не оставил PDF: файл не удалось преобразовать", ()),
    "preview.convert_not_pdf": ("то, что вернул контейнер, не PDF: не принято", ()),
    "preview.convert_not_a_file": ("контейнер вернул ссылку или не обычный файл: не принято", ()),
    "preview.convert_output_big": ("PDF от контейнера больше {limit} МБ: не принят", ("limit",)),
    "preview.convert_several": ("контейнер оставил больше одного файла: не принято", ()),
    "preview.not_converted": ("файл ещё не преобразован: страницы берутся из готового PDF, а преобразование запускает только flyarchive preview show", ()),
    "preview.input_changed": ("файл изменился во время просмотра: копия для контейнера не совпала с исходным sha256", ()),
    "preview.setup_bad_image": ("негодное имя образа: {image}", ("image",)),
    "preview.setup_pull_failed": ("образ не скачан ({why})", ("why",)),
    "preview.setup_no_image": ("образа {image} на этой машине нет: flyarchive preview setup без ключа --image скачает его", ("image",)),
    "preview.setup_no_digest": ("у образа {image} нет дайджеста репозитория (собран на этой машине?): закрепить его нельзя", ("image",)),

    # описание схем и сканов локальной моделью со зрением (FR-92): почему документ ещё не описан, отказы команды, замечания прохода.
    # Слова и значения из ответов модели и просмотра идут параметрами как есть; ключа модели в них нет никогда
    "vision.waiting": ("ждёт описания: в документе нет текста, его опишет локальная модель со зрением", ()),
    "vision.off": ("ждёт описания: описание изображений выключено (включить: flyarchive inbox set --vision on)", ()),
    "vision.model_down": ("локальная модель не отвечает ({why}): документы ждут описания", ("why",)),
    "vision.model_timeout": ("локальная модель не ответила за {seconds} с: документы ждут описания", ("seconds",)),
    "vision.model_not_loaded": ("локальная модель не загружена: документы ждут описания", ()),
    "vision.model_busy": ("видеокарта занята другой моделью ({loaded}): документы ждут описания", ("loaded",)),
    "vision.model_not_configured": ("локальная модель не задана (настройка llm_local_model): документы ждут описания", ()),
    "vision.bad_answer": ("страница {page}: ответ модели негоден (пусто или не текст)", ("page",)),
    "vision.failed": ("описать не удалось: {why}", ("why",)),
    "vision.blocked": ("{rel}: описание страницы {page} не попало в индекс: правила нашли {rule}", ("rel", "page", "rule")),
    "vision.changed": ("{rel}: файл в корпусе не совпал с записанным sha256, описание не записано", ("rel",)),
    "vision.limit_needed": ("старые документы описываются только по команде и не больше N за раз: нужен ключ --limit N", ()),
    "vision.bad_limit": ("--limit: целое число от 1 до {max}", ("max",)),
    "problem.vision_failed": ("описание изображений: не описано страниц: {pages} в документах: {docs} (последняя причина: {why}); "
                              "повтор в следующих проходах", ("pages", "docs", "why")),
    "problem.vision_aborted": ("описание изображений прервано ({error}): документы ждут описания, повтор в следующем проходе", ("error",)),

    # единый модуль настроек (FR-95): отказы settings.py. Значение настройки в сообщение не попадает никогда (строка может оказаться
    # секретом по ошибке владельца): только ключ, источник («settings.json» или имя переменной окружения) и что ожидалось
    "settings.unknown_key": ("в файле настроек неизвестная настройка: {key}", ("key",)),
    "settings.home_in_file": ("в файле настроек {path} не может быть ключа home: каталог архива — тот, где лежит файл", ("path",)),
    "settings.file_open": ("файл настроек {path} принадлежит другому пользователю или открыт на запись другим — не использую", ("path",)),
    "settings.file_unreadable": ("файл настроек {path} не читается ({error_type})", ("path", "error_type")),
    "settings.bad_json": ("файл настроек {path} — не JSON (строка {line}, столбец {column})", ("path", "line", "column")),
    "settings.not_object": ("в файле настроек {path} должен быть объект JSON", ("path",)),
    "settings.bad_int": ("{key} ({source}): целое число от {low} до {high}", ("key", "source", "low", "high")),
    "settings.bad_number": ("{key} ({source}): число от {low} до {high}", ("key", "source", "low", "high")),
    "settings.bad_choice": ("{key} ({source}): одно из значений: {allowed}", ("key", "source", "allowed")),
    "settings.bad_switch": ("{key} ({source}): true или false (в переменной окружения: 1, 0, true, false, on, off, yes, no)", ("key", "source")),
    "settings.bad_text": ("{key} ({source}): строка без управляющих знаков", ("key", "source")),
    "settings.bad_list": ("{key} ({source}): список строк без управляющих знаков (в переменной окружения — через запятую)", ("key", "source")),
    "settings.bad_path": ("{key} ({source}): абсолютный путь без управляющих знаков (~ и подстановка каталога архива в начале раскрываются "
                          "до проверки)", ("key", "source")),
    "settings.bad_url": ("{key} ({source}): адрес http или https с именем узла, без имени пользователя и пароля", ("key", "source")),
    "settings.bad_env_name": ("{key} ({source}): имя переменной окружения: заглавные латинские буквы, цифры и подчёркивание, первой не цифра",
                              ("key", "source")),

    # таблица источников (FR-99): отказы sources.py. Значение из файла в сообщение не попадает никогда: только место — корень, номер
    # правила (с единицы), ключ — и что ожидалось
    "sources.file_open": ("таблица источников {path} принадлежит другому пользователю или открыта на запись другим — не использую", ("path",)),
    "sources.file_unreadable": ("таблица источников {path} не читается ({error_type})", ("path", "error_type")),
    "sources.bad_json": ("таблица источников {path} — не JSON (строка {line}, столбец {column})", ("path", "line", "column")),
    "sources.duplicate_key": ("в таблице источников ключ повторён: {where}", ("where",)),
    "sources.unknown_key": ("в таблице источников неизвестный ключ: {where}", ("where",)),
    "sources.missing_key": ("{where}: ключ обязателен", ("where",)),
    "sources.key_not_for_kind": ("{where}: ключ не применяется к корню вида {kind}", ("where", "kind")),
    "sources.not_object": ("{where}: нужен объект JSON", ("where",)),
    "sources.not_list": ("{where}: нужен непустой список", ("where",)),
    "sources.not_text": ("{where}: нужна непустая строка без управляющих знаков, не длиннее {high} знаков", ("where", "high")),
    "sources.bad_kind": ("{where}: вид корня — mail, pages или files", ("where",)),
    "sources.bad_name": ("{where}: нужно одно имя каталога без разделителей, без «.» и «..» и без управляющих знаков", ("where",)),
    "sources.reserved_root": ("{where}: корень входящих встроен в код, в таблице его задать нельзя", ("where",)),
    "sources.reserved_base": ("{where}: эта база принадлежит приёмке через входящую папку, в таблице её задать нельзя", ("where",)),
    "sources.bad_rule": ("{where}: правило — ровно один признак (contains или first_part_prefix) и база source", ("where",)),
    "sources.rule_no_source": ("{where}: у правила нет базы (source)", ("where",)),
    "sources.not_lowercase": ("{where}: подстрока должна быть в нижнем регистре: путь сравнивается в нижнем регистре", ("where",)),
    "sources.bad_prefix": ("{where}: приставка — непустая строка без разделителей и управляющих знаков", ("where",)),
    "sources.bad_title": ("{where}: название — только folder", ("where",)),
    "sources.bad_rank": ("{where}: ранг — целое число от 0 до {high}", ("where", "high")),
    "sources.alias_target": ("{where}: псевдоним ведёт на корень, которого нет в таблице", ("where",)),
    "sources.alias_clash": ("{where}: старое имя совпадает с корнем, псевдоним закрыл бы настоящий корень", ("where",)),
}

# Русские слова системных видов. Параметр хранит код вида (латиницей), русский текст сообщения берёт слово отсюда.
# Незнакомое значение остаётся в тексте как есть, сборку это не роняет.
WORDS = {
    "finding.executable": {"kind": {
        "pe": "программа Windows", "elf": "программа Linux", "macho-or-class": "программа macOS или Java", "lnk": "ярлык Windows",
        "jar": "программа Java", "apk": "приложение Android", "msi": "установщик Windows", "script": "скрипт",
        "by-extension": "запускаемый файл", "other": "программа"}},
    "finding.prompt_injection": {"kind": {
        "cancel_instructions": "отмена прежних указаний", "role_change": "смена роли", "chat_markers": "служебные метки диалога",
        "addresses_model": "обращение к модели", "hide_instruction": "просьба скрыть указание",
        "extract_prompt": "выманивание служебных указаний", "exfiltrate": "вывод данных наружу"}},
    "finding.secret": {"kind": {
        "private_key": "закрытый ключ", "aws_key": "ключ AWS", "github_token": "токен GitHub", "api_key": "ключ API",
        "slack_token": "токен Slack", "archive_token": "токен архива", "password": "пароль"}},
}
WORDS["finding.executable_renamed"] = WORDS["finding.executable"]
# что не работает без библиотеки или программы: код вида — в параметре use сообщений проверки окружения (tools/doctor.py)
WORDS["lib.missing"] = {"use": {
    "index": "нельзя завести таблицу индекса и искать по архиву",
    "schema": "нельзя завести таблицу индекса: нет описания её столбцов",
    "pdf": "pdf не проверяются при приёмке и их текст не попадает в индекс",
    "images": "не показываются картинки, а страницы сканов уходят модели без уменьшения",
    "docx": "документы Word (.docx) не попадают в индекс и не читаются сервером документов",
    "xlsx": "таблицы Excel (.xlsx) не попадают в индекс и не читаются сервером документов",
    "pptx": "презентации (.pptx) не попадают в индекс и не читаются сервером документов",
    "msg": "письма Outlook (.msg) не разбираются при приёмке и не попадают в индекс",
    "ole": "старые документы Office (.doc, .xls, .ppt) и письма .msg не опознаются и не принимаются",
    "docling": "нет разбора документов с сохранением таблиц и распознавания сканов на месте",
    "charts": "сервер документов не строит графики",
    "pdf_out": "сервер документов не создаёт файлы pdf"}}
WORDS["doctor.tool_missing"] = {"use": {
    "bwrap": "не открывается просмотр непроверенных файлов и не запускаются оболочки внешних моделей в песочнице",
    "7z": "не распаковываются и не показываются архивы 7z и rar (zip и tar читаются и так)",
    "dot": "сервер документов не строит схемы",
    "docker": "в просмотре не показываются файлы Office, HTML и метафайлы",
    "systemctl": "нельзя поставить службы и таймер разбора: архив работает вручную, командами flyarchive",
    "dsh": "нет веб-оболочки DSH с разделом «Архив»; архив работает и без неё: команда, страница поиска, подключение сторонних оболочек",
    "tailscale": "шлюз в частную сеть не поставить: архив доступен только с этой машины"}}
WORDS["doctor.sandbox_blocked"] = WORDS["doctor.tool_missing"]


# Правила находок: поле rule у Finding и записей отчёта. Перечень полный — его сверяет с кодом tests/test_messages.py,
# а по `messages.py --rules` плагин проверяет, что у каждого правила есть английское название.
RULES = ("executable", "macros", "active_content", "hidden_text", "garbled", "prompt_injection", "secret", "encrypted",
         "needs_vision", "attachment", "mismatch", "unreadable", "archive", "llm_unchecked", "llm_partial", "llm_malicious", "llm_other")


class MessageError(ValueError):
    """Сообщение собрано неверно: неизвестный код, лишний или недостающий параметр, негодное значение."""


class Message(str):
    """Русский текст, который знает свой код и параметры. Собирается только через make()."""

    def __new__(cls, code, text, args):
        self = super().__new__(cls, text)
        self.code, self.args = code, args
        return self

    def to_json(self):
        """Вид для JSON: {"code", "args", "text"}. Параметры отдаются копией."""
        return {"code": self.code, "args": dict(self.args), "text": str(self)}

    def __reduce__(self):
        return _restore, (self.code, dict(self.args))


def _simple(value):
    """Параметр — строка, число, булево или null (число — конечное: в JSON иного нет)."""
    return value is None or isinstance(value, (str, int)) or (isinstance(value, float) and math.isfinite(value))


def make(code, /, **args):
    """Собирает сообщение из каталога. Ошибка сборки — MessageError."""
    entry = CATALOG.get(code) if isinstance(code, str) else None
    if entry is None:
        raise MessageError(f"неизвестный код сообщения: {code!r}")
    template, params = entry
    missing, extra = [p for p in params if p not in args], [p for p in args if p not in params]
    if missing or extra:
        raise MessageError(f"{code}: " + "; ".join(part for part in (
            f"нет параметров: {', '.join(missing)}" if missing else "", f"лишние параметры: {', '.join(extra)}" if extra else "") if part))
    for name, value in args.items():
        if not _simple(value):
            raise MessageError(f"{code}: параметр {name} должен быть строкой, числом, булевым или null, а не {type(value).__name__}")
    shown = dict(args)
    for name, words in WORDS.get(code, {}).items():
        shown[name] = words.get(args[name], args[name])        # в текст — русское слово вида, в args остаётся код
    return Message(code, template.format(**shown), {p: args[p] for p in params})


def _restore(code, args):
    return make(code, **args)


class CodedError(Exception):
    """Отказ с сообщением из каталога: str(e) — русский текст, e.message — сообщение с кодом и параметрами."""

    @property
    def message(self):
        return of(self)


def of(error):
    """Сообщение исключения: своё, если оно из каталога, иначе общее generic.text с текстом исключения как есть."""
    first = error.args[0] if len(error.args) == 1 else None
    return first if isinstance(first, Message) else make("generic.text", text=str(error))


def main(argv=None):
    import argparse
    import json
    p = argparse.ArgumentParser(prog="messages", description="Каталог сообщений: код, имена параметров, русский шаблон.")
    p.add_argument("--json", action="store_true", help="{код: [имена параметров]}")
    p.add_argument("--words", action="store_true", help="{код: {параметр: {код вида: русское слово}}} — слова системных видов")
    p.add_argument("--rules", action="store_true", help="[правило, …] — все правила находок (поле rule)")
    a = p.parse_args(argv)
    if a.json:
        print(json.dumps({code: list(params) for code, (_, params) in CATALOG.items()}))
    elif a.words:
        print(json.dumps(WORDS, ensure_ascii=False))
    elif a.rules:
        print(json.dumps(list(RULES)))
    else:
        for code, (template, params) in CATALOG.items():
            print(f"{code}\t{', '.join(params)}\t{template}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main())
