"""Просмотр писем, архивов и вложений: родитель (FR-77) — ответы по схеме, вложение тем же просмотром, ключ кэша — sha256 его байтов.

Рабочий процесс подставной (`Fake` из test_preview_kit): тест сам решает, что он «вернул», в том числе порчу. Родитель письмо и архив
не разбирает: считает sha256 потоком, просит рабочий процесс достать вложение и принимает от него только то, что прошло проверку.
Настоящий bwrap — в test_preview_mail_sandbox.py.
"""
import hashlib
import json
import os

import pytest

import gatekit as K
import preview as P
from test_preview_kit import BATCH, env, png, walk_modes, worker, write  # noqa: F401
from test_preview_parent import hold, in_thread, say, sparse, waits_for

SHA = lambda data: hashlib.sha256(data).hexdigest()           # noqa: E731

ATTACHMENT = {"name": "договор.pdf", "size": 1234, "type": "pdf", "member": 0, "executable": False}
MAIL = {"kind": "mail", "type": "eml", "mail": {
    "from": "Petrov <petrov@example.org>", "to": ["ivanov@example.org"], "cc": ["boss@example.org"], "date": "2024-03-05T14:32:00+05:00",
    "subject": "Договор", "text": "Добрый день.", "truncated": False, "attachments": [ATTACHMENT, {**ATTACHMENT, "name": "фото.png", "type": "png", "member": 1}]}}
LISTING = {"kind": "listing", "type": "zip", "listing": [{"name": "а.txt", "size": 5}, {"name": "папка/б.bin", "size": 100}], "truncated": False}
PDF = {"kind": "pages", "type": "pdf", "pages": 3, "shown": 3}


def mail_with(**changes):
    return {**MAIL, "mail": {**MAIL["mail"], **changes}}


def mail_without(key):
    return {**MAIL, "mail": {k: v for k, v in MAIL["mail"].items() if k != key}}


def attachment_with(index=0, **changes):
    items = [dict(a) for a in MAIL["mail"]["attachments"]]
    items[index] = {**items[index], **changes}
    return mail_with(attachments=items)


def listing_with(**changes):
    return {**LISTING, **changes}


def show(env, path, area="queue", members=()):
    return P.show(env.home, area, path, members)


# ── письмо: ответ по договору ───────────────────────────────────
def test_письмо_ответ_по_договору_и_кэш(env, worker):
    worker.describe = lambda ext: MAIL
    data = b"raw mail bytes"
    path = env.put("queue", "письмо.eml", data)
    answer = show(env, path)
    assert answer == {"kind": "mail", "mail": MAIL["mail"], "meta": {"name": "письмо.eml", "type": "eml", "size": len(data), "sha256": SHA(data),
                                                                       "batch": BATCH, "origin": {"archive": None, "inner": None}}}
    assert json.loads(json.dumps(answer, ensure_ascii=False)) == answer
    assert show(env, path) == answer and worker.ops == ["describe"] and env.cached() == [f"{SHA(data)}/desc.json"]
    with open(os.path.join(env.cache, SHA(data), "desc.json"), encoding="utf-8") as f:
        assert json.load(f) == {"v": 1, **MAIL}


def test_архив_ответ_по_договору_и_кэш(env, worker):
    worker.describe = lambda ext: LISTING
    data = b"raw zip bytes"
    path = env.put("queue", "архив.zip", data)
    answer = show(env, path)
    assert answer == {"kind": "listing", "listing": LISTING["listing"], "truncated": False,
                      "meta": {"name": "архив.zip", "type": "zip", "size": len(data), "sha256": SHA(data), "batch": BATCH, "origin": {"archive": None, "inner": None}}}
    assert show(env, path) == answer and worker.ops == ["describe"] and env.cached() == [f"{SHA(data)}/desc.json"]
    assert listing_with(truncated=True)["truncated"] is True
    worker.describe = lambda ext: listing_with(truncated=True)
    assert show(env, env.put("queue", "большой.zip", b"other"))["truncated"] is True


def test_набор_ключей_ответа_письма_и_архива(env, worker):
    worker.describe = lambda ext: MAIL
    assert set(show(env, env.put("queue", "а.eml", b"1"))) == {"kind", "meta", "mail"}
    worker.describe = lambda ext: LISTING
    assert set(show(env, env.put("queue", "а.zip", b"2"))) == {"kind", "meta", "listing", "truncated"}


def test_у_письма_и_архива_страниц_нет_page_отказ(env, worker):
    for name, desc in (("а.eml", MAIL), ("а.zip", LISTING)):
        worker.describe = lambda ext, d=desc: d
        path = env.put("queue", name, name.encode())
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", path, "1")
        assert e.value.message.code == "preview.no_pages"
    assert worker.ops == ["describe", "describe"]


