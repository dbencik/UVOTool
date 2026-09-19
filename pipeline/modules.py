from dataclasses import dataclass, field


@dataclass
class ModuleOutput:
    id: str
    label: str
    type: str  # "string", "number", "date", "boolean", "array"
    enabled: bool = True


@dataclass
class ModuleInput:
    id: str
    label: str
    type: str
    required: bool = True


@dataclass
class DataModule:
    id: str
    name: str
    description: str
    color: str
    primary_key: str
    inputs: list[ModuleInput]
    outputs: list[ModuleOutput]
    source_url: str
    rate_limit: float = 1.0
    cache_ttl: int = 30  # days

    def to_dict(self) -> dict:
        """Serialize for API/JSON."""
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "color": self.color, "primary_key": self.primary_key,
            "source_url": self.source_url,
            "rate_limit": self.rate_limit, "cache_ttl": self.cache_ttl,
            "inputs": [{"id": i.id, "label": i.label, "type": i.type, "required": i.required} for i in self.inputs],
            "outputs": [{"id": o.id, "label": o.label, "type": o.type, "enabled": o.enabled} for o in self.outputs],
        }


# ---------------------------------------------------------------------------
# Module definitions
# ---------------------------------------------------------------------------

ORSF = DataModule(
    id="orsf",
    name="ORSF — Obchodný register",
    description="Základné údaje o firme z Obchodného registra SR",
    color="#1971c2",
    primary_key="ico",
    source_url="https://api.orsf.sk/v1/companies/{ico}",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("nazov", "Názov firmy", "string"),
        ModuleOutput("status", "Stav subjektu", "string"),
        ModuleOutput("pravna_forma", "Právna forma", "string"),
        ModuleOutput("nace", "NACE kód", "string"),
        ModuleOutput("datum_vzniku", "Dátum vzniku", "date"),
        ModuleOutput("datum_zaniku", "Dátum zániku", "date"),
        ModuleOutput("adresa", "Adresa sídla", "string"),
        ModuleOutput("mesto", "Mesto", "string"),
        ModuleOutput("psc", "PSČ", "string"),
        ModuleOutput("velkost", "Veľkostná kategória", "string"),
        ModuleOutput("dic", "DIČ", "string"),
        ModuleOutput("icdph", "IČ DPH", "string"),
        ModuleOutput("pocet_aktivit", "Počet NACE aktivít", "number"),
    ],
)

RUZ = DataModule(
    id="ruz",
    name="RÚZ — Register účtovných závierok",
    description="Finančné údaje firmy z Registra účtovných závierok",
    color="#2b8a3e",
    primary_key="ico",
    source_url="https://www.registeruz.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("trzby_posledne", "Tržby (posledné)", "number"),
        ModuleOutput("trzby_predosle", "Tržby (predošlé)", "number"),
        ModuleOutput("zisk_posledne", "Zisk (posledné)", "number"),
        ModuleOutput("zisk_predosle", "Zisk (predošlé)", "number"),
        ModuleOutput("rok_zavierky", "Rok závierky", "number"),
    ],
)

RPVS = DataModule(
    id="rpvs",
    name="RPVS — Register partnerov VS",
    description="Koneční užívatelia výhod z Registra partnerov verejného sektora",
    color="#e8590c",
    primary_key="ico",
    source_url="https://rpvs.gov.sk/opendatav2/partneri",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("is_registered", "Je registrovaný", "boolean"),
        ModuleOutput("obchodne_meno", "Obchodné meno", "string"),
        ModuleOutput("ubos", "Koneční užívatelia výhod", "array"),
        ModuleOutput("opravnene_osoby", "Oprávnené osoby", "array"),
        ModuleOutput("platnost_od", "Platnosť od", "date"),
        ModuleOutput("platnost_do", "Platnosť do", "date"),
    ],
)

FS_DLZNICI = DataModule(
    id="fs_dlznici",
    name="FS — Dlžníci Finančnej správy",
    description="Kontrola dlhov voči Finančnej správe SR",
    color="#e03131",
    primary_key="ico",
    source_url="https://www.financnasprava.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("je_dlznik", "Je dlžník", "boolean"),
        ModuleOutput("dlh_suma", "Suma dlhu", "number"),
        ModuleOutput("typ_dlhu", "Typ dlhu", "string"),
    ],
)

