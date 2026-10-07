"""Приёмка входящих документов: статические проверки, баллы, решение.

    check_file(путь) -> {"decision": accept | review | quarantine | skip | duplicate, "score", "findings", …}

Правила решения:
- программа или скрипт — в карантин сразу, без подсчёта баллов;
- находка уровня CRITICAL — в карантин;
- не документ и не программа (видео, значки) — пропускается, в архив не идёт;
- сумма баллов не больше 20 — принять, больше — на утверждение человеку.
Содержимое файла — данные. Проверка ничего из него не исполняет.
"""
import email
import hashlib
import html
import os
import re
import zipfile
from collections import namedtuple
from email import policy
from html.parser import HTMLParser

import filetype_sniff
import known
import messages

LEVELS = {"CRITICAL": 50, "HIGH": 25, "MEDIUM": 10, "LOW": 5}
THRESHOLD = 20
MAX_TEXT = 2_000_000              # символов текста на проверку из одного файла
MAX_RAW = 300 * 1024 * 1024       # байт, которые читаются целиком для поиска по сырому содержимому
MAX_XML = 500 * 1024 * 1024       # распакованный объём частей документа Office
MAX_ODF = 64 * 1024 * 1024        # распакованный объём текстовых частей ODF и EPUB: они читаются в память целиком
QUOTE = 200

class Finding(namedtuple("Finding", "rule level where quote msg where_msg", defaults=(None, None))):
    """Находка. rule, level, where, quote — как были (строки). msg — что нашлось, where_msg — где: сообщения из каталога
    (messages.make) или, в записи отчёта и квитанции, их словари {code, args, text}; у старых записей — None.
    Если quote — цитата документа, в msg.args стоят quoted: true и excerpt (та же цитата без слов перед ней: quote = «слова: excerpt»);
    если описание — их нет, а quote совпадает с текстом msg."""
    __slots__ = ()

    def _asdict(self):
        """Запись для JSON: сообщения — словарями, чтобы код и параметры не потерялись при записи в отчёт."""
        d = super()._asdict()
        for key in ("msg", "where_msg"):
            if isinstance(d[key], messages.Message):
                d[key] = d[key].to_json()
        return d


def _found(rule, level, msg, where, quote=None):
    """Находка с сообщениями. quote — прежний текст цитаты; без него это текст msg (так у описаний)."""
    return Finding(rule, level, str(where), str(msg) if quote is None else quote, msg, where)


def fit(build, tail):
    """Сообщение build(tail), текст которого не длиннее QUOTE. tail — параметр, стоящий в конце шаблона: он укорачивается
    ровно настолько, насколько надо, так что текст совпадает с прежней цитатой, обрезанной до QUOTE."""
    msg = build(tail)
    over = len(msg) - QUOTE
    return build(tail[:max(0, len(tail) - over)]) if over > 0 else msg


def _whole():
    return messages.make("where.file")


def _cite(build, text, limit=None):
    """Сообщение и quote находки с цитатой документа. build(excerpt) собирает сообщение (messages.make с quoted=True и excerpt),
    text — сама цитата, секреты в ней уже затёрты (redact или _quote). quote — прежний вид «слова сообщения: цитата», при limit обрезанный
    до него; excerpt — та же цитата без слов, с той же обрезкой."""
    label = str(build(""))
    quote = f"{label}: {text}"
    if limit is not None:
        quote = quote[:limit]
    return build(quote[len(label) + 2:]), quote


# ── внедрение инструкций ────────────────────────────────────────
def _rx(pattern):
    return re.compile(pattern, re.I | re.M)