def test_родитель_не_разбирает_письмо_а_берёт_ответ_рабочего_процесса(env, worker):
    real = K.eml_with([("настоящее.txt", b"real attachment")], sender="Real <real@example.org>")
    worker.describe = lambda ext: mail_with(**{"from": "Worker <w@example.test>"})
    answer = show(env, env.put("queue", "письмо.eml", real))
    assert answer["mail"]["from"] == "Worker <w@example.test>" and answer["mail"]["attachments"][0]["name"] == "договор.pdf"
    worker.member = lambda n: ("подложное.txt", b"fake attachment bytes")
    answer = show(env, env.put("queue", "второе.eml", real + b"\r\n"), members=[0])
    assert worker.inputs[-1] == b"fake attachment bytes" and answer["meta"]["sha256"] == SHA(b"fake attachment bytes") and answer["meta"]["name"] == "подложное.txt"


# ── порча ответа: письмо ────────────────────────────────────────
OK_MAIL_FIELDS = dict(subject="т" * 1000, text="я" * 20_000, date="2024-03-05T14:32:00", **{"from": "я" * 300})

GARBAGE_MAIL = {
    "лишний ключ": {**MAIL, "html": "<b>разметка</b>"},
    "нет ключа mail": {"kind": "mail", "type": "eml"},
    "mail не словарь": {**MAIL, "mail": ["from"]},
    "лишний ключ в письме": mail_with(html="<b>x</b>"),
    "нет from": mail_without("from"), "нет to": mail_without("to"), "нет cc": mail_without("cc"), "нет date": mail_without("date"),
    "нет subject": mail_without("subject"), "нет text": mail_without("text"), "нет truncated": mail_without("truncated"),
    "нет attachments": mail_without("attachments"),
    "from не строка": mail_with(**{"from": ["a@example.org"]}), "from пустой None": mail_with(**{"from": None}),
    "from длиннее 300": mail_with(**{"from": "я" * 301}), "from с управляющим": mail_with(**{"from": "a\x1bb"}),
    "from со сменой направления": mail_with(**{"from": "a‮b"}), "from с переводом строки": mail_with(**{"from": "a\nb"}),
    "to строкой": mail_with(to="ivanov@example.org"), "to не список": mail_with(to={"a": 1}), "to не строка в списке": mail_with(to=["a", 5]),
    "to больше ста": mail_with(to=["a@example.org"] * 101), "to строка длиннее 300": mail_with(to=["я" * 301]), "to с управляющим": mail_with(to=["a\x00b"]),
    "cc строкой": mail_with(cc="x"), "cc больше ста": mail_with(cc=["a@example.org"] * 101), "cc не строка": mail_with(cc=[None]),
    "date не строка": mail_with(date=5), "date словами": mail_with(date="вчера"), "date без времени": mail_with(date="2024-03-05"),
    "date с пробелом": mail_with(date="2024-03-05 14:32:00"), "date с мусором в конце": mail_with(date="2024-03-05T14:32:00+05:00; DROP"),
    "subject не строка": mail_with(subject=1), "subject длиннее 1000": mail_with(subject="т" * 1001), "subject с управляющим": mail_with(subject="a\rb"),
    "text не строка": mail_with(text=["т"]), "text длиннее 20000": mail_with(text="я" * 20_001),
    "truncated строкой": mail_with(truncated="нет"), "truncated числом": mail_with(truncated=0),
    "вложения не список": mail_with(attachments={"a": 1}), "вложений больше 200": mail_with(attachments=[{**ATTACHMENT, "member": i} for i in range(201)]),
    "вложение не словарь": mail_with(attachments=["договор.pdf"]),
    "лишний ключ у вложения": attachment_with(path="/etc/passwd"),
    "у вложения нет name": mail_with(attachments=[{k: v for k, v in ATTACHMENT.items() if k != "name"}]),
    "у вложения нет executable": mail_with(attachments=[{k: v for k, v in ATTACHMENT.items() if k != "executable"}]),
    "у вложения нет member": mail_with(attachments=[{k: v for k, v in ATTACHMENT.items() if k != "member"}]),
    "name не строка": attachment_with(name=5), "name длиннее 255": attachment_with(name="я" * 256), "name с управляющим": attachment_with(name="a\x1bb.pdf"),
    "name со сменой направления": attachment_with(name="a‮b.pdf"), "name с разделителем строк": attachment_with(name="a b"),
    "size отрицательный": attachment_with(size=-1), "size дробный": attachment_with(size=1.5), "size булево": attachment_with(size=True),
    "size строкой": attachment_with(size="5"), "size больше терабайта": attachment_with(size=(1 << 40) + 1),
    "type не строка": attachment_with(type=7), "type с путём": attachment_with(type="../x"), "type длиннее 20": attachment_with(type="a" * 21),
    "type с разметкой": attachment_with(type="<b>"), "type пустой": attachment_with(type=""),
    "member не число": attachment_with(member="0"), "member булево": attachment_with(member=False), "member дробный": attachment_with(member=0.0),
    "member не по порядку": attachment_with(index=1, member=0), "member за списком": attachment_with(index=1, member=7), "member отрицательный": attachment_with(member=-1),
    "executable строкой": attachment_with(executable="да"), "executable числом": attachment_with(executable=1),
}