SP_DLZNICI = DataModule(
    id="sp_dlznici",
    name="SP — Dlžníci Sociálnej poisťovne",
    description="Kontrola dlhov voči Sociálnej poisťovni",
    color="#c92a2a",
    primary_key="ico",
    source_url="https://www.socpoist.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("je_dlznik", "Je dlžník", "boolean"),
        ModuleOutput("dlh_suma", "Suma dlhu", "number"),
        ModuleOutput("obdobie", "Obdobie", "string"),
    ],
)

UVO = DataModule(
    id="uvo",
    name="UVO — Vestník verejného obstarávania",
    description="Parsing dokumentov z Vestníka UVO",
    color="#495057",
    primary_key="vestnik",
    source_url="https://www.uvo.gov.sk",
    inputs=[ModuleInput("vestnik", "Číslo vestníka", "string", required=True)],
    outputs=[
        ModuleOutput("documents", "Dokumenty", "array"),
        ModuleOutput("total_count", "Počet dokumentov", "number"),
    ],
)

TED = DataModule(
    id="ted",
    name="TED — Tenders Electronic Daily",
    description="Európske verejné obstarávanie z TED",
    color="#1864ab",
    primary_key="country",
    source_url="https://api.ted.europa.eu",
    inputs=[
        ModuleInput("country", "Krajina (ISO kód)", "string", required=True),
        ModuleInput("year", "Rok", "string", required=True),
    ],
    outputs=[
        ModuleOutput("documents", "Dokumenty", "array"),
        ModuleOutput("total_count", "Počet dokumentov", "number"),
    ],
)

ANALYZE = DataModule(
    id="analyze",
    name="Analýza — štatistiky obstarávania",
    description="Analytické štatistiky nad celou databázou vestníkov",
    color="#7048e8",
    primary_key="db",
    source_url="local DB",
    inputs=[],
    outputs=[
        ModuleOutput("single_bidder_rate", "Miera jedného uchádzača", "number"),
        ModuleOutput("top_vitazi", "Top víťazi", "array"),
        ModuleOutput("top_obstaravatelia", "Top obstarávatelia", "array"),
        ModuleOutput("cenove_anomalie", "Cenové anomálie", "array"),
    ],
)

GRAPH = DataModule(
    id="graph",
    name="Graf — sieťová analýza",
    description="Sieťová analýza vzťahov medzi obstarávateľmi a dodávateľmi",
    color="#9c36b5",
    primary_key="db",
    source_url="local DB",
    inputs=[],
    outputs=[
        ModuleOutput("nodes", "Uzly grafu", "array"),
        ModuleOutput("edges", "Hrany grafu", "array"),
        ModuleOutput("pagerank", "PageRank skóre", "array"),
        ModuleOutput("communities", "Komunity", "array"),
        ModuleOutput("self_dealing", "Self-dealing väzby", "array"),
    ],
)

FRSR_DPH = DataModule(
    id="frsr_dph",
    name="FR SR — DPH registrácie",
    description="DPH registrácia, IBAN účty, zrušenia a výmazy",
    color="#d6336c",
    primary_key="ico",
    source_url="https://www.financnasprava.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("ic_dph", "Číslo IČ DPH", "string"),
        ModuleOutput("iban_list", "Zoznam IBAN účtov", "array"),
        ModuleOutput("datum_registracie", "Dátum registrácie k DPH", "date"),
        ModuleOutput("druh_registracie", "Druh registrácie", "string"),
        ModuleOutput("je_zruseny", "Bola registrácia zrušená", "boolean"),
        ModuleOutput("datum_zrusenia", "Dátum zrušenia", "date"),
        ModuleOutput("je_vymazany", "Bol z DPH vymazaný", "boolean"),
        ModuleOutput("datum_vymazu", "Dátum výmazu", "date"),
    ],
)

