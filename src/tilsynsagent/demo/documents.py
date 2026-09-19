"""Synthetic plan documents, so the grounding path can be demonstrated on
demand rather than when the register happens to produce the right change.

They go through the entire real path: written to disk, served over a
``file://`` doklink, fetched by documents/cache.py, parsed by pypdf, split
by the same CLAUSE_RE. Nothing is stubbed except the PDF content itself.

One template, ``standard``, numbers its clauses ``6.3``, the shape a
municipal plan document actually uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("tilsynsagent.demo.documents")

# Demo documents are written here and served as file:// URLs. Kept apart from
# the fetch cache so demo/reset.py can delete every generated document without
# touching real documents a run has already paid to fetch.
DEMO_DOCUMENT_DIRNAME = "demo-documents"

TEMPLATES = ("standard",)

# The clauses every demo document contains. Real Danish, and real in
# substance: each says something the five register fields cannot - a storey
# count in prose, a height with a Danish decimal comma, an explicit statement
# that a change of this kind creates nothing new for a reader to see.
#
# 6.2's comma (8,5) is load-bearing for demo 1: obs/grounded.py's
# clause_is_verbatim fails a quote that "tidies" it to 8.5, and that is
# exactly the failure a prompt without the verbatim instruction produces.
DEMO_CLAUSES: list[tuple[str, str]] = [
    (
        "1.1",
        "Lokalplanens formål er at fastlægge områdets anvendelse til boligformål "
        "og at sikre, at ny bebyggelse indpasses i det eksisterende bymiljø.",
    ),
    (
        "3.1",
        "Området må kun anvendes til boligformål i form af tæt-lav og etageboliger "
        "med tilhørende fælles friarealer.",
    ),
    (
        "6.2",
        "Bebyggelse må ikke opføres med en større højde end 8,5 m målt fra "
        "naturligt terræn til tagryg.",
    ),
    (
        "6.3",
        "Inden for delområde I må der opføres 2 bygninger i 2 etager med hver "
        "8 boliger. Bebyggelsesprocenten må ikke overstige 40.",
    ),
    (
        "7.1",
        "Bebyggelsens facader skal fremstå i blank mur, pudset mur eller træ i "
        "afdæmpede jordfarver.",
    ),
    (
        "9.2",
        "Ubebyggede arealer skal anlægges som fælles opholdsareal. Terrænregulering "
        "på mere end 0,5 m kræver kommunalbestyrelsens tilladelse.",
    ),
    (
        "11.1",
        "En ændring af planens administrative status medfører ikke ændringer i de "
        "bestemmelser, der gælder for områdets bebyggelse og anvendelse.",
    ),
]

# Explanatory prose surrounding the clauses. Deliberately about the same
# subjects as the clauses - planning, buildings, heights, zones - so
# retrieval has to distinguish a binding clause from a paragraph merely
# discussing the same topic, which is what it does on a real document.
_NARRATIVE_SECTIONS: list[tuple[str, list[str]]] = [
    (
        "Baggrund for lokalplanen",
        [
            "Kommunalbestyrelsen har besluttet at udarbejde denne lokalplan for at "
            "give mulighed for en omdannelse af området fra erhvervsformål til "
            "boligformål. Området har gennem en længere årrække været anvendt til "
            "lettere industri og lager, og de eksisterende bygninger fremstår i dag "
            "nedslidte og delvist uudnyttede.",
            "Lokalplanen skal sikre, at omdannelsen sker med respekt for det "
            "omgivende bymiljø, og at ny bebyggelse tilpasses den eksisterende "
            "bygningsstruktur i skala, materialer og udtryk. Planen fastlægger "
            "rammer for bebyggelsens omfang og placering, herunder bygningshøjder "
            "og etageantal, samt for de ubebyggede arealers indretning.",
            "Der er i forbindelse med planens udarbejdelse gennemført en screening "
            "for miljøvurdering. Kommunalbestyrelsen har på baggrund af screeningen "
            "vurderet, at planen ikke vil få væsentlig indvirkning på miljøet, og "
            "at der derfor ikke skal udarbejdes en egentlig miljøvurdering.",
        ],
    ),
    (
        "Lokalplanens forhold til anden planlægning",
        [
            "Området er i kommuneplanen udlagt til blandet bolig og erhverv. "
            "Lokalplanen er i overensstemmelse med kommuneplanens rammer for "
            "området, herunder rammerne for bebyggelsesprocent og bygningshøjde.",
            "Området ligger i byzone og forbliver i byzone med denne lokalplans "
            "vedtagelse. Der ændres ikke ved områdets zonestatus. En del af området "
            "er omfattet af en ældre byplanvedtægt, som aflyses for det område, "
            "denne lokalplan omfatter.",
            "Området er ikke omfattet af fredninger, og der er ikke registreret "
            "beskyttede naturtyper inden for planens afgrænsning. Der er ikke "
            "kendskab til jordforurening på matriklerne, men bygherre skal være "
            "opmærksom på reglerne om anmeldelse ved jordflytning.",
        ],
    ),
    (
        "Lokalplanens indhold",
        [
            "Lokalplanen giver mulighed for opførelse af boliger i form af tæt-lav "
            "og etagebebyggelse. Bebyggelsen placeres omkring et fælles gårdrum, "
            "der anlægges som opholdsareal for områdets beboere. Vejadgang sker fra "
            "den eksisterende adgangsvej mod nord.",
            "Bygningshøjder og etageantal er fastlagt med henblik på at sikre gode "
            "lysforhold i gårdrummet og en hensigtsmæssig overgang til den lavere "
            "bebyggelse mod syd. De nærmere bestemmelser herom fremgår af planens "
            "bestemmelser nedenfor.",
            "Parkering anlægges som terrænparkering langs adgangsvejen. Der stilles "
            "krav om anlæg af cykelparkering i tilknytning til hver boligopgang. "
            "Affaldshåndtering sker fra fælles affaldsøer placeret ved indkørslen "
            "til området.",
        ],
    ),
    (
        "Borgerinddragelse",
        [
            "Forslag til lokalplanen har været fremlagt i offentlig høring i otte "
            "uger. Der indkom i høringsperioden bemærkninger fra naboer vedrørende "
            "bebyggelsens højde og indbliksgener mod de tilstødende haver.",
            "Kommunalbestyrelsen har på baggrund af de indkomne bemærkninger "
            "justeret bestemmelserne om bebyggelsens placering, således at "
            "bygningskroppen mod syd trækkes yderligere tilbage fra skel. "
            "Bestemmelserne om bygningshøjde er fastholdt uændret.",
        ],
    ),
]


def demo_document_dir(base: Path | None = None) -> Path:
    from tilsynsagent.documents.cache import cache_dir

    return (base or cache_dir().parent) / DEMO_DOCUMENT_DIRNAME


def build_document(
    *,
    plan_id: int,
    template: str = "standard",
    directory: Path | None = None,
) -> Path:
    """Writes one synthetic plan PDF and returns its path.

    reportlab is a dev dependency, imported here rather than at module scope
    so an installed runtime without it can still import demo/ - the demo
    document builder is the only thing that needs it, and a production
    deployment never calls this.
    """
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; expected one of {TEMPLATES}")

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    directory = directory or demo_document_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"lokalplan-{plan_id}-{template}.pdf"

    styles = getSampleStyleSheet()
    body = ParagraphStyle("clause", parent=styles["BodyText"], fontSize=10, leading=14)
    heading = ParagraphStyle("h", parent=styles["Heading2"], fontSize=12, spaceAfter=4)

    story = [
        Paragraph(f"Lokalplan nr. {plan_id}", styles["Title"]),
        Paragraph("Redegørelse", heading),
    ]
    # The explanatory sections a real lokalplan carries before its
    # bestemmelser. Not decoration: extract.py refuses any document under
    # 2,000 characters of extractable text as a probable scan, and a
    # seven-clause document is well under that. More importantly, a plan
    # whose entire text is the seven clauses retrieval is looking for makes
    # retrieval look easy - the demo would be showing a search problem that
    # does not exist. Real documents are ~80,000 characters, of which the
    # relevant clause is one paragraph.
    for title, paragraphs in _NARRATIVE_SECTIONS:
        story.append(Paragraph(title, heading))
        for para in paragraphs:
            story.append(Paragraph(para, body))
            story.append(Spacer(1, 2 * mm))

    story.append(Paragraph("Bestemmelser", heading))
    story.append(Spacer(1, 4 * mm))
    for clause_id, text in DEMO_CLAUSES:
        story.append(Paragraph(f"{clause_id} {text}", body))
        story.append(Spacer(1, 3 * mm))

    SimpleDocTemplate(str(path), pagesize=A4, title=f"Lokalplan {plan_id}").build(story)
    return path


def doklink_for(path: Path) -> str:
    """The file:// URL the register would have carried for this document.

    documents/cache.py accepts file:// precisely so a demo document travels
    the same fetch/extract/split path a real one does - see that module.
    """
    return path.resolve().as_uri()


def build_demo_documents(
    plan_ids: list[int], *, template: str = "standard", directory: Path | None = None
) -> dict[int, str]:
    """Builds one document per plan and returns {plan_id: doklink}."""
    links = {}
    for plan_id in plan_ids:
        path = build_document(plan_id=plan_id, template=template, directory=directory)
        links[plan_id] = doklink_for(path)
    logger.info("built %d demo document(s), template=%s", len(links), template)
    return links


def clear_demo_documents(*, directory: Path | None = None) -> int:
    """Deletes generated demo documents. Returns how many were removed."""
    directory = directory or demo_document_dir()
    if not directory.exists():
        return 0
    removed = 0
    for path in directory.glob("*.pdf"):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("could not remove demo document %s: %s", path, exc)
    return removed
