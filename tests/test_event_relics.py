from types import SimpleNamespace

from tree_sitter import Language, Parser
import tree_sitter_c_sharp

from local_game_data import Dataset
from local_game_data.csharp import ExtractionError, children, text
from local_game_data.event_relics import option_relic_classes
from local_game_data.wiki import records, export_all
import json


DARV_RELICS = {"DUSTY_TOME", "RUNIC_PYRAMID", "BLACK_STAR", "EMPTY_CAGE", "ECTOPLASM",
               "PANDORAS_BOX", "VELVET_CHOKER", "SOZU", "PHILOSOPHERS_STONE", "ASTROLABE",
               "SNECKO_EYE", "CALLING_BELL"}


def test_indirect_option_pool_is_followed_but_unrelated_relics_are_not():
    source = b'''public class Example {
        private static object _pool = new[] { ModelDb.Relic<First>(), ModelDb.Relic<Second>() };
        private object Pool => _pool;
        public object AllPossibleOptions => Pool.Select(r => RelicOption(r.ToMutable()));
        private object Unused => ModelDb.Relic<MustNotBeAttributed>();
    }'''
    parser = Parser(Language(tree_sitter_c_sharp.language()))
    declaration = next(n for n in parser.parse(source).root_node.named_children if n.type == "class_declaration")
    members = {}
    for node in declaration.child_by_field_name("body").named_children:
        if node.type == "property_declaration":
            members[text(node.child_by_field_name("name"))] = [node]
        if node.type == "field_declaration":
            for group in children(node, "variable_declaration"):
                for var in children(group, "variable_declarator"):
                    members[text(var.child_by_field_name("name"))] = [var]
    model = SimpleNamespace(name="Example", key="Example", node=declaration, members=members)
    class Index:
        def find(self, owner, name):
            if name not in members: raise ExtractionError(name)
            return model, members[name]
    relics, sources = option_relic_classes(Index(), model)
    assert relics == {"First", "Second"}
    assert any(s["member"] == "Example._pool" for s in sources)


def test_darv_lists_all_twelve_relics(extracted):
    dataset = Dataset(extracted / "dataset.json")
    for route in ("events", "ancients"):
        event = next(r for r in dataset.get(route) if r["id"] == "DARV")
        assert set(event["relics"]) == DARV_RELICS
        assert event["name"] == "达弗"
        assert any(source["member"].endswith("._validRelicSets") for source in event["_relic_option_sources"])


def test_all_darv_relics_are_attributed_without_wiki_baseline(extracted, tmp_path):
    export_all(extracted / "dataset.json", tmp_path / "out", tmp_path / "empty-baseline")
    rows = records(json.loads((tmp_path / "out/relics_0.113.1.json").read_text(encoding="utf-8")))
    for relic in DARV_RELICS:
        assert rows[relic.lower()]["ancient"] == "达弗"
    assert rows["archaic_tooth"]["ancient"] == "欧洛巴斯"
    assert rows["touch_of_orobas"]["ancient"] == "欧洛巴斯"
    for relic in ("dream_catcher", "the_boot", "mr_struggles"):
        assert rows[relic]["ancient"] is None