FRSR_DANE = DataModule(
    id="frsr_dane",
    name="FR SR — Daňový profil",
    description="Daňoví dlžníci, registrované subjekty, DIČ",
    color="#c2255c",
    primary_key="ico",
    source_url="https://www.financnasprava.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("dic", "Daňové identifikačné číslo", "string"),
        ModuleOutput("je_registrovany", "Je registrovaný daňový subjekt", "boolean"),
        ModuleOutput("je_dlznik", "Je na zozname daňových dlžníkov", "boolean"),
        ModuleOutput("dlh_suma", "Výška dlhu v EUR", "number"),
        ModuleOutput("dlznik_nazov_match", "Spôsob matchovania (exact/fuzzy)", "string"),
    ],
)

FRSR_DPH_ODPOCTY = DataModule(
    id="frsr_dph_odpocty",
    name="FR SR — DPH odpočty",
    description="Nadmerné odpočty DPH a vlastná daňová povinnosť",
    color="#ae3ec9",
    primary_key="ico",
    source_url="https://www.financnasprava.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("ma_odpocty", "Firma žiada nadmerné odpočty", "boolean"),
        ModuleOutput("posledne_obdobie", "Posledné zdaňovacie obdobie", "string"),
        ModuleOutput("posledny_odpocet", "Posledný nadmerný odpočet", "number"),
        ModuleOutput("posledna_dan", "Posledná vlastná daňová povinnosť", "number"),
        ModuleOutput("celkovy_odpocet", "Celkový nadmerný odpočet", "number"),
        ModuleOutput("celkova_dan", "Celková vlastná daň", "number"),
        ModuleOutput("pocet_obdobi", "Počet zdaňovacích období", "number"),
        ModuleOutput("trend", "Ročný trend [{obdobie, odpocet, dan}]", "array"),
    ],
)

FRSR_SPOLAHLIV = DataModule(
    id="frsr_spolahliv",
    name="FR SR — Spoľahlivosť daňovníka",
    description="Index daňovej spoľahlivosti z Finančnej správy",
    color="#862e9c",
    primary_key="ico",
    source_url="https://www.financnasprava.sk",
    inputs=[ModuleInput("ico", "IČO", "string", required=True)],
    outputs=[
        ModuleOutput("ids_status", "Spoľahlivý / nespoľahlivý / neznámy", "string"),
        ModuleOutput("dic", "DIČ", "string"),
        ModuleOutput("nazov", "Názov subjektu", "string"),
    ],
)

WATCHDOG = DataModule(
    id="watchdog",
    name="Watchdog — monitoring sledovaných firiem",
    description="Monitoring nových zákaziek pre sledované firmy",
    color="#e67700",
    primary_key="watchlist",
    source_url="config/watchlist.json",
    inputs=[],
    outputs=[
        ModuleOutput("matches", "Zhody", "array"),
        ModuleOutput("total_matches", "Počet zhôd", "number"),
    ],
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

INPUT = DataModule(
    id="input",
    name="Vstup",
    description="Vstupné dáta — jedno IČO, zoznam IČO, alebo CSV súbor s IČO stĺpcom",
    color="#495057",
    primary_key="ico",
    inputs=[],
    outputs=[
        ModuleOutput("ico", "IČO", "string"),
        ModuleOutput("nazov", "Názov firmy", "string"),
        ModuleOutput("rok", "Rok", "number"),
        ModuleOutput("cpv", "CPV kód", "string"),
        ModuleOutput("vestnik", "Číslo vestníka", "string"),
        ModuleOutput("country", "Krajina (TED)", "string"),
    ],
    source_url="manuálny vstup / CSV upload",
    rate_limit=0,
    cache_ttl=0,
)

OUTPUT = DataModule(
    id="output",
    name="Výstup",
    description="Výstup pipeline — zobrazenie výsledkov, export, notifikácia",
    color="#1a1a1a",
    primary_key="",
    inputs=[
        ModuleInput("data", "Dáta", "object", required=True),
    ],
    outputs=[],
    source_url="dashboard / export",
    rate_limit=0,
    cache_ttl=0,
)

# Pipeline modules — only per-IČO data enrichment sources
# Analyze, Graph, Watchdog are global tools, not pipeline modules
MODULE_REGISTRY: dict[str, DataModule] = {
    m.id: m
    for m in [INPUT, ORSF, RUZ, RPVS, FS_DLZNICI, SP_DLZNICI, UVO, TED,
              FRSR_DPH, FRSR_DANE, FRSR_DPH_ODPOCTY, FRSR_SPOLAHLIV, OUTPUT]
}
