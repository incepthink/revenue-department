"""The documents a new case is filed with (one slot each) and the fields read from each of them.

Single source of truth for the intake extraction schemas, the rules fallback and the labels on the results page.
Which slots are required is not decided here: the rules in force do (requirement_catalog.intake_slot, rci.intake.checks).
Field names that also exist in rci.pipeline.extract.fields for the slot's exhibit type reuse that rule when no LLM
is available.

Files uploaded without saying what they are get the slot AUTO; the intake identifies them (rci.intake.identify) as
one of SLOTS or as OTHER — any further document filed with the case (society registration, resolution, NOC, …).
"""
from __future__ import annotations

from dataclasses import dataclass

from rci.pipeline.extract.fields import FIELDS, FieldSpec

TENURES = ("occupancy_class_2", "govt_lease", "occupancy_class_1")


@dataclass(frozen=True)
class SlotField:
    name: str
    kind: str  # date | amount | area | cts | text | tenure (parsed like rci.pipeline.extract.fields.parse_value)
    label: str
    description: str


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    label_mr: str
    exhibit_type: str  # closest catalog exhibit type (for the rules fallback)
    multiple: bool
    what: str  # what the document is, for the model
    fields: tuple[SlotField, ...]
    # identification without an LLM (rci.intake.identify): (term, weight) found in the page text — a heading counts
    # three times — and words that give the document away in a filename
    keywords: tuple[tuple[str, float], ...] = ()
    filename_hints: tuple[str, ...] = ()

    def rule_specs(self) -> list[FieldSpec]:
        """Rules-extractor specs for the fields the pipeline already knows how to find without an LLM."""
        known = {s.name: s for s in FIELDS.get(self.exhibit_type, [])}
        return [known[f.name] for f in self.fields if f.name in known and known[f.name].rule is not None]


F = SlotField
CTS = F("cts_no", "cts", "CTS no.", "City survey (नगर भूमापन / CTS) number of the land")
SURVEY = F("survey_no", "text", "Survey / Gat no.", "Survey (स.नं.) or Gat (गट क्र.) number with hissa, exactly as written")
VILLAGE = F("village", "text", "Village", "Village (मौजे / गाव) name")
AREA = F("area_sqm", "area", "Area", "Land area with its unit (sq m / sq ft)")

