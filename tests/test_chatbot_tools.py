"""Tests des outils de l'assistant WhatsApp et de leur limitation à l'expéditeur (tecpap.chatbot.tools)."""

from __future__ import annotations

import json

import pytest

from tecpap.chatbot.tools import (
    MAX_RECENT_ORDERS,
    TOOL_NAMES,
    AssistantTools,
    ToolResult,
    order_reference,
    phone_variants,
)
from tecpap.rag.answer import Answer

ALICE = "whatsapp:+212600000001"
BOB = "whatsapp:+212600000002"
PUBLIC_KEYS = {"reference", "date", "product", "quantity", "unit", "status"}
FORBIDDEN_WORDS = ("prix", "price", "telephone", "phone", "client", "email", "montant", "devise")


def make_order(db, *, client: str, whatsapp_from: str | None = None, status: str = "en_attente", **fields) -> int:
    data = {"entreprise_cliente": client, "type_produit": "Sachets fond plat", "quantite": 1000,
            "prix_unitaire": 0.42, "prix_total": 420.0, "source": "whatsapp" if whatsapp_from else "email",
            "whatsapp_from": whatsapp_from, **fields}
    order_id = db.create_order(data)
    if status != "en_attente":
        db.update_order_status(order_id, status)
    return order_id


@pytest.fixture
def db(temp_db):
    return temp_db


@pytest.fixture
def alice(db):
    return AssistantTools(db.connection, db, ALICE)


@pytest.fixture
def bob(db):
    return AssistantTools(db.connection, db, BOB)


@pytest.fixture
def two_clients(db):
    """Alice (Snack Atlas) et Bob (Boulangerie Nour) ont chacun des commandes."""
    ids = {
        "alice_pending": make_order(db, client="Snack Atlas", whatsapp_from=ALICE, numero_commande="A-100",
                                    quantite=3000),
        "alice_validated": make_order(db, client="Snack Atlas", whatsapp_from=ALICE, status="validee",
                                      type_produit="Sac fond carré avec poignées plates", quantite=2000,
                                      grammage=80, type_papier="KE"),
        "bob_pending": make_order(db, client="Boulangerie Nour", whatsapp_from=BOB, numero_commande="B-200",
                                  quantite=9000),
        "bob_validated": make_order(db, client="Boulangerie Nour", whatsapp_from=BOB, status="validee",
                                    numero_commande="B-201", quantite=7000),
    }
    return ids


def _references(orders) -> set[str]:
    return {order["reference"] for order in orders}


class TestConstruction:
    @pytest.mark.parametrize("sender", [None, "", "whatsapp:", "pas un numéro", "123", 600000001])
    def test_invalid_sender_is_refused(self, db, sender):
        with pytest.raises(ValueError):
            AssistantTools(db.connection, db, sender)

    @pytest.mark.parametrize("sender", ["whatsapp:+212600000001", "+212600000001", "0600000001",
                                        "+212 6 00 00 00 01"])
    def test_sender_forms(self, db, sender):
        assert AssistantTools(db.connection, db, sender).phone == "+212600000001"

    def test_repr_masks_the_phone(self, alice):
        assert "600000001" not in repr(alice)

    def test_phone_variants(self):
        assert phone_variants("+212600000001") == ("+212600000001", "212600000001", "00212600000001", "0600000001")
        assert phone_variants("+14155238886") == ("+14155238886", "14155238886", "0014155238886")

    def test_order_reference(self):
        assert order_reference("BC-1", 4) == "BC-1"
        assert order_reference("  ", 4) == "CMD-4"
        assert order_reference(None, 9) == "CMD-9"


