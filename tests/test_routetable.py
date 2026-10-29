"""선언형 라우트 표 매크로 (Meilisearch 형태) · 매니페스트 기반 프레임워크 감지 · 테스트 데이터 분류."""
from pathlib import Path

from oss_analyzer.adapters.astgrep import detect
from oss_analyzer.core import routetable
from oss_analyzer.core.classify import classify

ROOT = '''#[routes::routes(
    routes(
        "/health" => get(get_health),
        // "/old" => get(old),
        "/indexes" => sub(indexes::IndexesApi),
    ),
    tag = "Root mod impl fn",
)]
'''
INDEXES = '''#[routes::routes(
    routes(
        "" => [get(list_indexes), post(create_index)],
        "/{index_uid}/settings" => sub(settings::SettingsApi),
    ),
)]
'''
MACRO = '''macro_rules! make_routes {
    ($({route: $route:literal, verb: $verb:ident, attr: $attr:ident},)*) => {
        #[routes::routes(
            routes(
                "" => [get(get_all)],
                $( $route => [get($attr::get), $verb($attr::update)]),*
            ),
        )]
        pub struct SettingsApi;
    };
}
make_routes!(
    {route: "/synonyms", verb: put, attr: synonyms},
    {route: "/faceting", verb: patch, attr: faceting},
);
'''


def test_route_tables_compose_and_expand_macro_repetition():
    root = routetable.parse_table("mod.rs", ROOT, 1, following="pub struct MeiliApi;")
    idx = routetable.parse_table("indexes.rs", INDEXES, 1, following="\npub struct IndexesApi;")
    values = routetable.macro_invocation_values({"settings.rs": MACRO.splitlines()}, "make_routes")
    settings = routetable.parse_table("settings.rs", MACRO, 1, values)
    assert (root.owner, idx.owner, settings.owner) == ("MeiliApi", "IndexesApi", "SettingsApi")
    got = {(e.method, full) for e, full, _ in routetable.compose([root, idx, settings]) if e.kind == "http"}
    assert got == {
        ("GET", "/health"), ("GET", "/indexes"), ("POST", "/indexes"),
        ("GET", "/indexes/{index_uid}/settings"),
        ("GET", "/indexes/{index_uid}/settings/synonyms"), ("PUT", "/indexes/{index_uid}/settings/synonyms"),
        ("GET", "/indexes/{index_uid}/settings/faceting"), ("PATCH", "/indexes/{index_uid}/settings/faceting"),
    }
    synonyms = next(e for e, full, _ in routetable.compose([root, idx, settings]) if full.endswith("synonyms") and e.method == "PUT")
    assert synonyms.handler == "synonyms::update" and synonyms.line == 13  # 호출부의 route: 줄


def test_detect_ignores_dev_dependencies(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "Cargo.toml").write_text('[dependencies]\naxum = "0.7"\n[dev-dependencies]\nrocket = "0.5"\n', encoding="utf-8")
    files = ["a/Cargo.toml"]
    assert detect({"manifests": ["Cargo.toml"], "contains": ["axum"]}, tmp_path, files)
    assert not detect({"manifests": ["Cargo.toml"], "contains": ["rocket"]}, tmp_path, files)


def test_test_data_and_license_classification():
    assert classify("crates/x/src/snapshots/a.snap")[0] == "test-data"
    assert classify("fuzz/corpus/parse/test_1")[0] == "test-data"
    assert classify("tests/assets/data.ndjson")[0] == "test-data"
    assert classify("LICENSE-MIT")[0] == "doc"
    assert classify("tests/snapshots/helper.rs")[0] == "test"  # 코드는 테스트로
