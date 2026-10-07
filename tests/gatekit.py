"""Заготовки файлов для тестов приёмки: настоящие по содержимому, крошечные по размеру."""
import io
import struct
import tarfile
import zipfile
import zlib

PE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 50 + b"This program cannot be run in DOS mode."
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56
MACHO = b"\xcf\xfa\xed\xfe\x07\x00\x00\x01" + b"\x00" * 24
LNK = b"\x4c\x00\x00\x00\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46" + b"\x00" * 56
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 17
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20
TIFF = b"II*\x00\x08\x00\x00\x00" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 20
RTF = b"{\\rtf1\\ansi Hello}"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
SEVENZ = b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 24
RAR = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 24
EML = (b"Received: from mail.example.org by mx.example.org; Tue, 5 Mar 2024 14:32:00 +0500\r\n"
       b"From: Petrov <petrov@example.org>\r\nTo: ivanov@example.org\r\n"
       b"Subject: =?utf-8?b?0JTQvtCz0L7QstC+0YA=?=\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
       b"Message-ID: <abc123@example.org>\r\nMIME-Version: 1.0\r\n"
       b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
       + "Добрый день. Направляю договор на согласование.\r\n".encode("utf-8"))


def zip_bytes(files, comment=b""):
    """files: {имя: байты}. Обычный zip без пароля."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.comment = comment
    return buf.getvalue()


def ooxml(kind, text="Обычный текст документа.", macros=False, hidden=None, white=None):
    """Минимальный docx/xlsx/pptx/vsdx: достаточно для определения типа и разбора текста docx."""
    main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml",
            "vsdx": "visio/document.xml"}[kind]
    runs = f"<w:r><w:t>{text}</w:t></w:r>"
    if hidden:
        runs += f"<w:r><w:rPr><w:vanish/></w:rPr><w:t>{hidden}</w:t></w:r>"
    if white:
        runs += f'<w:r><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:t>{white}</w:t></w:r>'
    body = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p>{runs}</w:p></w:body></w:document>")
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                               '<Default Extension="xml" ContentType="application/xml"/>'
                               f'<Override PartName="/{main}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                               "</Types>",
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{main}"/>'
                       "</Relationships>",
        main: body,
    }
    if macros:
        files[main.split("/")[0] + "/vbaProject.bin"] = OLE
    return zip_bytes({k: v.encode("utf-8") if isinstance(v, str) else v for k, v in files.items()})


def odf(mimetype="application/vnd.oasis.opendocument.text"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mimetype)
        z.writestr("content.xml", "<office:document-content/>")
    return buf.getvalue()


def jar():
    return zip_bytes({"META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\nMain-Class: Evil\n", "Evil.class": b"\xca\xfe\xba\xbe"})


def apk():
    return zip_bytes({"AndroidManifest.xml": b"\x03\x00\x08\x00", "classes.dex": b"dex\n035\x00"})


def tar_bytes(files, mode="w", links=None):
    """files: {имя: байты}; links: {имя: цель} — символические ссылки."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            t.addfile(info)
    return buf.getvalue()


def zip_raw(entries):
    """entries: [(имя, байты)] — имена пишутся как есть, в том числе с «..» и абсолютные."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def zip_encrypted_flag(files):
    """Zip, у которого в заголовках выставлен признак шифрования (как у архива с паролем)."""
    data = bytearray(zip_bytes(files))
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        while i >= 0:
            data[i + off] |= 0x01
            i = data.find(sig, i + 4)
    return bytes(data)


def zip_cp866(name, data):
    """Zip с русским именем в кодировке DOS, как делает проводник Windows и старые архиваторы."""
    raw = bytearray(zip_bytes({"X" * len(name.encode("cp866")): data}))
    return bytes(raw.replace(b"X" * len(name.encode("cp866")), name.encode("cp866")))


APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        " + b"\x00\x02\x00\x00\x00\x09" + b"\x00" * 133


def eml_exchange():
    """Письмо из Exchange: первые килобайты занимают длинные заголовки Received."""
    hop = (b"Received: from relay-a-8492.mailfront.region1.prod.example.com (2001:db8:20b:4c9::14)\r\n"
           b" by relay-b-7979.mailfront.region2.prod.example.com with HTTPS; Mon, 02 Feb 2026 10:15:30 +0000\r\n"
           b"ARC-Seal: i=1; a=rsa-sha256; s=selector1; d=example.com; cv=none;\r\n"
           b" b=" + b"QUJD" * 40 + b"\r\n")
    return hop * 30 + EML


def iwork():
    return zip_bytes({"Index/Document.iwa": b"\x00\x01\x02", "Metadata/Properties.plist": b"bplist00"})


def pdf_objects(objects, tail=b""):
    """Настоящий PDF из перечня объектов, с таблицей ссылок. objects: [байты тела объекта]."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1) + b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out) + tail


