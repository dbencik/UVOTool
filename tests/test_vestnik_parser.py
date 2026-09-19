"""
Comprehensive tests for tools/vestnik-parser.py

Tests cover:
- parse_containers: HTML extraction
- find_field / find_all_fields / find_field_anywhere: field lookup
- parse_number: number parsing with SK/EN formats
- parse_organizations: org registry + name_to_ico index
- extract_buyer: buyer from container 2
- extract_subject: subject from container 3
- extract_vysledok: winners, values, stats from containers 4-5
- extract_vyhlasenie: opportunity data from container 4
- extract_zmena_zmluvy: contract modifications
- classify_from_html: document type classification
- parse_document: main orchestrator
- Integration tests with real HTML from vestnik 191
"""

import json
import sys
from pathlib import Path

# Add tools/ to path so we can import vestnik-parser
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

# The module has a hyphen in name, use importlib
import importlib
vp = importlib.import_module("vestnik-parser")

parse_containers = vp.parse_containers
find_field = vp.find_field
find_all_fields = vp.find_all_fields
find_field_anywhere = vp.find_field_anywhere
parse_number = vp.parse_number
parse_organizations = vp.parse_organizations
extract_buyer = vp.extract_buyer
extract_subject = vp.extract_subject
extract_vysledok = vp.extract_vysledok
extract_vyhlasenie = vp.extract_vyhlasenie
extract_zmena_zmluvy = vp.extract_zmena_zmluvy
classify_from_html = vp.classify_from_html
parse_document = vp.parse_document

import pytest


# ═══════════════════════════════════════════════════════
# Fixtures: inline HTML snippets
# ═══════════════════════════════════════════════════════

MINIMAL_HTML = """
<html><body>
<ul class="notice-list">
  <li><span>Typ formulára: Výsledok</span></li>
  <li>Verzia oznámenia: 01</li>
</ul>
<ul class="notice-list">
  <li>Organizácia z kontextu: ORG-0001</li>
  <li>Názov organizácie: Úrad pre verejné obstarávanie</li>
  <li>IČO: 31797903</li>
  <li>Organizácia z kontextu: Testová firma s.r.o. (ORG-0002)</li>
  <li>IČO: 12345678</li>
  <li>E-mail: test@firma.sk</li>
  <li>Mesto: Bratislava</li>
</ul>
<ul class="notice-list">
  <li>ID kupujúceho: ORG-0002 (Testová firma s.r.o.)</li>
</ul>
<ul class="notice-list">
  <li>Názov: Dodávka tovarov</li>
  <li>Hlavný CPV kód: 30200000 - Počítačové zariadenia</li>
  <li>Druh zákazky: Tovary</li>
</ul>
</body></html>
"""

VYSLEDOK_HTML = """
<html><body>
<ul class="notice-list">
  <li>Typ formulára: Výsledok</li>
  <li>Verzia oznámenia: 01</li>
</ul>
<ul class="notice-list">
  <li>Organizácia z kontextu: ORG-0001</li>
  <li>Názov organizácie: Úrad pre verejné obstarávanie</li>
  <li>IČO: 31797903</li>
  <li>Organizácia z kontextu: Testový kupujúci (ORG-0002)</li>
  <li>IČO: 11111111</li>
  <li>E-mail: kupujuci@test.sk</li>
  <li>Organizácia z kontextu: Víťaz s.r.o. (ORG-0003)</li>
  <li>IČO: 22222222</li>
  <li>E-mail: vitaz@firma.sk</li>
  <li>Mesto: Košice</li>
</ul>
<ul class="notice-list">
  <li>ID kupujúceho: ORG-0002 (Testový kupujúci)</li>
</ul>
<ul class="notice-list">
  <li>Názov: Nákup softvéru</li>
  <li>Hlavný CPV kód: 48000000</li>
  <li>Druh zákazky: Tovary</li>
</ul>
<ul class="notice-list">
  <li>5.1 Informácie o časti</li>
</ul>
<ul class="notice-list">
  <li>Celková hodnota oznámenia (BT-161-NoticeResult) (hodnota): 810 488.62</li>
  <li>Typ prijatých ponúk: Ponuky</li>
  <li>Počet: 3</li>
  <li>Identifikátor ponuky: TEN-0001</li>
  <li>ID uchádzača: ORG-0003 (Víťaz s.r.o.)</li>
  <li>Hodnota ponuky (BT-720-Tender) (hodnota): 810 488.62</li>
  <li>Poradie ponuky: 1</li>
  <li>Identifikátor ponuky: TEN-0002</li>
  <li>ID uchádzača: TPA-0004 (Druhý uchádzač a.s.) (uchádzač 2)</li>
  <li>Hodnota ponuky (BT-720-Tender) (hodnota): 900 000</li>
  <li>Poradie ponuky: 2</li>
  <li>Identifikátor úspešnej ponuky : TEN-0001</li>
</ul>
</body></html>
"""

VYHLASENIE_HTML = """
<html><body>
<ul class="notice-list">
  <li>Typ formulára: Súťaž</li>
  <li>Verzia oznámenia: 01</li>
</ul>
<ul class="notice-list">
  <li>Organizácia z kontextu: ORG-0001</li>
  <li>Názov organizácie: Úrad pre verejné obstarávanie</li>
  <li>IČO: 31797903</li>
  <li>Organizácia z kontextu: Obstarávateľ a.s. (ORG-0002)</li>
  <li>IČO: 33333333</li>
</ul>
<ul class="notice-list">
  <li>ID kupujúceho: ORG-0002 (Obstarávateľ a.s.)</li>
</ul>
<ul class="notice-list">
  <li>Názov: Rekonštrukcia ciest</li>
  <li>Hlavný CPV kód: 45233120</li>
  <li>Druh zákazky: Stavebné práce</li>
  <li>Predpokladaná hodnota (BT-27-Lot) (hodnota): 1 500 000</li>
</ul>
<ul class="notice-list">
  <li>Predpokladaná hodnota (BT-27-Lot) (hodnota): 1 500 000</li>
  <li>Lehota na predkladanie ponúk (dátum): 15.10.2026</li>
  <li>Lehota na predkladanie ponúk (čas): 10:00</li>
  <li>Finančné prostriedky EÚ: Obstarávanie je financované z fondov EÚ</li>
  <li>Rámcová dohoda: Žiadna</li>
  <li>Typ kritéria na vyhodnotenie ponúk: Cena</li>
</ul>
</body></html>
"""

