"""Generated draft forms expose every property of their record schema."""
from pathlib import Path
import json
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_examples


class GenerateDraftCompletenessTests(unittest.TestCase):
    def test_every_generated_draft_has_exact_schema_object_keys(self):
        schemas = generate_examples.catalog(include_ticket_plan=False)

        def check(value, schema, document, location):
            if "$ref" in schema:
                address, _, fragment = schema["$ref"].partition("#")
                if address:
                    document = schemas[address.rsplit(":", 1)[-1]]
                target = document
                for token in fragment.removeprefix("/").split("/") if fragment else ():
                    target = target[token.replace("~1", "/").replace("~0", "~")]
                return check(value, target, document, location)
            if "oneOf" in schema:
                if value is None:
                    return
                return check(value, schema["oneOf"][0], document, location)
            if schema.get("type") == "object":
                properties = schema.get("properties", {})
                with self.subTest(location=location):
                    self.assertEqual(set(properties), set(value))
                for key in properties.keys() & value.keys():
                    check(value[key], properties[key], document, f"{location}.{key}")
            elif schema.get("type") == "array":
                for index, item in enumerate(value):
                    check(item, schema["items"], document, f"{location}[{index}]")

        with tempfile.TemporaryDirectory(prefix="awf-draft-completeness-") as raw:
            destination = Path(raw)
            generate_examples.generate(destination)
            drafts = []
            for path in (destination / ".agentic/templates").iterdir():
                if path.is_file():
                    draft = json.loads(path.read_text(encoding="utf-8"))
                    if "template_for" in draft:
                        drafts.append(draft)
            self.assertTrue(drafts, "generator produced no draft forms")
            self.assertIn("publication-scan", {draft["template_for"] for draft in drafts})
            for draft in drafts:
                name = draft["template_for"]
                check(draft["record"], schemas[name], schemas[name], name)


if __name__ == "__main__":
    unittest.main()