def pdf_plain(text=b"Hello", tail=b""):
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    return pdf_objects([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"], tail)


def pdf_with_js():
    stream = b"BT /F1 12 Tf 72 720 Td (Invoice) Tj ET"
    return pdf_objects([
        b"<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Action /S /JavaScript /JS (app.alert(1)) >>"])


RECEIVED = b"Received: from mx.example.org by mail.example.org; Tue, 5 Mar 2024 14:32:05 +0500\r\nX-Export-Tool: other\r\n"


def letter(mid="<abc123@example.org>", subject="Договор", body="Добрый день. Направляю договор на согласование.",
           sender="Petrov <petrov@example.org>", date="Tue, 05 Mar 2024 14:32:00 +0500", extra=b"", crlf=True, html=None):
    """Письмо с заданными полями. extra — служебные заголовки в начале, как у другой выгрузки."""
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "ivanov@example.org", subject, date
    if mid:
        m["Message-ID"] = mid
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    data = extra + m.as_bytes()
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if crlf else data.replace(b"\r\n", b"\n")


def eml_many_recipients(size=6000):
    """Письмо, у которого первые килобайты занимает список получателей; служебных заголовков в начале нет."""
    people = b",\r\n ".join(b"Person %d <person%d@example.org>" % (i, i) for i in range(size // 40))
    return (b"From: Petrov <petrov@example.org>\r\nTo: ivanov@example.org\r\nCC: " + people + b"\r\n"
            b"Subject: Plan\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\nMessage-ID: <many@example.org>\r\n"
            b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody of the letter.\r\n")


# ── образцы для просмотра ──────────────────────────
def _png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_image(width=8, height=8, color=(200, 30, 30)):
    """Настоящий PNG без Pillow: сплошная заливка цветом RGB."""
    row = b"\x00" + bytes(color) * width
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(row * height, 9)) +
            _png_chunk(b"IEND", b""))


def png_declared(width, height):
    """PNG с заявленным размером width×height, один бит на точку, все точки чёрные. Файл крошечный, потому что строки одинаковы:
    так проверяется предел по заявленному размеру, и в памяти ничего большого не оказывается."""
    packer = zlib.compressobj(9)
    row = bytes(1 + (width + 7) // 8)
    parts, step = [], 256
    for _ in range(height // step):
        parts.append(packer.compress(row * step))
    parts.append(packer.compress(row * (height % step)))
    parts.append(packer.flush())
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", b"".join(parts)) + _png_chunk(b"IEND", b"")


def image_bytes(fmt, size=(40, 30), color=(30, 120, 200)):
    """Картинка в формате Pillow (PNG, JPEG, GIF, BMP, WEBP, TIFF); нужен Pillow: без него тест, которому нужен образец, пропускается с названием библиотеки."""
    import pytest
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def pdf_pages(count=1, box=(612, 792), text=b"Page"):
    """Настоящий PDF из count страниц одинакового размера box (в пунктах)."""
    first = 3
    kids = b" ".join(b"%d 0 R" % (first + i) for i in range(count))
    content, font = first + count, first + count + 1
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    page = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (box[0], box[1], content, font))
    return pdf_objects([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % count,
                        *[page] * count, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
                        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"])


def pdf_blank(count=1, box=(612, 792)):
    """PDF из count пустых страниц: ни текста, ни картинок. Так выглядит скан для индексатора: текстового слоя нет."""
    kids = b" ".join(b"%d 0 R" % (3 + i) for i in range(count))
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] >>" % box
    return pdf_objects([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % count, *[page] * count])


def svg_bytes(width=200, height=100, script=False, text="Hi"):
    """SVG с красным прямоугольником и подписью; script — со сценарием и внешней ссылкой, как у вредоносного."""
    active = ('<script>alert(document.cookie)</script><image href="http://127.0.0.1:9/x.png" width="10" height="10"/>'
              '<rect width="5" height="5" onload="alert(1)"/>' if script else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">{active}'
            f'<rect width="{width}" height="{height}" fill="red"/><text x="10" y="50">{text}</text></svg>').encode("utf-8")


# ── образцы для просмотра писем, архивов и календаря ─
_ATTACHMENT_TYPES = {"png": ("image", "png"), "pdf": ("application", "pdf"), "txt": ("text", "plain"), "zip": ("application", "zip")}


def eml_with(attachments=(), subject="Договор", body="Добрый день. Направляю договор на согласование.", html=None, to="ivanov@example.org",
             cc=None, sender="Petrov <petrov@example.org>", date="Tue, 05 Mar 2024 14:32:00 +0500"):
    """Письмо с вложениями. attachments: [(имя, байты)] — вложение любого типа по расширению имени, [(имя, EmailMessage)] — вложенное письмо."""
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"], m["Message-ID"] = sender, to, subject, date, "<with@example.org>"
    if cc:
        m["Cc"] = cc
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    for name, data in attachments:
        if isinstance(data, bytes):
            maintype, subtype = _ATTACHMENT_TYPES.get(name.rsplit(".", 1)[-1].lower(), ("application", "octet-stream"))
            m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        else:
            m.add_attachment(data, filename=name)
    return m.as_bytes()


def eml_message(**kw):
    """То же письмо как объект: чтобы положить его вложением в другое."""
    import email
    from email import policy
    return email.message_from_bytes(eml_with(**kw), policy=policy.default)


def eml_declared(size=1 << 30):
    """Письмо, у которого вложение объявлено в гигабайт (size в заголовке), а тела нет: настоящий размер вложения — ноль."""
    return (b"From: Petrov <petrov@example.org>\r\nTo: ivanov@example.org\r\nSubject: Declared\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            b"Message-ID: <declared@example.org>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"B\"\r\n\r\n"
            b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody.\r\n"
            b"--B\r\nContent-Type: application/pdf; name=\"big.pdf\"\r\nContent-Disposition: attachment; filename=\"big.pdf\"; size="
            + str(size).encode() + b"\r\nContent-Transfer-Encoding: base64\r\n\r\n\r\n--B--\r\n")


# Составной файл OLE (CFB версии 3) и письмо Outlook (.msg) на нём: потоки меньше 4096 байт лежат в мини-потоке, как у настоящих
_FREE, _END, _FATSECT, _NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
_SECTOR, _MINI, _CUTOFF = 512, 64, 4096


def cfb(tree):
    """tree: {имя: байты | (байты, объявленный размер) | словарь — хранилище}. Объявленный размер можно соврать: так делает вредный файл."""
    entries = []

    def add(name, kind, payload):
        entries.append({"name": name, "kind": kind, "payload": payload, "child": _NOSTREAM, "right": _NOSTREAM, "start": _END, "size": 0})
        return len(entries) - 1

    def walk(node, children):
        ids = []
        for name, value in children.items():
            if isinstance(value, dict):
                child = add(name, 1, None)
                walk(child, value)
            else:
                child = add(name, 2, value)
            ids.append(child)
        for a, b in zip(ids, ids[1:]):
            entries[a]["right"] = b
        if ids:
            entries[node]["child"] = ids[0]

    walk(add("Root Entry", 5, None), tree)
    sectors, edges, mini_data, mini_fat = [], {}, bytearray(), []

    def chain(data):
        count = -(-len(data) // _SECTOR)
        first = len(sectors)
        for i in range(count):
            sectors.append(data[i * _SECTOR:(i + 1) * _SECTOR].ljust(_SECTOR, b"\0"))
            edges[first + i] = first + i + 1 if i + 1 < count else _END
        return first

    for e in entries:
        if e["kind"] != 2:
            continue
        payload, declared = e["payload"] if isinstance(e["payload"], tuple) else (e["payload"], None)
        e["size"] = len(payload) if declared is None else declared
        if not payload:
            e["start"] = _END
        elif len(payload) < _CUTOFF:
            e["start"], count = len(mini_data) // _MINI, -(-len(payload) // _MINI)
            mini_data += payload.ljust(count * _MINI, b"\0")
            mini_fat += [e["start"] + i + 1 if i + 1 < count else _END for i in range(count)]
        else:
            e["start"] = chain(payload)
    entries[0]["start"], entries[0]["size"] = (chain(bytes(mini_data)) if mini_data else _END), len(mini_data)
    mini_first, mini_count = _END, 0
    if mini_fat:
        table = struct.pack("<%dI" % len(mini_fat), *mini_fat)
        mini_first, mini_count = chain(table.ljust(-(-len(table) // _SECTOR) * _SECTOR, b"\xff")), -(-len(table) // _SECTOR)
    dir_len = -(-len(entries) // 4) * 512
    dir_first = len(sectors)
    for i in range(dir_len // _SECTOR):
        sectors.append(b"")
        edges[dir_first + i] = dir_first + i + 1 if i + 1 < dir_len // _SECTOR else _END
    raw = bytearray()
    for e in entries:
        name = e["name"].encode("utf-16-le")
        raw += struct.pack("<64sHBBIII16sIQQIQ", name.ljust(64, b"\0"), len(name) + 2, e["kind"], 1, _NOSTREAM, e["right"], e["child"],
                           b"\0" * 16, 0, 0, 0, e["start"], e["size"])
    raw += struct.pack("<64sHBBIII16sIQQIQ", b"", 0, 0, 0, _NOSTREAM, _NOSTREAM, _NOSTREAM, b"\0" * 16, 0, 0, 0, 0, 0) * ((dir_len - len(raw)) // 128)
    for i in range(dir_len // _SECTOR):
        sectors[dir_first + i] = bytes(raw[i * _SECTOR:(i + 1) * _SECTOR])
    fats = 1
    while len(sectors) + fats > 128 * fats:
        fats += 1
    fat = [_FREE] * (128 * fats)
    for index, nxt in edges.items():
        fat[index] = nxt
    fat_first = len(sectors)
    for i in range(fats):
        fat[fat_first + i] = _FATSECT
    table = struct.pack("<%dI" % len(fat), *fat)
    sectors += [table[i * _SECTOR:(i + 1) * _SECTOR] for i in range(fats)]
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, fats, dir_first, 0, _CUTOFF, mini_first, mini_count, _END, 0)
    struct.pack_into("<109I", header, 76, *([fat_first + i for i in range(fats)] + [_FREE] * (109 - fats)))
    return bytes(header) + b"".join(sectors)


def _utf16(text):
    return text.encode("utf-16-le")


def _property(tag_id, tag_type, value=0):
    return struct.pack("<HHIQ", tag_type, tag_id, 6, value)


def msg_tree(subject="Тема", body="Текст письма", sender="Иван", attachments=(), recipients=(), date=None, html=None, embedded=False):
    """Дерево потоков письма Outlook. attachments: [(имя, байты | (байты, объявленный размер) | дерево вложенного письма)];
    recipients: [("to"|"cc", имя, адрес)]; date — время с поясом; embedded — это дерево само ляжет вложением."""
    tree = {"__substg1.0_001A001F": _utf16("IPM.Note"), "__substg1.0_0037001F": _utf16(subject), "__substg1.0_1000001F": _utf16(body),
            "__substg1.0_0C1A001F": _utf16(sender)}
    if not embedded:
        tree["__nameid_version1.0"] = {"__substg1.0_00020102": b"", "__substg1.0_00030102": b"", "__substg1.0_00040102": b""}
    if html is not None:
        tree["__substg1.0_10130102"] = html.encode("utf-8")
    props = bytearray(24 if embedded else 32)
    struct.pack_into("<IIII", props, 8, len(recipients), len(attachments), len(recipients), len(attachments))
    if date is not None:
        import datetime
        props += _property(0x0039, 0x0040, int((date - datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds() * 10_000_000))
    tree["__properties_version1.0"] = bytes(props)
    for i, (kind, name, address) in enumerate(recipients):
        tree["__recip_version1.0_#%08X" % i] = {"__properties_version1.0": bytes(8) + _property(0x0C15, 3, {"to": 1, "cc": 2}[kind]),
                                                "__substg1.0_3001001F": _utf16(name), "__substg1.0_39FE001F": _utf16(address)}
    for i, (name, data) in enumerate(attachments):
        nested = isinstance(data, dict)
        tree["__attach_version1.0_#%08X" % i] = {
            "__substg1.0_3707001F": _utf16(name), "__substg1.0_3704001F": _utf16(name),
            "__properties_version1.0": bytes(8) + _property(0x3705, 3, 5 if nested else 1),
            "__substg1.0_3701000D" if nested else "__substg1.0_37010102": data}
    return tree


def msg_bytes(**kw):
    """Письмо Outlook (.msg): настоящий файл, который читает extract_msg."""
    return cfb(msg_tree(**kw))


def zip_many(count, prefix="файл"):
    """Zip из count крошечных файлов."""
    return zip_bytes({f"{prefix}-{i}.txt": b"x" for i in range(count)})


def _b64(text):
    import base64
    return base64.b64decode(text)


# 7z, собранный настоящей программой 7z: «а.txt» (12 байт) и «папка/б.bin» (100 байт). Три вида: без пароля, пароль на содержимом, пароль и на оглавлении
SEVENZ_FILES = ("а.txt", "папка/б.bin")
SEVENZ_PLAIN = _b64(
    "N3q8ryccAARl8wuT1AAAAAAAAAAgAAAAAAAAAImUkUABAG/Qv9GA0LjQstC10YIAAQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8wMTIz"
    "NDU2Nzg5Ojs8PT4/QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl9gYWJjAAAAgTMHrg/UkfZdQhX2s0V9aU2L+82eS/i0SY3yu1IqxhfCk+4GAF7YROll25wwRgAt"
    "9wC1OzCtKEH8gaRHjDfwcApNIdsvaxYYzX+vUQ3EFWok89Xpjv6UsHB3++AAABcGdAEJYAAHCwEAASMDAQEFXQAQAAAMcgoB+emlvgAA")
SEVENZ_PASSWORD = _b64(
    "N3q8ryccAARgmIFy/gAAAAAAAAAiAAAAAAAAAKceEhtjh2eUhgtEqVXzwRyNksFrgz7KVHMCCMo/c7Moivn/70c0g9Aq3dnIEKtFXcsKZCAwufeYwsXFKWLpM0viIzRO4qFv"
    "avs+jARfHnuWaOH3PT7xu5iJ22lp20HAWjm9bH6p1TWLRpcMvjU5nWZ3EEXXlFuLWIQf2S3NLKRRoRg+jQAAgTMHrg/VNSexaT46084QAHR6cY3v9/IzidRcD2NcmTpcCQTS"
    "9yytgpxAb0EhppHB0nl9pAG8bgd6ZyGt1C9eZzlkjFmLkzZs1K2lub1FUu5oqByKl82xbKiK9W4a6WqOxQ7zPjQHyzAiAxNEpcqMg3GzUwhmfZGkv5oAABcGgIABCX4ABwsB"
    "AAEjAwEBBV0AEAAADICSCgEbVhm6AAA=")
SEVENZ_HEADERS = _b64(
    "N3q8ryccAAQdZTV+AAEAAAAAAAA+AAAAAAAAAL/rfo7riX0PTdr7V8Fygl/cL1Hjta4hvDdvjwACJJ+T55NFQ6p4q+NytzyfRqgrR4XA9MfpYzQZFMLgVOdQesy6zb7WLDd0LTx3"
    "r0A1bgr/WfbIBiWw7qcYAFd8Evs0by3zbVPUR6mgIgHrw6wA3lssXhGP7zJ7zMHFGvDoJ95+BWvaGGARIuV/dpBHczoV63sUb32OLRa0wHNdfijMz0us08bqTakPs3ovDWHO"
    "c3TpUvusUb66qiSR9aIwsmuMMXGy7A8nULo2Qr425SmQr620unaC/Gy+nJQuwBMlbtLwoZddxGlHyD6xnYXk2MfONl7KIeGlOw1sNSmgL8U1rkW1cRtrFwaAgAEJgIAABwsB"
    "AAIkBvEHARJTD+H1yCz1c3QCKkWt/imBcEkjAwEBBV0AEAAAAQAMfoCSCgEebtyZAAA=")


def _vint(n):
    out = bytearray()
    while True:
        low, n = n & 0x7F, n >> 7
        out.append(low | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _rar_block(kind, body, data=b""):
    fields = _vint(kind) + _vint(2 if data else 0) + (_vint(len(data)) if data else b"") + body
    size = _vint(len(fields))
    return struct.pack("<I", zlib.crc32(size + fields)) + size + fields + data


def rar5(files):
    """Настоящий архив RAR 5 без сжатия: files — [(имя, байты)]. Собран руками по описанию формата; читает программа 7z."""
    out = b"Rar!\x1a\x07\x01\x00" + _rar_block(1, _vint(0))
    for name, data in files:
        raw = name.encode("utf-8")
        out += _rar_block(2, _vint(0x4) + _vint(len(data)) + _vint(0o644) + struct.pack("<I", zlib.crc32(data)) + _vint(0) + _vint(1)
                          + _vint(len(raw)) + raw, data)
    return out + _rar_block(5, _vint(0))


def ics(events, extra=""):
    """Календарь: events — [{"summary", "start", "end", "location", "attendees": число, "params": параметры начала}]; start — «20261005T100000Z»."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Образец//RU"]
    for i, e in enumerate(events):
        lines += ["BEGIN:VEVENT", f"UID:event-{i}@example.test", f"DTSTART{e.get('params', '')}:{e['start']}"]
        if e.get("end"):
            lines.append(f"DTEND{e.get('params', '')}:{e['end']}")
        if "summary" in e:
            lines.append("SUMMARY:" + e["summary"])
        if e.get("location"):
            lines.append("LOCATION:" + e["location"])
        for n in range(e.get("attendees", 0)):
            lines.append(f"ATTENDEE;CN=\"Гость {n}\";ROLE=REQ-PARTICIPANT:mailto:guest{n}@example.test")
        lines += ["BEGIN:VALARM", "TRIGGER:-PT15M", "DESCRIPTION:напоминание", "ACTION:DISPLAY", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n" + extra).encode("utf-8")


# ── образцы для просмотра через контейнер: настоящие по содержимому, LibreOffice их открывает и рисует страницу ──
def _unz(text):
    import base64
    return zlib.decompress(base64.b64decode(text))

# doc: настоящий файл Word 97, собранный LibreOffice из двух строк текста; старые двоичные форматы руками не пишутся
OFFICE_DOC = _unz(
    "eNrtmd9PW2UYx5/3tJS2m1BKRWRTOlYZslF+doJOhcIYdAJlIKhzQ6AgIO1B6JQlXhiNyS40wXjhjcaY4JXGoP4BeqN3Rm92sbt5aWKWabxZsoHf9znvwVJATgsxG+tDPrznnJ7zPs/7"
    "vM/7nuc976+/FF3//Juy3yhNniIbra65yJFyTQCPeYIDTV1bXVtbMy+v5eSekjuqlH1oR//lAdnn+cAJXMANDoCD4AFQAApVvxepMif3ppwjHX9J8tNpSqCcp8uUiZQgYlLrs/LMqsX7"
    "rEpOf/b6zfk7m/Ev3wVy/HtBMfCBBzkmiB4CpeBhUAYOgcPgEfAoKAd+cARUKBsCKB9Tx8dQVoHHQTU4Dk6AGhAEtaAO1IMG0AiaQAicBE+AZtACnuT3GdEp8DR4BjwLWkEbCIN20AFO"
    "g05wBnSBbhABZ8FzoAf0gj4QBf3gHBgAg+B5MASGwQvgRfASOA9eBhfARdXGV+6CuVPAApvbiCGHS+OY+MEIjU7Zfz3T4/P6gj6Z9A/r87GaDv21S/GJRJJjomdAXuvQxzkS5HEQJ/x7"
    "sJn+bvn29Z1jURhpRNbiRcS5Uct5jkhD4G/Pn2sa+92B3tIxq8VplGZVHNcHqDog2gIyisLV1BexUT9oj5RSvMtlXwDRiJ0SXXZnElyM5NEofhvparH/py2dtFouSBOdPH66aAI6YzSN"
    "efVVHjmFVLx8k3zLi+QICIyOvogDih1QfJiikXwyFB1GHIc9sp4mHn9h2B/DrOxHfE3QIuZoOfI85I35hECNGHfL7/NoqvUI4RVyVNkRq9O0wPdqGKN21O1EvFeg3grRyvaNw7o53DGN"
    "+hNsnxe1LbJ9B2Gflx1y0inko7L9F5yiklvYgFLOFN14LsY2ybNifsrQNMT3hcUQzxBR+H+CJtf7IYm/CTyZ2iI3Zg+jLcSeccEzLljgg2fcqNMFE3zs5RC3jychjlo5TU05jZlLHs84"
    "aT1rDalS86RciBozG4fCmZIlYSXMBuGmOIxdgNG9KN9EKV+dsjnSeSHUYyWOB9CRcRrDkzIYGyutaW+D46ZVAE9Dkyja+RkZAGOwdJ4dbnSyH/onuK5JC20664AmC9b1cgKhb6jdjwk1"
    "gklTXpNTa38WdUkrpG0dqPUNPhvFQBplDfAkfCc82fvBqH2jb7t3YWVaewN73d6R0G7a28Olzi86nS5xnfIes+fhz1D2Fpu1p3shjIEqnB/gPfIjBseU2GLyVylGI59pPLTfXdI2XTXH"
    "1jEbZw08A+RkF6/Nur2vM23+83Mn7RyxGVvy9sZc1gyRzaF1/b3P/rrVN+X58kMnHT/23TWp6S2V3wqVH7pUHuhW+d0BlbfJXDem8t051bjf7xi5q6asbk3RZ+V4K/njCyF6/C546qb3"
    "+1SHqDdZAbWP6rOjieYt3OSyl1Btwb/noR3XCYX8lhTq2JFynC5X+P8NNWRvWJgZ5D2+TMJFM7xcoMr1L084H7PtPhzLsswrP4WLxlKerdSMVU1O9qeUpcXf/yUaXcs5Pyc5ua9FJtFx"
    "ZBizSMP9yDh0rMsv8bIswV9lZ/n7gfxN5zKIjEQus8b5PJZ2h1x4JWmKzxZSag5yHpOTu1DU14uc3JciZP83qVz/duovt3PO2c9i85H8AK3V0Kk6ao0ShaMala+8E/Sv/NR2ZCVhrwBH"
    "lxL2AGjE71UNVLDFGnC79S9/NLn689VPgoc8H32M9e+JW1/L/Y28tGtyT6JUrQPN/X1zrbvd9ZzsH9nL/V8ZJ+l7SFvGvvwuUmpOgO38yXmO+miMZjK234uoND4bqt0AizKzPgH3cYaU"
    "rbihXeq1ZaBf2mt+KamnQeRpY1nbUKD0Z7L/y7bmG8d5yCZlvhnnD82XeU8ndafG3B3aTqqg39wztqr/KPhKHQ+zrhh1pGW+llfvWbRf7l6Z383yNmnOzB/NWegfAck9HMO72f//Byni"
    "8rQ=")

# xls: настоящий файл Excel 97, собранный LibreOffice из таблицы 2 на 3
OFFICE_XLS = _unz(
    "eNrtV11oHFUUPnd2szubpNnJnz+tjdtUt2mTSGsfjGjNpmnVgjVLjFikoJt0ksbuJnGzogV/tqkFBQtKH3wpSCEvoviDoA8KJm8+KIpQqD6lvhV9mIoihSbjd87cadZ1F3aTUrDuGc7c"
    "e8+dc8+Ze37uuT9837x07tONF6kIHqAArbgRChXQFND0BxaRoWkrruv6ZLcG/ylY1i3bMAj71QHZ5mFt14hua3BzwjBN48lRjPbTFNosHadq4BZ4TOF6lfCsVPhdpVCTv3b5nL85jweA"
    "xfHPuZ7jvx7YAGwEbgA2AaPeEUDNwBZgK7AN2C4+QXQr8Dbg7cCNwE3AO7TsDt0ybkG/U4/vquWaGw4RE1YM1dGXG75lk4vtL8IjPgkuil/8AjxMM+wbg6n0WOxGwV7RIaVYhwU46R70"
    "FJ0FtYk+FupX8t4j3gdIUKxPe/QhIyG6n5Z3p7ybiPm/EJ6fhbILfvkNR89rb+sgqFMDyH+TlKJ08Sxdv9mOYCPNc3w9Yk/Z2VR6SeJmnv50YwUxuhBjOrML/Y/K6EaVdPrf0eexd6X2"
    "udv73immby9D7ylD31GGHvkX/YyBmMqTy62VD0jbnA9J25IPStuaD0vblq9znxXfPYUM/EZQfIpmj9p27i2EpsKzqEwg0chpRb/S05ygnWERFXI4EXME+wV7Ie6mBodD6Guke5NOKCR8"
    "c0El8E5Qx2XLS/kmUr+JlG7+hax/leJyVGicSmVsnB7P544jdwzMzKTtWRwiSTuVnXVxdJjUH/SOjjGzHbi69e26Nfh88fKQ9Y881AiFj+BHOHKbRXkLy119//KPB0eT/c8IPb96I6G7"
    "DdkYdQIzi8EdwtEt7zn5tg0nEM6j+GC8c2QyY8/GHrdfjA1PZ1JTnfFd98YH2mkLdZSdT6Ym7Fg8eVIWfF3ecQjfLXCpf1tBvwv9k70X5novOP3bC/rnkIwi+CUlzxz1qB7l8/itoiex"
    "tkmvyJ4RbQ1HpeUxc7UGV8fQQb3ZENXb2CRNwBvw8W1Yft9LQyoqNFWCZhTRluXQ94550iOFkdIjJXPM9apRL3pFpSzgOUPmAnrOkDkuCX6jJPM4j3qGdEztgexE+N7ZKXxBp0voEccq"
    "UboYVC98zMOlykNGC30e8vI/0YPYW4bNWr4vADAB950wCxdSfrlUv57aS6FiCdR7NZhlGl5se1MPc81zcHIsOz07PZ6L7X9pzE7H7r+vdyQ1aqfTtrj43snx8b71ySdjPfwrLq9hlFqX"
    "lk699/uVoaPWB++Y1L3ts5/YQC/r2OT5Pl0fJvQmPqbrxEO6Vjyi68UZ7ZCXlonjS/o7NZ8PlfSr0V8c+vx358/es8k68y7077ny0T7OmkW0w7pO9dOhVaBrOfrNBNfz/if7VBQDpXg4"
    "NBMRr/8UpGfpGI2KHseq1r8FVjH0YeRWca8Kh30/H4TcDHx0CDo8tyb5gYI7WCU8m4EHrsXZEGpCe8324wRb7f3vTn1X86qIJ+gF/H8GtSnb/gC8YFxswpQcatZpUMpDl5ZfV8X+c/31"
    "4TX5+yBhTHSwxQOr06dvDf+/FZir3f8F/gZGDB5W")


_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_OFFICE = "application/vnd.openxmlformats-officedocument."


def _package(main, main_type, files, links=None):
    """Пакет OOXML: [Content_Types].xml, связи верхнего уровня, основная часть main и остальное из files: {имя: (тип содержимого или None, XML)}.
    links — {часть: [(вид связи, цель)]}: связи части кладутся рядом с ней, в _rels/."""
    override = lambda name, ct: f'<Override PartName="/{name}" ContentType="{ct}"/>'          # noqa: E731
    out = {"[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                  '<Default Extension="xml" ContentType="application/xml"/>' + override(main, main_type)
                                  + "".join(override(n, ct) for n, (ct, _) in files.items() if ct) + "</Types>",
           "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                          f'<Relationship Id="rId1" Type="{_R}/officeDocument" Target="{main}"/></Relationships>'}
    out.update({name: xml for name, (_, xml) in files.items()})
    for part, targets in (links or {}).items():
        folder, _, leaf = part.rpartition("/")
        out[f"{folder}/_rels/{leaf}.rels"] = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                              + "".join(f'<Relationship Id="rId{n}" Type="{_R}/{kind}" Target="{target}"/>'
                                                        for n, (kind, target) in enumerate(targets, 1)) + "</Relationships>")
    return zip_bytes({k: v.encode("utf-8") for k, v in out.items()})


def docx_page(text="Sample document line."):
    """Настоящий docx из одного абзаца: его открывает и рисует LibreOffice."""
    body = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return _package("word/document.xml", _OFFICE + "wordprocessingml.document.main+xml", {"word/document.xml": (None, body)})


def xlsx_sheet(rows=(("Apples", 3), ("Pears", 5))):
    """Настоящий xlsx с одним листом: строки — текст (inlineStr), числа — числа."""
    cells = ""
    for r, row in enumerate(rows, 1):
        cells += f'<row r="{r}">' + "".join(
            (f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str) else f'<c r="{chr(65 + c)}{r}"><v>{v}</v></c>')
            for c, v in enumerate(row)) + "</row>"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    book = f'<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="{_R}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    sheet = f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>{cells}</sheetData></worksheet>'
    return _package("xl/workbook.xml", _OFFICE + "spreadsheetml.sheet.main+xml",
                    {"xl/workbook.xml": (None, book), "xl/worksheets/sheet1.xml": (_OFFICE + "spreadsheetml.worksheet+xml", sheet)},
                    {"xl/workbook.xml": [("worksheet", "worksheets/sheet1.xml")]})


def pptx_slide(text="Sample slide text"):
    """Настоящий pptx из одного слайда с одной надписью: образец слайдов, тема и разметка — самые короткие, какие LibreOffice открывает."""
    p = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="' + _R + '" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    tree = '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{}</p:spTree></p:cSld>'
    box = ('<p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="914400"/>'
           '<a:ext cx="6400800" cy="914400"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:p><a:r>'
           f"<a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>")
    theme = ('<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="T"><a:themeElements><a:clrScheme name="C">'
             + "".join(f'<a:{n}><a:srgbClr val="{v}"/></a:{n}>' for n, v in (("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F497D"), ("lt2", "EEECE1"),
                                                                              ("accent1", "4F81BD"), ("accent2", "C0504D"), ("accent3", "9BBB59"),
                                                                              ("accent4", "8064A2"), ("accent5", "4BACC6"), ("accent6", "F79646"),
                                                                              ("hlink", "0000FF"), ("folHlink", "800080")))
             + '</a:clrScheme><a:fontScheme name="F"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
             '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="M">'
             + "".join(f"<a:{n}>" + "".join(x for _ in range(3)) + f"</a:{n}>" for n, x in (
                 ("fillStyleLst", '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'),
                 ("lnStyleLst", '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'),
                 ("effectStyleLst", "<a:effectStyle><a:effectLst/></a:effectStyle>"),
                 ("bgFillStyleLst", '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>')))
             + "</a:fmtScheme></a:themeElements></a:theme>")
    master = (f'<?xml version="1.0"?><p:sldMaster {p}>' + tree.format("") + '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
              'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
              '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>')
    layout = f'<?xml version="1.0"?><p:sldLayout {p} type="blank">' + tree.format("") + "</p:sldLayout>"
    slide = f'<?xml version="1.0"?><p:sld {p}>' + tree.format(box) + "</p:sld>"
    deck = (f'<?xml version="1.0"?><p:presentation {p}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>")
    return _package("ppt/presentation.xml", _OFFICE + "presentationml.presentation.main+xml", {
        "ppt/presentation.xml": (None, deck), "ppt/slides/slide1.xml": (_OFFICE + "presentationml.slide+xml", slide),
        "ppt/slideLayouts/slideLayout1.xml": (_OFFICE + "presentationml.slideLayout+xml", layout),
        "ppt/slideMasters/slideMaster1.xml": (_OFFICE + "presentationml.slideMaster+xml", master),
        "ppt/theme/theme1.xml": (_OFFICE + "theme+xml", theme)}, {
        "ppt/presentation.xml": [("slideMaster", "slideMasters/slideMaster1.xml"), ("slide", "slides/slide1.xml"), ("theme", "theme/theme1.xml")],
        "ppt/slides/slide1.xml": [("slideLayout", "../slideLayouts/slideLayout1.xml")],
        "ppt/slideLayouts/slideLayout1.xml": [("slideMaster", "../slideMasters/slideMaster1.xml")],
        "ppt/slideMasters/slideMaster1.xml": [("slideLayout", "../slideLayouts/slideLayout1.xml"), ("theme", "../theme/theme1.xml")]})


def emf_rect(width=200, height=100):
    """Настоящий EMF: заголовок, один прямоугольник и конец файла. LibreOffice рисует его страницей."""
    box = (0, 0, width - 1, height - 1)
    records = struct.pack("<II4i", 43, 24, *box) + struct.pack("<IIIII", 14, 20, 0, 16, 20)
    header = struct.pack("<II4i4iIIIIHHIII2i2i", 1, 88, *box, 0, 0, width * 10, height * 10, 0x464D4520, 0x10000, 88 + len(records), 3, 1, 0, 0, 0, 0,
                         1024, 768, 320, 240)
    return header + records


def wmf_rect(width=200, height=100):
    """Настоящий WMF с заголовком «placeable»: размер окна, один прямоугольник и конец файла."""
    head = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, width, height, 1440, 0)
    check = 0
    for (word,) in struct.iter_unpack("<H", head):
        check ^= word
    body = struct.pack("<IHhh", 5, 0x020C, height, width) + struct.pack("<IHhhhh", 7, 0x041B, height, width, 0, 0) + struct.pack("<IH", 3, 0)
    return head + struct.pack("<H", check) + struct.pack("<HHHIHIH", 1, 9, 0x300, (18 + len(body)) // 2, 0, 7, 0) + body