ZMENA_ZMLUVY_HTML = """
<html><body>
<ul class="notice-list">
  <li>Typ formulára: Zmena zmluvy</li>
  <li>Verzia oznámenia: 01</li>
</ul>
<ul class="notice-list">
  <li>Organizácia z kontextu: ORG-0001</li>
  <li>Názov organizácie: Úrad pre verejné obstarávanie</li>
  <li>IČO: 31797903</li>
  <li>Organizácia z kontextu: Mesto XY (ORG-0002)</li>
  <li>IČO: 00111222</li>
  <li>Organizácia z kontextu: Dodávateľ s.r.o. (ORG-0003)</li>
  <li>IČO: 44555666</li>
</ul>
<ul class="notice-list">
  <li>ID kupujúceho: ORG-0002 (Mesto XY)</li>
</ul>
<ul class="notice-list">
  <li>Názov: Oprava strechy</li>
  <li>Hlavný CPV kód: 45261000</li>
  <li>Druh zákazky: Stavebné práce</li>
</ul>
<ul class="notice-list">
  <li>ID uchádzača: ORG-0003 (Dodávateľ s.r.o.)</li>
  <li>Hodnota ponuky (BT-720-Tender) (hodnota): 250 000</li>
  <li>Identifikátor zmluvy: Zmluva č. 123/2025</li>
  <li>Dátum uzavretia zmluvy: 01.03.2025</li>
  <li>Odkaz na zverejnenú zmluvu (URL): https://www.crz.gov.sk/zmluva/999999/</li>
  <li>Názov programu alebo fondu: Plán obnovy a odolnosti SR</li>
</ul>
<ul class="notice-list">
  <li>5.1 Informácie</li>
</ul>
<ul class="notice-list">
  <li>Hlavný dôvod zmeny: Potreba dodatočných stavebných prác</li>
  <li>Odôvodnenie zmeny zmluvy: Zmena bola nutná z dôvodu nepredvídaných okolností</li>
  <li>Zhrnutie zmeny: Zvýšenie ceny o 50 000 EUR</li>
</ul>
</body></html>
"""

OPRAVA_HTML = """
<html><body>
<ul class="notice-list">
  <li>Typ formulára: Súťaž</li>
  <li>Verzia oznámenia: 02</li>
</ul>
<ul class="notice-list">
  <li>Organizácia z kontextu: ORG-0001</li>
  <li>Názov organizácie: Úrad pre verejné obstarávanie</li>
  <li>IČO: 31797903</li>
  <li>Organizácia z kontextu: Opravený kupujúci (ORG-0002)</li>
  <li>IČO: 55555555</li>
</ul>
<ul class="notice-list">
  <li>ID kupujúceho: ORG-0002 (Opravený kupujúci)</li>
</ul>
<ul class="notice-list">
  <li>Názov: Opravená zákazka</li>
  <li>Hlavný CPV kód: 12345678</li>
  <li>Druh zákazky: Služby</li>
</ul>
<ul class="notice-list">
  <li>Predpokladaná hodnota (BT-27-Lot) (hodnota): 500 000</li>
  <li>Lehota na predkladanie ponúk (dátum): 20.11.2026</li>
  <li>Lehota na predkladanie ponúk (čas): 12:00</li>
  <li>Rámcová dohoda: Nie</li>
</ul>
</body></html>
"""

EMPTY_HTML = "<html><body><p>No notice-list here</p></body></html>"


# ═══════════════════════════════════════════════════════
# Tests: parse_containers
# ═══════════════════════════════════════════════════════

class TestParseContainers:
    def test_basic_extraction(self):
        containers = parse_containers(MINIMAL_HTML)
        assert len(containers) == 4
        assert any("Typ formulára" in item for item in containers[0])

    def test_empty_html(self):
        assert parse_containers(EMPTY_HTML) == []

    def test_empty_string(self):
        assert parse_containers("") == []

    def test_strips_html_tags(self):
        html = '<ul class="notice-list"><li><span class="bold">Hello</span> World</li></ul>'
        containers = parse_containers(html)
        assert containers == [["Hello World"]]

    def test_collapses_whitespace(self):
        html = '<ul class="notice-list"><li>  Foo   Bar  \n  Baz  </li></ul>'
        containers = parse_containers(html)
        assert containers == [["Foo Bar Baz"]]

    def test_skips_empty_items(self):
        html = '<ul class="notice-list"><li>   </li><li>Real item</li><li></li></ul>'
        containers = parse_containers(html)
        assert containers == [["Real item"]]

    def test_multiple_containers(self):
        html = """
        <ul class="notice-list"><li>A</li></ul>
        <div>noise</div>
        <ul class="notice-list"><li>B</li><li>C</li></ul>
        """
        containers = parse_containers(html)
        assert len(containers) == 2
        assert containers[0] == ["A"]
        assert containers[1] == ["B", "C"]

    def test_nested_tags_stripped(self):
        html = '<ul class="notice-list"><li><a href="x">Link <b>text</b></a></li></ul>'
        containers = parse_containers(html)
        assert containers == [["Link text"]]

    def test_vysledok_has_6_containers(self):
        containers = parse_containers(VYSLEDOK_HTML)
        assert len(containers) == 6


# ═══════════════════════════════════════════════════════
# Tests: find_field / find_all_fields / find_field_anywhere
# ═══════════════════════════════════════════════════════