AI_EN = r"(?:ai|llm|language model|assistant|chatbot|gpt|claude|model)"
AI_RU = r"(?:модель|нейросеть|ассистент|ии|искусственный интеллект|языковая модель|чат-?бот|проверяющ\w+)"
INJECTION = [
    ("cancel_instructions", _rx(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all|any|your)\b"
        r"[^.\n]{0,40}\b(?:instructions?|prompts?|rules?|directions?|guidelines?)\b")),
    ("cancel_instructions", _rx(
        r"\b(?:игнорируй|игнорируйте|проигнорируй|забудь|забудьте|отмени|не учитывай)\b[^.\n]{0,40}"
        r"\b(?:предыдущ|прежн|все|выше|ранее|свои)\w*[^.\n]{0,40}\b(?:инструкц|указан|правил|команд|промпт)")),
    ("role_change", _rx(
        r"\byou are now (?:a|an|the|in)\b|\bfrom now on,? you (?:are|will|must)\b|\bpretend (?:to be|you are)\b")),
    ("role_change", _rx(
        r"\b(?:ты теперь|теперь ты|отныне ты)\b[^.\n]{0,40}\b(?:отвечаешь|должен|должна|должны|будешь|обязан\w*|"
        r"являешься|игнорируешь|выполняешь)\b")),
    ("chat_markers", _rx(
        r"<\|im_start\|>|<\|im_end\|>|\[/?INST\]|<<SYS>>|<\|system\|>|<\|assistant\|>|"
        r"^\s*###\s*(?:system|instructions?)\s*:|\bBEGIN SYSTEM PROMPT\b|<tool_use>|<function_calls>")),
    ("addresses_model", _rx(
        r"\b" + AI_EN + r"\b\s*[,:]\s*(?:please\s+)?(?:ignore|do not|don't|you must|you should|always|never|respond|"
        r"reply|return|output|summari[sz]e)\b")),
    ("addresses_model", _rx(
        r"\b" + AI_RU + r"\s*[:,—–-]\s*[^\n]{0,80}?\b(?:игнорируй|не упоминай|не сообщай|не говори|верни|ответь|"
        r"выведи|ты должн\w*|поставь|считай, что)\b")),
    ("hide_instruction", _rx(
        r"\bdo not (?:mention|reveal|tell|disclose)\b[^.\n]{0,60}\b(?:this|these|the above)\b[^.\n]{0,30}"
        r"\b(?:instructions?|message|line|text|prompt)\b")),
    ("hide_instruction", _rx(
        r"\bне (?:упоминай|сообщай|раскрывай|говори)\b[^.\n]{0,60}\b(?:эт\w*|данн\w+)\s+(?:строк|инструкц|сообщен|текст|указан)")),
    ("extract_prompt", _rx(
        r"\b(?:reveal|print|show|repeat)\b[^.\n]{0,40}\b(?:system prompt|your instructions)\b")),
    ("extract_prompt", _rx(
        r"\b(?:покажи|выведи|раскрой|повтори)\b[^.\n]{0,40}\bсистемн\w+\s+(?:промпт|инструкц|сообщен)")),
    ("exfiltrate", _rx(
        # «отправь X на адрес» — обычная деловая фраза; находка только когда X — переписка, данные, секреты
        r"\b(?:send|post|forward|upload|exfiltrate|leak)\b[^.\n]{0,40}\b(?:conversation|chat|history|context|"
        r"contents?|data|documents?|files?|secrets?|credentials?|passwords?|tokens?|keys?|prompt|everything)\b"
        r"[^.\n]{0,40}\b(?:to|at)\s+https?://|!\[[^\]]*\]\(https?://[^)\s]*[?&][^)\s]*=")),
    ("exfiltrate", _rx(
        r"\b(?:отправь|перешли|передай|загрузи|выложи)\b[^.\n]{0,40}\b(?:документ|файл|данн|переписк|истори|"
        r"содержим|архив|парол|ключ|токен|секрет|вс[её]\b)\w*[^.\n]{0,40}\b(?:на|по адресу)\s+https?://")),
]

