"""Integration test for PostgresTaxonomyRepository, run against a real
Postgres database. seeded_corpus loads the real info/error.xml taxonomy
(call_command("load_error_taxonomy", ...) in conftest.py) — this exercises
tree assembly against the actual production data, not a synthetic fixture.
"""

from dataplane.repositories.postgres.taxonomy_repository import PostgresTaxonomyRepository


async def test_tree_starts_one_level_below_the_root(seeded_corpus, session):
    repo = PostgresTaxonomyRepository(session)
    tree = await repo.tree()

    codes = {node.code for node in tree}
    assert codes == {"leksikal", "gramatikal", "ejaan", "lainnya"}  # not "eror" itself


async def test_tree_nests_correctly_down_to_leaves(seeded_corpus, session):
    repo = PostgresTaxonomyRepository(session)
    tree = await repo.tree()

    gramatikal = next(node for node in tree if node.code == "gramatikal")
    assert not gramatikal.is_leaf

    frasa_nomina = next(child for child in gramatikal.children if child.code == "frasa-nomina")
    assert not frasa_nomina.is_leaf

    urtfn = next(child for child in frasa_nomina.children if child.code == "urtfn")
    assert urtfn.is_leaf
    assert urtfn.path == "eror;gramatikal;frasa-nomina;urtfn"
    assert urtfn.gloss  # every real leaf carries a non-empty gloss

    ejaan = next(node for node in tree if node.code == "ejaan")
    ejk = next(child for child in ejaan.children if child.code == "ejk")
    assert ejk.is_leaf
    assert ejk.path == "eror;ejaan;ejk"  # no subcategory level for ejaan, unlike gramatikal
