#!/usr/bin/env python3
"""Focused regressions for company-date and shared-contact namesake safety."""
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parent
LOADER = importlib.machinery.SourceFileLoader("screening_run", str(ROOT / "run"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
screening = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(screening)


class SafetyRegressionTests(unittest.TestCase):
    def test_company_dob_is_not_incorporation_date(self):
        company = {"record_id": "CUS-CO", "entity_type": "Company", "dob": "1982-07-14"}
        self.assertIsNone(screening.company_incorporation_date([company]))
        company["incorporation_date"] = "2005-04-12"
        self.assertEqual("2005-04-12", screening.company_incorporation_date([company]).iso())

    def test_shared_contact_namesake_stays_a_separate_candidate(self):
        primary = {
            "record_id": "CUS-1", "entity_type": "Individual", "full_name": "Amina Bello",
            "phone": "+44 7700 000001", "address": "1 Example Street, London", "dob": None,
        }
        junior = {
            "record_id": "CUS-2", "entity_type": "Individual", "full_name": "Amina Bello Jr",
            "phone": "+44 7700 000001", "address": "1 Example Street, London", "dob": "2000-01-01",
        }
        bank = SimpleNamespace(
            customers_by_phone={"+447700000001": [primary, junior]},
            cluster_of={"CUS-1": "CUS-1", "CUS-2": "CUS-2"},
            ns=screening.NameSystem([], []),
        )
        self.assertEqual([junior], screening.shared_contact_namesakes([primary], bank))


if __name__ == "__main__":
    unittest.main()