@pytest.mark.parametrize("name", sorted(GARBAGE_MAIL))
def test_негодный_ответ_о_письме_none_с_пояснением_и_в_кэш_не_попадает(env, worker, name):
    worker.behave = say(GARBAGE_MAIL[name])
    answer = show(env, env.put("queue", "письмо.eml", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and answer["note"]["args"] == {"what": "schema"}, answer
    assert env.cached() == [] and not [n for n in os.listdir(env.cache) if n.startswith(".work")]


def test_хорошие_граничные_ответы_о_письме_принимаются(env, worker):
    cases = {"всё на пределе": mail_with(**OK_MAIL_FIELDS, to=["я" * 300] * 100, cc=["я" * 300] * 100,
                                         attachments=[{"name": "я" * 255, "size": 1 << 40, "type": "a" * 20, "member": i, "executable": bool(i % 2)} for i in range(200)]),
             "пусто": mail_with(**{"from": ""}, to=[], cc=[], date=None, subject="", text="", attachments=[]),
             "с поясом Z": mail_with(date="2024-03-05T14:32:00Z"), "с долями": mail_with(date="2024-03-05T14:32:00.123456+05:30"),
             "тип с точкой и плюсом": attachment_with(type="a.b+c-d_e"), "размер ноль": attachment_with(size=0)}
    for name, desc in cases.items():
        worker.describe = lambda ext, d=desc: d
        answer = show(env, env.put("queue", f"{len(name)}.eml", name.encode()))
        assert answer["kind"] == "mail" and answer["mail"] == desc["mail"], name


# ── порча ответа: архив ─────────────────────────────────────────
GARBAGE_LISTING = {
    "лишний ключ": {**LISTING, "html": "x"}, "нет listing": {"kind": "listing", "type": "zip", "truncated": False},
    "нет truncated": {"kind": "listing", "type": "zip", "listing": []}, "truncated строкой": listing_with(truncated="нет"),
    "truncated числом": listing_with(truncated=1),
    "listing не список": listing_with(listing={"a": 1}), "listing строкой": listing_with(listing="а.txt"),
    "строк больше 500": listing_with(listing=[{"name": f"ф{i}", "size": 1} for i in range(501)]),
    "строка не словарь": listing_with(listing=["а.txt"]), "лишний ключ в строке": listing_with(listing=[{"name": "а", "size": 1, "path": "/x"}]),
    "в строке нет size": listing_with(listing=[{"name": "а"}]), "в строке нет name": listing_with(listing=[{"size": 1}]),
    "name не строка": listing_with(listing=[{"name": 5, "size": 1}]), "name длиннее 255": listing_with(listing=[{"name": "я" * 256, "size": 1}]),
    "name с управляющим": listing_with(listing=[{"name": "a\x1bb", "size": 1}]), "name со сменой направления": listing_with(listing=[{"name": "‮exe", "size": 1}]),
    "size отрицательный": listing_with(listing=[{"name": "а", "size": -1}]), "size дробный": listing_with(listing=[{"name": "а", "size": 0.5}]),
    "size булево": listing_with(listing=[{"name": "а", "size": True}]), "size строкой": listing_with(listing=[{"name": "а", "size": "1"}]),
    "size больше предела": listing_with(listing=[{"name": "а", "size": 1 << 60}]),
    "type с путём": listing_with(type="../x"), "type не строка": listing_with(type=1),
}


@pytest.mark.parametrize("name", sorted(GARBAGE_LISTING))
def test_негодный_ответ_о_составе_архива_none_с_пояснением_и_в_кэш_не_попадает(env, worker, name):
    worker.behave = say(GARBAGE_LISTING[name])
    answer = show(env, env.put("queue", "архив.zip", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and answer["note"]["args"] == {"what": "schema"}, answer
    assert env.cached() == []


def test_хорошие_граничные_ответы_об_архиве_принимаются(env, worker):
    cases = {"пятьсот строк": listing_with(listing=[{"name": "я" * 255, "size": 1 << 50} for _ in range(500)], truncated=True),
             "пусто": listing_with(listing=[]), "размер ноль": listing_with(listing=[{"name": "а", "size": 0}])}
    for name, desc in cases.items():
        worker.describe = lambda ext, d=desc: d
        answer = show(env, env.put("queue", f"{len(name)}.zip", name.encode()))
        assert answer["kind"] == "listing" and answer["listing"] == desc["listing"], name


def test_испорченное_описание_письма_и_архива_в_кэше_считается_отсутствующим(env, worker):
    for desc, name in ((MAIL, "а.eml"), (LISTING, "а.zip")):
        worker.describe = lambda ext, d=desc: d
        data = name.encode()
        path = env.put("queue", name, data)
        show(env, path)
        target = os.path.join(env.cache, SHA(data), "desc.json")
        for bad in (b"", b"{", json.dumps({"v": 2, **desc}).encode(), json.dumps({"v": 1, "kind": desc["kind"], "type": "eml"}).encode(),
                    json.dumps({"v": 1, **{**desc, "html": "x"}}).encode()):
            write(target, bad)
            assert show(env, path)["kind"] == desc["kind"]
    assert worker.ops == ["describe"] * 12


# ── вложение тем же просмотром ──────────────────────────────────
def mail_bytes(tag=b"1"):
    return b"raw mail bytes " + tag


def test_вложение_сначала_достаётся_потом_описывается_как_обычный_файл(env, worker):
    worker.member = lambda n: ("отчёт.PDF", b"%PDF attachment")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "pages" and (answer["pages"], answer["shown"]) == (3, 3)
    assert worker.ops == ["member", "describe"] and worker.calls == [("member", "eml", 0), ("describe", "pdf", None)]       # расширение — от имени вложения
    assert worker.inputs == [mail_bytes(), b"%PDF attachment"]                                                              # рабочий процесс описывает байты вложения


def test_сведения_о_вложении_имя_размер_sha256_и_ни_следа_квитанции_письма(env, worker):
    worker.member = lambda n: ("а.pdf", b"attachment bytes")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    env.receipt({"name": "а.pdf", "type": "docx", "archive": "чужой.zip", "inner": "не то"})        # квитанция другого файла пачки с тем же именем
    env.receipt({"name": "письмо.eml", "type": "eml", "archive": "письма.zip", "inner": "письмо.eml"})
    assert show(env, mail, members=[0])["meta"] == {"name": "а.pdf", "type": "pdf", "size": len(b"attachment bytes"), "sha256": SHA(b"attachment bytes"),
                                                    "batch": BATCH, "origin": {"archive": None, "inner": None}}


def test_ключ_кэша_вложения_sha256_его_байтов_а_не_письма(env, worker):
    worker.member = lambda n: ("а.pdf", b"attachment bytes")
    mail = mail_bytes()
    answer = show(env, env.put("queue", "письмо.eml", mail), members=[0])
    assert answer["meta"]["sha256"] == SHA(b"attachment bytes") != SHA(mail)
    assert env.cached() == [f"{SHA(b'attachment bytes')}/desc.json"] and not os.path.exists(os.path.join(env.cache, SHA(mail)))


def test_два_вложения_одного_письма_не_перекрывают_друг_друга(env, worker):
    worker.member = lambda n: (f"файл{n}.pdf", f"bytes of attachment {n}".encode())
    worker.describe = lambda ext: {"kind": "pages", "type": "pdf", "pages": 1 + len(worker.inputs[-1]), "shown": min(1 + len(worker.inputs[-1]), 20)}
    mail = env.put("queue", "письмо.eml", mail_bytes())
    first, second = show(env, mail, members=[0]), show(env, mail, members=[1])
    assert first["meta"]["sha256"] == SHA(b"bytes of attachment 0") and second["meta"]["sha256"] == SHA(b"bytes of attachment 1")
    assert first["meta"]["name"] == "файл0.pdf" and second["meta"]["name"] == "файл1.pdf"
    assert env.cached() == sorted([f"{SHA(b'bytes of attachment 0')}/desc.json", f"{SHA(b'bytes of attachment 1')}/desc.json"])
    assert show(env, mail, members=[0]) == first and show(env, mail, members=[1]) == second          # из кэша: каждое своё
    assert worker.ops == ["member", "describe", "member", "describe", "member", "member"]


def test_одно_и_то_же_вложение_из_разных_писем_описывается_один_раз(env, worker):
    worker.member = lambda n: ("а.pdf", b"the same attachment")
    first = show(env, env.put("queue", "письмо-1.eml", mail_bytes(b"1")), members=[0])
    second = show(env, env.put("queue", "письмо-2.eml", mail_bytes(b"2")), members=[3])
    assert first["pages"] == second["pages"] and first["meta"]["sha256"] == second["meta"]["sha256"]
    assert worker.ops == ["member", "describe", "member"]
    assert env.cached() == [f"{SHA(b'the same attachment')}/desc.json"]


def test_вложение_письма_во_вложении_уровень_за_уровнем(env, worker):
    def member(n):                                         # из письма достаётся пересланное письмо, из него — глубокое вложение
        if worker.inputs[-1] == mail_bytes():
            return "пересланное.eml", b"middle letter bytes"
        return "глубокое.pdf", b"deep attachment bytes"

    worker.member = member
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[5, 0])
    assert worker.ops == ["member", "member", "describe"] and [call[2] for call in worker.calls[:2]] == [5, 0]
    assert worker.inputs == [mail_bytes(), b"middle letter bytes", b"deep attachment bytes"]
    assert answer["meta"]["name"] == "глубокое.pdf" and answer["meta"]["sha256"] == SHA(b"deep attachment bytes")
    assert env.cached() == [f"{SHA(b'deep attachment bytes')}/desc.json"]


def test_глубже_двух_уровней_отказ_до_запуска(env, worker):
    mail = env.put("queue", "письмо.eml", mail_bytes())
    for members in ([0, 1, 2], [0, 0, 0, 0]):
        with pytest.raises(P.PreviewError) as e:
            show(env, mail, members=members)
        assert e.value.message.code == "preview.bad_member"
        with pytest.raises(P.PreviewError):
            P.page(env.home, "queue", mail, "1", members)
    assert worker.calls == []


def test_номер_вне_списка_отказ_с_кодом_и_ничего_не_описано(env, worker):
    worker.member = lambda n: {"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": n, "count": 2}}
    mail = env.put("queue", "письмо.eml", mail_bytes())
    with pytest.raises(P.PreviewError) as e:
        show(env, mail, members=[7])
    assert e.value.message.code == "preview.no_member" and e.value.message.args == {"member": 7, "count": 2} and e.value.message.to_json()["text"]
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [7])
    assert e.value.message.code == "preview.no_member"
    assert worker.ops == ["member", "member"] and env.cached() == []


def test_у_файла_не_письма_вложений_нет_отказ_с_кодом(env, worker):
    worker.member = lambda n: {"kind": "none", "type": "txt", "reason": "no_member", "args": {"member": n, "count": 0}}
    with pytest.raises(P.PreviewError) as e:
        show(env, env.put("queue", "а.txt", "просто текст".encode("utf-8")), members=[0])
    assert e.value.message.args == {"member": 0, "count": 0}


def test_отказ_по_номеру_во_втором_уровне(env, worker):
    worker.member = lambda n: ("среднее.eml", b"middle") if n == 1 else {"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": n, "count": 1}}
    with pytest.raises(P.PreviewError) as e:
        show(env, env.put("queue", "письмо.eml", mail_bytes()), members=[1, 4])
    assert e.value.message.args == {"member": 4, "count": 1} and worker.ops == ["member", "member"]


