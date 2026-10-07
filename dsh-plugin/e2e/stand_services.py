"""Службы одноразового стенда сквозных проверок (FR-91): поиск, документы, сервер MCP — те же tools/*.py, но поверх архива стенда.

    python3 stand_services.py search|office|mcp

Репозиторий не меняется: зашитые в модулях адреса и порты подменяются после загрузки. Всё берётся из окружения, которое готовит
stand.cjs: BA_E2E_TOOLS (снимок tools), BA_E2E_DIR (каталог стенда), FLYARCHIVE_HOME (архив стенда), FLYARCHIVE_EMBED_URL (векторы стенда),
BA_E2E_SEARCH_PORT, BA_E2E_OFFICE_PORT, BA_E2E_MCP_PORT. Каждая служба сначала сверяет, что работает внутри каталога стенда, и иначе не стартует.
"""
import os
import sys

DIR = os.path.realpath(os.environ["BA_E2E_DIR"])
TOOLS = os.path.realpath(os.environ["BA_E2E_TOOLS"])
HOME = os.path.realpath(os.environ["FLYARCHIVE_HOME"])
SEARCH_PORT, OFFICE_PORT, MCP_PORT = (int(os.environ[k]) for k in ("BA_E2E_SEARCH_PORT", "BA_E2E_OFFICE_PORT", "BA_E2E_MCP_PORT"))
EMBED = os.environ["FLYARCHIVE_EMBED_URL"]

assert HOME.startswith(DIR + os.sep), "FLYARCHIVE_HOME вне стенда: " + HOME
assert TOOLS.startswith(DIR + os.sep), "tools вне стенда: " + TOOLS
assert EMBED.startswith("http://127.0.0.1:"), "векторы не на петле: " + EMBED
assert os.path.realpath(os.path.expanduser("~")).startswith(DIR + os.sep), "HOME вне стенда"
sys.path.insert(0, TOOLS)
sys.stdout.reconfigure(encoding="utf-8")


def search():
    import auth
    import search as engine
    import webui

    engine.DB = os.path.join(HOME, "index", "lance")
    engine.OLLAMA = EMBED
    assert engine.EMBED_OPTIONS == {"num_gpu": 0}, "вектор запроса должен считаться на процессоре"
    webui.CORPUS = os.path.join(HOME, "corpus")
    webui.PORT = SEARCH_PORT                         # от порта зависят ссылки url в ответах поиска
    auth.LOGIN_TTL = 12 * 3600
    webui.Handler.guard = auth.Guard("search")
    webui.Handler.guard.startup_check()
    server = webui.Server(("127.0.0.1", webui.PORT), webui.Handler)
    print(f"слушаю http://127.0.0.1:{webui.PORT}; индекс {engine.DB}; векторы {engine.OLLAMA}", flush=True)
    server.serve_forever()


def office():
    import auth
    import office_server as O

    O.BASE = HOME                                    # от него считаются входящая папка для submit_document и каталог настроек
    O.CORPUS = os.path.join(HOME, "corpus")
    O.OUT = os.path.join(DIR, "out")
    O.PORT = OFFICE_PORT
    O.Handler.guard = auth.Guard("office")
    O.Handler.guard.startup_check()
    os.makedirs(O.OUT, exist_ok=True)
    O.sweep()
    server = O.Server(("127.0.0.1", O.PORT), O.Handler)
    print(f"слушаю http://127.0.0.1:{O.PORT}; корпус {O.CORPUS}", flush=True)
    server.serve_forever()


def mcp():
    import auth
    import mcp_server as M

    search_url, office_url = f"http://127.0.0.1:{SEARCH_PORT}", f"http://127.0.0.1:{OFFICE_PORT}"
    for tool in M.TOOLS:                              # адреса зашиты в таблице инструментов: живые заменяются адресами стенда
        method, url = tool["route"]
        tool["route"] = (method, url.replace(M.SEARCH, search_url).replace(M.OFFICE, office_url))
    assert all(t["route"][1].startswith((search_url, office_url)) for t in M.TOOLS), "в таблице остался чужой адрес"
    M.SEARCH, M.OFFICE, M.PORT = search_url, office_url, MCP_PORT
    M.Handler.guard = auth.Guard("mcp")
    M.Handler.guard.remote_full = True                # как в боевом запуске переходника
    M.Handler.guard.startup_check()
    server = M.Server(("127.0.0.1", M.PORT), M.Handler)
    print(f"слушаю http://127.0.0.1:{M.PORT}/mcp, инструментов {len(M.TOOLS)}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    {"search": search, "office": office, "mcp": mcp}[sys.argv[1]]()