class TestScope:
    def test_each_sender_sees_only_their_orders(self, alice, bob, two_clients):
        assert _references(alice.my_recent_orders(10)) == {"A-100", f"CMD-{two_clients['alice_validated']}"}
        assert _references(bob.my_recent_orders(10)) == {"B-200", "B-201"}

    def test_results_are_minimal(self, alice, two_clients):
        for order in alice.my_recent_orders(10):
            assert set(order) == PUBLIC_KEYS
            assert not any(word in key for key in order for word in FORBIDDEN_WORDS)
        serialized = json.dumps(alice.my_recent_orders(10), ensure_ascii=False)
        assert "0.42" not in serialized and "420" not in serialized
        assert "Snack Atlas" not in serialized

    def test_order_content(self, alice, two_clients):
        order = alice.my_order_status("A-100")
        assert order["product"] == "Sachets fond plat"
        assert order["quantity"] == 3000 and isinstance(order["quantity"], int)
        assert order["unit"] == "US" and order["status"] == "en_attente"
        assert len(order["date"]) == 10

    def test_order_without_quantity_or_product(self, db, alice):
        db.create_order({"entreprise_cliente": "Snack Atlas", "whatsapp_from": ALICE, "numero_commande": "Q-0",
                         "source": "whatsapp", "nature_produit": "  Gobelets   carton  "})
        db.connection.execute("UPDATE commandes SET quantite = NULL, unite = NULL WHERE numero_commande = 'Q-0'")
        order = alice.my_order_status("Q-0")
        assert order["quantity"] is None and order["unit"] == "US" and order["product"] == "Gobelets carton"

    def test_whatsapp_prefix_and_number_forms_match(self, db, alice):
        make_order(db, client="Snack Atlas", whatsapp_from="+212600000001", numero_commande="F-1")
        make_order(db, client="Snack Atlas", whatsapp_from="whatsapp:+212 6 00 00 00 01", numero_commande="F-2")
        make_order(db, client="Snack Atlas", whatsapp_from="0600000001", numero_commande="F-3")
        make_order(db, client="Snack Atlas", whatsapp_from="WhatsApp:+212600000001", numero_commande="F-4")
        assert _references(alice.my_recent_orders(10)) == {"F-1", "F-2", "F-3", "F-4"}

    def test_similar_numbers_do_not_match(self, db, alice):
        make_order(db, client="X", whatsapp_from="whatsapp:+2126000000011", numero_commande="N-1")
        make_order(db, client="X", whatsapp_from="whatsapp:+212600000010", numero_commande="N-2")
        make_order(db, client="X", whatsapp_from="%", numero_commande="N-3")
        assert alice.my_recent_orders(10) == []

    def test_deleted_orders_are_hidden(self, db, alice):
        order_id = make_order(db, client="Snack Atlas", whatsapp_from=ALICE, numero_commande="D-1")
        db.delete_order(order_id)
        assert alice.my_recent_orders(10) == [] and alice.my_order_status("D-1") is None

    def test_rejected_orders_are_visible(self, db, alice):
        make_order(db, client="Snack Atlas", whatsapp_from=ALICE, numero_commande="R-1", status="rejetee")
        assert alice.my_order_status("R-1")["status"] == "rejetee"

    def test_client_orders_stay_hidden_even_after_a_validated_whatsapp_order(self, db, alice):
        email_order = make_order(db, client="Snack Atlas", numero_commande="EM-1", email_from="achat@exemple.ma")
        whatsapp_order = make_order(db, client="Snack Atlas", whatsapp_from=ALICE)
        db.update_order_status(whatsapp_order, "validee")
        assert db.attach_phone_to_client(db.get_order(whatsapp_order)["client_id"], ALICE) is True
        # Seules les commandes envoyées depuis le numéro de l'expéditeur lui sont visibles.
        assert alice.my_order_status("EM-1") is None
        assert alice.my_order_status(f"CMD-{email_order}") is None
        assert _references(alice.my_recent_orders(10)) == {f"CMD-{whatsapp_order}"}

    def test_client_phone_alone_does_not_give_access(self, db, alice):
        db.get_or_create_client("Snack Atlas", telephone="+212600000001")
        make_order(db, client="Snack Atlas", numero_commande="EM-2", email_from="achat@exemple.ma")
        assert alice.my_recent_orders(10) == []

    def test_ordering_in_the_name_of_another_company_does_not_reveal_its_orders(self, db, bob):
        """Une commande au nom d'une entreprise existante ne lui rattache pas le numéro de l'expéditeur :
        les commandes email de cette entreprise restent cachées tant que l'équipe commerciale n'a rien validé."""
        victim = make_order(db, client="Chocolaterie Amandine", numero_commande="VICTIM-1", status="validee",
                            email_from="achats@exemple.ma")
        attack = bob.submit_order({"entreprise_cliente": "Chocolaterie Amandine", "type_produit": "Sachets fond plat",
                                   "quantite": 10})
        telephone = db.connection.execute(
            "SELECT telephone FROM clients WHERE nom = 'Chocolaterie Amandine'").fetchone()[0]
        assert telephone is None
        assert bob.my_order_status("VICTIM-1") is None
        assert bob.my_order_status(f"CMD-{victim}") is None
        assert _references(bob.my_recent_orders(10)) == {f"CMD-{attack}"}
        assert bob.last_validated_order_draft("Chocolaterie Amandine") is None

    def test_sender_never_sees_another_senders_order_by_reference(self, alice, two_clients):
        for reference in ("B-200", "B-201", f"CMD-{two_clients['bob_pending']}", str(two_clients["bob_validated"])):
            assert alice.my_order_status(reference) is None

    @pytest.mark.parametrize("reference", [
        "' OR 1=1 --", "B-200' OR '1'='1", "%", "_", "*", "A-100; DROP TABLE commandes", "\" OR \"\"=\"",
        "CMD-0 OR 1=1", "1) OR (1=1", "", "   ", "x" * 500,
    ])
    def test_injection_like_references(self, alice, db, two_clients, reference):
        assert alice.my_order_status(reference) is None
        assert db.connection.execute("SELECT COUNT(*) FROM commandes").fetchone()[0] == 4

    @pytest.mark.parametrize("reference", ["A-100", "a-100", " A-100 ", "A - 100", "a-100.", "«A-100»"])
    def test_reference_matching_ignores_case_spaces_and_punctuation(self, alice, two_clients, reference):
        assert alice.my_order_status(reference)["reference"] == "A-100"

    @pytest.mark.parametrize("reference", ["A100", "A-1000", "A-10", "-100"])
    def test_reference_must_match_exactly(self, alice, two_clients, reference):
        assert alice.my_order_status(reference) is None

    def test_reference_by_internal_id(self, alice, two_clients):
        order_id = two_clients["alice_validated"]
        for reference in (f"CMD-{order_id}", f"cmd{order_id}", str(order_id)):
            assert alice.my_order_status(reference)["reference"] == f"CMD-{order_id}"

    @pytest.mark.parametrize("reference", [None, 12, ["A-100"], {"reference": "A-100"}])
    def test_non_text_reference(self, alice, two_clients, reference):
        assert alice.my_order_status(reference) is None

    def test_recent_orders_limit(self, db, alice):
        for index in range(MAX_RECENT_ORDERS + 5):
            make_order(db, client="Snack Atlas", whatsapp_from=ALICE, numero_commande=f"L-{index}")
        assert len(alice.my_recent_orders(100)) == MAX_RECENT_ORDERS
        assert len(alice.my_recent_orders(0)) == 1
        assert len(alice.my_recent_orders(-3)) == 1
        assert len(alice.my_recent_orders("10")) == 5
        assert len(alice.my_recent_orders(True)) == 5

    def test_recent_orders_are_newest_first(self, db, alice):
        first = make_order(db, client="Snack Atlas", whatsapp_from=ALICE)
        second = make_order(db, client="Snack Atlas", whatsapp_from=ALICE)
        assert [order["reference"] for order in alice.my_recent_orders()] == [f"CMD-{second}", f"CMD-{first}"]