@pytest.mark.parametrize("reason,args,code,note_args", [
    ("too_big", {"limit": 64}, "preview.too_big", {"limit": 64}), ("memory", {}, "preview.memory", {}),
    ("broken", {"error_type": "OleFileError"}, "preview.broken", {"error_type": "OleFileError"}), ("encrypted", {}, "preview.encrypted", {})])
def test_отказ_рабочего_процесса_при_извлечении_none_с_пояснением_без_исключения(env, worker, reason, args, code, note_args):
    worker.member = lambda n: {"kind": "none", "type": "eml", "reason": reason, "args": args}
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == code and answer["note"]["args"] == note_args
    assert answer["meta"] == {"name": None, "type": "eml", "size": None, "sha256": None, "batch": BATCH, "origin": {"archive": None, "inner": None}}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == code
    assert env.cached() == [] and worker.ops == ["member", "member"]


GARBAGE_MEMBER = {
    "ничего не записал": (lambda op, outdir: P.Run(0), "missing"),
    "result.json не JSON": (say("{не json".encode("utf-8")), "schema"),
    "вместо вложения описание": (say({"kind": "text", "type": "txt", "text": "т", "truncated": False}), "schema"),
    "вместо вложения письмо": (say(MAIL), "schema"),
    "вид page": (say({"kind": "page", "page": 1}), "schema"),
    "лишний ключ": (say({"kind": "member", "name": "а.pdf", "size": 5}), "schema"),
    "нет имени": (say({"kind": "member"}), "schema"),
    "имя не строка": (say({"kind": "member", "name": ["а.pdf"]}), "schema"),
    "имя длиннее 255": (say({"kind": "member", "name": "я" * 256}), "schema"),
    "имя с управляющим": (say({"kind": "member", "name": "а\x1bб"}), "schema"),
    "none с чужой причиной": (say({"kind": "none", "type": "eml", "reason": "хочу", "args": {}}), "schema"),
    "no_member без count": (say({"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": 1}}), "schema"),
    "no_member с булевым": (say({"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": True, "count": 1}}), "schema"),
    "no_member отрицательный": (say({"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": -1, "count": 1}}), "schema"),
    "too_big со строкой": (say({"kind": "none", "type": "eml", "reason": "too_big", "args": {"limit": "много"}}), "schema"),
}


