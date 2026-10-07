"""Карантин не индексируется: FR-46."""
import os

import index_more as X


def test_индексатор_не_заходит_в_карантин(tmp_path, monkeypatch):
    root = tmp_path / "входящие"
    for rel in ("a.txt", "x/d.txt", "_карантин/b.txt", "x/_карантин/c.txt", "x/_карантин/глубже/e.txt"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("текст", encoding="utf-8")
    monkeypatch.setattr(X, "ROOTS", [("входящие", str(root), None)])
    found = sorted(os.path.relpath(p, root).replace(os.sep, "/") for _, _, p, _ in X.work_items(set()))
    assert found == ["a.txt", "x/d.txt"]


def test_обычные_каталоги_обходятся_как_раньше(tmp_path, monkeypatch):
    root = tmp_path / "почта"
    for rel in ("Inbox/письмо.eml", "Inbox/карантин.txt", "Sent/отчёт.md"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("текст", encoding="utf-8")
    monkeypatch.setattr(X, "ROOTS", [("mail", str(root), None)])
    assert len(list(X.work_items(set()))) == 3