class TestLastValidatedOrder:
    def test_last_validated_order(self, alice, bob, two_clients):
        assert alice.my_last_validated_order()["reference"] == f"CMD-{two_clients['alice_validated']}"
        assert bob.my_last_validated_order()["reference"] == "B-201"

    def test_none_without_validated_order(self, db, alice):
        make_order(db, client="Snack Atlas", whatsapp_from=ALICE)
        assert alice.my_last_validated_order() is None
        assert alice.last_validated_order_draft() is None
        assert alice.known_client_name() is None

    def test_reorder_draft_has_no_prices(self, alice, two_clients):
        draft = alice.last_validated_order_draft()
        assert draft["type_produit"] == "Sac fond carré avec poignées plates"
        assert draft["quantite"] == 2000 and draft["entreprise_cliente"] == "Snack Atlas"
        assert draft["grammage"] == 80 and draft["type_papier"] == "KE"
        assert draft["reference"] == f"CMD-{two_clients['alice_validated']}"
        assert not {"prix_unitaire", "prix_total", "devise", "whatsapp_from", "client_id"} & set(draft)

    def test_reorder_draft_for_a_named_client(self, db, alice):
        make_order(db, client="Café Nakhil", whatsapp_from=ALICE, status="validee", quantite=5000,
                   numero_commande="CN-1")
        make_order(db, client="Snack Atlas", whatsapp_from=ALICE, status="validee", quantite=1200)
        assert alice.last_validated_order_draft()["entreprise_cliente"] == "Snack Atlas"
        assert alice.last_validated_order_draft("cafe nakhil")["quantite"] == 5000
        assert alice.last_validated_order_draft("Inconnu SARL") is None

    def test_generic_client_name_is_not_reused(self, db, alice):
        make_order(db, client=None, whatsapp_from=ALICE, status="validee")
        draft = alice.last_validated_order_draft()
        assert "entreprise_cliente" not in draft
        assert alice.known_client_name() is None

    def test_known_client_name(self, alice, two_clients):
        assert alice.known_client_name() == "Snack Atlas"