def member_file(kind):
    def behave(op, outdir):
        write(os.path.join(outdir, "result.json"), json.dumps({"kind": "member", "name": "а.pdf"}).encode("utf-8"))
        target = os.path.join(outdir, "member.bin")
        if kind == "нет":
            return P.Run(0)
        if kind == "ссылка":
            os.symlink("/etc/hostname", target)
        elif kind == "каталог":
            os.mkdir(target)
        elif kind == "больше предела":
            sparse(target, P.MEMBER_MAX + 1)
        elif kind == "канал":
            os.mkfifo(target)
        return P.Run(0)
    return behave


for _kind, _what in (("нет", "missing"), ("ссылка", "not_a_file"), ("каталог", "not_a_file"), ("больше предела", "too_big"), ("канал", "not_a_file")):
    GARBAGE_MEMBER[f"member.bin: {_kind}"] = (member_file(_kind), _what)


def link_result(op, outdir):
    os.symlink("/etc/hostname", os.path.join(outdir, "result.json"))
    return P.Run(0)


GARBAGE_MEMBER["result.json — ссылка"] = (link_result, "not_a_file")


@pytest.mark.parametrize("name", sorted(GARBAGE_MEMBER))
def test_негодный_ответ_об_извлечении_none_и_отказ_страницы_кэш_пуст_и_каталог_убран(env, worker, name):
    behave, what = GARBAGE_MEMBER[name]
    worker.behave = behave
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer" and answer["note"]["args"] == {"what": what}, answer
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == "preview.bad_answer" and e.value.message.args == {"what": what}
    assert env.cached() == [] and not [n for n in os.listdir(env.cache) if n.startswith(".work")]


