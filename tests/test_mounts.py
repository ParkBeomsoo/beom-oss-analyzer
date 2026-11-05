"""라우터 객체·마운트 조합 (FastAPI include_router / Express app.use + require / Gin Group)."""
from pathlib import Path

from oss_analyzer.core import mounts
from oss_analyzer.core.resolve import Resolver


def _mr(tmp_path: Path, files: dict[str, str]) -> mounts.MountResolver:
    for p, t in files.items():
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(t, encoding="utf-8")
    res = Resolver(tmp_path, "", {}, lambda *a: [], list(files))
    return mounts.build(res, files)


def test_fastapi_prefix_chain_with_constant(tmp_path):
    mr = _mr(tmp_path, {
        "app/main.py": 'from fastapi import FastAPI\nfrom app.routers import orders\nAPI = "/api/v1"\napp = FastAPI()\n'
                       'app.include_router(orders.router, prefix=API + "/shop")\n',
        "app/routers/orders.py": 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/orders")\n',
    })
    assert mr.prefixes("app/routers/orders.py", "router")[0] == ["/api/v1/shop/orders"]
    assert mr.prefixes("app/main.py", "app")[0] == [""]


def test_express_use_with_middleware_and_require(tmp_path):
    mr = _mr(tmp_path, {
        "src/app.js": 'const express = require("express");\nconst api = require("./routes");\nconst app = express();\n'
                      'app.use("/api", auth, api);\n',
        "src/routes/index.js": 'const r = express.Router();\nconst users = require("./users");\nr.use("/v2", users);\nmodule.exports = r;\n',
        "src/routes/users.js": 'const router = express.Router();\nmodule.exports = router;\n',
    })
    assert mr.prefixes("src/routes/users.js", "router")[0] == ["/api/v2"]


def test_gin_nested_groups(tmp_path):
    mr = _mr(tmp_path, {"main.go": 'r := gin.Default()\nv1 := r.Group("/api/v1")\nitems := v1.Group("/items")\n'})
    assert mr.prefixes("main.go", "items")[0] == ["/api/v1/items"]


def test_unmounted_router_is_flagged(tmp_path):
    mr = _mr(tmp_path, {"x.py": 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/x")\n'})
    prefixes, notes = mr.prefixes("x.py", "router")
    assert prefixes == ["/x"] and any("마운트되지 않아" in n for n in notes)