# ── секреты ─────────────────────────────────────────────────────
SECRETS = [
    ("private_key", re.compile(r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY)"), 1),
    ("aws_key", re.compile(r"\b(AKIA)[0-9A-Z]{16}\b"), 1),
    ("github_token", re.compile(r"\b(gh[opsu]_)[A-Za-z0-9]{36}\b"), 1),
    ("api_key", re.compile(r"\b(sk-)[A-Za-z0-9_-]{20,}"), 1),
    ("slack_token", re.compile(r"\b(xox[baprs]-)[A-Za-z0-9-]{10,}"), 1),
    ("archive_token", re.compile(r"\b(ba_)[A-Za-z0-9_-]{43}"), 1),
    # не параметр ссылки: в приглашениях Zoom и Teams стоит «?pwd=…», это код встречи, а не пароль
    ("password", re.compile(r"(?i)(?<![?&\w])((?:password|passwd|pwd|пароль)\s*[:=]\s*)(?=[A-Za-z0-9!@#$%^&*()_+=.,?-]*\d)"
                          r"([A-Za-z0-9!@#$%^&*()_+=.,?-]{6,})"), 2),
]

PEM_END = re.compile(r"-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")
SECRET_REACH = 4096    # на сколько знаков в обе стороны от окна ищется секрет, который окно режет


def _mask(m, group):
    """Как находка secret показывает найденное: начало по правилу, два знака значения и звёздочки."""
    return m.group(1) + (m.group(2)[:2] if group == 2 else "") + "***"


def _secret_spans(text, lo, hi):
    """Секреты, задевающие text[lo:hi]: [(начало, конец, как показать)] без перекрытий, по порядку. Ищутся и в запасе по обе стороны окна:
    секрет, который окно режет, тоже затирается. У закрытого ключа секрет — весь блок до строки END (или до конца текста): тело ключа
    правилом не ловится, но в окно оно попасть может."""
    a, b = max(0, lo - SECRET_REACH), min(len(text), hi + SECRET_REACH)
    spans = []
    for kind, rx, group in SECRETS:
        for m in rx.finditer(text, a, b):
            end = m.end()
            if kind == "private_key":
                tail = PEM_END.search(text, end)
                end = tail.end() if tail else len(text)
            if end > lo and m.start() < hi:
                spans.append((m.start(), end, _mask(m, group)))
    spans.sort(key=lambda s: (s[0], -s[1]))
    merged = []
    for span in spans:
        if merged and span[0] < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], span[1]), merged[-1][2])
        else:
            merged.append(span)
    return merged


def _masked(text, lo, hi):
    """text[lo:hi], в котором секреты затёрты тем же способом, что показывает находка secret (_mask)."""
    out, pos = [], lo
    for start, end, shown in _secret_spans(text, lo, hi):
        out.append(text[pos:max(start, pos)])
        out.append(shown)
        pos = max(pos, min(end, hi))
    out.append(text[pos:hi])
    return "".join(out)


def redact(text):
    """Цитата без секретов: каждое значение, подходящее под SECRETS, показано так же, как в находке secret («Пароль: Qw***»)."""
    return _masked(text, 0, len(text))


GARBLED_MIN = 40       # столько подряд странных знаков — уже не случайность
GARBLED_SHARE = 0.92    # доля байтов, которые при обратном превращении дают обычную латиницу
ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def _where(text, pos):
    return messages.make("where.line", line=text.count(chr(10), 0, pos) + 1)


def _quote(text, start, end):
    """Окно вокруг совпадения для цитаты находки; секреты в нём затёрты, пока окно не обрезано."""
    a, b = max(0, start - 40), min(len(text), end + 80)
    return re.sub(r"\s+", " ", _masked(text, a, b)).strip()[:QUOTE]