def test_вложение_ровно_предельного_размера_принимается(env, worker, monkeypatch):
    monkeypatch.setattr(P, "MEMBER_MAX", 100)
    worker.member = lambda n: ("а.bin", b"x" * 100)
    assert show(env, env.put("queue", "письмо.eml", mail_bytes()), members=[0])["kind"] == "pages"
    worker.member = lambda n: ("б.bin", b"x" * 101)
    answer = show(env, env.put("queue", "письмо-2.eml", mail_bytes(b"2")), members=[0])
    assert answer["kind"] == "none" and answer["note"]["args"] == {"what": "too_big"}


@pytest.mark.parametrize("run,code", [(P.Run(1), "preview.worker_failed"), (P.Run(-9), "preview.worker_failed"), (P.Run(None, True), "preview.timeout")])
def test_рабочий_процесс_упал_при_извлечении_none_и_исключения_нет(env, worker, run, code):
    def behave(op, outdir):
        write(os.path.join(outdir, "result.json"), json.dumps({"kind": "member", "name": "а.pdf"}).encode("utf-8"))
        write(os.path.join(outdir, "member.bin"), b"x")
        return run                                                                       # даже готовый ответ упавшего не принимается
    worker.behave = behave
    mail = env.put("queue", "письмо.eml", mail_bytes())
    assert show(env, mail, members=[0])["note"]["code"] == code
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == code and env.cached() == []


def test_исключение_при_запуске_извлечения_не_выходит_наружу(env, worker):
    def explode(op, outdir):
        raise RuntimeError("не запустилось")
    worker.behave = explode
    answer = show(env, env.put("queue", "письмо.eml", mail_bytes()), members=[0])
    assert answer["note"]["code"] == "preview.worker_failed" and answer["note"]["args"] == {"why": "RuntimeError"}


def test_имя_вложения_от_рабочего_процесса_данные_а_не_путь_и_расширение_только_буквы_и_цифры(env, worker):
    cases = (("отчёт.PDF", "pdf"), ("a b.p d f", ""), ("../../x.pdf", "pdf"), ("без-расширения", ""), ("а.тхт", ""), ("a;rm -rf.sh", "sh"), ("x.a;rm", ""), ("x.abcdefghij", "abcdefghij"),
             ("x.abcdefghijk", ""))
    mail = env.put("queue", "письмо.eml", mail_bytes())
    for number, (name, ext) in enumerate(cases):
        worker.member = lambda n, name=name, number=number: (name, f"bytes {number}".encode())
        answer = show(env, mail, members=[0])
        assert answer["meta"]["name"] == name and worker.calls[-1] == ("describe", ext, None), (name, worker.calls[-1])


