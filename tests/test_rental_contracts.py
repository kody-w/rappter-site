"""Public rental boundary checks; stdlib only, no network or payment operations."""

import copy
import hashlib
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SITE = "https://rappter.com/"
PLAN_URL = SITE + "paid-rental-plan.json"
SCHEMA_URL = SITE + "paid-rental-plan.schema.json"
PROTOCOL_URL = "https://github.com/kody-w/rapp-1/blob/main/SPEC.md"
SCHEMA_ID = "ai-contractor-paid-rental-plan/1"
STATUS = "planned-disabled"

# Public beta at 01815d0; rentals.json also matched the live bytes on 2026-09-07.
BETA_HASHES = {
    "rentals.json": "85c0f83b46384815ec8a88e1e393504768b941fb041b31b2c2f892b133a05cde",
    "beta-terms.html": "5054421a725d95045be2d477d5e8f79054acfd561b9b8ef87e97f427578aa2f6",
    "safety.html": "bacf8d7f922de7abb8e86a079df91a18794ee0da59f7dbfd4e83b8ab41e0b348",
    "privacy.html": "2ce45da9e6f72bba794af5578e818d5fcf09721ea0936c902314623c060ec43a",
    "install.json": "fea7e72fbc35693c0bc24f8ad2cafe0ced7f423e30dad61322a391c0e6f52eea",
}
INGRESS_HASHES = {
    r"<form\b.*?</form>": "799f444f5de76c693041b094c0af4846e8181ba459f4965fcba6c4a572f5443b",
    r"<script>.*?</script>": "1ae6411d5e417741e00571cf43c2902c78c1ebc84ef3ef36ce2ac16d1823c2d5",
    r'<script type="application/ld\+json">.*?</script>': "47649b99f6f1664ef0eb87b83089f4583d88e723816844b82b2bca7e463978b6",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"),
                      object_pairs_hook=unique_object)


def walk(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (index,))


def at_path(value, path):
    for key in path:
        value = value[key]
    return value


def urls(value):
    return {item for _, item in walk(value)
            if isinstance(item, str) and item.startswith("https://")}


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = set()
        self.forms = []
        self.plan_attributes = {}
        self.plan_text = []
        self.in_plan = False
        self.feed(text)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag in ("a", "link") and "href" in attrs:
            self.links.add(attrs["href"])
        if tag == "form":
            self.forms.append(attrs)
        if tag == "section" and attrs.get("id") == "paid-rental-plan":
            self.plan_attributes = attrs
            self.in_plan = True

    def handle_endtag(self, tag):
        if tag == "section":
            self.in_plan = False

    def handle_data(self, data):
        if self.in_plan:
            self.plan_text.append(data)


class RentalContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = load_json("paid-rental-plan.json")
        cls.schema = load_json("paid-rental-plan.schema.json")
        cls.beta = load_json("rentals.json")
        cls.agent = load_json("agent.json")
        cls.rent = (ROOT / "rent.html").read_text(encoding="utf-8")

    def assert_schema(self, value, schema):
        """Check only the schema vocabulary published here; refuse unknown keywords."""
        self.assertLessEqual(set(schema), {
            "$schema", "$id", "title", "description", "type", "const",
            "required", "properties", "additionalProperties", "items",
        })
        types = {"object": dict, "array": list, "string": str,
                 "integer": int, "boolean": bool}
        if schema["type"] == "integer":
            self.assertTrue(type(value) is int or
                            (type(value) is float and value.is_integer()))
        else:
            self.assertIs(type(value), types[schema["type"]])
        if "const" in schema:
            self.assertEqual(value, schema["const"])
        if schema["type"] == "object":
            self.assertIs(schema["additionalProperties"], False)
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
            self.assertEqual(len(schema["required"]), len(schema["properties"]))
            self.assertEqual(set(value), set(schema["properties"]))
            for key, child_schema in schema["properties"].items():
                self.assert_schema(value[key], child_schema)
        elif schema["type"] == "array":
            for child in value:
                self.assert_schema(child, schema["items"])

    def assert_local_artifact(self, url):
        parsed = urlsplit(urljoin(SITE + "rent", url))
        if parsed.geturl() == PROTOCOL_URL:
            return  # Public SPEC.md verified separately; CI is deliberately offline.
        self.assertEqual((parsed.scheme, parsed.netloc), ("https", "rappter.com"))
        self.assertFalse(parsed.query)
        path = ROOT / unquote(parsed.path).lstrip("/")
        self.assertTrue(path.resolve().is_relative_to(ROOT))
        self.assertFalse(path.is_symlink())
        if path.is_dir():
            path = path / "index.html"
        elif not path.suffix:
            path = path.with_suffix(".html")
        self.assertTrue(path.is_file(), f"missing public artifact: {url}")
        if parsed.fragment:
            self.assertIn(parsed.fragment, Page(path.read_text(encoding="utf-8")).ids)

    def test_exact_schema_and_closed_shape(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["$id"], SCHEMA_URL)
        self.assertEqual(self.plan["schema"], SCHEMA_ID)
        self.assertEqual(self.plan["schema_url"], SCHEMA_URL)
        self.assert_schema(self.plan, self.schema)
        numeric_equivalent = copy.deepcopy(self.plan)
        numeric_equivalent["terms"]["deposit_minor"] = 500.0
        self.assert_schema(numeric_equivalent, self.schema)

    def test_schema_rejects_missing_unknown_and_wrong_type_fields(self):
        for path, value in walk(self.plan):
            with self.subTest(path=path, mutation="wrong type"):
                candidate = copy.deepcopy(self.plan)
                if path:
                    at_path(candidate, path[:-1])[path[-1]] = None
                else:
                    candidate = None
                with self.assertRaises(AssertionError):
                    self.assert_schema(candidate, self.schema)
            if isinstance(value, dict):
                for key in value:
                    with self.subTest(path=path, missing=key):
                        candidate = copy.deepcopy(self.plan)
                        del at_path(candidate, path)[key]
                        with self.assertRaises(AssertionError):
                            self.assert_schema(candidate, self.schema)
                for key in ("checkout_url", "charge_endpoint", "provider_token", "receipt"):
                    with self.subTest(path=path, forbidden=key):
                        candidate = copy.deepcopy(self.plan)
                        at_path(candidate, path)[key] = "not-published"
                        with self.assertRaises(AssertionError):
                            self.assert_schema(candidate, self.schema)

    def test_schema_rejects_enabled_or_reinterpreted_terms(self):
        mutations = [
            (("schema",), "ai-contractor-public-offer/1"),
            (("status",), "active"),
            (("paid_ready",), True), (("paid_ready",), 0),
            (("paid_ready",), "false"),
            (("terms", "currency"), "rpp"),
            (("terms", "deposit_minor"), 5),
            (("terms", "deposit_minor"), "500"),
            (("terms", "deposit_minor"), 500.5),
            (("terms", "deposit_minor"), True),
            (("terms", "automatic_collection"), True),
            (("terms", "review_timeout_is_satisfaction"), True),
            (("terms", "review_timeout_authorizes_collection"), True),
            (("terms", "balance_requires"), ["review-timeout"]),
            (("terms", "paid_completion_requires"), ["payment-authorization"]),
            (("evidence", "history"), "replace-prior-outcome"),
            (("evidence", "capture_is_irreversible"), True),
            (("evidence", "fixed_irreversibility_window_promised"), True),
            (("discovery", "grants_payment_authority"), True),
            (("discovery", "grants_governance_authority"), True),
            (("protocol", "proves_ownership"), True),
            (("protocol", "changes_core_spec"), True),
        ]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                candidate = copy.deepcopy(self.plan)
                at_path(candidate, path[:-1])[path[-1]] = value
                with self.assertRaises(AssertionError):
                    self.assert_schema(candidate, self.schema)

    def test_disabled_readiness_and_no_payment_actions(self):
        self.assertEqual(self.plan["status"], STATUS)
        self.assertIs(self.plan["paid_ready"], False)
        self.assertIs(self.plan["replaces_current_offer"], False)
        self.assertEqual(self.plan["same_product_as"], SITE + "rentals.json")
        self.assertEqual(self.plan["activation_requires"], [
            "tested-production-payment-adapter", "approved-production-release",
        ])
        self.assertEqual(urls(self.plan), {
            SCHEMA_URL, SITE + "rentals.json", SITE + "rent#paid-rental-plan", PROTOCOL_URL,
        })
        for path, _ in walk(self.plan):
            self.assertFalse(set(path) & {
                "act", "apply", "checkout", "charge", "endpoints",
                "checkout_url", "charge_url", "payment_endpoint", "charge_endpoint",
            })

    def test_money_and_explicit_satisfaction_boundaries(self):
        terms = self.plan["terms"]
        self.assertEqual((terms["currency"], terms["amount_unit"]), ("USD", "minor"))
        self.assertIs(type(terms["deposit_minor"]), int)
        self.assertEqual(terms["deposit_minor"], 500)
        self.assertEqual(terms["minor_units_per_major"], 100)
        self.assertEqual(terms["deposit_minor"] // terms["minor_units_per_major"], 5)
        self.assertEqual(terms["quote_basis"], "fixed-customer-accepted-total-including-deposit")
        self.assertEqual(terms["balance_calculation"], "accepted_quote_total_minor - deposit_minor")
        self.assertIn("explicit-customer-deposit-authorization", terms["deposit_requires"])
        self.assertEqual(terms["balance_requires"], [
            "accepted-fixed-quote", "exact-deliverable-digest",
            "explicit-customer-approval-bound-to-quote-and-deliverable",
        ])
        self.assertEqual(terms["paid_completion_requires"], [
            "accepted-fixed-quote", "exact-deliverable-digest",
            "explicit-customer-approval-bound-to-quote-and-deliverable",
            "verified-provider-capture-matching-rental-quote-currency-and-total-paid",
        ])
        for key in ("automatic_collection", "review_timeout_is_satisfaction",
                    "review_timeout_authorizes_collection"):
            self.assertIs(terms[key], False)
        self.assertIs(terms["quote_changes_require_customer_acceptance"], True)

    def test_evidence_and_rights_do_not_invent_authority(self):
        evidence = self.plan["evidence"]
        self.assertEqual(evidence["kind"], "requirements-only-not-transaction-evidence")
        self.assertEqual(evidence["history"], "append-only-outcomes")
        self.assertEqual(evidence["later_outcomes"], ["dispute", "refund", "chargeback"])
        self.assertEqual(evidence["capture_must_match"], ["rental", "accepted-quote", "currency", "amount"])
        self.assertIn("unverified-webhook", evidence["not_capture_evidence"])
        for key in ("capture_is_irreversible", "fixed_irreversibility_window_promised",
                    "transaction_records_published"):
            self.assertIs(evidence[key], False)
        self.assertEqual(self.plan["rights"]["distinct_concepts"], [
            "identity", "rental-entitlement", "asset-title", "paid-deliverable-rights",
        ])
        self.assertIs(self.plan["rights"]["rental_transfers_title"], False)
        self.assertIs(self.plan["rights"]["payment_alone_grants_title_or_deliverable_rights"], False)
        self.assertTrue(all(value is False for value in self.plan["discovery"].values()))
        self.assertEqual(self.plan["protocol"]["reference"], PROTOCOL_URL)
        self.assertEqual(self.plan["protocol"]["scope"], "bytes-and-signatures-only")
        self.assertIs(self.plan["protocol"]["proves_ownership"], False)
        self.assertIs(self.plan["protocol"]["changes_core_spec"], False)

    def test_current_beta_is_byte_identical(self):
        for name, expected in BETA_HASHES.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(self.beta["schema"], "ai-contractor-public-offer/1")
        self.assertEqual(self.beta["status"], "applications-open")
        self.assertEqual((self.beta["price_minor"], self.beta["currency"], self.beta["duration_days"]),
                         (0, "USD", 7))
        for key in ("payment_method_required", "automatic_charge", "debt_created",
                    "title_transferred", "acceptance_automatic", "attachments_accepted"):
            self.assertIs(self.beta[key], False)
        self.assertEqual(self.beta["fulfillment"], "manual-human-approved")
        self.assertEqual(self.beta["data_mode"], "typed-non-confidential-description-to-synthetic-equivalent")

    def test_beta_ingress_and_structured_offer_are_unchanged(self):
        for pattern, expected in INGRESS_HASHES.items():
            matches = re.findall(pattern, self.rent, re.S)
            self.assertEqual(len(matches), 1)
            self.assertEqual(hashlib.sha256(matches[0].encode()).hexdigest(), expected)
        page = Page(self.rent)
        self.assertEqual(len(page.forms), 1)
        self.assertEqual(page.forms[0]["action"], self.beta["apply"]["url"])
        self.assertEqual(page.forms[0]["method"], self.beta["apply"]["method"])
        self.assertIs(self.beta["apply"]["requires_explicit_human_request"], True)
        self.assertEqual(self.beta["apply"]["fields"]["offer_id"], "seven-day-ai-beta")

    def test_agent_mirrors_preserve_beta_and_only_discover_plan(self):
        self.assertEqual((ROOT / "agent.json").read_bytes(),
                         (ROOT / ".well-known/agent.json").read_bytes())
        original = copy.deepcopy(self.agent)
        discovery = original.pop("planned_paid_rental")
        baseline = json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(baseline).hexdigest(),
                         "5388b0bb65e8a114ae94c6a88f948dcc942e3da823523853f4590e436a913493")
        self.assertEqual(discovery, {
            "contract": PLAN_URL, "schema": self.plan["schema"],
            "status": self.plan["status"], "paid_ready": self.plan["paid_ready"],
            "discovery_only": True,
        })
        self.assertIs(discovery["paid_ready"], False)
        self.assertIs(discovery["discovery_only"], True)
        self.assertEqual(self.agent["offer"]["contract"], SITE + "rentals.json")
        self.assertEqual(self.agent["offer"]["act"], {
            key: value for key, value in self.beta["apply"].items() if key != "fields"
        })

    def test_visible_and_machine_documentation_match(self):
        page = Page(self.rent)
        self.assertEqual(page.plan_attributes, {
            "id": "paid-rental-plan", "data-contract-schema": self.plan["schema"],
            "data-status": self.plan["status"], "data-paid-ready": "false",
        })
        text = " ".join("".join(page.plan_text).split())
        for phrase in (
            "PLANNED / DISABLED", "Paid rental plan — not available.",
            "$5 USD (500 minor units)", "current seven-day beta stays $0",
            "explicit customer approval bound to the exact deliverable and quote",
            "verified provider capture", "Silence or a review timeout is not satisfaction",
            "does not authorize collection", "no default automatic collection",
            "Disputes, refunds, and chargebacks append new outcomes",
            "no fixed irreversibility window is promised",
            "tested production payment adapter", "approved production release",
            "discovery grants neither payment nor governance authority",
        ):
            self.assertIn(phrase, text)
        self.assertTrue({
            SITE + "rentals.json", SITE + ".well-known/agent.json", PLAN_URL,
            "/paid-rental-plan.json", "/paid-rental-plan.schema.json",
        }.issubset(page.links))
        llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
        for phrase in (f"Schema: {self.plan['schema']}", f"Status: {self.plan['status']}",
                       "paid_ready: false", "disabled, not an active offer",
                       "$5 USD deposit (500 minor units)", PLAN_URL, SCHEMA_URL,
                       "Silence or review timeout is not", "no default automatic collection"):
            self.assertIn(phrase, llms)

    def test_all_disclosed_public_artifact_references_exist(self):
        references = urls(self.plan) | urls(self.schema) | urls(self.agent) | urls(self.beta)
        references |= Page(self.rent).links
        references |= set(re.findall(r"https://[^\s<>]+", (ROOT / "llms.txt").read_text()))
        references -= {self.schema["$schema"], self.beta["apply"]["url"]}
        for reference in sorted(references):
            with self.subTest(reference=reference):
                self.assert_local_artifact(reference)


if __name__ == "__main__":
    unittest.main()