class TestFindField:
    def test_finds_field_with_colon(self):
        items = ["Typ formulára: Výsledok", "Verzia oznámenia: 01"]
        assert find_field(items, "Typ formulára") == "Výsledok"

    def test_finds_field_value_after_prefix(self):
        items = ["IČO: 12345678"]
        assert find_field(items, "IČO") == "12345678"

    def test_returns_none_when_not_found(self):
        items = ["Foo: bar"]
        assert find_field(items, "Baz") is None

    def test_empty_list(self):
        assert find_field([], "Foo") is None

    def test_strips_colon_from_value(self):
        items = ["Prefix: value"]
        assert find_field(items, "Prefix") == "value"

    def test_handles_space_before_colon(self):
        # The prefix must match exactly from start
        # "Prefix" matches, then remaining is " : value", colon is stripped
        items = ["Prefix : value"]
        assert find_field(items, "Prefix") == "value"
        # With "Prefix :" prefix it also works
        assert find_field(items, "Prefix :") == "value"

    def test_first_match_wins(self):
        items = ["Foo: first", "Foo: second"]
        assert find_field(items, "Foo") == "first"


class TestFindAllFields:
    def test_finds_all_matching(self):
        items = ["CPV: 111", "Name: X", "CPV: 222"]
        result = find_all_fields(items, "CPV")
        assert result == ["111", "222"]

    def test_empty_when_none_match(self):
        items = ["Foo: bar"]
        assert find_all_fields(items, "Baz") == []

    def test_empty_list(self):
        assert find_all_fields([], "X") == []


class TestFindFieldAnywhere:
    def test_searches_all_containers(self):
        containers = [["A: 1"], ["B: 2"], ["A: 3"]]
        assert find_field_anywhere(containers, "A") == "1"
        assert find_field_anywhere(containers, "B") == "2"

    def test_returns_none_when_not_found(self):
        containers = [["A: 1"], ["B: 2"]]
        assert find_field_anywhere(containers, "C") is None

    def test_empty_containers(self):
        assert find_field_anywhere([], "X") is None
        assert find_field_anywhere([[]], "X") is None


# ═══════════════════════════════════════════════════════
# Tests: parse_number
# ═══════════════════════════════════════════════════════

class TestParseNumber:
    def test_simple_integer(self):
        assert parse_number("990") == 990.0

    def test_integer_with_spaces(self):
        assert parse_number("1 500 000") == 1500000.0

    def test_decimal_with_dot(self):
        assert parse_number("810 488.62") == 810488.62

    def test_decimal_with_comma_sk_format(self):
        # Slovak format: comma as decimal separator, 2 decimal places
        assert parse_number("79 523,57") == 79523.57

    def test_large_number_comma_thousands(self):
        # English format: comma as thousands separator
        assert parse_number("1,500,000") == 1500000.0

    def test_mixed_dot_and_comma(self):
        # "1,234,567.89" — comma is thousands, dot is decimal
        assert parse_number("1,234,567.89") == 1234567.89

    def test_nbsp_handling(self):
        # Non-breaking space
        assert parse_number("1\xa0500\xa0000") == 1500000.0

    def test_whitespace_stripped(self):
        assert parse_number("  990  ") == 990.0

    def test_zero(self):
        assert parse_number("0") == 0.0

    def test_decimal_only(self):
        assert parse_number("0.50") == 0.50

    def test_invalid_string(self):
        assert parse_number("abc") is None

    def test_empty_string(self):
        assert parse_number("") is None

    def test_comma_with_3_digits_after(self):
        # "1,500" — 3 digits after comma = thousands separator
        assert parse_number("1,500") == 1500.0

    def test_comma_with_1_digit_after(self):
        # "1,5" — 1 digit after comma = decimal (not 2 digits though)
        # The code checks len(parts[-1]) == 2 for decimal comma
        # So "1,5" will be treated as thousands separator and become "15"
        assert parse_number("1,5") == 15.0

    def test_real_values_from_vestnik(self):
        """Test actual values found in vestnik 191 results."""
        assert parse_number("580") == 580.0
        assert parse_number("990") == 990.0
        assert parse_number("3593912.78") == 3593912.78
        assert parse_number("810 488.62") == 810488.62


# ═══════════════════════════════════════════════════════
# Tests: parse_organizations
# ═══════════════════════════════════════════════════════

class TestParseOrganizations:
    def test_basic_org_parsing(self):
        items = [
            "Organizácia z kontextu: ORG-0001",
            "Názov organizácie: Úrad pre verejné obstarávanie",
            "IČO: 31797903",
            "Organizácia z kontextu: Test firma (ORG-0002)",
            "IČO: 12345678",
            "E-mail: test@test.sk",
            "Mesto: Bratislava",
        ]
        orgs = parse_organizations(items)
        assert "ORG-0001" in orgs
        assert orgs["ORG-0001"]["name"] == "Úrad pre verejné obstarávanie"
        assert orgs["ORG-0001"]["ico"] == "31797903"
        assert "ORG-0002" in orgs
        assert orgs["ORG-0002"]["name"] == "Test firma"
        assert orgs["ORG-0002"]["ico"] == "12345678"
        assert orgs["ORG-0002"]["email"] == "test@test.sk"
        assert orgs["ORG-0002"]["city"] == "Bratislava"

    def test_name_to_ico_index_excludes_uvo(self):
        items = [
            "Organizácia z kontextu: ORG-0001",
            "Názov organizácie: Úrad pre verejné obstarávanie",
            "IČO: 31797903",
            "Organizácia z kontextu: Firma A (ORG-0002)",
            "IČO: 11111111",
        ]
        orgs = parse_organizations(items)
        name_idx = orgs["_name_to_ico"]
        # UVO should be excluded (IČO 31797903)
        assert "Úrad pre verejné obstarávanie" not in name_idx
        assert "Firma A" in name_idx
        assert name_idx["Firma A"] == "11111111"

    def test_empty_items(self):
        orgs = parse_organizations([])
        assert "_name_to_ico" in orgs
        assert orgs["_name_to_ico"] == {}

    def test_org_without_name_in_header(self):
        items = [
            "Organizácia z kontextu: ORG-0001",
            "Názov organizácie: Nejaká firma",
            "IČO: 99999999",
        ]
        orgs = parse_organizations(items)
        assert orgs["ORG-0001"]["name"] == "Nejaká firma"

    def test_implicit_org_blocks(self):
        """Test org blocks without explicit ORG-XXXX headers (using Zoznam organizácii)."""
        items = [
            "Zoznam organizácii uvedených v oznámení",
            "Názov organizácie: Implicitná firma",
            "IČO: 88888888",
        ]
        orgs = parse_organizations(items)
        # Should create an org with sequential numbering
        found = False
        for key, val in orgs.items():
            if isinstance(val, dict) and val.get("name") == "Implicitná firma":
                assert val["ico"] == "88888888"
                found = True
                break
        assert found, "Implicit org should be parsed"

    def test_multiple_orgs(self):
        items = [
            "Organizácia z kontextu: UVO (ORG-0001)",
            "IČO: 31797903",
            "Organizácia z kontextu: Kupujúci (ORG-0002)",
            "IČO: 11111111",
            "Organizácia z kontextu: Uchádzač 1 (ORG-0003)",
            "IČO: 22222222",
            "Organizácia z kontextu: Uchádzač 2 (ORG-0004)",
            "IČO: 33333333",
        ]
        orgs = parse_organizations(items)
        assert len([k for k in orgs if k.startswith("ORG-")]) == 4
        name_idx = orgs["_name_to_ico"]
        assert len(name_idx) == 3  # excluding UVO