def scan_text(text, mail=False):
    """Находки в тексте, который увидит модель: указания ей, невидимые символы, секреты.

    mail — текст письма или веб-страницы: там символы нулевой ширины обычны (ими набивают
    строку предпросмотра), поэтому находка мягче."""
    out = []
    text = text[:MAX_TEXT]
    body = text.lstrip("\ufeff")

    tags = [ch for ch in body if 0xE0000 <= ord(ch) <= 0xE007F]
    if tags:
        hidden = "".join(chr(ord(ch) - 0xE0000) for ch in tags if 0xE0020 <= ord(ch) <= 0xE007E)
        msg, quote = _cite(lambda ex: messages.make("finding.hidden_tags", quoted=True, excerpt=ex), redact(hidden), QUOTE)
        out.append(_found("hidden_text", "HIGH", msg, _where(body, body.index(tags[0])), quote))
        body += "\n" + hidden                     # спрятанное проверяется наравне с видимым
    zero = [i for i, ch in enumerate(body) if ord(ch) in ZERO_WIDTH]
    if len(zero) >= 3:
        out.append(_found("hidden_text", "LOW" if mail else "MEDIUM", messages.make("finding.hidden_zero_width", count=len(zero)),
                          _where(body, zero[0])))
    bidi = [i for i, ch in enumerate(body) if ord(ch) in BIDI]
    if bidi:
        msg, quote = _cite(lambda ex: messages.make("finding.hidden_bidi", quoted=True, excerpt=ex),
                           _quote(body, bidi[0], bidi[0]).replace(body[bidi[0]], "⟲"))
        out.append(_found("hidden_text", "LOW" if mail else "MEDIUM", msg, _where(body, bidi[0]), quote))

    odd = [i for i, ch in enumerate(body) if 0x2000 <= ord(ch) < 0xA000]
    if len(odd) >= GARBLED_MIN:
        # текст из латиницы, прочитанный как UTF-16, превращается в иероглифы; обратное превращение его выдаёт
        raw = "".join(body[i] for i in odd).encode("utf-16-le")
        readable = sum(1 for x in raw if 32 <= x < 127 or x in (9, 10, 13))
        if readable >= GARBLED_SHARE * len(raw):
            msg = messages.make("finding.garbled", count=len(odd), decoded=redact(" ".join(raw.decode("latin-1").split()))[:80])
            out.append(_found("garbled", "MEDIUM", msg, _where(body, odd[0]), str(msg)[:QUOTE]))

    best = None
    for kind, rx in INJECTION:                    # одно правило — одна находка, сколько бы раз ни повторялось
        m = rx.search(body)
        if m and (best is None or m.start() < best[1].start()):
            best = (kind, m)
    if best:
        kind, m = best
        msg, quote = _cite(lambda ex: messages.make("finding.prompt_injection", kind=kind, quoted=True, excerpt=ex),
                           _quote(body, m.start(), m.end()))
        out.append(_found("prompt_injection", "HIGH", msg, _where(body, m.start()), quote))

    for kind, rx, group in SECRETS:
        m = rx.search(body)
        if m:
            msg, quote = _cite(lambda ex: messages.make("finding.secret", kind=kind, quoted=True, excerpt=ex), _mask(m, group))
            out.append(_found("secret", "HIGH", msg, _where(body, m.start()), quote))
            break
    return out


# ── извлечение текста ───────────────────────────────────────────
class Unreadable(messages.CodedError):
    """Документ не читается. Аргумент — сообщение из каталога, не длиннее QUOTE: оно же становится цитатой находки."""


def _decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