SLOTS: tuple[Slot, ...] = (
    Slot("satbara", "7/12 extract", "७/१२ उतारा", "SEVEN_TWELVE", False,
         "a 7/12 extract (सातबारा उतारा, गाव नमुना सात व बारा) — the village land record",
         (SURVEY, VILLAGE,
          F("taluka", "text", "Taluka", "Taluka"),
          F("holder_name", "text", "Occupant (भोगवटादार)", "Occupant / holder recorded on the extract"),
          F("area", "text", "Area", "Area with its unit (e.g. हे. आर. / sq m), exactly as written"),
          F("tenure", "tenure", "Occupancy class", "Occupancy class (भोगवटादार वर्ग १ / वर्ग २) or government lease, exactly as written"),
          F("other_rights", "text", "Other rights (इतर हक्क)", "Entries in the other rights (इतर हक्क) column: loans, charges, restrictions, conditions"),
          F("issue_date", "date", "Issued on", "Date the extract was issued / printed")),
         (("7/12", 3), ("सातबारा", 3), ("सात बारा", 3), ("गाव नमुना सात", 3), ("गाव नमुना 7", 3), ("satbara", 3), ("भोगवटादार", 1), ("इतर हक्क", 1)),
         ("7-12", "7_12", "7x12", "satbara", "saatbara")),
    Slot("property_card", "Property card", "मिळकत पत्रिका", "PROPERTY_CARD", False,
         "a property card (मिळकत पत्रिका) issued by the City Survey office",
         (CTS, VILLAGE, AREA,
          F("holder_name", "text", "Holder (धारक)", "Holder (धारक) as recorded"),
          F("tenure", "tenure", "Tenure", "Tenure (धारणाधिकार / सत्ता प्रकार), exactly as written"),
          F("issue_date", "date", "Issued on", "Date the certified copy was issued")),
         (("मिळकत पत्रिका", 3), ("property card", 3), ("नगर भूमापन", 1), ("सत्ता प्रकार", 1)),
         ("property card", "property_card", "propertycard", "pr card", "milkat")),
    Slot("grant", "Original grant / allotment", "मूळ अनुदान / वाटप आदेश", "LEASE_SANAD", False,
         "the original grant, allotment order, lease deed or sanad by which the government gave the land to the society",
         (F("order_no", "text", "Order no.", "Order / sanad / reference number"),
          F("grant_date", "date", "Granted on", "Date of the grant / allotment / lease"),
          F("grantee", "text", "Granted to", "Who the land was granted / leased to"),
          F("issuing_authority", "text", "Issued by", "Authority that issued the grant"),
          CTS, SURVEY, AREA,
          F("tenure", "tenure", "Tenure", "Tenure created by the grant (occupancy class 2 / government lease), exactly as written"),
          F("conditions", "text", "Conditions", "Restrictive conditions, e.g. no transfer without the Collector's permission")),
         (("भाडेपट्टा करारनामा", 3), ("सनद", 3), ("sanad", 3), ("lease deed", 3), ("allotment", 3), ("वाटप आदेश", 3), ("अनुदान", 2), ("भाडेपट्टा देणार", 1)),
         ("grant", "allotment", "lease", "sanad")),
    Slot("application", "Application to the Collector", "जिल्हाधिकारी यांना अर्ज", "APPLICATION", False,
         "the society's written application addressed to the District Collector asking to convert the land to Occupancy Class-I",
         (F("addressed_to", "text", "Addressed to", "Addressee of the application (e.g. मा. जिल्हाधिकारी)"),
          F("society_name", "text", "Society", "Full name of the applicant cooperative housing society"),
          F("registration_no", "text", "Registration no.", "Society registration number"),
          F("application_date", "date", "Dated", "Date of the application"),
          CTS, SURVEY, VILLAGE, AREA,
          F("tenure", "tenure", "Present tenure", "Present tenure of the land as stated, exactly as written"),
          F("request", "text", "Request", "What the application asks for, in a few words copied from it"),
          F("signatory", "text", "Signed by", "Name (token) and designation of whoever signed the application")),
         (("अर्ज", 2), ("जिल्हाधिकारी", 2), ("application", 2), ("collector", 2), ("विनंती", 1), ("अर्जदार", 1)),
         ("application", "arj", "arja")),
    Slot("aadhaar", "Identity proof (Aadhaar)", "ओळखपत्र (आधार)", "OTHER", False,
         "an Aadhaar card of the person signing the application",
         (F("name", "text", "Name", "Name of the card holder (a token)"),),
         (("आधार", 3), ("aadhaar", 3), ("unique identification", 3), ("भारतीय विशिष्ट ओळख", 3)),
         ("aadhaar", "aadhar", "adhar")),
    Slot("tax_receipts", "Property tax receipts", "मालमत्ता कर पावत्या", "OTHER", True,
         "a property tax receipt issued by the municipal corporation / council / gram panchayat",
         (F("property_no", "text", "Property / assessment no.", "Property or assessment number"),
          F("payer", "text", "Paid by", "Name of the owner / payer"),
          F("period", "text", "Period", "Tax period or financial year covered, exactly as written"),
          F("receipt_date", "date", "Receipt date", "Date of the receipt / payment"),
          F("amount_paid", "amount", "Amount paid", "Amount paid"),
          F("arrears", "amount", "Arrears outstanding", "Arrears / balance still outstanding after this payment")),
         (("मालमत्ता कर", 3), ("property tax", 3), ("कर पावती", 3), ("घरपट्टी", 3), ("tax receipt", 3), ("पावती", 1)),
         ("tax", "receipt", "pavti", "gharpatti")),
    Slot("index_ii", "Index II", "सूची क्र. २", "OTHER", False,
         "an Index II (सूची क्र. २) issued by the Sub-Registrar for the last registered transaction on the property",
         (F("document_no", "text", "Document no.", "Registered document number (दस्त क्रमांक)"),
          F("registration_date", "date", "Registered on", "Date of registration"),
          F("sub_registrar", "text", "Sub-Registrar", "Sub-Registrar office"),
          F("transaction", "text", "Transaction", "Nature of the document (e.g. conveyance, sale deed)"),
          F("parties", "text", "Parties", "Parties to the transaction"),
          CTS, SURVEY,
          F("consideration", "amount", "Consideration", "Consideration / market value stated")),
         (("सूची क्र. 2", 3), ("सूची क्रमांक 2", 3), ("सूची क्र.2", 3), ("index ii", 3), ("index-ii", 3), ("index 2", 3), ("दुय्यम निबंधक", 2), ("sub registrar", 2)),
         ("index", "suchi")),
)

AUTO = "auto"  # uploaded without a slot: the intake identifies the document first

# Anything else filed with the case. Only the land identifiers are read (they join the cross-document checks); the
# scrutiny adds what the document is, a summary and whether it concerns this application at all.
OTHER = Slot("other", "Additional document", "इतर कागदपत्र", "OTHER", True,
             "a document that is none of the standard filing documents (7/12 extract, property card, original grant, "
             "application to the Collector, Aadhaar card, property tax receipt, Index II)",
             (F("society_name", "text", "Society", "Name of the cooperative housing society, if the document names one"),
              F("document_date", "date", "Dated", "Date of the document"),
              CTS, SURVEY, VILLAGE, AREA))

BY_KEY: dict[str, Slot] = {s.key: s for s in (*SLOTS, OTHER)}


def slot_catalog() -> list[dict]:
    return [{"key": s.key, "label": s.label, "label_mr": s.label_mr, "multiple": s.multiple}
            for s in (*SLOTS, OTHER)]