class TestSubmitOrder:
    def test_order_is_saved_pending_with_the_sender(self, db, alice):
        order_id = alice.submit_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                                       "quantite": 2500, "unite": "US"})
        order = db.get_order(order_id)
        assert order["statut"] == "en_attente"
        assert order["whatsapp_from"] == ALICE and order["source"] == "whatsapp"
        assert order["client_telephone"] == "+212600000001"
        assert order["quantite"] == 2500 and order["produit_type"] == "Sachets fond plat"

    def test_status_and_trace_fields_cannot_be_forced(self, db, alice, two_clients):
        order_id = alice.submit_order({
            "entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 10,
            "statut": "validee", "whatsapp_from": BOB, "source": "email", "client_id": 1, "email_id": "x",
            "validated_by": "pirate", "id": 1,
        })
        order = db.get_order(order_id)
        assert order["statut"] == "en_attente" and order["whatsapp_from"] == ALICE and order["source"] == "whatsapp"
        assert order["validated_by"] is None and order["email_id"] is None
        assert order_id not in two_clients.values()

    @pytest.mark.parametrize("missing", ["entreprise_cliente", "type_produit", "quantite"])
    def test_missing_required_field(self, alice, missing):
        draft = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 10}
        draft[missing] = None
        with pytest.raises(ValueError):
            alice.submit_order(draft)

    def test_same_message_sid_creates_one_order(self, db, alice):
        draft = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 10}
        first = alice.submit_order(draft, message_sid="SM1")
        assert alice.submit_order(draft, message_sid="SM1") == first
        assert alice.submit_order(draft, message_sid="SM2") != first

    def test_message_sid_of_another_sender_does_not_attach(self, db, alice, bob):
        draft = {"entreprise_cliente": "X", "type_produit": "Sachets fond plat", "quantite": 10}
        bob_order = bob.submit_order(draft, message_sid="SM9")
        assert alice.submit_order(draft, message_sid="SM9") != bob_order

    def test_order_number_of_another_sender_is_dropped(self, db, alice, two_clients):
        order_id = alice.submit_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                                       "quantite": 10, "numero_commande": "B-200"})
        assert order_id != two_clients["bob_pending"]
        order = db.get_order(order_id)
        assert order["numero_commande"] is None
        assert "B-200" in order["informations_supplementaires"]
        assert db.get_order(two_clients["bob_pending"])["quantite"] == 9000

    def test_reused_own_order_number_creates_a_new_order_flagged_for_review(self, db, alice, two_clients):
        order_id = alice.submit_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                                       "quantite": 10, "numero_commande": "A-100"})
        assert order_id != two_clients["alice_pending"]
        order = db.get_order(order_id)
        assert (order["numero_commande"], order["quantite"]) == (None, 10)
        assert "A-100" in order["informations_supplementaires"]
        assert any("A-100" in item and f"CMD-{two_clients['alice_pending']}" in item
                   for item in order["anomalies_list"])
        assert db.get_order(two_clients["alice_pending"])["quantite"] == 3000

    def test_metadata_fields_are_passed_through(self, db, alice, monkeypatch):
        received = {}
        monkeypatch.setattr(db, "create_order", lambda data: received.update(data) or 99)
        alice.submit_order({"entreprise_cliente": "S", "type_produit": "Sachets fond plat", "quantite": 1,
                            "message_brut": "texte", "langue": "fr", "anomalies": ["a"], "inconnu": "x"})
        assert received["message_brut"] == "texte" and received["anomalies"] == ["a"]
        assert "inconnu" not in received