class _Html(HTMLParser):
    HIDDEN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])|"
                        r"opacity\s*:\s*0(?![.\d])|(?<![-\w])color\s*:\s*(?:#fff(?:fff)?\b|white\b)", re.I)
    VOID = {"br", "img", "hr", "meta", "link", "input", "area", "base", "col", "embed", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text, self.hidden = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = dict(attrs)
        self.stack.append((tag, bool(self.HIDDEN.search(a.get("style") or "")) or "hidden" in a))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(t in ("script", "style") for t, _ in self.stack):
            return
        self.text.append(data)
        if any(h for _, h in self.stack):
            self.hidden.append(data)


def _html_text(source, findings, level="LOW"):
    p = _Html()
    try:
        p.feed(source)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", source)
    hidden = re.sub(r"\s+", " ", " ".join(p.hidden)).strip()
    if sum(ch.isalpha() for ch in hidden) >= 20:
        msg, quote = _cite(lambda ex: messages.make("finding.hidden_html", quoted=True, excerpt=ex), redact(hidden), QUOTE)
        findings.append(_found("hidden_text", level, msg, messages.make("where.html_markup"), quote))
    return " ".join(p.text)


TEXT_NODE = re.compile(r"<(?:[a-z]+:)?t(?:\s[^>]*)?>([^<]*)</(?:[a-z]+:)?t>|<Text(?:\s[^>]*)?>(.*?)</Text>", re.S)
RUN = re.compile(r"<w:r[ >].*?</w:r>", re.S)
OFF = re.compile(r'w:val="(?:0|false|off)"')


def _ooxml_text(path, findings):
    """Текст всех частей документа Office: основная часть, колонтитулы, сноски, примечания, листы, слайды."""
    import xml.etree.ElementTree as ET
    parts = []
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if i.filename.lower().endswith(".xml") and not i.filename.startswith(("docProps/", "_rels/"))]
            if sum(i.file_size for i in infos) > MAX_XML:
                raise Unreadable(messages.make("finding.unreadable_unpacked_size", mb=MAX_XML // 1024 ** 2))
            main = [i for i in infos if i.filename.lower() in ("word/document.xml", "xl/workbook.xml", "ppt/presentation.xml", "visio/document.xml")]
            if not main:
                raise Unreadable(messages.make("finding.unreadable_no_main"))
            for info in infos:
                xml = z.read(info).decode("utf-8", "replace")
                if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
                    raise Unreadable(messages.make("finding.unreadable_dtd"))
                if info in main:
                    try:
                        ET.fromstring(xml)
                    except ET.ParseError as e:
                        raise Unreadable(fit(lambda s: messages.make("finding.unreadable_main_broken", error=s), str(e)))
                if info.filename.startswith("word/"):
                    for run in RUN.findall(xml):
                        words = " ".join(a or b for a, b in TEXT_NODE.findall(run)).strip()
                        if not words:
                            continue
                        vanish = re.search(r"<w:vanish(?:\s[^>]*)?/>", run)
                        if vanish and not OFF.search(vanish.group(0)):
                            msg, quote = _cite(lambda ex: messages.make("finding.hidden_word", quoted=True, excerpt=ex),
                                             redact(html.unescape(words)), QUOTE)
                            findings.append(_found("hidden_text", "MEDIUM", msg, messages.make("where.part", part=info.filename), quote))
                        elif re.search(r'<w:color w:val="(?:FFFFFF|ffffff)"', run):
                            msg, quote = _cite(lambda ex: messages.make("finding.hidden_white", quoted=True, excerpt=ex),
                                             redact(html.unescape(words)), QUOTE)
                            findings.append(_found("hidden_text", "MEDIUM", msg, messages.make("where.part", part=info.filename), quote))
                parts.append(" ".join(html.unescape(a or b) for a, b in TEXT_NODE.findall(xml)))
                if sum(map(len, parts)) > MAX_TEXT:
                    break
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise Unreadable(fit(lambda s: messages.make("finding.unreadable_open", error=s), str(e)))
    seen, unique = set(), []
    for f in findings:                            # одна находка на вид скрытого текста, а не на каждый кусок
        key = (f.rule, f.quote.split(":")[0])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings[:] = unique
    return "\n".join(parts)


def _odf_text(path):
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist()
                     if i.filename == "content.xml" or i.filename.lower().endswith((".xhtml", ".html", ".htm"))][:200]
            if sum(i.file_size for i in infos) > MAX_ODF:      # файл в сотни килобайт может распаковываться в гигабайты
                raise Unreadable(messages.make("finding.unreadable_unpacked_size", mb=MAX_ODF // 1024 ** 2))
            return "\n".join(re.sub(r"<[^>]+>", " ", z.read(i).decode("utf-8", "replace")) for i in infos)[:MAX_TEXT]
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise Unreadable(fit(lambda s: messages.make("finding.unreadable_open", error=s), str(e)))


# SVG и XHTML браузер открывает как страницу: сценарий в них выполнится при просмотре документа
MARKUP_PAGE = re.compile(r"<\s*(?:[\w.-]+:)?svg\b|www\.w3\.org/1999/xhtml", re.I)
MARKUP_ACTIVE = re.compile(r"<\s*(?:[\w.-]+:)?script\b|\bon[a-z]{3,}\s*=\s*[\"']|javascript\s*:", re.I)


def _markup_findings(text, findings):
    if not text.lstrip("\ufeff \t\r\n").startswith("<") or not MARKUP_PAGE.search(text):
        return
    m = MARKUP_ACTIVE.search(text)
    if m:
        msg, quote = _cite(lambda ex: messages.make("finding.active_markup", quoted=True, excerpt=ex),
                           _quote(text, m.start(), m.end()), QUOTE)
        findings.append(_found("active_content", "HIGH", msg, _where(text, m.start()), quote))


PDF_ACTIVE = re.compile(rb"/JavaScript\b|/Launch\b")       # в сыром файле: длинные метки случайно не совпадают
PDF_ACTIVE_OBJ = re.compile(r"/(?:JavaScript|JS|Launch)\b")  # в разобранных объектах: короткая /JS тоже
MAX_XREF = 200_000


def _pdf_text(path, findings):
    if os.path.getsize(path) <= MAX_RAW:
        with open(path, "rb") as f:
            raw = f.read()
        m = PDF_ACTIVE.search(raw)
        if m:
            findings.append(_found("active_content", "HIGH", messages.make("finding.active_pdf", marker=m.group(0).decode()),
                                   messages.make("where.byte", offset=m.start())))
        if b"/EmbeddedFile" in raw:
            findings.append(_found("active_content", "MEDIUM", messages.make("finding.pdf_embedded"),
                                   messages.make("where.pdf_attachment")))
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:                       # без библиотеки pdf не принимается «как будто проверен», с пустым текстом (FR-107)
            raise Unreadable(messages.make("lib.missing", package="pymupdf", use="pdf", file="requirements.txt")) from None
    try:
        fitz.TOOLS.mupdf_display_errors(False)    # предупреждения библиотеки о кривых PDF в вывод не нужны
    except Exception:
        pass
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise Unreadable(messages.make("finding.unreadable_pdf_open", error_type=type(e).__name__))
    with doc:
        if doc.needs_pass:
            findings.append(_found("encrypted", "HIGH", messages.make("finding.pdf_encrypted"), _whole()))
            return ""
        if not any(f.rule == "active_content" and f.level == "HIGH" for f in findings):
            # сценарий может лежать в сжатом потоке объектов, где сырой поиск его не видит
            for xref in range(1, min(doc.xref_length(), MAX_XREF)):
                try:
                    m = PDF_ACTIVE_OBJ.search(doc.xref_object(xref, compressed=True))
                except Exception:
                    continue
                if m:
                    findings.append(_found("active_content", "HIGH", messages.make("finding.active_pdf", marker=m.group(0)),
                                           messages.make("where.object", number=xref)))
                    break
        out, size = [], 0
        try:
            for page in doc:
                t = page.get_text()
                out.append(t)
                size += len(t)
                if size > MAX_TEXT:
                    break
        except Exception as e:
            raise Unreadable(messages.make("finding.unreadable_pdf_read", error_type=type(e).__name__))
    text = "\n".join(out)
    if not text.strip():
        findings.append(_found("needs_vision", "LOW", messages.make("finding.needs_vision_pdf"), _whole()))
    return text


def _attachment_findings(names_and_heads, findings):
    bad = [name or "без имени" for name, head in names_and_heads if filetype_sniff.is_executable(name or "", head)]
    if bad:
        msg = fit(lambda s: messages.make("finding.attachment_executable", names=s), ", ".join(bad))
        findings.append(_found("attachment", "HIGH", msg, messages.make("where.mail_attachments")))


def _eml_text(path, findings, keys):
    try:
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
    except Exception as e:
        raise Unreadable(messages.make("finding.unreadable_mail_parse", error_type=type(e).__name__))
    keys["mid"], keys["fp"] = known.keys_of_message(msg)
    keys["date"] = known.date_of_message(msg)
    parts = [f"От: {msg.get('From')}\nКому: {msg.get('To')}\nТема: {msg.get('Subject')}"]
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        ctype = part.get_content_type()
        try:
            if name or part.get_content_disposition() == "attachment":
                attachments.append((name, (part.get_payload(decode=True) or b"")[:512]))
            elif ctype == "text/plain":
                parts.append(part.get_content())
            elif ctype == "text/html":
                parts.append(_html_text(part.get_content(), findings))
        except Exception:
            continue                              # битая часть письма не мешает проверить остальные
    _attachment_findings(attachments, findings)
    return "\n".join(parts)


def _msg_text(path, findings, keys):
    try:
        import extract_msg
    except ImportError:
        raise Unreadable(messages.make("lib.missing", package="extract-msg", use="msg", file="requirements-optional.txt")) from None
    try:
        m = extract_msg.Message(path)
    except Exception as e:
        raise Unreadable(messages.make("finding.unreadable_mail_parse", error_type=type(e).__name__))
    try:
        keys["mid"], keys["fp"] = known.keys_of_outlook(m)
        keys["date"] = known._day(m.date)
        text = f"От: {m.sender}\nКому: {m.to}\nТема: {m.subject}\n\n{m.body or ''}"
        names = []
        for a in m.attachments:
            data = a.data if isinstance(getattr(a, "data", None), bytes) else b""
            names.append((getattr(a, "longFilename", None) or getattr(a, "shortFilename", None), data[:512]))
        _attachment_findings(names, findings)
        return text
    except Exception as e:
        raise Unreadable(messages.make("finding.unreadable_mail_read", error_type=type(e).__name__))
    finally:
        m.close()


def _strings(path):
    """Старые форматы Office: текст вытаскивается как есть, без разбора структуры."""
    with open(path, "rb") as f:
        raw = f.read(MAX_RAW)
    wide = re.findall(r"[\w\s.,:;!?<>|\[\]#()@/=\-]{6,}", raw.decode("utf-16-le", "ignore"))
    narrow = re.findall(r"[\w\s.,:;!?<>|\[\]#()@/=\-]{6,}", raw.decode("cp1251", "ignore"))
    return ("\n".join(wide) + "\n" + "\n".join(narrow))[:MAX_TEXT]


def _rtf_text(path, findings):
    with open(path, "rb") as f:
        raw = f.read(MAX_RAW).decode("latin-1")
    m = re.search(r"\\obj(?:data|emb|update|autlink)\b", raw)
    if m:
        findings.append(_found("active_content", "HIGH", messages.make("finding.active_rtf"),
                               messages.make("where.byte", offset=m.start())))
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda x: bytes([int(x.group(1), 16)]).decode("cp1251", "replace"), raw)
    return re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text)[:MAX_TEXT]


def extract(path, kind, findings, keys):
    """Текст документа для проверки. Попутно добавляет находки, видимые только в устройстве файла,
    а для письма — ключи сверки с архивом."""
    t = kind.type
    if t in ("docx", "xlsx", "pptx", "vsdx"):
        return _ooxml_text(path, findings)
    if t in ("odt", "ods", "odp", "epub"):
        return _odf_text(path)
    if t == "pdf":
        return _pdf_text(path, findings)
    if t == "eml":
        return _eml_text(path, findings, keys)
    if t == "msg":
        return _msg_text(path, findings, keys)
    if t in ("doc", "xls", "ppt"):
        return _strings(path)
    if t == "rtf":
        return _rtf_text(path, findings)
    with open(path, "rb") as f:
        data = f.read(MAX_TEXT * 2)
    if t == "html":
        return _html_text(_decode(data), findings)
    text = _decode(data)
    _markup_findings(text, findings)
    return text


# ── решение ─────────────────────────────────────────────────────
def decide(family, findings):
    score = sum(LEVELS[f.level] for f in findings)
    if family == "executable":
        return "quarantine", max(score, LEVELS["CRITICAL"])
    if any(f.level == "CRITICAL" for f in findings):
        return "quarantine", score
    if family == "other":
        return "skip", score
    return ("accept" if score <= THRESHOLD else "review"), score


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file(path, name=None, known=None):
    """Проверяет один файл. name — имя для отчёта и для сверки расширения, known — sha256 уже принятых."""
    name = name or os.path.basename(path)
    size = os.path.getsize(path)
    digest = sha256_of(path)
    kind = filetype_sniff.detect(path, name)
    v = {"name": name, "sha256": digest, "size": size, "family": kind.family, "type": kind.type,
         "decision": None, "score": 0, "findings": [], "reason": "", "reason_msg": None, "mid": None, "fp": None, "date": None}
    findings = []

    def done(decision, score, reason=None):
        """reason — сообщение из каталога: в запись идут и прежний русский текст (reason), и само сообщение (reason_msg)."""
        v.update(decision=decision, score=score, reason=str(reason) if reason else "", reason_msg=reason.to_json() if reason else None,
                 findings=[f._asdict() for f in findings])
        return v

    if known and digest in known:
        return done("duplicate", 0, messages.make("reason.duplicate_exact"))
    ext = filetype_sniff.ext_of(name)
    if kind.family == "executable":
        what = kind.type if kind.type in messages.WORDS["finding.executable"]["kind"] else "other"
        if ext and ext not in filetype_sniff.EXEC_EXT:
            msg = messages.make("finding.executable_renamed", kind=what, ext=ext)
        else:
            msg = messages.make("finding.executable", kind=what)
        findings.append(_found("executable", "CRITICAL", msg, _whole()))
        return done(*decide("executable", findings), reason=messages.make("reason.executable"))
    if kind.family == "other":
        if kind.type == "empty":
            reason = messages.make("reason.empty_file")
        elif kind.type == "broken-zip":
            reason = messages.make("reason.broken_file")
        elif kind.type == "macos-sidecar":
            reason = messages.make("reason.macos_sidecar")
        elif kind.type == "iwork":
            reason = messages.make("reason.iwork")
        elif kind.type == "ole" and kind.detail == "no-olefile":     # контейнер Office без olefile не разобран: причина — библиотека, а не тип
            reason = messages.make("lib.missing", package="olefile", use="ole", file="requirements-optional.txt")
        else:
            reason = messages.make("reason.not_document")
        return done(*decide("other", findings), reason=reason)
    if kind.family == "archive":
        return done("archive", 0, messages.make("reason.archive"))
    if not kind.ext_ok:
        findings.append(_found("mismatch", "MEDIUM", messages.make("finding.mismatch", ext=ext, detected=kind.type),
                               messages.make("where.file_name")))
    if kind.macros:
        findings.append(_found("macros", "HIGH", messages.make("finding.macros"), _whole()))
    if kind.detail == "broken":
        findings.append(_found("unreadable", "HIGH", messages.make("finding.broken_document"), _whole()))
    elif kind.family == "image":
        findings.append(_found("needs_vision", "LOW", messages.make("finding.needs_vision_image"), _whole()))
    else:
        try:
            text = extract(path, kind, findings, v)
        except Unreadable as e:
            findings.append(_found("unreadable", "HIGH", e.message, _whole(), str(e)[:QUOTE]))
            text = ""
        except Exception as e:                    # сбой разбора не должен ронять приёмку всей пачки
            findings.append(_found("unreadable", "HIGH", messages.make("finding.unreadable_crash", error_type=type(e).__name__), _whole()))
            text = ""
        findings.extend(scan_text(text, mail=kind.family == "mail" or kind.type == "html"))
    decision, score = decide(kind.family, findings)
    return done(decision, score)


def apply_llm(v, path, checker):
    """Проверка моделью после статических правил (FR-25..FR-27). Меняет запись отчёта на месте.

    Модель только добавляет находки: прежние остаются, решение пересчитывается по сумме и смягчиться не может.
    Изображение и скан без текста идут модели картинкой; текст, который она с них прочла, проходит те же
    статические правила. Сбой проверки документ не теряет: он идёт на утверждение.
    """
    if v.get("decision") not in ("accept", "review"):
        return v
    import llm_check
    name = v["name"]
    try:
        kind = filetype_sniff.detect(path, name)
        if any(f["rule"] == "needs_vision" for f in v["findings"]):
            pages, total = llm_check.render_pages(path, kind.type)
            result = checker.check_images(pages, name, total=total, ocr=lambda: llm_check.docling_text(path))
            extra = []
            for f in scan_text(result.text or ""):          # scan_text находит только строку: «строка N»
                where = messages.make("where.recognized_line", line=f.where_msg.args["line"])
                extra.append(Finding(f.rule, f.level, str(where), f.quote, f.msg, where))
            result = result._replace(findings=list(result.findings) + extra)
        else:
            result = checker.check_text(extract(path, kind, [], {}), name, kind.type)
    except Exception as e:                        # сбой проверки не должен ронять приёмку и терять документ
        miss = _found("llm_unchecked", "HIGH", messages.make("finding.llm_unchecked_failed", error_type=type(e).__name__), _whole())
        result = llm_check.Result([miss], None, "unchecked", "")
    # записи со старыми полями (без msg и where_msg) и с новыми читаются одинаково
    findings = [Finding(**{k: f[k] for k in Finding._fields if k in f}) for f in v["findings"]] + list(result.findings)
    decision, score = decide(v["family"], findings)
    if v["decision"] == "review":                 # смягчить решение модель не может ни при каком ответе
        decision = "review" if decision == "accept" else decision
    v.update(findings=[f._asdict() for f in findings], decision=decision, score=max(score, v["score"]), checked_by=result.model,
             checked_cloud=bool(result.cloud))        # проверила запасная облачная точка: текст документа ушёл с машины
    return v