# ── страница вложения ───────────────────────────────────────────
def test_страница_вложения_рисуется_один_раз_ключ_sha256_вложения(env, worker):
    worker.member = lambda n: ("а.pdf", b"pdf attachment")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    worker.render = lambda page: png(10 + page, 10)
    first = P.page(env.home, "queue", mail, "2", [4])
    assert first == png(12, 10) and worker.ops == ["member", "describe", "render"]
    assert worker.inputs[-1] == b"pdf attachment" and worker.calls[-1] == ("render", "pdf", 2)
    assert P.page(env.home, "queue", mail, "2", [4]) == first and worker.ops == ["member", "describe", "render", "member"]       # страница из кэша, вложение достаётся ради ключа
    assert env.cached() == [f"{SHA(b'pdf attachment')}/desc.json", f"{SHA(b'pdf attachment')}/page-2.png"]
    other = env.put("queue", "письмо-2.eml", mail_bytes(b"2"))
    assert P.page(env.home, "queue", other, "2", [0]) == first and worker.ops.count("render") == 1                               # то же вложение из другого письма


def test_страница_вложения_вне_диапазона_отказ_и_render_не_запущен(env, worker):
    worker.member = lambda n: ("а.pdf", b"pdf attachment")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "9", [0])
    assert e.value.message.code == "preview.no_page" and e.value.message.args == {"page": 9, "shown": 3} and "render" not in worker.ops


def test_страница_у_вложения_без_страниц_отказ(env, worker):
    worker.member = lambda n: ("а.txt", b"text attachment")
    worker.describe = lambda ext: {"kind": "text", "type": "txt", "text": "т", "truncated": False}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", env.put("queue", "письмо.eml", mail_bytes()), "1", [0])
    assert e.value.message.code == "preview.no_pages"


def test_картинка_во_вложении_страница_один_из_описания(env, worker):
    worker.member = lambda n: ("а.png", b"png attachment")
    worker.describe = lambda ext: {"kind": "image", "type": "png"}
    worker.render = lambda page: png(50, 40)
    mail = env.put("queue", "письмо.eml", mail_bytes())
    assert show(env, mail, members=[1])["kind"] == "image"
    assert P.page(env.home, "queue", mail, "1", [1]) == png(50, 40) and "render" not in worker.ops


def test_программа_во_вложении_none_program_и_содержимое_не_показывается(env, worker):
    worker.member = lambda n: ("настройка.exe", K.PE)
    worker.describe = lambda ext: {"kind": "none", "type": "pe", "reason": "program", "args": {}}
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.program" and answer["meta"]["type"] == "pe"
    assert set(answer) == {"kind", "meta", "note"} and answer["meta"]["sha256"] == SHA(K.PE) and answer["meta"]["name"] == "настройка.exe"
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == "preview.program" and env.cached() == []


# ── песочница, замки, слоты ─────────────────────────────────────
def test_без_bwrap_вложение_none_с_пояснением_и_рабочий_процесс_не_запущен(env, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "bwrap_path", lambda: None)
    monkeypatch.setattr(P, "_run_worker", lambda *a: calls.append(a))
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.no_sandbox" and answer["meta"]["sha256"] is None and answer["meta"]["name"] is None
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == "preview.no_sandbox" and calls == [] and env.cached() == []


def test_слоты_заняты_вложение_rendering_страница_отказ_рабочий_процесс_не_запущен(env, worker, monkeypatch):
    monkeypatch.setattr(P, "SLOT_WAIT", 0.2)
    mail = env.put("queue", "письмо.eml", mail_bytes())
    fds = [hold(os.path.join(env.cache, f".slot{i}")) for i in range(2)]
    try:
        assert show(env, mail, members=[0]) == {"kind": "rendering"}
        with pytest.raises(P.PreviewError) as e:
            P.page(env.home, "queue", mail, "1", [0])
        assert e.value.message.code == "preview.still_rendering"
    finally:
        for fd in fds:
            os.close(fd)
    assert worker.calls == []


def test_извлечение_занимает_слот_и_не_больше_двух_рабочих_процессов_разом(env, worker):
    worker.delay = 0.3
    mails = [env.put("queue", f"письмо{i}.eml", mail_bytes(str(i).encode())) for i in range(4)]
    worker.member = lambda n: (f"файл.pdf", f"attachment of the call {len(worker.calls)}".encode())
    runs = [in_thread(lambda p=p: show(env, p, members=[0])) for p in mails]
    for t, _ in runs:
        t.join()
    assert all("error" not in box for _, box in runs), [box for _, box in runs]
    assert worker.peak == 2


def test_объект_вложения_описывает_один_запрос_второй_получает_rendering(env, worker):
    worker.member = lambda n: ("а.pdf", b"shared attachment")
    worker.op_delay = {"describe": 0.8}
    mail = env.put("queue", "письмо.eml", mail_bytes())
    first, box = in_thread(lambda: show(env, mail, members=[0]))
    assert waits_for(lambda: "describe" in worker.ops)
    assert show(env, mail, members=[0]) == {"kind": "rendering"}
    first.join()
    assert box["value"]["kind"] == "pages" and worker.ops.count("describe") == 1