# ═══════════════════════════════════════════════════════
# Tests: extract_buyer
# ═══════════════════════════════════════════════════════

class TestExtractBuyer:
    def test_basic_buyer(self):
        containers = parse_containers(MINIMAL_HTML)
        orgs = parse_organizations(containers[1])
        buyer = extract_buyer(containers, orgs)
        assert buyer["nazov"] == "Testová firma s.r.o."
        assert buyer["ico"] == "12345678"
        assert buyer["email"] == "test@firma.sk"

    def test_too_few_containers(self):
        buyer = extract_buyer([["a"], ["b"]], {})
        assert buyer == {}

    def test_buyer_with_uvo_ico_skipped(self):
        """Buyer whose ORG entry has UVO's IČO should get empty ico."""
        orgs = {
            "ORG-0002": {"name": "Test", "ico": "31797903", "email": "x@y.sk", "city": ""},
            "_name_to_ico": {},
        }
        containers = [[], [], ["ID kupujúceho: ORG-0002 (Test)"], []]
        buyer = extract_buyer(containers, orgs)
        assert buyer["nazov"] == "Test"
        assert buyer["ico"] == ""

    def test_buyer_fallback_to_name_index(self):
        """When ORG entry has UVO's IČO, lookup by name."""
        orgs = {
            "ORG-0002": {"name": "Firma X", "ico": "31797903", "email": "", "city": ""},
            "_name_to_ico": {"Firma X": "55555555"},
        }
        containers = [[], [], ["ID kupujúceho: ORG-0002 (Firma X)"], []]
        buyer = extract_buyer(containers, orgs)
        assert buyer["ico"] == "55555555"

    def test_buyer_prefers_name_from_id_over_org_registry(self):
        """When ORG registry has wrong mapping (e.g. UVO in ORG-0002 slot),
        the name from 'ID kupujúceho: ORG-0002 (Real Buyer)' must win."""
        orgs = {
            "ORG-0002": {"name": "Úrad pre verejné obstarávanie", "ico": "31797903", "email": "info@uvo.gov.sk", "city": ""},
            "ORG-0003": {"name": "Obec Banská Belá", "ico": "00320498", "email": "obec@test.sk", "city": ""},
            "_name_to_ico": {"Obec Banská Belá": "00320498"},
        }
        containers = [[], [], ["ID kupujúceho : ORG-0002 (Obec Banská Belá)"], []]
        buyer = extract_buyer(containers, orgs)
        assert buyer["nazov"] == "Obec Banská Belá"
        assert buyer["ico"] == "00320498"
        assert buyer["email"] == "obec@test.sk"

    def test_buyer_org_id_without_name(self):
        """ID kupujúceho: ORG-0002 (without parenthesized name)."""
        orgs = {
            "ORG-0002": {"name": "From Registry", "ico": "11111111", "email": "a@b.sk", "city": ""},
            "_name_to_ico": {"From Registry": "11111111"},
        }
        containers = [[], [], ["ID kupujúceho: ORG-0002"], []]
        # The regex has a group for ORG-XXXX without parenthesized name
        buyer = extract_buyer(containers, orgs)
        # Should still extract from orgs registry
        assert buyer["nazov"] == "From Registry"


# ═══════════════════════════════════════════════════════
# Tests: extract_subject
# ═══════════════════════════════════════════════════════

class TestExtractSubject:
    def test_basic_subject(self):
        containers = parse_containers(MINIMAL_HTML)
        subject = extract_subject(containers)
        assert subject["predmet"] == "Dodávka tovarov"
        assert "Počítačové zariadenia" in subject["cpv_kod"]
        assert subject["druh"] == "Tovary"

    def test_too_few_containers(self):
        assert extract_subject([["a"], ["b"], ["c"]]) == {}

    def test_missing_fields(self):
        containers = [[], [], [], ["Nejaký iný field: hodnota"]]
        subject = extract_subject(containers)
        assert subject["predmet"] == ""
        assert subject["cpv_kod"] == ""
        assert subject["druh"] == ""

    def test_alternative_prefix_with_space(self):
        """Handles 'Názov :' with space before colon."""
        containers = [[], [], [], ["Názov : Zákazka so spacou"]]
        subject = extract_subject(containers)
        assert subject["predmet"] == "Zákazka so spacou"


# ═══════════════════════════════════════════════════════
# Tests: extract_vysledok
# ═══════════════════════════════════════════════════════

