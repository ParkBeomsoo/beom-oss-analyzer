"""동적 경로 해석 · Next.js 파일 라우팅 · 비밀값 가리기 · 단위 묶기."""
import textwrap
from pathlib import Path

from oss_analyzer.core.mask import mask_line
from oss_analyzer.core.nextjs import scan as next_scan
from oss_analyzer.core.resolve import Resolver, Scope, normalize_path, split_top, strip_comments
from oss_analyzer.core.units import build_units

ROUTES_RS = textwrap.dedent('''\
    fn add_routes(router: Router, path_prefix: &str, routes: Vec<(&str, MethodRouter)>) -> Router {
        let mut r = router;
        for (path, handler) in routes {
            r = r.route(&format!("{}{}", path_prefix, path), handler);
        }
        r
    }

    pub fn api_routes() -> Router {
        let cfg = config::load();
        let prefix = cfg.api_path;
        let mut router = Router::new();
        router = add_routes(router, &prefix, vec![
            ("/a", get(a)),
            // ("/old", get(old)),
            (
                "/b",
                post(b),
            ),
        ]);
        router = add_routes(router, "/other", vec![("/c", get(c))]);
        router
    }
''')
CONFIG_RS = textwrap.dedent('''\
    pub fn load() -> Config {
        let base = get_str_value(&env, "base_path", "/api");
        let api_path = format!("{}{}", base, get_str_value(&env, "api_path", "/v1"));
        Config { api_path }
    }
''')


def _resolver(tmp_path: Path) -> Resolver:
    (tmp_path / "routes.rs").write_text(ROUTES_RS, encoding="utf-8")
    (tmp_path / "config.rs").write_text(CONFIG_RS, encoding="utf-8")
    functions = {
        "routes.rs": [{"name": "add_routes", "start_line": 1, "end_line": 7}, {"name": "api_routes", "start_line": 9, "end_line": 23}],
        "config.rs": [{"name": "load", "start_line": 1, "end_line": 5}],
    }

    def callers(path, name):
        # 구조 지도처럼 호출자 함수당 간선 1개만 준다 → 해석기가 본문을 훑어 호출부 2개를 모두 찾아야 함
        return [("routes.rs", 13, {"path": "routes.rs", "start_line": 13, "end_line": 13})] if name == "add_routes" else []

    return Resolver(tmp_path, "rust", functions, callers, ["routes.rs", "config.rs"])


def test_resolves_table_driven_routes_with_call_site_pairing(tmp_path):
    res = _resolver(tmp_path)
    vals = res.resolve('&format!("{}{}", path_prefix, path)', Scope("routes.rs", 4, 1))
    got = sorted((v.text, v.extra["siblings"][0]) for v in vals)
    # 호출부별로 접두와 경로가 짝지어져야 한다 (/other + /a 같은 교차 조합 금지), 주석 처리된 /old 는 제외
    assert got == [("/api/v1/a", "get(a)"), ("/api/v1/b", "post(b)"), ("/other/c", "get(c)")]
    api = next(v for v in vals if v.text == "/api/v1/a")
    assert any("기본값" in a for a in api.assumptions)
    b = next(v for v in vals if v.text == "/api/v1/b")
    assert b.extra["item_loc"]["start_line"] == 17  # 경로 리터럴이 있는 줄


def test_text_helpers():
    assert split_top('a, (b, c), "d,e"') == ["a", "(b, c)", '"d,e"']
    assert strip_comments('x // c\ny /* z */ "//keep"') == 'x \ny  "//keep"'
    assert normalize_path("/v1/common//doSeparate/") == "/v1/common/doSeparate"


def test_nextjs_routing(tmp_path):
    files = [
        "package.json", "app/page.tsx", "app/(shop)/cart/page.tsx", "app/blog/[slug]/page.tsx",
        "app/dashboard/@team/page.tsx", "app/api/items/route.ts", "app/_private/x/page.tsx",
        "src/app/page.tsx", "src/middleware.ts", "app/actions.ts",
    ]
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"dependencies": {"next": "14"}}', encoding="utf-8")
    (tmp_path / "app/api/items/route.ts").write_text("export async function GET() {}\nexport const POST = async () => {}\n", encoding="utf-8")
    (tmp_path / "app/actions.ts").write_text("'use server'\nexport async function save() {}\n", encoding="utf-8")
    out = next_scan(tmp_path, files)
    feats = {(f["kind"], f["name"], f["inactive"]) for f in out["features"]}
    assert ("page", "/", False) in feats
    assert ("page", "/cart", False) in feats          # 라우트 그룹 제거
    assert ("page", "/blog/[slug]", False) in feats
    assert ("page", "/dashboard", False) in feats     # 병렬 슬롯은 경로에서 빠짐
    assert ("http", "/api/items", False) in feats
    assert {f["method"] for f in out["features"] if f["kind"] == "http"} == {"GET", "POST"}
    assert ("page", "/", True) in feats               # src/app 은 비활성
    assert ("middleware", "*", True) in feats         # 루트 app 인데 src 미들웨어 → 비활성
    assert ("server-action", "save", False) in feats
    assert not any("_private" in f["path"] for f in out["features"])
    assert any("src/app" in u["question"] for u in out["unknowns"])


def test_mask_line():
    assert mask_line("spring.datasource.password=S3cret!x")[0] == "spring.datasource.password=****"
    assert mask_line("redis://u:pw1234@h:6379")[0] == "redis://u:****@h:6379"
    assert mask_line('api_key = "${API_KEY}"')[1] is False
    assert mask_line("timeout=30")[1] is False


def test_small_sibling_dirs_are_packed():
    files = [{"path": f"pkg/{d}/F{i}.java", "category": "code", "lines": 50} for d in ("dto", "service", "mapper") for i in range(2)]
    units = build_units(files, {}, 1500)
    assert len(units) == 1 and len(units[0]["members"]) == 6
