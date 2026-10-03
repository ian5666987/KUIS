import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from main.models import ErrorTaxonomyNode

ERROR_XML_PATH = settings.BASE_DIR / 'info' / 'error.xml'


class Command(BaseCommand):
    help = (
        "Loads the error-type taxonomy from info/error.xml into "
        "ErrorTaxonomyNode. Idempotent and safe to run repeatedly (like "
        "seed.py) — replaces the whole table each run rather than being "
        "baked into migration history, since taxonomy content (glosses, "
        "new codes) may get corrected/extended independently of the "
        "schema. See docs/error-analytics-plan.md."
    )

    def handle(self, *args, **options):
        try:
            tree = ET.parse(ERROR_XML_PATH)
        except (OSError, ET.ParseError) as exc:
            raise CommandError(f"Couldn't read/parse {ERROR_XML_PATH}: {exc}")
        root_el = tree.getroot()

        root_name_el = root_el.find('./ROOT/FEATURE/NAME')
        if root_name_el is None or not (root_name_el.text or '').strip():
            raise CommandError(f"{ERROR_XML_PATH} has no ROOT/FEATURE/NAME — can't determine the taxonomy root.")
        root_code = root_name_el.text.strip()

        # EC (a FEATURE's own code) -> its direct children, gathered from
        # every <SYSTEM>. Built as a lookup so the tree can be walked from
        # the root regardless of what order SYSTEMS appear in the file (in
        # practice parent-before-child, but not relied on here).
        children_by_ec = {}
        for system in root_el.findall('./SYSTEMS/SYSTEM'):
            ec = (system.findtext('EC') or '').strip()
            for feature in system.findall('./FEATURES/FEATURE'):
                name = (feature.findtext('NAME') or '').strip()
                if not name:
                    continue
                gloss = (feature.findtext('GLOSS') or '').strip()
                children_by_ec.setdefault(ec, []).append((name, gloss))

        # A node is a leaf iff its code never appears as another SYSTEM's
        # EC — i.e. it has no FEATURES list of its own children.
        non_leaf_codes = set(children_by_ec.keys())

        # rows: (code, parent_code, path, gloss, is_leaf, top_category).
        # `path` is the exact semicolon-joined format the corpus XML's
        # `features=` attribute uses. `top_category` is the depth-1
        # ancestor's code, computed once here for every descendant
        # (including itself, for a depth-1 node) rather than re-derived
        # from `path` at query time.
        rows = []
        seen_codes = set()

        def visit(code, parent_code, path, gloss, top_category):
            if code in seen_codes:
                raise CommandError(
                    f"{ERROR_XML_PATH}: code '{code}' appears more than once in the taxonomy "
                    f"— every FEATURE code must be globally unique."
                )
            seen_codes.add(code)
            rows.append((code, parent_code, path, gloss, code not in non_leaf_codes, top_category))

            for child_name, child_gloss in children_by_ec.get(code, []):
                # parent_code is None only for the call visiting the root
                # itself — its direct children are depth 1, i.e. each
                # becomes its own top_category.
                child_top_category = child_name if parent_code is None else top_category
                visit(child_name, code, f"{path};{child_name}", child_gloss, child_top_category)

        visit(root_code, None, root_code, '', None)

        with transaction.atomic():
            ErrorTaxonomyNode.objects.all().delete()

            nodes_by_code = {
                code: ErrorTaxonomyNode.objects.create(
                    code=code, path=path, gloss=gloss, is_leaf=is_leaf, top_category=top_category,
                )
                for code, parent_code, path, gloss, is_leaf, top_category in rows
            }
            for code, parent_code, *_ in rows:
                if parent_code is not None:
                    node = nodes_by_code[code]
                    node.parent = nodes_by_code[parent_code]
                    node.save(update_fields=['parent'])

        leaf_count = sum(1 for *_, is_leaf, _ in rows if is_leaf)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(rows)} taxonomy nodes ({leaf_count} leaf error codes) from "
            f"{ERROR_XML_PATH.relative_to(settings.BASE_DIR)}."
        ))