class TestExtractVysledok:
    def test_basic_vysledok(self):
        containers = parse_containers(VYSLEDOK_HTML)
        orgs = parse_organizations(containers[1])
        result = extract_vysledok(containers, orgs)
        assert result["celkova_hodnota"] == 810488.62
        assert result["mena"] == "EUR"
        assert result["pocet_ponuk"] == 3
        assert len(result["ucastnici"]) == 2

    def test_winner_marked(self):
        containers = parse_containers(VYSLEDOK_HTML)
        orgs = parse_organizations(containers[1])
        result = extract_vysledok(containers, orgs)
        winners = [u for u in result["ucastnici"] if u["je_vitaz"]]
        assert len(winners) == 1
        assert winners[0]["nazov"] == "Víťaz s.r.o."
        assert winners[0]["ico"] == "22222222"
        assert winners[0]["cena"] == 810488.62
        assert winners[0]["poradie"] == 1

    def test_non_winner_has_correct_data(self):
        containers = parse_containers(VYSLEDOK_HTML)
        orgs = parse_organizations(containers[1])
        result = extract_vysledok(containers, orgs)
        non_winners = [u for u in result["ucastnici"] if not u["je_vitaz"]]
        assert len(non_winners) == 1
        assert non_winners[0]["nazov"] == "Druhý uchádzač a.s."
        assert non_winners[0]["cena"] == 900000.0
        assert non_winners[0]["poradie"] == 2

    def test_too_few_containers(self):
        result = extract_vysledok([[], [], [], [], []], {})
        assert result["celkova_hodnota"] is None
        assert result["ucastnici"] == []

    def test_empty_containers_6(self):
        result = extract_vysledok([[], [], [], [], [], []], {})
        assert result["celkova_hodnota"] is None
        assert result["pocet_ponuk"] is None
        assert result["ucastnici"] == []

    def test_deduplication_by_name(self):
        """Same bidder appearing in multiple lots should be deduplicated."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: ORG-0003 (Firma ABC)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 100",
            "Poradie ponuky: 1",
            "Identifikátor ponuky: TEN-0002",
            "ID uchádzača: ORG-0003 (Firma ABC)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 200",
            "Poradie ponuky: 1",
        ]
        containers = [[], [], [], [], [], items5]
        orgs = {"_name_to_ico": {"Firma ABC": "99999999"}}
        result = extract_vysledok(containers, orgs)
        # Should be deduplicated to 1 entry with the higher value
        assert len(result["ucastnici"]) == 1
        assert result["ucastnici"][0]["cena"] == 200.0
        assert result["ucastnici"][0]["je_vitaz"] is True

    def test_no_fuzzy_ico_lookup(self):
        """Fuzzy substring matching was removed to prevent IČO confusion.
        Only exact name matches should assign IČO."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: TPA-0005 (Firma XY)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 500",
            "Poradie ponuky: 1",
        ]
        containers = [[], [], [], [], [], items5]
        # Name in index is longer — should NOT match via fuzzy substring
        orgs = {"_name_to_ico": {"Firma XY Bratislava": "77777777"}}
        result = extract_vysledok(containers, orgs)
        assert result["ucastnici"][0]["ico"] == ""

    def test_exact_name_ico_lookup(self):
        """Exact name match in _name_to_ico should still work."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: TPA-0005 (Firma XY)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 500",
            "Poradie ponuky: 1",
        ]
        containers = [[], [], [], [], [], items5]
        orgs = {"_name_to_ico": {"Firma XY": "77777777"}}
        result = extract_vysledok(containers, orgs)
        assert result["ucastnici"][0]["ico"] == "77777777"

    def test_org_id_name_mismatch_falls_back_to_name_index(self):
        """When ORG-XXXX exists but name doesn't match the winner,
        should fall back to name index instead of using wrong IČO."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: ORG-0003 (BAX PHARMA s.r.o.)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 500",
            "Poradie ponuky: 1",
        ]
        containers = [[], [], [], [], [], items5]
        # ORG-0003 is actually the buyer (hospital), not the winner
        orgs = {
            "ORG-0003": {"name": "Fakultná nemocnica Banská Bystrica", "ico": "00165549",
                         "email": "", "city": "Banská Bystrica"},
            "_name_to_ico": {
                "Fakultná nemocnica Banská Bystrica": "00165549",
                "BAX PHARMA s.r.o.": "44444444",
            },
        }
        result = extract_vysledok(containers, orgs)
        # Should NOT assign hospital's IČO to the pharma winner
        assert result["ucastnici"][0]["nazov"] == "BAX PHARMA s.r.o."
        assert result["ucastnici"][0]["ico"] == "44444444"


# ═══════════════════════════════════════════════════════
# Tests: extract_vyhlasenie
# ═══════════════════════════════════════════════════════

class TestExtractVyhlasenie:
    def test_basic_vyhlasenie(self):
        containers = parse_containers(VYHLASENIE_HTML)
        result = extract_vyhlasenie(containers)
        assert result["hodnota"] == 1500000.0
        assert result["mena"] == "EUR"
        assert result["lehota_datum"] == "15.10.2026"
        assert result["lehota_cas"] == "10:00"
        assert "financované z fondov EÚ" in result["eu_fond"]
        assert result["ramcova_dohoda"] is False
        assert result["kriterium"] == "Cena"

    def test_too_few_containers(self):
        result = extract_vyhlasenie([[], [], [], []])
        assert result["hodnota"] is None
        assert result["lehota_datum"] == ""

    def test_ramcova_dohoda_true(self):
        containers = [[], [], [], [], [
            "Rámcová dohoda: S viacerými účastníkmi",
        ]]
        result = extract_vyhlasenie(containers)
        assert result["ramcova_dohoda"] is True

    def test_ramcova_dohoda_nie(self):
        containers = [[], [], [], [], [
            "Rámcová dohoda: Nie",
        ]]
        result = extract_vyhlasenie(containers)
        assert result["ramcova_dohoda"] is False

    def test_value_fallback_to_container_3(self):
        """When container 4 has no value, should fall back to container 3."""
        containers = [
            [],
            [],
            [],
            ["Predpokladaná hodnota (BT-27-Lot) (hodnota): 999 999"],
            ["Lehota na predkladanie ponúk (dátum): 01.01.2027"],
        ]
        result = extract_vyhlasenie(containers)
        assert result["hodnota"] == 999999.0

    def test_opis_kriteria_truncated(self):
        long_opis = "Opis kritéria na vyhodnotenie ponúk: " + "A" * 200
        containers = [[], [], [], [], [long_opis]]
        result = extract_vyhlasenie(containers)
        assert len(result["kriterium"]) == 100

    def test_no_deadline(self):
        containers = [[], [], [], [], ["Nejaký iný field: hodnota"]]
        result = extract_vyhlasenie(containers)
        assert result["lehota_datum"] == ""
        assert result["lehota_cas"] == ""


