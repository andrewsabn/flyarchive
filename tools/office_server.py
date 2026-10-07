r"""Инструменты работы с документами и графикой для языковой модели.

Поиск по архиву отдаёт фрагменты. Этого хватает, чтобы найти документ, но не
чтобы разобрать его целиком: в чанке 420 символов, а в договоре сорок страниц.
Здесь модель получает четыре умения:

    read_document   прочитать найденный документ целиком — pdf, docx, xlsx,
                    pptx, msg, eml, vsdx
    make_chart      построить график по числам и получить ссылку на картинку
    make_diagram    нарисовать схему на языке graphviz
    make_document   собрать docx, xlsx, pptx или pdf и отдать ссылкой

Visio читается (.vsdx; двоичный .vsd — нет), но не создаётся: библиотеки записи
.vsdx нет. Схемы делает graphviz — за этим Visio обычно и открывают.

Сервер отдельный от поиска: у него своя очередь, и тяжёлый разбор
стостраничного pdf не задерживает выдачу поиска.

    python3 tools/office_server.py
    http://127.0.0.1:8766/openapi.json
"""
import html, io, json, os, re, shutil, sys, threading, time, traceback, urllib.parse, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import corpus_path
import doctor
import messages
import perms
import settings
import version

_SETTINGS = settings.startup()
BASE = _SETTINGS["home"]
CORPUS = os.path.join(BASE, "corpus")
OUT = os.path.join(BASE, "out")
HOST = "127.0.0.1"                      # адрес прослушивания не настраивается: наружу смотрит один шлюз
PORT = _SETTINGS["office_port"]
MAX_CHARS = 60000          # потолок текста в одном ответе, чтобы не рвать контекст
SUBMIT_DIR = None          # куда кладётся переданный документ; None — входящая папка из настройки
SUBMIT_MAX = _SETTINGS["submit_max_mb"] * 1024 * 1024       # байт в одном переданном документе: настройка в мегабайтах
# тело запроса крупнее не читается. Защитный предел, не настройка: он покрывает документ в base64 с запасом на оболочку запроса
# при любой допустимой настройке submit_max_mb (верхняя граница схемы выбрана так, чтобы документ проходил и через шлюз, у которого тело уже)
BODY_MAX = 32 * 1024 * 1024
RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{n}" for p in ("com", "lpt") for n in range(1, 10)}
KEEP_HOURS = _SETTINGS["out_keep_hours"]    # через сколько часов убирать созданные файлы


# ── нет библиотеки или программы ────────────────────────────────
class Unavailable(messages.CodedError):
    """Инструмент на этой машине работать не может: нет библиотеки по формату или программы. Служба отвечает кодом 503, кодом сообщения из каталога
    (lib.missing, doctor.tool_missing) и подсказкой, что поставить, — а не обрывом соединения и не трассировкой в журнале."""
    status = 503


def missing_library(error):
    """Unavailable с сообщением lib.missing, если ImportError — это отсутствие библиотеки из таблицы doctor.LIBRARIES; иначе None (чужой сбой
    импорта, например не хватает зависимости самой библиотеки: он называется как есть)."""
    top = (getattr(error, "name", None) or "").split(".")[0]
    for module, package, must, use in doctor.LIBRARIES:
        if module == top:
            return Unavailable(messages.make("lib.missing", package=package, use=use,
                                             file=doctor.REQUIRED_FILE if must else doctor.OPTIONAL_FILE))
    return None


def failed(error, **extra):
    """Ответ о сбое инструмента: {"error": "Вид: текст", ...}. Нет библиотеки по формату — вместо ответа поднимается Unavailable."""
    if isinstance(error, ImportError):
        refusal = missing_library(error)
        if refusal is not None:
            raise refusal from None
    return {"error": f"{type(error).__name__}: {error}", **extra}