def test_замок_объекта_вложения_занят_show_сразу_rendering(env, worker):
    worker.member = lambda n: ("а.pdf", b"locked attachment")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    fd = hold(os.path.join(env.cache, SHA(b"locked attachment") + ".lock"))
    try:
        assert show(env, mail, members=[0]) == {"kind": "rendering"}
    finally:
        os.close(fd)
    assert worker.ops == ["member"] and show(env, mail, members=[0])["kind"] == "pages"


def open_fds():
    return len(os.listdir("/proc/self/fd"))


def test_дескрипторы_не_текут_и_рабочие_каталоги_убраны_при_любом_исходе(env, worker):
    mail = env.put("queue", "письмо.eml", mail_bytes())
    show(env, mail, members=[0])                                                          # прогрев: кэш, замки и слоты открыты один раз
    before = open_fds()
    for members in ([0], [1, 0], [2]):
        show(env, mail, members=members)
        P.page(env.home, "queue", mail, "1", members)
    worker.member = lambda n: {"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": n, "count": 0}}
    for _ in range(3):
        with pytest.raises(P.PreviewError):
            show(env, mail, members=[9])
    worker.member = lambda n: None
    worker.behave = lambda op, outdir: P.Run(0)
    show(env, mail, members=[0])
    assert open_fds() == before and not [n for n in os.listdir(env.cache) if n.startswith(".work")]


def test_рабочий_каталог_вложения_живёт_пока_идёт_описание_и_убран_после(env, worker):
    seen = []

    def describe(ext):
        seen.append(sorted(n for n in os.listdir(env.cache) if n.startswith(".work")))
        return PDF

    worker.describe = describe
    show(env, env.put("queue", "письмо.eml", mail_bytes()), members=[0])
    assert len(seen[0]) == 2 and not [n for n in os.listdir(env.cache) if n.startswith(".work")]       # на время описания живы каталог вложения и каталог самого описания


def test_кэш_не_каталог_вложение_none_и_отказ_страницы_с_пояснением(env, worker):
    target = os.path.join(env.home, "cache")
    write(target, b"file instead of a directory")
    mail = env.put("queue", "письмо.eml", mail_bytes())
    answer = show(env, mail, members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.cache_failed" and answer["note"]["args"] == {"error_type": "NotADirectoryError"}
    with pytest.raises(P.PreviewError) as e:
        P.page(env.home, "queue", mail, "1", [0])
    assert e.value.message.code == "preview.cache_failed" and worker.calls == []


# ── новые пояснения none ────────────────────────────────────────
NOTES = [
    ({"reason": "needs_extractor", "args": {"type": "7z"}}, "preview.needs_extractor", {"type": "7z"}),
    ({"reason": "too_big", "args": {"limit": 100}}, "preview.too_big", {"limit": 100}),
    ({"reason": "no_events", "args": {}}, "preview.no_events", {}),
]


@pytest.mark.parametrize("body,code,args", NOTES)
def test_новые_none_с_пояснением_из_каталога_и_в_кэш_не_попадают(env, worker, body, code, args):
    worker.describe = lambda ext: {"kind": "none", "type": "7z", **body}
    answer = show(env, env.put("queue", "а.7z", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == code and answer["note"]["args"] == args and answer["note"]["text"]
    assert env.cached() == []


def test_none_no_member_при_описании_не_принимается_это_ответ_только_извлечения(env, worker):
    worker.describe = lambda ext: {"kind": "none", "type": "eml", "reason": "no_member", "args": {"member": 1, "count": 0}}
    answer = show(env, env.put("queue", "письмо.eml", b"x"))
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.bad_answer"


def test_none_member_при_извлечении_только_no_member_и_названные_причины(env, worker):
    worker.member = lambda n: {"kind": "none", "type": "eml", "reason": "needs_extractor", "args": {"type": "7z"}}
    answer = show(env, env.put("queue", "письмо.eml", b"x"), members=[0])
    assert answer["kind"] == "none" and answer["note"]["code"] == "preview.needs_extractor"


# ── пределы ─────────────────────────────────────────────────────
def test_пределы_родителя_по_плану():
    assert (P.MAIL_PEOPLE, P.MAIL_LINE, P.MAIL_SUBJECT, P.MAX_ATTACHMENTS, P.NAME_LIMIT, P.LISTING_MAX, P.MEMBER_MAX) == (100, 300, 1000, 200, 255, 500, 64 << 20)
    assert P.MAX_MEMBERS == 2