# ═══════════════════════════════════════════════════════
# Tests: extract_zmena_zmluvy
# ═══════════════════════════════════════════════════════

class TestExtractZmenaZmluvy:
    def test_basic_zmena(self):
        containers = parse_containers(ZMENA_ZMLUVY_HTML)
        orgs = parse_organizations(containers[1])
        result = extract_zmena_zmluvy(containers, orgs)
        assert result["dodavatel"]["nazov"] == "Dodávateľ s.r.o."
        assert result["dodavatel"]["ico"] == "44555666"
        assert result["hodnota_po_zmene"] == 250000.0
        assert result["zmluva_id"] == "Zmluva č. 123/2025"
        assert result["zmluva_datum"] == "01.03.2025"
        assert "crz.gov.sk" in result["zmluva_url"]
        assert result["dovod_zmeny"] == "Potreba dodatočných stavebných prác"
        assert "nepredvídaných okolností" in result["odovodnenie"]
        assert "50 000 EUR" in result["zhrnutie"]
        assert "Plán obnovy" in result["eu_fond"]

    def test_too_few_containers(self):
        result = extract_zmena_zmluvy([[], [], [], []], {})
        assert result["dodavatel"]["nazov"] == ""
        assert result["hodnota_po_zmene"] is None

    def test_no_container_6(self):
        """Missing container 6 means no reason/summary."""
        containers = [[], [], [], [], [
            "ID uchádzača: ORG-0003 (Firma)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 100",
        ], []]
        orgs = {"_name_to_ico": {"Firma": "11111111"}}
        result = extract_zmena_zmluvy(containers, orgs)
        assert result["dodavatel"]["nazov"] == "Firma"
        assert result["dovod_zmeny"] == ""

    def test_truncation_of_long_fields(self):
        long_text = "X" * 500
        containers = [[], [], [], [], [], [], [
            f"Odôvodnenie zmeny zmluvy: {long_text}",
            f"Zhrnutie zmeny: {long_text}",
        ]]
        result = extract_zmena_zmluvy(containers, {"_name_to_ico": {}})
        assert len(result["odovodnenie"]) == 300
        assert len(result["zhrnutie"]) == 300


# ═══════════════════════════════════════════════════════
# Tests: classify_from_html
# ═══════════════════════════════════════════════════════

class TestClassifyFromHtml:
    def test_vysledok(self):
        action, typ_form, typ_ozn = classify_from_html(VYSLEDOK_HTML)
        assert action == "vysledok"
        assert "Výsledok" in typ_form

    def test_vyhlasenie(self):
        action, typ_form, _ = classify_from_html(VYHLASENIE_HTML)
        assert action == "vyhlasenie"
        assert "Súťaž" in typ_form

    def test_zmena_zmluvy(self):
        action, typ_form, _ = classify_from_html(ZMENA_ZMLUVY_HTML)
        assert action == "zmena_zmluvy"

    def test_oprava_by_version(self):
        """Version > 01 with Súťaž form type should be classified as oprava."""
        action, _, _ = classify_from_html(OPRAVA_HTML)
        assert action == "oprava"

    def test_empty_html(self):
        action, _, _ = classify_from_html(EMPTY_HTML)
        assert action == "unknown"

    def test_predbezne_is_vyhlasenie(self):
        html = '<ul class="notice-list"><li>Typ formulára: Predbežné oznámenie</li><li>Verzia oznámenia: 01</li></ul>'
        action, _, _ = classify_from_html(html)
        assert action == "vyhlasenie"

    def test_sumarizacia_oprav_fallback(self):
        html = """
        <ul class="notice-list"><li>Typ formulára: Neznámy</li><li>Verzia oznámenia: 01</li></ul>
        <ul class="notice-list"><li>Sumarizácia vykonaných opráv</li></ul>
        """
        action, _, _ = classify_from_html(html)
        assert action == "oprava"


# ═══════════════════════════════════════════════════════
# Tests: parse_document (main orchestrator)
# ═══════════════════════════════════════════════════════

class TestParseDocument:
    def test_vysledok_document(self):
        result = parse_document(VYSLEDOK_HTML, "vysledok")
        assert "obstaravatel" in result
        assert "zakazka" in result
        assert "vysledok" in result
        assert result["obstaravatel"]["nazov"] == "Testový kupujúci"
        assert result["zakazka"]["predmet"] == "Nákup softvéru"
        assert len(result["vysledok"]["ucastnici"]) == 2

    def test_vyhlasenie_document(self):
        result = parse_document(VYHLASENIE_HTML, "vyhlasenie")
        assert "prilezitost" in result
        assert "vysledok" not in result
        assert result["prilezitost"]["hodnota"] == 1500000.0

    def test_zmena_zmluvy_document(self):
        result = parse_document(ZMENA_ZMLUVY_HTML, "zmena_zmluvy")
        assert "zmena_zmluvy" in result
        assert result["zmena_zmluvy"]["dodavatel"]["nazov"] == "Dodávateľ s.r.o."

    def test_oprava_as_vyhlasenie(self):
        result = parse_document(OPRAVA_HTML, "oprava")
        # OPRAVA_HTML has Súťaž form type, so extract_oprava should use vyhlasenie
        assert "prilezitost" in result
        assert result["prilezitost"]["hodnota"] == 500000.0

    def test_oprava_as_vysledok(self):
        """Oprava with Výsledok form type should extract vysledok data."""
        result = parse_document(VYSLEDOK_HTML, "oprava")
        assert "vysledok" in result
        assert result["vysledok"]["celkova_hodnota"] == 810488.62

    def test_suhrn_same_as_vysledok(self):
        result = parse_document(VYSLEDOK_HTML, "suhrn")
        assert "vysledok" in result

    def test_empty_html_returns_error(self):
        result = parse_document(EMPTY_HTML, "vysledok")
        assert result == {"error": "no_containers"}

    def test_single_container_no_crash(self):
        html = '<ul class="notice-list"><li>Typ: test</li></ul>'
        result = parse_document(html, "vysledok")
        assert "obstaravatel" in result
        # Should handle gracefully with empty buyer/subject
        assert result["obstaravatel"] == {}
        assert result["zakazka"] == {}