class TestSearchCatalog:
    def test_without_knowledge_base_lists_the_catalog(self, alice):
        data = alice.search_catalog("vos produits ?")
        assert data["type"] == "catalog" and len(data["products"]) == 4
        assert data["products"][0] == {"number": 1, "product": "Sachets fond plat",
                                       "usages": ["sandwichs", "tacos", "viennoiseries"]}

    def test_cited_product_only(self, alice):
        data = alice.search_catalog("parlez-moi des poignées torsadées")
        assert [item["product"] for item in data["products"]] == ["Sac fond carré avec poignées torsadées"]

    def test_with_knowledge_answerer(self, db):
        class Answerer:
            def __init__(self):
                self.calls = []

            def answer(self, question, language, *, allowed_phones=()):
                self.calls.append((question, language, allowed_phones))
                return Answer("Réponse.", ("produits.md",), True)

        answerer = Answerer()
        tools = AssistantTools(db.connection, db, ALICE, answerer, language="darija")
        data = tools.search_catalog("  wach   kayn   sac kbir?  " + "x" * 1000)
        assert data == {"type": "answer", "text": "Réponse.", "grounded": True, "sources": ["produits.md"]}
        question, language, phones = answerer.calls[0]
        assert language == "darija" and phones == ("+212600000001",)
        assert question.startswith("wach kayn sac kbir?") and len(question) == 300


class TestToolSpecs:
    def test_names(self):
        assert [spec["function"]["name"] for spec in AssistantTools.tool_specs()] == list(TOOL_NAMES)

    def test_no_identity_parameter(self):
        forbidden = ("phone", "telephone", "tel", "client", "customer", "sender", "user", "id", "whatsapp",
                     "email", "numero", "number")
        for spec in AssistantTools.tool_specs():
            parameters = spec["function"]["parameters"]
            assert parameters["type"] == "object" and parameters["additionalProperties"] is False
            for name in parameters["properties"]:
                assert not any(word == name or name.startswith(word) for word in forbidden), name

    def test_no_write_tool(self):
        names = {spec["function"]["name"] for spec in AssistantTools.tool_specs()}
        assert not any(word in name for name in names for word in ("submit", "create", "update", "delete", "sql"))

    def test_specs_are_json_serializable(self):
        json.dumps(AssistantTools.tool_specs())


class TestExecute:
    def test_recent_orders_ignore_identity_arguments(self, alice, two_clients):
        result = alice.execute("my_recent_orders", {"limit": 50, "phone": BOB, "client": "Boulangerie Nour",
                                                    "whatsapp_from": BOB, "client_id": 2})
        assert result.ok and _references(result.data["orders"]) == {"A-100",
                                                                     f"CMD-{two_clients['alice_validated']}"}

    def test_order_status(self, alice, two_clients):
        found = alice.execute("my_order_status", {"reference": "A-100"})
        assert found.ok and found.data["order"]["reference"] == "A-100"
        other = alice.execute("my_order_status", {"reference": "B-200", "phone": BOB})
        assert other.ok and other.data["order"] is None and other.data["reference"] == "B-200"

    @pytest.mark.parametrize("arguments", [{}, {"reference": ""}, {"reference": 12}, {"reference": "x" * 1000}])
    def test_order_status_invalid_arguments(self, alice, arguments):
        result = alice.execute("my_order_status", arguments)
        assert not result.ok and result.error == "Référence de commande invalide."

    def test_last_validated_order(self, bob, two_clients):
        assert bob.execute("my_last_validated_order", {"phone": ALICE}).data["order"]["reference"] == "B-201"

    def test_search_catalog(self, alice):
        assert alice.execute("search_catalog", {"query": "sachets"}).data["type"] == "catalog"
        assert not alice.execute("search_catalog", {"query": "  "}).ok

    def test_request_human(self, alice):
        assert alice.execute("request_human", {}).data == {"handoff": True}

    @pytest.mark.parametrize("name", ["submit_order", "run_sql", "my_recent_orders ", "", None, 42,
                                      "__init__", "_orders", "conn"])
    def test_unknown_tool(self, alice, name):
        result = alice.execute(name, {})
        assert result == ToolResult(str(name)[:60], False, error="Outil inconnu.")

    @pytest.mark.parametrize("arguments", [None, "limit=5", ["limit"], 5])
    def test_non_mapping_arguments(self, alice, two_clients, arguments):
        assert alice.execute("my_recent_orders", arguments).ok

    @pytest.mark.parametrize("limit", ["5", None, True, 2.7, -1, 10 ** 12])
    def test_odd_limits(self, alice, two_clients, limit):
        result = alice.execute("my_recent_orders", {"limit": limit})
        assert result.ok and 1 <= len(result.data["orders"]) <= 2

    def test_tool_error_is_generic(self, alice, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("near \"SELECT\": syntax error in /app/tecpap/chatbot/tools.py")

        monkeypatch.setattr(alice, "my_recent_orders", broken)
        result = alice.execute("my_recent_orders", {})
        assert not result.ok and result.error == "L'outil n'a pas pu répondre."
        assert "SELECT" not in json.dumps(result.data) and "tools.py" not in result.error
