import json
import re

from django.core.cache import cache
from django.test import TestCase
from django.urls import get_resolver

from core.openapi import build_spec

HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
# Documentation endpoints describe themselves, so they are not in the spec.
UNDOCUMENTED = {"/api/openapi.json", "/api/docs/"}


def _api_routes():
    """Every /api/ route in the URLconf, as an OpenAPI-style path template."""
    routes = set()

    def walk(patterns, prefix=""):
        for p in patterns:
            part = str(p.pattern)
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, prefix + part)
            else:
                template = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", prefix + part)
                routes.add("/" + template)

    walk(get_resolver().url_patterns)
    return {r for r in routes if r.startswith("/api/")} - UNDOCUMENTED


def _refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


class OpenApiDocsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.spec = build_spec()

    def test_every_route_is_documented_and_vice_versa(self):
        self.assertEqual(set(self.spec["paths"]), _api_routes())

    def test_every_operation_is_complete(self):
        ids = []
        for path, item in self.spec["paths"].items():
            for method, op in item.items():
                if method not in HTTP_METHODS:
                    continue
                where = f"{method.upper()} {path}"
                self.assertTrue(op.get("summary"), where)
                self.assertTrue(op.get("tags"), where)
                self.assertIn("responses", op, where)
                self.assertTrue(any(code.startswith("2") for code in op["responses"]), where)
                ids.append(op["operationId"])
        self.assertEqual(len(ids), len(set(ids)), "operationId values must be unique")

    def test_documented_methods_match_the_views(self):
        """A documented method must not be rejected with 405 by the real endpoint."""
        for path, item in self.spec["paths"].items():
            url = re.sub(
                r"\{(\w+)\}",
                lambda m: "00000000-0000-0000-0000-000000000000" if m.group(1) in ("doc_id", "version_id") else "x",
                path,
            )
            for method in item:
                if method not in HTTP_METHODS:
                    continue
                response = getattr(self.client, method)(url)
                self.assertNotEqual(response.status_code, 405, f"{method.upper()} {path}")
            undocumented = HTTP_METHODS - {m for m in item if m in HTTP_METHODS}
            for method in undocumented & {"get", "post", "put", "delete"}:
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 405, f"{method.upper()} {path} undocumented")

    def test_all_refs_resolve(self):
        for ref in set(_refs(self.spec)):
            node = self.spec
            for part in ref.removeprefix("#/").split("/"):
                self.assertIn(part, node, ref)
                node = node[part]

    def test_secured_endpoints_declare_the_client_id_scheme(self):
        self.assertIn("ClientId", self.spec["components"]["securitySchemes"])
        for path in ("/api/documents/", "/api/documents/{doc_id}/"):
            for method, op in self.spec["paths"][path].items():
                if method in HTTP_METHODS:
                    self.assertEqual(
                        op["security"], [{"ClientId": []}, {"BearerAuth": []}], f"{method} {path}"
                    )
        for op in self.spec["paths"]["/api/shared/{token}/"].values():
            self.assertNotIn("security", op)

    def test_served_endpoints(self):
        r = self.client.get("/api/openapi.json")
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(data["openapi"], "3.0.3")
        self.assertEqual(data["servers"][0]["url"], "http://testserver")
        ui = self.client.get("/api/docs/")
        self.assertEqual(ui.status_code, 200)
        self.assertIn("swagger-ui-bundle.js", ui.content.decode())
        self.assertIn("/api/openapi.json", ui.content.decode())

    def test_documented_examples_match_real_responses(self):
        from proofing import services

        ex = self.spec["paths"]["/api/proofing/spellcheck/"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["example"]
        self.assertEqual(ex["issues"], services.find_spelling_issues("I will Recieve it teh day."))
        rd = self.spec["components"]["schemas"]["Readability"]["properties"]
        real = services.readability("The cat sat. The dog ran.")
        self.assertEqual(rd["fleschReadingEase"]["example"], real["fleschReadingEase"])
        self.assertEqual(rd["fleschKincaidGrade"]["example"], real["fleschKincaidGrade"])
