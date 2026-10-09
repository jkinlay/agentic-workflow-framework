import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import build_release  # noqa: E402
from agentic import ValidationError  # noqa: E402
from agentic.canonical import sha256  # noqa: E402
from agentic.installer import (INSTALL_OWNED_PATHS, SOURCE_CONFIG_PATHS,  # noqa: E402
                               SOURCE_CONFIG_PREFIX)


class FakeTree:
    def __init__(self):
        self.data = {
            source: ("template:" + target).encode("utf-8")
            for target, source in build_release.SOURCE_CONFIGS.items()
        }
        self.data.update({
            "README.md": b"release\n",
            ".agentic/installed-manifest.json": b"receipt\n",
            ".agentic-state/operating/changes/change.json": b"state\n",
        })

    def file_list(self, **kwargs):
        self.kwargs = kwargs
        return sorted(self.data)

    def inspect(self, path):
        return object() if path in self.data else None

    def read(self, path):
        return self.data[path]


class AdoptableReleaseBuildTests(unittest.TestCase):
    def test_source_mapping_matches_authoritative_installer_configuration(self):
        self.assertEqual(set(SOURCE_CONFIG_PATHS), set(build_release.SOURCE_CONFIGS))
        self.assertTrue(set(SOURCE_CONFIG_PATHS) < set(INSTALL_OWNED_PATHS))
        self.assertTrue(all(source.startswith(SOURCE_CONFIG_PREFIX)
                            for source in build_release.SOURCE_CONFIGS.values()))

    def test_logical_release_substitutes_templates_and_excludes_install_state(self):
        tree = FakeTree()
        paths = build_release.release_paths(tree)
        self.assertIn("README.md", paths)
        self.assertNotIn(".agentic/installed-manifest.json", paths)
        self.assertNotIn(".agentic-state/operating/changes/change.json", paths)
        for target, source in build_release.SOURCE_CONFIGS.items():
            self.assertIn(target, paths)
            self.assertNotIn(source, paths)
            self.assertEqual(tree.data[source], build_release.release_bytes(tree, target))

    def test_virtual_release_rejects_case_colliding_paths(self):
        tree = FakeTree()
        tree.data["Readme.md"] = b"ambiguous release member\n"
        files = {
            path: sha256(build_release.release_bytes(tree, path))
            for path in build_release.release_paths(tree)
            if path not in {"MANIFEST.json", "MANIFEST.md"}
        }
        raw = json.dumps({
            "format": "awf-manifest-1",
            "template_version": "1.9.4",
            "files": files,
        }).encode("utf-8")
        with self.assertRaisesRegex(ValidationError, "Case-colliding paths"):
            build_release.verify_virtual_release(tree, raw)


if __name__ == "__main__":
    unittest.main()
