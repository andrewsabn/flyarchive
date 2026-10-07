"""Права на файлы архива (NFR-01, часть без прав администратора).

Каталог архива и его части закрыты от других пользователей машины: 700 на каталогах,
600 на файлах. Код служб лежит в каталоге репозитория, а не в архиве, и этой проверкой не
охвачен: закрыть его от записи другим пользователям (chmod go-w) нужно самому — кто может
менять код, получает всё, что могут службы.

Это защита от других пользователей и чужих служб на той же машине. От процессов самого
владельца она не защищает: для них — песочница (sandbox.py).

    flyarchive perms check [--deep]
    flyarchive perms fix   [--deep]
"""
import collections
import os
import stat

PRIVATE_UMASK = 0o077                # маска служб и команды: созданное — только владельцу (то же, что UMask=0077 в файлах служб)
SHALLOW = ("secrets", "logs")        # здесь файлы проверяются и без --deep
OTHERS = 0o077                       # любые права группы и остальных

Problem = collections.namedtuple("Problem", "path text want")     # want — права после исправления или None


class PermsError(Exception):
    pass


def close_umask():
    """Закрывает маску процесса: всё, что служба или команда создаёт, — в том числе внутри библиотек, как таблица индекса, — только владельцу,
    какой бы маска ни была у запустившего. Службы под systemd получают то же от UMask=0077; здесь — для запуска руками. Возвращает прежнюю маску."""
    return os.umask(PRIVATE_UMASK)


def _one(path, st, uid):
    if st.st_uid != uid:
        yield Problem(path, f"владелец не тот: uid {st.st_uid}, ожидался {uid}", None)
    current = stat.S_IMODE(st.st_mode)
    if current & OTHERS:
        yield Problem(path, f"открыт другим пользователям: права {current:o}", current & ~OTHERS)


def _children(path):
    """Содержимое каталога без ссылок: ссылку наружу не проверяем и не исправляем."""
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return
    for name in names:
        full = os.path.join(path, name)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if not stat.S_ISLNK(st.st_mode):
            yield full, st


def _walk(path, uid):
    for full, st in _children(path):
        yield from _one(full, st, uid)
        if stat.S_ISDIR(st.st_mode):
            yield from _walk(full, uid)


def scan(home, uid=None, deep=False):
    """Перечисляет нарушения. Без deep — каталог архива, его части и файлы журналов и секретов."""
    uid = os.getuid() if uid is None else uid
    try:
        st = os.lstat(home)
    except OSError:
        raise PermsError(f"нет каталога архива: {home}")
    if not stat.S_ISDIR(st.st_mode):
        raise PermsError(f"нет каталога архива: {home}")
    yield from _one(home, st, uid)
    for full, st in _children(home):
        if not stat.S_ISDIR(st.st_mode):
            continue
        yield from _one(full, st, uid)
        if deep:
            yield from _walk(full, uid)
        elif os.path.basename(full) in SHALLOW:
            for inner, inner_st in _children(full):
                yield from _one(inner, inner_st, uid)


def audit(home, uid=None, deep=False):
    return list(scan(home, uid, deep))


def fix(home, uid=None, deep=False):
    """Закрывает права там, где это может сделать владелец. Возвращает число исправленного."""
    changed = 0
    for p in scan(home, uid, deep):
        if p.want is None:
            continue
        try:
            os.chmod(p.path, p.want)
            changed += 1
        except OSError:
            pass                      # чужой файл: останется в проверке
    return changed
