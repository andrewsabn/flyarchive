"""Подставной `docker` для тестов просмотра через контейнер (FR-78): сценарий в PATH, который пишет свои аргументы в журнал
и кладёт в выходной каталог то, что скажет тест. Настоящий docker и образ — в test_preview_container_real.py.

    fake = FakeDocker(tmp_path).install(monkeypatch)     # PATH теперь начинается с каталога подставного docker
    fake.plan(run={"files": {"data.pdf": pdf_bytes}})      # что сделает `docker run`
    fake.runs()                                            # разобранные запуски: аргументы, входные файлы в момент запуска, подключения

План — JSON в каталоге состояния (путь в FAKE_DOCKER_STATE, его наследуют и дочерние процессы). Разделы плана:
    run     {"files": {имя: байты | {"symlink": цель} | {"dir": 1} | {"fifo": 1} | {"sparse": размер}}, "exit": код, "sleep": секунд, "stderr": текст}
    image   {"exit": код, "stderr": текст, "sleep": секунд}            docker image inspect
    pull    {"exit", "stderr", "sleep"}                                 docker pull
    digests список RepoDigests, который покажет `image inspect --format`
    ps      имена контейнеров для `docker ps`; ps_answer — {"exit", "stderr", "sleep"} для него
    created {имя: время ISO} для `docker inspect`
    events  текст ответа `docker events` (по умолчанию пусто)           rm_exit, kill_exit — коды возврата
"""
import base64
import json
import os
import stat
import sys
import textwrap

SCRIPT = textwrap.dedent('''\
    #!{python}
    import base64, hashlib, json, os, sys, time
    state = os.environ["FAKE_DOCKER_STATE"]
    args = sys.argv[1:]
    try:
        with open(os.path.join(state, "plan.json"), encoding="utf-8") as f:
            plan = json.load(f)
    except OSError:
        plan = {{}}


    def log(entry):
        with open(os.path.join(state, "log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({{"argv": args, "pid": os.getpid(), "time": time.time(), **entry}}, ensure_ascii=False) + "\\n")


    def answer(section):
        part = plan.get(section) or {{}}
        time.sleep(part.get("sleep", 0))
        if part.get("stderr"):
            sys.stderr.write(part["stderr"] + "\\n")
        return part.get("exit", 0)


    def mounts():
        found = {{}}
        for i, a in enumerate(args):
            if a == "-v" and i + 1 < len(args):
                source, _, rest = args[i + 1].partition(":")
                found[rest.split(":")[0]] = (source, rest)
        return found


    def listing(folder):
        out = {{}}
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return None
        for name in names:
            path = os.path.join(folder, name)
            st = os.lstat(path)
            entry = {{"mode": oct(st.st_mode & 0o7777), "size": st.st_size}}
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, "rb") as f:
                    entry["sha256"] = hashlib.sha256(f.read()).hexdigest()
                    f.seek(0)
                    entry["head"] = f.read(64).decode("latin-1")
            out[name] = entry
        return out


    cmd = args[0] if args else ""
    if cmd == "run":
        found = mounts()
        inbox = found.get("/in")
        log({{"op": "run", "mounts": {{k: v[0] for k, v in found.items()}}, "mount_args": [v[1] for v in found.values()],
              "in": listing(inbox[0]) if inbox else None,
              "in_dir_mode": oct(os.stat(inbox[0]).st_mode & 0o7777) if inbox else None,
              "out_before": listing(found["/out"][0]) if "/out" in found else None}})
        plan_run = plan.get("run") or {{}}
        time.sleep(plan_run.get("sleep", 0))
        if "/out" in found:
            for name, spec in (plan_run.get("files") or {{}}).items():
                path = os.path.join(found["/out"][0], name)
                if isinstance(spec, str):
                    with open(path, "wb") as f:
                        f.write(base64.b64decode(spec))
                elif "symlink" in spec:
                    os.symlink(spec["symlink"], path)
                elif "dir" in spec:
                    os.mkdir(path)
                elif "fifo" in spec:
                    os.mkfifo(path)
                elif "sparse" in spec:
                    with open(path, "wb") as f:
                        f.truncate(spec["sparse"])
                elif "grow" in spec:                      # растёт, пока его не остановят: kill клиента или docker kill
                    with open(path, "wb") as f:
                        for _ in range(spec["grow"]):
                            f.write(b"x" * (1 << 20))
                            f.flush()
                            time.sleep(0.05)
        if plan_run.get("stderr"):
            sys.stderr.write(plan_run["stderr"] + "\\n")
        sys.exit(plan_run.get("exit", 0))
    elif cmd == "image" and args[1:2] == ["inspect"]:
        log({{"op": "image-inspect"}})
        fmt = args[args.index("--format") + 1] if "--format" in args else ""
        code = answer("image")
        if code == 0:
            print(json.dumps(plan.get("digests", [])) if "RepoDigests" in fmt else "sha256:fake-image-id")
        sys.exit(code)
    elif cmd == "pull":
        log({{"op": "pull"}})
        sys.exit(answer("pull"))
    elif cmd == "ps":
        log({{"op": "ps"}})
        code = answer("ps_answer")
        print("\\n".join(plan.get("ps", [])))
        sys.exit(code)
    elif cmd == "inspect":
        log({{"op": "inspect"}})
        for name in args:
            if name in plan.get("created", {{}}):
                print("/" + name + " " + plan["created"][name])
        sys.exit(0)
    elif cmd in ("kill", "rm"):
        log({{"op": cmd}})
        sys.exit(plan.get(cmd + "_exit", 0))
    elif cmd == "events":
        log({{"op": "events"}})
        sys.stdout.write(plan.get("events", ""))
        sys.exit(0)
    log({{"op": "other"}})
    sys.stderr.write("fake docker: неизвестная команда " + cmd + "\\n")
    sys.exit(1)
''')


class FakeDocker:
    def __init__(self, tmp):
        self.state = str(tmp / "fake-docker-state")
        self.bin = str(tmp / "fake-docker-bin")
        os.makedirs(self.state)
        os.makedirs(self.bin)
        path = os.path.join(self.bin, "docker")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SCRIPT.format(python=sys.executable))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        self.plan_data = {}
        self.plan()

    def install(self, monkeypatch):
        monkeypatch.setenv("FAKE_DOCKER_STATE", self.state)
        monkeypatch.setenv("PATH", self.bin + os.pathsep + os.environ.get("PATH", ""))
        return self

    @property
    def environ(self):
        """Окружение для запуска `flyarchive` дочерним процессом."""
        return {**os.environ, "FAKE_DOCKER_STATE": self.state, "PATH": self.bin + os.pathsep + os.environ.get("PATH", "")}

    def plan(self, **sections):
        """Дополняет план; значения run.files — байты (кодируются) или словарь-описание."""
        for name, value in sections.items():
            if name == "run" and value.get("files"):
                value = {**value, "files": {k: base64.b64encode(v).decode("ascii") if isinstance(v, bytes) else v for k, v in value["files"].items()}}
            self.plan_data[name] = value
        with open(os.path.join(self.state, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(self.plan_data, f)
        return self

    def calls(self):
        try:
            with open(os.path.join(self.state, "log.jsonl"), encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def ops(self):
        return [c["op"] for c in self.calls()]

    def runs(self):
        return [c for c in self.calls() if c["op"] == "run"]
