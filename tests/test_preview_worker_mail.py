"""Рабочий процесс просмотра: письма и их вложения (FR-77). Здесь он вызывается в этом же процессе на маленьких образцах;
запуск в настоящем bwrap — в test_preview_mail_sandbox.py, ответы родителя — в test_preview_members.py.

Письмо разбирает только рабочий процесс. Образцы «больших» писем — маленькие файлы с большим заявленным размером: ничего,
что занимает гигабайты, не создаётся.
"""
import datetime
import json
import os
import shutil

import pytest

import filetype_sniff
import gate as G
import gatekit as K
import preview as P
import preview_worker as W
from test_preview_worker import run


def ext_of(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return ext if ext.isalnum() and ext.isascii() and len(ext) <= 10 else ""


def member(tmp, data, name="письмо.eml", number=0):
    """Вложение номер number из письма: (ответ, выходной каталог). Байты вложения — в out/member.bin."""
    src = tmp / "data"
    src.write_bytes(data)
    out = tmp / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    return W.extract(str(src), str(out), ext_of(name), number), out


def describe_mail(tmp, data, name="письмо.eml"):
    result, _ = run(tmp, data, name)
    assert result["kind"] == "mail", result
    assert P._check(json.loads(json.dumps(result, ensure_ascii=False)), "describe")["kind"] == "mail"       # то, что родитель примет
    return result["mail"]


def attachments_of(tmp, data, name="письмо.eml"):
    return describe_mail(tmp, data, name)["attachments"]


def raw_letter(parts, headers=b""):
    """Письмо из готовых частей MIME: parts — байты каждой части вместе с её заголовками."""
    return (b"From: Petrov <petrov@example.org>\r\nTo: ivanov@example.org\r\nSubject: Raw\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            b"Message-ID: <raw@example.org>\r\nMIME-Version: 1.0\r\n" + headers + b"Content-Type: multipart/mixed; boundary=\"B\"\r\n\r\n"
            + b"".join(b"--B\r\n" + p + b"\r\n" for p in parts) + b"--B--\r\n")


PLAIN = b"Content-Type: text/plain; charset=utf-8\r\n\r\nBody.\r\n"


def attachment(name_param, data=b"data", ctype=b"application/octet-stream", disposition=b"attachment"):
    import base64
    return (b"Content-Type: " + ctype + b"\r\nContent-Disposition: " + disposition + b"; " + name_param +
            b"\r\nContent-Transfer-Encoding: base64\r\n\r\n" + base64.b64encode(data))


# ── шапка и текст ───────────────────────────────────────────────
def test_письмо_шапка_текст_и_пустой_список_вложений(tmp_path):
    mail = describe_mail(tmp_path, K.EML)
    assert mail["from"] == "Petrov <petrov@example.org>" and mail["to"] == ["ivanov@example.org"] and mail["cc"] == []
    assert mail["date"] == "2024-03-05T14:32:00+05:00" and mail["subject"] == "Договор"           # тема была закодирована по RFC 2047
    assert mail["text"] == "Добрый день. Направляю договор на согласование.\r\n" or mail["text"].strip() == "Добрый день. Направляю договор на согласование."
    assert mail["truncated"] is False and mail["attachments"] == []
    assert set(mail) == {"from", "to", "cc", "date", "subject", "text", "truncated", "attachments"}


def test_ответ_письма_по_виду_и_типу(tmp_path):
    result, out = run(tmp_path, K.EML, "письмо.eml")
    assert set(result) == {"kind", "type", "mail"} and result["type"] == "eml" and os.listdir(out) == []


def test_получатели_и_копия_списками_с_именами(tmp_path):
    data = K.eml_with(to="Анна Иванова <ann@example.org>, \"Петров, Пётр\" <peter@example.org>, bare@example.org", cc="Борис <bob@example.org>")
    mail = describe_mail(tmp_path, data)
    assert mail["to"] == ["Анна Иванова <ann@example.org>", "\"Петров, Пётр\" <peter@example.org>", "bare@example.org"]
    assert mail["cc"] == ["Борис <bob@example.org>"]


def test_получателей_не_больше_ста_и_строка_до_300_знаков(tmp_path):
    many = ", ".join(f"p{i}@example.org" for i in range(150))
    mail = describe_mail(tmp_path, K.eml_with(to=many, cc=many))
    assert len(mail["to"]) == 100 and len(mail["cc"]) == 100 and mail["to"][0] == "p0@example.org" and mail["to"][99] == "p99@example.org"
    long = "я" * 400
    mail = describe_mail(tmp_path, K.eml_with(to=f"{long} <long@example.org>", sender=f"{long} <long@example.org>"))
    assert all(len(line) <= 300 for line in mail["to"] + [mail["from"]]) and mail["to"][0].endswith("…") and mail["from"].endswith("…")


def test_тема_до_тысячи_знаков_и_без_управляющих(tmp_path):
    mail = describe_mail(tmp_path, K.eml_with(subject="т" * 1500))
    assert len(mail["subject"]) == 1000 and mail["subject"].endswith("…")
    raw = K.EML.replace(b"Subject: =?utf-8?b?0JTQvtCz0L7QstC+0YA=?=", b"Subject: =?utf-8?q?a=1Bb=0Dc=E2=80=AEd?=")      # ESC, CR и знак смены направления
    mail = describe_mail(tmp_path, raw)
    assert "\x1b" not in mail["subject"] and "\r" not in mail["subject"] and "‮" not in mail["subject"] and mail["subject"].startswith("a")


@pytest.mark.parametrize("raw,expected", [
    (b"Tue, 5 Mar 2024 14:32:00 +0500", "2024-03-05T14:32:00+05:00"),
    (b"Tue, 5 Mar 2024 14:32:00 GMT", "2024-03-05T14:32:00+00:00"),
    (b"Tue, 5 Mar 2024 14:32:00 -0000", "2024-03-05T14:32:00"),                       # пояс не назван — время без пояса
    (b"\xd0\xb2\xd1\x87\xd0\xb5\xd1\x80\xd0\xb0 \xd0\xbe\xd0\xba\xd0\xbe\xd0\xbb\xd0\xbe \xd1\x82\xd1\x80\xd1\x91\xd1\x85", None),
    (b"Tue, 99 Foo 2024 14:32:00 +0500", None), (b"", None)])
def test_дата_письма_iso_или_null(tmp_path, raw, expected):
    data = K.EML.replace(b"Date: Tue, 5 Mar 2024 14:32:00 +0500", b"Date: " + raw)
    assert describe_mail(tmp_path, data)["date"] == expected


def test_письмо_без_даты_и_без_темы_и_без_получателя(tmp_path):
    data = (b"From: Petrov <petrov@example.org>\r\nMessage-ID: <x@example.org>\r\nMIME-Version: 1.0\r\nReceived: from a by b; Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + "Только текст.\r\n".encode("utf-8"))
    mail = describe_mail(tmp_path, data)
    assert mail["date"] is None and mail["subject"] == "" and mail["to"] == [] and mail["cc"] == [] and mail["text"].strip() == "Только текст."


def plain_mail(body):
    """Письмо из одной текстовой части: тело ровно из этих знаков, без перевода строки в конце."""
    return K.EML[:K.EML.index(b"\r\n\r\n") + 4] + body.encode("utf-8")


@pytest.mark.parametrize("letters,truncated", [(19_999, False), (20_000, False), (20_001, True), (45_000, True)])
@pytest.mark.parametrize("alphabet", ["a", "ж"])
def test_текст_письма_первые_20000_знаков_и_признак_обрезки(tmp_path, letters, truncated, alphabet):
    body = alphabet * letters
    mail = describe_mail(tmp_path, plain_mail(body))
    assert mail["text"] == body[:20_000] and mail["truncated"] is truncated


def test_управляющие_знаки_в_тексте_письма_заменены_а_переводы_строк_целы(tmp_path):
    raw = K.EML[:K.EML.index(b"\r\n\r\n") + 4] + "строка\r\n\tотступ\r\nа\x01б\x1bв\x7fг\r\n".encode("utf-8")
    mail = describe_mail(tmp_path, raw)
    assert "\x01" not in mail["text"] and "\x1b" not in mail["text"] and "�" in mail["text"]
    assert "строка\r\n\tотступ\r\n" in mail["text"] or "строка\n\tотступ\n" in mail["text"]


def test_письмо_в_другой_кодировке(tmp_path):
    import base64
    body = "Привет, это письмо в кодировке Windows-1251.".encode("cp1251")
    subject = "=?koi8-r?B?" + base64.b64encode("Тема в КОИ8".encode("koi8-r")).decode() + "?="
    data = (f"From: a@example.org\r\nSubject: {subject}\r\nMIME-Version: 1.0\r\nMessage-ID: <k@example.org>\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            "Content-Type: text/plain; charset=windows-1251\r\nContent-Transfer-Encoding: base64\r\n\r\n").encode() + base64.b64encode(body)
    mail = describe_mail(tmp_path, data)
    assert mail["subject"] == "Тема в КОИ8" and mail["text"].strip() == "Привет, это письмо в кодировке Windows-1251."


def test_письмо_с_неизвестной_кодировкой_текста_читается_с_заменой(tmp_path):
    data = (b"From: a@example.org\r\nSubject: x\r\nMIME-Version: 1.0\r\nMessage-ID: <u@example.org>\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            b"Content-Type: text/plain; charset=no-such-charset\r\n\r\nhello \xe9\xe8\r\n")
    mail = describe_mail(tmp_path, data)
    assert mail["text"].startswith("hello") and "\x00" not in mail["text"]


# ── HTML-часть письма ───────────────────────────────────────────
HTML = ("<html><head><style>p {color: red}</style><script>alert('x')</script></head><body><p>Видимый <b>текст</b> письма.</p>"
        "<p style=\"display:none\">спрятанное слово</p><a href=\"http://example.test/x\">ссылка</a></body></html>")


def html_only(html):
    return (b"From: a@example.org\r\nSubject: html\r\nMIME-Version: 1.0\r\nMessage-ID: <h@example.org>\r\nDate: Tue, 5 Mar 2024 14:32:00 +0500\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n" + html.encode("utf-8"))


def test_html_письма_переводится_в_текст_тем_же_разбором_что_в_приёмке(tmp_path):
    text = describe_mail(tmp_path, html_only(HTML))["text"]
    assert text == G._html_text(HTML, [])                               # тот самый разбор приёмки, а не второй
    assert "Видимый" in text and "текст" in text and "ссылка" in text
    assert "<" not in text and ">" not in text and "alert" not in text and "color" not in text and "http://" not in text


def test_когда_есть_и_текст_и_html_берётся_текст(tmp_path):
    data = K.eml_with(body="Простой текст письма.", html="<html><body><p>Разметка письма.</p></body></html>")
    text = describe_mail(tmp_path, data)["text"]
    assert text.strip() == "Простой текст письма." and "Разметка" not in text


def test_пустой_текст_и_есть_html_берётся_html(tmp_path):
    data = K.eml_with(body=" ", html="<html><body><p>Только в разметке.</p></body></html>")
    assert "Только в разметке." in describe_mail(tmp_path, data)["text"]


def test_html_с_длинным_текстом_обрезается_до_20000(tmp_path):
    text = describe_mail(tmp_path, html_only("<p>" + "я" * 30_000 + "</p>"))
    assert text["text"] == "я" * 20_000 and text["truncated"] is True


def test_разметка_в_ответе_письма_не_встречается_никак(tmp_path):
    answer = json.dumps(run(tmp_path, html_only(HTML), "письмо.eml")[0], ensure_ascii=False)
    assert "<script" not in answer and "<style" not in answer and "<p" not in answer and "</" not in answer


# ── вложения: список ────────────────────────────────────────────
def test_вложения_имя_размер_тип_по_содержимому_номер_и_признак_программы(tmp_path):
    png, pdf, text = K.png_image(8, 8), K.PDF, "привет".encode("utf-8")
    data = K.eml_with([("фото.png", png), ("отчёт.pdf", pdf), ("записка.txt", text), ("настройка.exe", K.PE)])
    assert attachments_of(tmp_path, data) == [
        {"name": "фото.png", "size": len(png), "type": "png", "member": 0, "executable": False},
        {"name": "отчёт.pdf", "size": len(pdf), "type": "pdf", "member": 1, "executable": False},
        {"name": "записка.txt", "size": len(text), "type": "txt", "member": 2, "executable": False},
        {"name": "настройка.exe", "size": len(K.PE), "type": "pe", "member": 3, "executable": True}]


def test_тип_вложения_по_содержимому_а_не_по_имени(tmp_path):
    data = K.eml_with([("счёт.pdf", K.png_image(4, 4)), ("картинка.png", K.PDF), ("без-расширения", K.zip_bytes({"а.txt": b"x"}))])
    assert [a["type"] for a in attachments_of(tmp_path, data)] == ["png", "pdf", "zip"]


@pytest.mark.parametrize("name,data,type_", [
    ("а.exe", K.PE, "pe"), ("б.bin", K.ELF, "elf"), ("в.bat", b"@echo off\r\n", "by-extension"), ("г.txt", b"#!/bin/sh\necho x\n", "script"),
    ("д.jar", K.jar(), "jar"), ("е.apk", K.apk(), "apk"), ("ж.lnk", K.LNK, "lnk"), ("з.doc", K.PE, "pe")])
def test_программа_во_вложении_помечена_executable_true(tmp_path, name, data, type_):
    (found,) = attachments_of(tmp_path, K.eml_with([(name, data)]))
    assert found["executable"] is True and found["type"] == type_, found


@pytest.mark.parametrize("name,data,type_", [("отчёт.zip", K.jar(), "jar"), ("фото.zip", K.apk(), "apk")])
def test_программа_под_безобидным_именем_помечена_по_содержимому(tmp_path, name, data, type_):
    """Имя и первые байты программу не выдают (обычный zip): отметку даёт только разбор содержимого."""
    import filetype_sniff
    assert not filetype_sniff.is_executable(name, data[:filetype_sniff.HEAD])
    (found,) = attachments_of(tmp_path, K.eml_with([(name, data)]))
    assert found["executable"] is True and found["type"] == type_, found


@pytest.mark.parametrize("name,data", [("а.txt", b"hello"), ("б.pdf", K.PDF), ("в.png", K.PNG), ("г.zip", K.zip_bytes({"а.txt": b"x"})),
                                       ("д.docx", K.ooxml("docx")), ("е.csv", b"a,b\n1,2\n")])
def test_обычное_вложение_executable_false(tmp_path, name, data):
    (found,) = attachments_of(tmp_path, K.eml_with([(name, data)]))
    assert found["executable"] is False


def test_вложенное_письмо_вложением_тип_eml_размер_настоящий(tmp_path):
    inner = K.eml_message(subject="Вложенное", body="Текст вложенного письма.")
    data = K.eml_with([("пересланное.eml", inner)])
    (found,) = attachments_of(tmp_path, data)
    assert found["name"] == "пересланное.eml" and found["type"] == "eml" and found["executable"] is False
    got, out = member(tmp_path, data, number=0)
    assert found["size"] == (out / "member.bin").stat().st_size and got == {"kind": "member", "name": "пересланное.eml"}
    assert filetype_sniff.detect(str(out / "member.bin"), "x.eml").type == "eml"
    assert "Вложенное" in describe_mail(tmp_path, (out / "member.bin").read_bytes())["subject"]


def test_размер_вложения_настоящий_а_не_объявленный_в_заголовке(tmp_path):
    (found,) = attachments_of(tmp_path, K.eml_declared(1 << 30))
    assert found == {"name": "big.pdf", "size": 0, "type": "empty", "member": 0, "executable": False}
    liar = raw_letter([PLAIN, attachment(b"filename=\"x.bin\"; size=999999999999", b"12345")])
    assert attachments_of(tmp_path, liar)[0]["size"] == 5


def test_вложений_в_списке_не_больше_200_номера_до_199(tmp_path):
    data = K.eml_with([(f"ф{i}.txt", f"{i}".encode()) for i in range(250)])
    found = attachments_of(tmp_path, data)
    assert len(found) == 200 and [a["member"] for a in found] == list(range(200)) and found[199]["name"] == "ф199.txt"
    assert len(attachments_of(tmp_path, K.eml_with([(f"ф{i}.txt", b"x") for i in range(200)]))) == 200


@pytest.mark.parametrize("count,truncated", [(199, False), (200, False), (201, True), (250, True)])
def test_список_вложений_обрезан_до_200_признак_truncated_true_и_при_коротком_тексте(tmp_path, count, truncated):
    mail = describe_mail(tmp_path, K.eml_with([(f"ф{i}.txt", b"x") for i in range(count)]))
    assert len(mail["attachments"]) == min(count, 200) and len(mail["text"]) < 100 and mail["truncated"] is truncated


def test_имя_вложения_обрезается_до_255_и_очищается_от_управляющих(tmp_path):
    long = "я" * 400 + ".pdf"
    (found,) = attachments_of(tmp_path, K.eml_with([(long, K.PDF)]))
    assert len(found["name"]) == 255 and found["name"].endswith("…") and found["name"].startswith("яяя")
    odd = raw_letter([PLAIN, attachment(b"filename*=utf-8''a%1B%0Ab%E2%80%AEc%00d.txt", b"x")])
    (found,) = attachments_of(tmp_path, odd)
    assert found["name"].startswith("a") and found["name"].endswith("d.txt")
    assert not any(ch in found["name"] for ch in "\x1b\n‮\x00")


def test_имя_вложения_с_путём_остаётся_данными(tmp_path):
    (found,) = attachments_of(tmp_path, K.eml_with([("../../etc/passwd", b"x"), ][:1]))
    assert found["name"] in ("../../etc/passwd", "passwd")


def test_вложение_без_имени_получает_имя_по_номеру(tmp_path):
    data = raw_letter([PLAIN, b"Content-Type: application/pdf\r\nContent-Disposition: attachment\r\n\r\n" + K.PDF.replace(b"\n", b"\r\n"),
                       attachment("filename=\"второе.txt\"".encode("utf-8"), b"x")])
    found = attachments_of(tmp_path, data)
    assert [a["name"] for a in found] == ["attachment-1", "второе.txt"] and found[0]["type"] == "pdf"


def test_части_текста_и_картинки_внутри_html_вложениями_не_считаются(tmp_path):
    data = raw_letter([PLAIN, b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + "вторая часть текста без имени\r\n".encode("utf-8"),
                       attachment("filename=\"документ.pdf\"".encode("utf-8"), K.PDF)])
    assert [a["name"] for a in attachments_of(tmp_path, data)] == ["документ.pdf"]


def test_одно_испорченное_вложение_не_прячет_письмо(tmp_path, monkeypatch):
    data = K.eml_with([("целое.txt", b"fine"), ("битое.bin", b"broken"), ("ещё.txt", b"also fine")])
    real = W._eml_bytes

    def flaky(part):
        if part.get_filename() == "битое.bin":
            raise ValueError("не раскодировалось")
        return real(part)

    monkeypatch.setattr(W, "_eml_bytes", flaky)
    assert attachments_of(tmp_path, data) == [
        {"name": "целое.txt", "size": 4, "type": "txt", "member": 0, "executable": False},
        {"name": "битое.bin", "size": 0, "type": "unknown", "member": 1, "executable": False},
        {"name": "ещё.txt", "size": 9, "type": "txt", "member": 2, "executable": False}]
    got, out = member(tmp_path, data, number=1)                          # а открыть именно его нельзя: сбой назван, файла нет
    assert got == {"kind": "none", "type": "eml", "reason": "broken", "args": {"error_type": "ValueError"}} and os.listdir(out) == []
    assert member(tmp_path, data, number=2)[1].joinpath("member.bin").read_bytes() == b"also fine"


def test_нехватка_памяти_на_вложении_не_прячется_как_пустое_вложение(tmp_path, monkeypatch):
    def hungry(part):
        raise MemoryError

    monkeypatch.setattr(W, "_eml_bytes", hungry)
    assert run(tmp_path, K.eml_with([("а.txt", b"x")]), "письмо.eml")[0] == {"kind": "none", "type": "eml", "reason": "memory", "args": {}}


def test_письмо_без_частей_и_простое_письмо_без_вложений(tmp_path):
    assert attachments_of(tmp_path, K.EML) == [] and attachments_of(tmp_path, K.eml_with()) == []


# ── вложения: достать байты ─────────────────────────────────────
def test_вложение_достаётся_байт_в_байт_и_ответ_называет_имя(tmp_path):
    files = [("а.png", K.png_image(8, 8)), ("б.pdf", K.PDF), ("в.txt", "привет, мир".encode("utf-8")), ("г.exe", K.PE), ("д.bin", bytes(range(256)) * 3)]
    data = K.eml_with(files)
    for number, (name, content) in enumerate(files):
        got, out = member(tmp_path, data, number=number)
        assert got == {"kind": "member", "name": name} and (out / "member.bin").read_bytes() == content
        assert os.listdir(out) == ["member.bin"]


def test_два_вложения_одного_письма_не_путаются(tmp_path):
    data = K.eml_with([("один.txt", b"first"), ("два.txt", b"second")])
    assert (member(tmp_path, data, number=0)[1] / "member.bin").read_bytes() == b"first"
    assert (member(tmp_path, data, number=1)[1] / "member.bin").read_bytes() == b"second"


@pytest.mark.parametrize("number", [2, 3, 7, 199, 200, 10 ** 6])
def test_номер_вне_списка_отказ_а_не_чужое_вложение(tmp_path, number):
    data = K.eml_with([("один.txt", b"first"), ("два.txt", b"second")])
    got, out = member(tmp_path, data, number=number)
    assert got == {"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": number, "count": 2}} and os.listdir(out) == []


def test_номер_за_двухсотым_отказ_даже_если_вложение_в_письме_есть(tmp_path):
    data = K.eml_with([(f"ф{i}.txt", f"{i}".encode()) for i in range(205)])
    assert member(tmp_path, data, number=199)[1].joinpath("member.bin").read_bytes() == b"199"
    got, _ = member(tmp_path, data, number=200)
    assert got["reason"] == "no_member" and got["args"] == {"member": 200, "count": 200}


@pytest.mark.parametrize("name,data,type_", [("а.txt", "просто текст".encode("utf-8"), "txt"), ("а.zip", K.zip_bytes({"а.txt": b"x"}), "zip"), ("а.bin", K.PE, "pe"),
                                             ("пусто.txt", b"", "empty"), ("а.png", K.png_image(4, 4), "png")])
def test_у_файла_не_письма_вложений_нет(tmp_path, name, data, type_):
    got, out = member(tmp_path, data, name, 0)
    assert got == {"kind": "none", "type": type_, "reason": "no_member", "args": {"member": 0, "count": 0}} and os.listdir(out) == []


def test_вложение_с_именем_из_путей_ложится_только_в_member_bin(tmp_path):
    data = K.eml_with([("../../escape.txt", b"payload")])
    got, out = member(tmp_path, data, number=0)
    assert got["kind"] == "member" and os.listdir(out) == ["member.bin"] and sorted(os.listdir(tmp_path)) == ["data", "out"]
    odd = raw_letter([PLAIN, attachment(b"filename=\"/etc/cron.d/x\"", b"payload")])
    got, out = member(tmp_path, odd, number=0)
    assert os.listdir(out) == ["member.bin"] and sorted(os.listdir(tmp_path)) == ["data", "out"]


def test_имя_в_ответе_о_вложении_обрезано_и_очищено(tmp_path):
    odd = raw_letter([PLAIN, attachment(b"filename*=utf-8''" + ("%D1%8F" * 400).encode() + b"%1B.pdf", b"x")])
    got, _ = member(tmp_path, odd, number=0)
    assert got["kind"] == "member" and len(got["name"]) == 255 and "\x1b" not in got["name"]


def test_вложение_больше_предела_отказ_и_граница_точна(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "MEMBER_MAX", 1 << 20)
    exact = K.eml_with([("ровно.bin", bytes(1 << 20))])
    got, out = member(tmp_path, exact, number=0)
    assert got["kind"] == "member" and (out / "member.bin").stat().st_size == 1 << 20
    over = K.eml_with([("больше.bin", bytes((1 << 20) + 1))])
    got, out = member(tmp_path, over, number=0)
    assert got == {"kind": "none", "type": "eml", "reason": "too_big", "args": {"limit": 1}} and os.listdir(out) == []


def test_письмо_больше_предела_отказ_и_для_описания_и_для_вложения(tmp_path, monkeypatch):
    data = K.eml_with([("а.txt", b"x" * 500)])
    monkeypatch.setattr(W, "MAIL_MAX", len(data) - 1)
    assert run(tmp_path, data, "письмо.eml")[0] == {"kind": "none", "type": "eml", "reason": "too_big", "args": {"limit": 0}}
    got, out = member(tmp_path, data, number=0)
    assert got["reason"] == "too_big" and os.listdir(out) == []
    monkeypatch.setattr(W, "MAIL_MAX", len(data))
    assert run(tmp_path, data, "письмо.eml")[0]["kind"] == "mail"


def test_вложенное_письмо_во_вложении_достаётся_уровень_за_уровнем(tmp_path):
    inner = K.eml_message(subject="Среднее", attachments=[("глубокое.txt", b"deep")])
    data = K.eml_with([("среднее.eml", inner)])
    got, out = member(tmp_path, data, number=0)
    middle = (out / "member.bin").read_bytes()
    assert describe_mail(tmp_path, middle)["attachments"][0]["name"] == "глубокое.txt"
    got, out = member(tmp_path, middle, "среднее.eml", number=0)
    assert got == {"kind": "member", "name": "глубокое.txt"} and (out / "member.bin").read_bytes() == b"deep"


# ── сбой разбора ────────────────────────────────────────────────
def test_сбой_разбора_письма_none_с_названием_сбоя_без_исключения(tmp_path, monkeypatch):
    import email

    def boom(*a, **k):
        raise RuntimeError("разбор упал")

    monkeypatch.setattr(email, "message_from_bytes", boom)
    monkeypatch.setattr(email, "message_from_binary_file", boom)
    assert run(tmp_path, K.EML, "письмо.eml")[0] == {"kind": "none", "type": "eml", "reason": "broken", "args": {"error_type": "RuntimeError"}}
    got, out = member(tmp_path, K.eml_with([("а.txt", b"x")]), number=0)
    assert got["kind"] == "none" and got["reason"] == "broken" and os.listdir(out) == []


def test_нехватка_памяти_при_разборе_письма_none_memory(tmp_path, monkeypatch):
    import email

    def boom(*a, **k):
        raise MemoryError

    monkeypatch.setattr(email, "message_from_bytes", boom)
    monkeypatch.setattr(email, "message_from_binary_file", boom)
    assert run(tmp_path, K.EML, "письмо.eml")[0] == {"kind": "none", "type": "eml", "reason": "memory", "args": {}}


def test_письмо_с_глубокой_вложенностью_частей_не_роняет_процесс(tmp_path):
    depth = 1500
    head = b"From: a@example.org\r\nTo: b@example.org\r\nSubject: Deep\r\nMessage-ID: <d@example.org>\r\nMIME-Version: 1.0\r\n"
    body = b"".join(b"Content-Type: multipart/mixed; boundary=\"b%d\"\r\n\r\n--b%d\r\n" % (i, i) for i in range(depth)) + b"Content-Type: text/plain\r\n\r\nx\r\n"
    result, _ = run(tmp_path, head + body, "глубокое.eml")
    assert result["kind"] in ("mail", "none") and (result["kind"] == "mail" or result["reason"] == "broken")


def test_основная_точка_входа_письмо_и_вложение(tmp_path):
    src, out = tmp_path / "data", tmp_path / "out"
    src.write_bytes(K.eml_with([("а.txt", b"hello")]))
    out.mkdir()
    assert W.main(["describe", str(src), str(out), "eml"]) == 0
    answer = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert answer["kind"] == "mail" and answer["mail"]["attachments"][0]["name"] == "а.txt"
    assert W.main(["member", str(src), str(out), "eml", "0"]) == 0
    assert json.loads((out / "result.json").read_text(encoding="utf-8")) == {"kind": "member", "name": "а.txt"}
    assert (out / "member.bin").read_bytes() == b"hello"
    for argv in (["member", str(src), str(out), "eml"], ["member", str(src), str(out), "eml", "x"], ["member", str(src), str(out), "eml", "-1"],
                 ["member", str(src), str(out), "eml", "0", "1"]):
        assert W.main(argv) == 2, argv


# ── письма Outlook (.msg) на настоящем extract_msg ──────────────
@pytest.fixture
def extract_msg():
    return pytest.importorskip("extract_msg")


WHEN = datetime.datetime(2026, 10, 5, 10, 0, tzinfo=datetime.timezone.utc)


def test_msg_шапка_текст_получатели_и_вложения(tmp_path, extract_msg):
    inner = K.msg_tree(subject="Вложенное", body="внутри", attachments=[("глубокое.txt", b"deep")], embedded=True)
    data = K.msg_bytes(subject="Договор", body="Добрый день из Outlook.", sender="Иван", date=WHEN,
                       recipients=[("to", "Анна", "ann@example.test"), ("cc", "Борис", "bob@example.test"), ("to", "Вера", "vera@example.test")],
                       attachments=[("записка.txt", "привет".encode("utf-8")), ("пересланное.msg", inner), ("фото.png", K.png_image(6, 6))])
    mail = describe_mail(tmp_path, data, "письмо.msg")
    assert mail["from"] == "Иван" and mail["subject"] == "Договор" and mail["text"].strip() == "Добрый день из Outlook."
    assert mail["to"] == ["Анна <ann@example.test>", "Вера <vera@example.test>"] and mail["cc"] == ["Борис <bob@example.test>"]
    assert datetime.datetime.fromisoformat(mail["date"]) == WHEN and mail["truncated"] is False
    names = [(a["name"], a["type"], a["member"], a["executable"]) for a in mail["attachments"]]
    assert names == [("записка.txt", "txt", 0, False), ("пересланное.msg", "msg", 1, False), ("фото.png", "png", 2, False)]
    assert mail["attachments"][0]["size"] == len("привет".encode("utf-8")) and mail["attachments"][2]["size"] == len(K.png_image(6, 6))


def test_msg_вложения_достаются_и_вложенное_письмо_читается_как_письмо(tmp_path, extract_msg):
    inner = K.msg_tree(subject="Вложенное", body="внутри", attachments=[("глубокое.txt", b"deep")], embedded=True)
    data = K.msg_bytes(attachments=[("записка.txt", b"hello"), ("пересланное.msg", inner)])
    got, out = member(tmp_path, data, "письмо.msg", 0)
    assert got == {"kind": "member", "name": "записка.txt"} and (out / "member.bin").read_bytes() == b"hello"
    got, out = member(tmp_path, data, "письмо.msg", 1)
    nested = (out / "member.bin").read_bytes()
    assert got["kind"] == "member" and filetype_sniff.detect(str(out / "member.bin"), "x.msg").type == "msg"
    mail = describe_mail(tmp_path, nested, "пересланное.msg")
    assert mail["subject"] == "Вложенное" and [a["name"] for a in mail["attachments"]] == ["глубокое.txt"]
    got, out = member(tmp_path, nested, "пересланное.msg", 0)
    assert (out / "member.bin").read_bytes() == b"deep"
    got, _ = member(tmp_path, data, "письмо.msg", 2)
    assert got == {"kind": "none", "type": "msg", "reason": "no_member", "args": {"member": 2, "count": 2}}


def test_msg_только_html_текст_тем_же_разбором(tmp_path, extract_msg):
    data = K.msg_bytes(body="", html=HTML)
    assert describe_mail(tmp_path, data, "письмо.msg")["text"].strip() == G._html_text(HTML, []).strip()


def test_msg_программа_во_вложении_помечена(tmp_path, extract_msg):
    data = K.msg_bytes(attachments=[("настройка.exe", K.PE), ("записка.txt", b"x")])
    found = attachments_of(tmp_path, data, "письмо.msg")
    assert [(a["type"], a["executable"]) for a in found] == [("pe", True), ("txt", False)]


def test_msg_с_вложением_объявленным_в_гигабайт_отказ_быстро_и_без_исключения(tmp_path, extract_msg):
    data = K.msg_bytes(attachments=[("большое.bin", (b"y" * 5000, 1 << 30))])
    result, out = run(tmp_path, data, "письмо.msg")
    assert result["kind"] == "none" and result["reason"] == "broken" and result["args"]["error_type"] == "OleFileError" and os.listdir(out) == []
    got, _ = member(tmp_path, data, "письмо.msg", 0)
    assert got["kind"] == "none" and got["reason"] == "broken"


def test_msg_текст_письма_до_20000_знаков(tmp_path, extract_msg):
    mail = describe_mail(tmp_path, K.msg_bytes(body="ж" * 20_001), "письмо.msg")
    assert mail["text"] == "ж" * 20_000 and mail["truncated"] is True


@pytest.mark.parametrize("count,truncated", [(200, False), (201, True)])
def test_msg_список_вложений_обрезан_до_200_признак_truncated_true(tmp_path, extract_msg, count, truncated):
    mail = describe_mail(tmp_path, K.msg_bytes(attachments=[(f"ф{i}.txt", b"x") for i in range(count)]), "письмо.msg")
    assert len(mail["attachments"]) == min(count, 200) and mail["truncated"] is truncated


def test_msg_с_201_вложением_достаётся_последнее_из_двухсот_а_двести_первого_нет(tmp_path, extract_msg):
    data = K.msg_bytes(attachments=[(f"ф{i}.txt", f"{i}".encode()) for i in range(201)])
    assert member(tmp_path, data, "письмо.msg", 199)[0] == {"kind": "member", "name": "ф199.txt"}
    got, _ = member(tmp_path, data, "письмо.msg", 200)
    assert got["reason"] == "no_member" and got["args"] == {"member": 200, "count": 200}


def test_msg_сбой_extract_msg_none_без_исключения(tmp_path, extract_msg, monkeypatch):
    def boom(*a, **k):
        raise ValueError("сломано")

    monkeypatch.setattr(extract_msg, "Message", boom)
    assert run(tmp_path, K.msg_bytes(), "письмо.msg")[0] == {"kind": "none", "type": "msg", "reason": "broken", "args": {"error_type": "ValueError"}}


# ── пределы двух процессов ──────────────────────────────────────
def test_пределы_письма_у_обоих_процессов_совпадают():
    for name in ("MAIL_PEOPLE", "MAIL_LINE", "MAIL_SUBJECT", "MAX_ATTACHMENTS", "NAME_LIMIT", "LISTING_MAX", "MEMBER_MAX", "NAME_BAD"):
        assert getattr(W, name) == getattr(P, name), name
    assert (P.MAIL_PEOPLE, P.MAIL_LINE, P.MAIL_SUBJECT, P.MAX_ATTACHMENTS, P.NAME_LIMIT, P.LISTING_MAX) == (100, 300, 1000, 200, 255, 500)
    assert P.MEMBER_MAX == 64 << 20 and W.MAIL_MAX == 100 << 20


def test_самый_большой_допустимый_ответ_о_письме_меньше_предела_result_json():
    """Четыре байта на знак: на пределе по каждому полю ответ всё равно помещается в то, что родитель читает."""
    emoji = "😀"
    mail = {"from": emoji * 300, "to": [emoji * 300] * 100, "cc": [emoji * 300] * 100, "date": "2024-03-05T14:32:00+05:00", "subject": emoji * 1000,
            "text": emoji * 20_000, "truncated": True,
            "attachments": [{"name": emoji * 255, "size": 1 << 40, "type": "a" * 20, "member": i, "executable": True} for i in range(200)]}
    raw = json.dumps({"kind": "mail", "type": "eml", "mail": mail}, ensure_ascii=False).encode("utf-8")
    assert len(raw) < P.RESULT_MAX
    assert P._check(json.loads(raw.decode("utf-8")), "describe")["kind"] == "mail"