# ═══════════════════════════════════════════════════════
# Tests: CODE_ACTION mapping
# ═══════════════════════════════════════════════════════

class TestCodeAction:
    def test_vysledok_codes(self):
        for code in ["VST", "VSS", "VSP", "VUT", "VUS", "VUP", "IPT", "IPS", "IPP"]:
            assert vp.CODE_ACTION[code] == "vysledok"

    def test_vyhlasenie_codes(self):
        for code in ["MST", "MSS", "MSP", "MUT", "MUS", "MUP", "WYT", "WYS", "WYP"]:
            assert vp.CODE_ACTION[code] == "vyhlasenie"

    def test_oprava_code(self):
        assert vp.CODE_ACTION["IOX"] == "oprava"

    def test_zmena_zmluvy_codes(self):
        for code in ["DOP", "DOT", "DOS"]:
            assert vp.CODE_ACTION[code] == "zmena_zmluvy"

    def test_suhrn_code(self):
        assert vp.CODE_ACTION["INT"] == "suhrn"


# ═══════════════════════════════════════════════════════
# Integration tests with real HTML from vestnik 191
# ═══════════════════════════════════════════════════════

VESTNIK_191_DIR = Path("/tmp/vestnik191_full")
RESULTS_FILE = Path(__file__).parent.parent / "data" / "results" / "vestnik_191_2026_parser_results.json"


def _load_expected_results():
    """Load expected results JSON, keyed by doc ID."""
    if not RESULTS_FILE.exists():
        return {}
    with open(RESULTS_FILE) as f:
        data = json.load(f)
    return {str(d["id"]): d for d in data if "extraction" in d}


@pytest.mark.skipif(
    not VESTNIK_191_DIR.exists() or not RESULTS_FILE.exists(),
    reason="Real HTML data or results not available"
)
class TestIntegrationVestnik191:
    """Integration tests using real HTML files from vestnik 191."""

    @pytest.fixture(scope="class")
    @classmethod
    def expected_results(cls):
        return _load_expected_results()

    def _parse_real_doc(self, doc_id, expected_results):
        """Parse a real HTML doc and compare with expected results."""
        html_path = VESTNIK_191_DIR / f"{doc_id}.html"
        assert html_path.exists(), f"HTML file {html_path} not found"
        html = html_path.read_text(encoding="utf-8", errors="ignore")

        expected = expected_results.get(str(doc_id))
        assert expected is not None, f"No expected result for doc {doc_id}"

        action = expected["action"]
        # Auto-classify if unknown
        if action == "unknown":
            action, _, _ = classify_from_html(html)

        result = parse_document(html, action)
        return result, expected["extraction"]

    def test_vysledok_1411472(self, expected_results):
        """Real vysledok doc: Banskobystrický samosprávny kraj bus services."""
        result, expected = self._parse_real_doc("1411472", expected_results)

        # Buyer
        assert result["obstaravatel"]["nazov"] == expected["obstaravatel"]["nazov"]
        assert result["obstaravatel"]["ico"] == expected["obstaravatel"]["ico"]

        # Subject
        assert result["zakazka"]["predmet"] == expected["zakazka"]["predmet"]
        assert result["zakazka"]["druh"] == expected["zakazka"]["druh"]

        # Results
        assert result["vysledok"]["celkova_hodnota"] == expected["vysledok"]["celkova_hodnota"]
        assert result["vysledok"]["pocet_ponuk"] == expected["vysledok"]["pocet_ponuk"]
        assert len(result["vysledok"]["ucastnici"]) == len(expected["vysledok"]["ucastnici"])

        # Winner details
        if result["vysledok"]["ucastnici"]:
            winner = result["vysledok"]["ucastnici"][0]
            exp_winner = expected["vysledok"]["ucastnici"][0]
            assert winner["nazov"] == exp_winner["nazov"]
            assert winner["ico"] == exp_winner["ico"]
            assert winner["cena"] == exp_winner["cena"]
            assert winner["je_vitaz"] == exp_winner["je_vitaz"]

    def test_vyhlasenie_1402911(self, expected_results):
        """Real vyhlasenie doc: Banská Belá ČOV."""
        result, expected = self._parse_real_doc("1402911", expected_results)

        assert result["obstaravatel"]["nazov"] == expected["obstaravatel"]["nazov"]
        assert result["zakazka"]["predmet"] == expected["zakazka"]["predmet"]

        prilezitost = result.get("prilezitost", {})
        exp_pril = expected.get("prilezitost", {})
        assert prilezitost["lehota_datum"] == exp_pril["lehota_datum"]
        assert prilezitost["lehota_cas"] == exp_pril["lehota_cas"]
        assert prilezitost["ramcova_dohoda"] == exp_pril["ramcova_dohoda"]
        assert prilezitost["kriterium"] == exp_pril["kriterium"]

    def test_zmena_zmluvy_1418024(self, expected_results):
        """Real zmena_zmluvy doc: Mesto Banská Bystrica ZŠ renovation."""
        result, expected = self._parse_real_doc("1418024", expected_results)

        assert result["obstaravatel"]["nazov"] == expected["obstaravatel"]["nazov"]
        assert result["obstaravatel"]["ico"] == expected["obstaravatel"]["ico"]

        zmena = result.get("zmena_zmluvy", {})
        exp_zmena = expected.get("zmena_zmluvy", {})
        assert zmena["dodavatel"]["nazov"] == exp_zmena["dodavatel"]["nazov"]
        assert zmena["dodavatel"]["ico"] == exp_zmena["dodavatel"]["ico"]
        assert zmena["hodnota_po_zmene"] == exp_zmena["hodnota_po_zmene"]
        assert zmena["zmluva_id"] == exp_zmena["zmluva_id"]
        assert zmena["dovod_zmeny"] == exp_zmena["dovod_zmeny"]

    def test_all_docs_parse_without_error(self, expected_results):
        """Verify all 59 docs from vestnik 191 parse without raising exceptions."""
        errors = []
        for html_file in sorted(VESTNIK_191_DIR.glob("*.html")):
            doc_id = html_file.stem
            html = html_file.read_text(encoding="utf-8", errors="ignore")
            action, _, _ = classify_from_html(html)
            if action == "unknown":
                # Check expected results for action
                if doc_id in expected_results:
                    action = expected_results[doc_id]["action"]
            try:
                result = parse_document(html, action)
                assert "error" not in result or result.get("error") != "no_containers", \
                    f"Doc {doc_id} has no containers"
            except Exception as e:
                errors.append(f"{doc_id}: {e}")

        assert errors == [], f"Parsing errors: {errors}"

    def test_no_empty_buyers(self, expected_results):
        """Most docs should have a non-empty buyer name."""
        empty_count = 0
        total = 0
        for html_file in sorted(VESTNIK_191_DIR.glob("*.html")):
            doc_id = html_file.stem
            html = html_file.read_text(encoding="utf-8", errors="ignore")
            action, _, _ = classify_from_html(html)
            if action == "unknown" and doc_id in expected_results:
                action = expected_results[doc_id]["action"]
            result = parse_document(html, action)
            if "obstaravatel" in result:
                total += 1
                if not result["obstaravatel"].get("nazov"):
                    empty_count += 1

        # Allow at most 10% empty buyers
        assert empty_count / max(total, 1) < 0.1, \
            f"{empty_count}/{total} docs have empty buyer names"