# ── приём документа во входящую папку ───────────────────────────
def submit_document(client, name, content):
    """Кладёт переданный документ во входящую папку. В архив он попадёт только через приёмку.

    Возвращает (код ответа, ответ, сведения для журнала). Документ появляется в папке сразу целым:
    пишется в скрытый каталог и переименовывается, недописанное разбор не увидит."""
    import base64
    import binascii
    import hashlib
    import secrets
    import shutil
    bad_name = (not isinstance(name, str) or not name.strip() or len(name) > 200 or name.startswith(".") or name != name.strip()
                or any(ch in name for ch in "/\\") or any(ord(ch) < 32 for ch in name)
                or name.split(".")[0].lower() in RESERVED)
    if bad_name:
        return 400, {"error": "имя файла: без каталогов и управляющих знаков, не с точки, до 200 знаков"}, {}
    if not isinstance(content, str) or not content:
        return 400, {"error": "content_base64: содержимое файла в base64"}, {"name": name}
    try:
        if len(content) % 4:
            raise ValueError("длина не кратна четырём")
        data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        return 400, {"error": "content_base64: это не base64"}, {"name": name}
    if len(data) > SUBMIT_MAX:
        return 413, {"error": f"документ больше предела {SUBMIT_MAX} байт"}, {"name": name, "size": len(data)}
    folder = SUBMIT_DIR
    if folder is None:
        import inbox
        folder = inbox.load_config(BASE)["inbox"]
    who = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in client)[:40]
    final = os.path.join(folder, f"mcp-{who}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{secrets.token_hex(3)}")
    tmp = os.path.join(folder, "." + os.path.basename(final))
    try:
        os.makedirs(folder, exist_ok=True)
        os.mkdir(tmp, 0o700)
        with open(os.open(os.path.join(tmp, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
        os.rename(tmp, final)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return 500, {"error": "не удалось записать во входящую папку"}, {"name": name, "size": len(data)}
    return 200, {"accepted": True, "name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                 "note": "документ во входящей папке; в архив он попадёт после приёмки при ближайшем разборе"}, \
        {"name": name, "size": len(data)}


# ── чтение документов ───────────────────────────────────────────
def resolve(rel):
    """Путь из выдачи поиска -> реальный файл, с защитой от выхода за корпус."""
    return corpus_path.resolve(rel, CORPUS)


def from_pdf(p, pages):
    import pymupdf
    with pymupdf.open(p) as d:
        total = len(d)
        rng = parse_pages(pages, total)
        parts = [f"[страница {i + 1}]\n" + d[i].get_text() for i in rng]
    return "\n\n".join(parts), {"страниц всего": total, "прочитано": len(rng)}


def from_docx(p, _pages):
    import docx
    d = docx.Document(p)
    out = [x.text for x in d.paragraphs if x.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n".join(out), {"абзацев": len(d.paragraphs), "таблиц": len(d.tables)}


def from_xlsx(p, pages):
    import openpyxl
    wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    names = wb.sheetnames
    want = [s.strip() for s in str(pages).split(",")] if pages else names
    out, used = [], []
    for name in names:
        if name not in want:
            continue
        used.append(name)
        out.append(f"[лист {name}]")
        for row in wb[name].iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                out.append(" | ".join(cells).rstrip(" |"))
    wb.close()
    return "\n".join(out), {"листы": names, "прочитано": used}


def from_pptx(p, _pages):
    from pptx import Presentation
    pr = Presentation(p)
    out = []
    for i, slide in enumerate(pr.slides, 1):
        out.append(f"[слайд {i}]")
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                out.append(sh.text_frame.text)
    return "\n".join(out), {"слайдов": len(pr.slides)}


def from_msg(p, _pages):
    import extract_msg
    m = extract_msg.Message(p)
    head = [f"От: {m.sender}", f"Кому: {m.to}", f"Дата: {m.date}", f"Тема: {m.subject}"]
    att = [a.longFilename or a.shortFilename for a in m.attachments]
    body = m.body or ""
    m.close()
    return "\n".join(head) + "\n\n" + body, {"вложения": att}


def from_eml(p, _pages):
    import email, email.policy
    with open(p, "rb") as f:
        m = email.message_from_binary_file(f, policy=email.policy.default)
    head = [f"От: {m.get('From')}", f"Кому: {m.get('To')}",
            f"Дата: {m.get('Date')}", f"Тема: {m.get('Subject')}"]
    try:
        body = m.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
    except Exception:
        text = ""
    return "\n".join(head) + "\n\n" + re.sub(r"<[^>]+>", " ", text), {}


def from_vsdx(p, _pages):
    """Visio — это zip с XML. Вытаскиваем текст фигур: названия блоков и подписи."""
    out = []
    with zipfile.ZipFile(p) as z:
        pages = sorted(n for n in z.namelist() if re.match(r"visio/pages/page\d+\.xml$", n))
        for i, name in enumerate(pages, 1):
            xml = z.read(name).decode("utf-8", "replace")
            texts = [re.sub(r"<[^>]+>", "", t).strip()
                     for t in re.findall(r"<Text[^>]*>(.*?)</Text>", xml, re.S)]
            texts = [t for t in texts if t]
            if texts:
                out.append(f"[лист {i}]\n" + "\n".join(texts))
    return "\n\n".join(out), {"листов": len(out)}


def from_text(p, _pages):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read(), {}


READERS = {".pdf": from_pdf, ".docx": from_docx, ".xlsx": from_xlsx, ".xlsm": from_xlsx,
           ".pptx": from_pptx, ".msg": from_msg, ".eml": from_eml, ".vsdx": from_vsdx}


MAX_PAGE_PARTS = 100


def parse_pages(spec, total):
    """'1-5' или '3' или '' -> список индексов, не выходящий за границы."""
    if not spec:
        return list(range(min(total, 40)))
    out = set()
    for part in str(spec).split(",")[:MAX_PAGE_PARTS]:
        a, dash, b = part.strip().partition("-")
        try:
            first = int(a)
            last = int(b) if dash else first
        except ValueError:
            continue
        # границы — до построения: «1-9999999999» иначе стало бы множеством из миллиардов чисел
        out.update(range(max(first, 1) - 1, min(last, total)))
    return sorted(out)


def read_document(path, pages):
    p = resolve(path)
    if not p:
        return {"error": "документ не найден или путь вне архива", "path": path}
    ext = os.path.splitext(p)[1].lower()
    if ext == ".vsd":                                 # двоичный формат, не zip: разбор .vsdx на нём давал бы ошибку про «не zip»
        return {"error": "старый формат Visio (.vsd) не читается; читается .vsdx", "path": path}
    fn = READERS.get(ext, from_text if ext in (".txt", ".md", ".csv", ".json", ".xml") else None)
    if not fn:
        return {"error": f"формат {ext} не читается", "path": path}
    try:
        text, info = fn(p, pages)
    except Exception as e:
        return failed(e, path=path)
    text = text.strip()
    cut = len(text) > MAX_CHARS
    return {"path": path, "name": os.path.basename(p), "info": info,
            "truncated": cut, "chars": len(text),
            "text": text[:MAX_CHARS] + ("\n… обрезано, запроси нужные страницы" if cut else "")}


# ── создание файлов ─────────────────────────────────────────────
_converter = None
RICH_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
            ".odt", ".ods", ".odp", ".epub", ".html", ".md", ".csv"}


def converter():
    """Один разборщик на процесс: загрузка моделей вёрстки занимает секунды."""
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter
        _converter = DocumentConverter()
    return _converter


def read_rich(path, pages):
    """Разбор с сохранением вёрстки: таблицы остаются таблицами."""
    p = resolve(path)
    if not p:
        return {"error": "документ не найден или путь вне архива", "path": path}
    ext = os.path.splitext(p)[1].lower()
    if ext not in RICH_EXT:
        return {"error": f"формат {ext} этому разборщику не подходит, "
                         f"возьми read_document", "path": path}
    try:
        kw = {}
        if pages:
            rng = parse_pages(pages, 10000)
            if rng:
                kw["page_range"] = (rng[0] + 1, rng[-1] + 1)
        res = converter().convert(p, **kw)
        doc = res.document
        md = doc.export_to_markdown()
        tables = len(getattr(doc, "tables", []) or [])
    except Exception as e:
        return failed(e, path=path)
    cut = len(md) > MAX_CHARS
    return {"path": path, "name": os.path.basename(p), "tables": tables,
            "chars": len(md), "truncated": cut,
            "text": md[:MAX_CHARS] + ("\n… обрезано, запроси нужные страницы" if cut else "")}


def file_url(name):
    """Ссылка на созданный файл. Подписана и живёт сутки: браузер токен не шлёт,
    а ссылка из чата должна открываться."""
    url = f"http://127.0.0.1:{PORT}/file?n=" + urllib.parse.quote(name)
    guard = Handler.guard
    return url + "&" + guard.link_query("file", name) if guard else url


def out_path(prefix, ext):
    """Имя нового файла в каталоге результатов. Каталог и файл создаются закрытыми (700 и 600) при любой маске процесса: файл заводится пустым
    сразу, библиотека потом пишет в него, не меняя прав."""
    os.makedirs(OUT, mode=0o700, exist_ok=True)
    name = f"{prefix}-{time.strftime('%H%M%S')}-{os.urandom(3).hex()}{ext}"
    path = os.path.join(OUT, name)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return path, name


def sweep():
    """Созданные файлы живут двое суток: иначе каталог растёт без предела."""
    if not os.path.isdir(OUT):
        return
    edge = time.time() - KEEP_HOURS * 3600
    for n in os.listdir(OUT):
        p = os.path.join(OUT, n)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < edge:
                os.remove(p)
        except OSError:
            pass


def make_chart(kind, title, labels, series, ylabel):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kind = (kind or "bar").lower()
    if not series:
        return {"error": "нужен хотя бы один ряд в series"}
    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    names = list(series.keys())
    if kind == "pie":
        ax.pie(series[names[0]], labels=labels, autopct="%1.0f%%")
    elif kind in ("line", "область"):
        for n in names:
            ax.plot(labels, series[n], marker="o", label=n)
    elif kind == "barh":
        ax.barh(labels, series[names[0]])
    else:
        w = 0.8 / max(1, len(names))
        for i, n in enumerate(names):
            ax.bar([x + i * w for x in range(len(labels))], series[n], width=w, label=n)
        ax.set_xticks([x + 0.4 - w / 2 for x in range(len(labels))])
        ax.set_xticklabels(labels, rotation=30, ha="right")
    if title:
        ax.set_title(title)
    if ylabel:
        ax.set_ylabel(ylabel)
    if len(names) > 1 and kind != "pie":
        ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    p, name = out_path("chart", ".png")
    try:
        fig.savefig(p)
    except BaseException:
        os.remove(p)
        raise
    finally:
        plt.close(fig)
    url = file_url(name)
    return {"url": url, "file": p, "markdown": f"![{title or 'график'}]({url})"}


def make_diagram(dot, fmt):
    import subprocess
    fmt = (fmt or "png").lower()
    if fmt not in ("png", "svg"):
        fmt = "png"
    if shutil.which("dot") is None:
        raise Unavailable(messages.make("doctor.tool_missing", name="dot", use="dot"))
    p, name = out_path("diagram", "." + fmt)
    try:
        r = subprocess.run(["dot", "-T" + fmt, "-o", p], input=(dot or "").encode("utf-8"),
                           capture_output=True, timeout=60)
    except Exception as e:
        os.remove(p)
        return {"error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0:
        os.remove(p)
        return {"error": "graphviz отказался: " + r.stderr.decode("utf-8", "replace")[:400]}
    return {"url": file_url(name), "file": p}


def build_docx(title, blocks, p):
    import docx
    d = docx.Document()
    if title:
        d.add_heading(title, 0)
    for b in blocks:
        t, v = b.get("type", "text"), b.get("value")
        if t == "heading":
            d.add_heading(str(v), int(b.get("level", 1)))
        elif t == "bullets":
            for item in v or []:
                d.add_paragraph(str(item), style="List Bullet")
        elif t == "table":
            rows = v or []
            if rows:
                tb = d.add_table(rows=len(rows), cols=len(rows[0]))
                tb.style = "Table Grid"
                for i, row in enumerate(rows):
                    for j, cell in enumerate(row):
                        tb.cell(i, j).text = str(cell)
        else:
            d.add_paragraph(str(v))
    d.save(p)


def build_xlsx(title, blocks, p):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, b in enumerate(blocks, 1):
        ws = wb.create_sheet((b.get("name") or f"Лист{i}")[:31])
        for row in b.get("value") or []:
            ws.append([("" if c is None else c) for c in row])
    if not wb.sheetnames:
        wb.create_sheet(title or "Лист1")
    wb.save(p)


def build_pptx(title, blocks, p):
    from pptx import Presentation
    from pptx.util import Pt
    pr = Presentation()
    if title:
        s = pr.slides.add_slide(pr.slide_layouts[0])
        s.shapes.title.text = title
    for b in blocks:
        s = pr.slides.add_slide(pr.slide_layouts[1])
        s.shapes.title.text = str(b.get("name") or b.get("value") or "")
        body = s.placeholders[1].text_frame
        items = b.get("bullets") or ([b.get("value")] if b.get("name") else [])
        for j, item in enumerate([x for x in items if x]):
            para = body.paragraphs[0] if j == 0 else body.add_paragraph()
            para.text = str(item)
            para.font.size = Pt(18)
    pr.save(p)


PDF_FONT = _SETTINGS["pdf_font"]            # свой файл шрифта с кириллицей для pdf; пусто — ищется среди обычных (doctor.find_font)
_PDF_FACES = ("FlyArchiveSans", "FlyArchiveSans-Bold")           # имена, под которыми шрифт зарегистрирован в reportlab
_pdf_registered = {}
_pdf_lock = threading.Lock()


def _pdf_texts(title, blocks):
    """Все строки, которые попадут в pdf: заголовок, абзацы, пункты списков, ячейки таблиц."""
    out = [str(title or "")]
    for b in blocks:
        t, v = b.get("type", "text"), b.get("value")
        if t == "bullets":
            out += [str(item) for item in v or []]
        elif t == "table" and v:
            out += [str(c) for row in v for c in row]
        else:
            out.append(str(v))
    return out


def _pdf_latin(text):
    """Строка набирается стандартными шрифтами pdf (латиница и знаки Windows-1252), шрифта с кириллицей не требует."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _pdf_faces(font):
    """Регистрирует шрифт в reportlab (один раз на сочетание файлов) и называет начертания: (обычное, жирное)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular, bold = _PDF_FACES
    with _pdf_lock:
        if _pdf_registered.get("font") != font:
            pdfmetrics.registerFont(TTFont(regular, font.regular))
            pdfmetrics.registerFont(TTFont(bold, font.bold or font.regular))
            pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold, italic=regular, boldItalic=bold)
            _pdf_registered["font"] = font
    return regular, bold


def build_pdf(title, blocks, p):
    """pdf стандартными шрифтами reportlab кириллицы не знает: русский текст выходил бы квадратами. Поэтому, если на машине есть шрифт с кириллицей
    (настройка pdf_font или обычные места, doctor.find_font), документ набирается им целиком; шрифта нет, а в тексте есть знаки вне латиницы — отказ
    Unavailable (503) с названием пакета; чисто латинский текст без шрифта собирается стандартными шрифтами."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    font = doctor.find_font(PDF_FONT)
    regular = bold = None
    if font is not None:
        regular, bold = _pdf_faces(font)
    elif not all(_pdf_latin(text) for text in _pdf_texts(title, blocks)):
        raise Unavailable(messages.make("doctor.font_missing", package=doctor.FONT_PACKAGE))
    st = getSampleStyleSheet()
    if regular:
        for name, face in (("Title", bold), ("Heading2", bold), ("BodyText", regular)):
            st[name].fontName = face
    story = []
    if title:
        story += [Paragraph(html.escape(title), st["Title"]), Spacer(1, 10)]
    for b in blocks:
        t, v = b.get("type", "text"), b.get("value")
        if t == "heading":
            story.append(Paragraph(html.escape(str(v)), st["Heading2"]))
        elif t == "bullets":
            for item in v or []:
                story.append(Paragraph("• " + html.escape(str(item)), st["BodyText"]))
        elif t == "table" and v:
            tb = Table([[str(c) for c in row] for row in v])
            style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]
            if regular:
                style += [("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold)]
            tb.setStyle(TableStyle(style))
            story += [tb, Spacer(1, 8)]
        else:
            story.append(Paragraph(html.escape(str(v)), st["BodyText"]))
        story.append(Spacer(1, 6))
    SimpleDocTemplate(p, pagesize=A4).build(story)




PALETTE = ["#dbeafe", "#dcfce7", "#fef3c7", "#fae8ff", "#ffe4e6",
           "#e0f2fe", "#ecfccb", "#f1f5f9"]


def make_landscape(title, groups, edges, direction):
    """Схема по краткому описанию: группы с узлами и связи между ними.

    Модель описывает состав, оформление берёт на себя сервер. Так аргумент
    вызова остаётся коротким — на сорока узлах около полутора тысяч символов
    вместо десяти тысяч исходника graphviz."""
    if not groups:
        return {"error": "нужен хотя бы один блок в groups"}
    ids, lines = {}, []
    rd = (direction or "TB").upper()
    if rd not in ("TB", "LR", "BT", "RL"):
        rd = "TB"
    lines.append("digraph L {")
    lines.append(f'  graph [rankdir={rd}, splines=ortho, nodesep=0.35, ranksep=0.6, '
                 f'fontname="DejaVu Sans"{", label=" + q(title) + ", labelloc=t, fontsize=18" if title else ""}];')
    lines.append('  node [shape=box, style="rounded,filled", fontname="DejaVu Sans", '
                 'fontsize=11, margin="0.15,0.08"];')
    lines.append('  edge [color="#64748b", arrowsize=0.7];')

    for gi, g in enumerate(groups):
        name = str(g.get("name") or f"Блок {gi + 1}")
        color = PALETTE[gi % len(PALETTE)]
        lines.append(f"  subgraph cluster_{gi} {{")
        lines.append(f'    label={q(name)}; style="rounded,filled"; '
                     f'fillcolor="#f8fafc"; color="#cbd5e1"; fontsize=13;')
        for node in g.get("nodes") or []:
            nid = f"n{len(ids)}"
            ids[str(node)] = nid
            lines.append(f'    {nid} [label={q(str(node))}, fillcolor="{color}"];')
        lines.append("  }")

    for e in edges or []:
        if isinstance(e, dict):
            a, b, lbl = e.get("from"), e.get("to"), e.get("label")
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            a, b, lbl = e[0], e[1], (e[2] if len(e) > 2 else None)
        else:
            continue
        # связь к неизвестному узлу пропускаем молча: модель иногда называет
        # блок, а не его содержимое, и падать из-за этого незачем
        if str(a) in ids and str(b) in ids:
            tail = f" [label={q(str(lbl))}, fontsize=9]" if lbl else ""
            lines.append(f"  {ids[str(a)]} -> {ids[str(b)]}{tail};")
    lines.append("}")
    return make_diagram("\n".join(lines), "png")


def q(s):
    """Кавычки для graphviz: экранируем то, что ломает разбор."""
    return '"' + str(s).replace("\\", " ").replace('"', "'").replace("\n", " ") + '"'
BUILDERS = {"docx": build_docx, "xlsx": build_xlsx, "pptx": build_pptx, "pdf": build_pdf}


def make_document(kind, title, blocks):
    kind = (kind or "docx").lower()
    fn = BUILDERS.get(kind)
    if not fn:
        return {"error": f"не умею {kind}, доступны: " + ", ".join(BUILDERS)}
    p, name = out_path("doc", "." + kind)
    try:
        fn(title, blocks or [], p)
    except Unavailable:
        os.remove(p)                                  # отказ «нет шрифта»: файла нет, служба отвечает 503 с подсказкой, а не словарём ошибки
        raise
    except Exception as e:
        os.remove(p)                                  # недособранный файл не остаётся
        return failed(e)
    return {"url": file_url(name), "file": p, "size": os.path.getsize(p)}


# ── описание для модели ─────────────────────────────────────────
def par(name, desc, typ="string", req=False):
    return {"name": name, "in": "query", "required": req,
            "description": desc, "schema": {"type": typ}}


def body_schema(props, required):
    return {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "properties": props, "required": required}}}}


def spec():
    ok = {"200": {"description": "готово", "content":
                  {"application/json": {"schema": {"type": "object"}}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "Документы и графика",
                 "version": version.VERSION,
                 "description": "Чтение документов архива целиком и создание "
                                "файлов: графики, схемы, Word, Excel, PowerPoint, PDF."},
        "servers": [{"url": f"http://127.0.0.1:{PORT}"}],
        "paths": {
            "/read": {"get": {
                "operationId": "read_document",
                "summary": "Прочитать документ из архива целиком",
                "description": "Путь берётся из поля path выдачи search_archive. "
                               "Читает pdf, docx, xlsx, pptx, msg, eml, vsdx.",
                "parameters": [
                    par("path", "Путь из выдачи поиска, поле path", req=True),
                    par("pages", "Для pdf — страницы вида 1-5 или 3,7. "
                                 "Для xlsx — имена листов через запятую. "
                                 "Пусто — первые 40 страниц или все листы"),
                ], "responses": ok}},
            "/rich": {"get": {
                "operationId": "read_document_rich",
                "summary": "Разобрать документ с сохранением таблиц",
                "description":
                    "Медленнее обычного чтения в разы, зато таблицы остаются "
                    "таблицами, а не потоком слов. Бери, когда в документе "
                    "важна таблица: реестр, смета, расписание, отчёт. "
                    "Читает и старые форматы: doc, xls, ppt, odt, ods, odp, epub. "
                    "Для вопроса «о чём документ» хватает read_document.",
                "parameters": [
                    par("path", "Путь из выдачи поиска, поле path", req=True),
                    par("pages", "Страницы вида 1-5; пусто — весь документ"),
                ], "responses": ok}},
            "/chart": {"post": {
                "operationId": "make_chart",
                "summary": "Построить график и вернуть ссылку на картинку",
                "requestBody": body_schema({
                    "kind": {"type": "string",
                             "description": "bar, barh, line или pie", "default": "bar"},
                    "title": {"type": "string"},
                    "ylabel": {"type": "string", "description": "Подпись оси значений"},
                    "labels": {"type": "array", "items": {"type": "string"},
                               "description": "Подписи по горизонтали"},
                    "series": {"type": "object",
                               "description": "Ряды: имя ряда -> массив чисел. "
                                              "Для pie достаточно одного ряда"},
                }, ["labels", "series"]), "responses": ok}},
            "/diagram": {"post": {
                "operationId": "make_diagram",
                "summary": "Нарисовать схему на языке graphviz",
                "description": "Замена Visio: блок-схемы, архитектурные карты, "
                               "связи систем. Синтаксис DOT.",
                "requestBody": body_schema({
                    "dot": {"type": "string",
                            "description": "Исходник DOT, например: "
                                           "digraph G { Экран -> Хранилище -> Отчёт }"},
                    "fmt": {"type": "string", "description": "png или svg", "default": "png"},
                }, ["dot"]), "responses": ok}},
            "/landscape": {"post": {
                "operationId": "make_landscape",
                "summary": "Схема ландшафта по краткому описанию — предпочитай её",
                "description":
                    "Рисует карту частей: группы в рамках, связи между ними. "
                    "Оформление делает сервер, от тебя нужен только состав. "
                    "Пользуйся этим вместо make_diagram везде, где схема — это "
                    "блоки по слоям и стрелки между ними: устройство "
                    "приложения, этапы работ, карта связей.",
                "requestBody": body_schema({
                    "title": {"type": "string"},
                    "direction": {"type": "string",
                                  "description": "TB сверху вниз или LR слева направо",
                                  "default": "TB"},
                    "groups": {"type": "array", "items": {"type": "object"},
                               "description": "Слои: [{name: Экраны, "
                                              "nodes: [Маршрут, Журнал прогулок]}, "
                                              "{name: Данные, nodes: [Хранилище]}]"},
                    "edges": {"type": "array", "items": {},
                              "description": "Связи парами имён узлов: "
                                             "[[Маршрут, Хранилище], [Журнал прогулок, Хранилище]]. "
                                             "Можно с подписью третьим элементом"},
                }, ["groups"]), "responses": ok}},
            "/document": {"post": {
                "operationId": "make_document",
                "summary": "Собрать документ Word, Excel, PowerPoint или PDF",
                "requestBody": body_schema({
                    "kind": {"type": "string", "description": "docx, xlsx, pptx или pdf"},
                    "title": {"type": "string"},
                    "blocks": {"type": "array", "items": {"type": "object"},
                               "description":
                                   "Для docx и pdf: [{type: heading|text|bullets|table, "
                                   "value: ...}]. Для xlsx: [{name: имя листа, "
                                   "value: [[строка], [строка]]}]. "
                                   "Для pptx: [{name: заголовок слайда, bullets: [пункты]}]"},
                }, ["kind"]), "responses": ok}},
        },
    }


# ── сервер ──────────────────────────────────────────────────────
class Server(ThreadingHTTPServer):
    """Очередь по умолчанию равна пяти — модель шлёт вызовы пачками."""
    request_queue_size = 128
    daemon_threads = True


class Handler(auth.Guarded, BaseHTTPRequestHandler):
    server_version = "flyarchive-office"

    def log_message(self, fmt, *a):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % a if a else fmt}", flush=True)

    def send(self, obj, ctype="application/json; charset=utf-8", code=200):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def tool(self, fn, *args):
        """Вызов инструмента и ответ на него. Нет библиотеки или программы — отказ 503 с кодом сообщения и подсказкой; неожиданный сбой — 500
        с видом сбоя, трассировка остаётся в журнале службы как подробность. Клиент в обоих случаях получает ответ, а не обрыв соединения."""
        try:
            answer = fn(*args)
        except Unavailable as e:
            return self.refuse(e)
        except ImportError as e:
            refusal = missing_library(e)
            return self.refuse(refusal) if refusal else self.crash(e)
        except Exception as e:
            return self.crash(e)
        return self.send(answer)

    def refuse(self, e):
        """Отказ инструмента: {"error": текст, "code": код сообщения}, как у службы поиска; переходник MCP читает error строкой."""
        self._ba["outcome"] = "отказ: " + e.message.code
        return self.send({"error": str(e), "code": e.message.code}, code=e.status)

    def crash(self, e):
        traceback.print_exc()
        self._ba["outcome"] = "сбой: " + type(e).__name__
        return self.send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if n > BODY_MAX:
            self.close_connection = True
            return {}
        try:
            b = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}
        return b if isinstance(b, dict) else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": (q.get(k) or [d])[0]
        if u.path == "/file":
            # созданный файл открывается токеном или подписанной ссылкой из ответа
            if not self.admit("read", "/file", {"n": g("n")}, link=("file", g("n"), g("e"), g("s"))):
                return
            return self.serve_file(g("n"))
        if not self.admit("read", u.path[:80], {k: g(k) for k in ("path", "pages") if g(k)}):
            return
        if u.path == "/openapi.json":
            return self.send(spec())
        if u.path == "/read":
            return self.tool(read_document, g("path"), g("pages"))
        if u.path == "/rich":
            return self.tool(read_rich, g("path"), g("pages"))
        if u.path == "/":
            return self.send({"инструменты": ["read_document", "read_document_rich",
                                              "make_chart",
                                              "make_landscape", "make_diagram",
                                              "make_document"],
                              "описание": f"http://127.0.0.1:{PORT}/openapi.json"})
        self.send({"error": "не найдено"}, code=404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self.admit("full", u.path[:80]):       # создание файлов — уровень «полный»
            return
        b = self.body()
        if u.path == "/submit":
            code, answer, facts = submit_document(self.client.name, b.get("name"), b.get("content_base64"))
            self.note(**facts)
            return self.send(answer, code=code)
        self.note(**{k: b[k] for k in ("kind", "title", "fmt", "direction") if b.get(k)})
        sweep()
        if u.path == "/chart":
            return self.tool(make_chart, b.get("kind"), b.get("title"), b.get("labels"), b.get("series"), b.get("ylabel"))
        if u.path == "/diagram":
            return self.tool(make_diagram, b.get("dot"), b.get("fmt"))
        if u.path == "/landscape":
            return self.tool(make_landscape, b.get("title"), b.get("groups"), b.get("edges"), b.get("direction"))
        if u.path == "/document":
            return self.tool(make_document, b.get("kind"), b.get("title"), b.get("blocks"))
        self.send({"error": "не найдено"}, code=404)

    def serve_file(self, name):
        """Отдаёт созданный файл. Имя — только базовое, без переходов по каталогам."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return self.send({"error": "плохое имя"}, code=400)
        p = os.path.join(OUT, name)
        if not os.path.isfile(p):
            return self.send({"error": "файл не найден или уже убран"}, code=404)
        types = {".png": "image/png", ".svg": "image/svg+xml", ".pdf": "application/pdf"}
        ctype = types.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype not in ("image/png", "application/pdf"):
            # подписи схемы приходят от модели: svg открывается в песочнице, сценарий в нём не выполнится
            self.send_header("Content-Security-Policy", "sandbox")
        if ctype == "application/octet-stream":
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)


def selftest():
    assert resolve("../../etc/passwd") is None            # за корпус не выпускаем
    assert resolve("/etc/passwd") is None
    assert parse_pages("1-3", 10) == [0, 1, 2]
    assert parse_pages("2,5", 10) == [1, 4]
    assert parse_pages("", 100) == list(range(40))        # без указания — первые 40
    assert parse_pages("50-60", 10) == []                 # за границы не выходим
    s = spec()
    ids = {v[m]["operationId"] for v in s["paths"].values() for m in v}
    assert ids == {"read_document", "read_document_rich", "make_chart",
                   "make_landscape", "make_diagram", "make_document"}, ids
    assert ".doc" in RICH_EXT and ".xls" in RICH_EXT      # старые форматы тоже
    assert q('a"b\nc') == '"a\'b c"', q('a"b\nc')
    assert set(BUILDERS) == {"docx", "xlsx", "pptx", "pdf"}
    print("selftest ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--selftest" in sys.argv:
        selftest()
    else:
        perms.close_umask()                                # всё, что служба создаёт, — только владельцу, при любой маске запустившего
        try:
            Handler.guard = auth.Guard("office")
            Handler.guard.startup_check()
        except auth.AuthError as e:
            raise SystemExit(f"служба документов не запущена: {e}")
        os.makedirs(OUT, mode=0o700, exist_ok=True)       # закрытым, при любой маске процесса
        sweep()
        s = Server((HOST, PORT), Handler)
        print(f"слушаю http://{HOST}:{PORT}", flush=True)
        s.serve_forever()