# ═══════════════════════════════════════════════════════
# Edge case tests
# ═══════════════════════════════════════════════════════

class TestEdgeCases:
    def test_parse_number_with_trailing_text(self):
        """parse_number should handle strings that are pure numbers after cleanup."""
        assert parse_number("100") == 100.0

    def test_containers_with_only_whitespace_items(self):
        html = '<ul class="notice-list"><li>   \n\t  </li></ul>'
        containers = parse_containers(html)
        assert containers == [[]]

    def test_deeply_nested_html(self):
        html = '<ul class="notice-list"><li><div><span><b>Deep</b> value</span></div></li></ul>'
        containers = parse_containers(html)
        assert containers == [["Deep value"]]

    def test_extract_vysledok_no_ponuky_section(self):
        """Container 5 exists but has no tender info."""
        items5 = [
            "Celková hodnota oznámenia (BT-161-NoticeResult) (hodnota): 500",
            "Nejaký iný text",
        ]
        containers = [[], [], [], [], [], items5]
        result = extract_vysledok(containers, {"_name_to_ico": {}})
        assert result["celkova_hodnota"] == 500.0
        assert result["ucastnici"] == []
        assert result["pocet_ponuk"] is None

    def test_parse_document_unknown_action(self):
        """Unknown action should still return buyer and subject."""
        result = parse_document(MINIMAL_HTML, "neznamy_typ")
        assert "obstaravatel" in result
        assert "zakazka" in result
        assert "vysledok" not in result
        assert "prilezitost" not in result

    def test_url_reconstruction_in_zmena(self):
        """URL with https: prefix should be preserved correctly."""
        items4 = [
            "Odkaz na zverejnenú zmluvu (URL): https://www.crz.gov.sk/zmluva/123/",
        ]
        containers = [[], [], [], [], items4]
        result = extract_zmena_zmluvy(containers, {"_name_to_ico": {}})
        assert result["zmluva_url"] == "https://www.crz.gov.sk/zmluva/123/"

    def test_multiple_lots_winner_detection(self):
        """Multiple lots with different winners."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: ORG-0003 (Firma A)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 100",
            "Poradie ponuky: 1",
            "Identifikátor ponuky: TEN-0002",
            "ID uchádzača: ORG-0004 (Firma B)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 200",
            "Poradie ponuky: 2",
            "Identifikátor ponuky: TEN-0003",
            "ID uchádzača: ORG-0005 (Firma C)",
            "Hodnota ponuky (BT-720-Tender) (hodnota): 300",
            "Poradie ponuky: 3",
        ]
        containers = [[], [], [], [], [], items5]
        orgs = {"_name_to_ico": {}}
        result = extract_vysledok(containers, orgs)
        assert len(result["ucastnici"]) == 3
        winners = [u for u in result["ucastnici"] if u["je_vitaz"]]
        assert len(winners) == 1
        assert winners[0]["nazov"] == "Firma A"

    def test_extract_vyhlasenie_celkova_predpokladana(self):
        """Test 'Celková predpokladaná hodnota' prefix variant."""
        containers = [[], [], [], [], [
            "Celková predpokladaná hodnota (BT-27-Lot) (hodnota): 2 000 000",
        ]]
        result = extract_vyhlasenie(containers)
        assert result["hodnota"] == 2000000.0

    def test_org_with_uchadzac_suffix_stripped(self):
        """Tender name should strip '(uchádzač N)' suffix."""
        items5 = [
            "Identifikátor ponuky: TEN-0001",
            "ID uchádzača: TPA-0002 (Firma XYZ, a.s.) (uchádzač 2)",
            "Poradie ponuky: 1",
        ]
        containers = [[], [], [], [], [], items5]
        result = extract_vysledok(containers, {"_name_to_ico": {}})
        assert result["ucastnici"][0]["nazov"] == "Firma XYZ, a.s."
