"""Export app-owned sidecars; originals and unrelated metadata remain untouched."""

from pathlib import Path
from xml.etree import ElementTree as ET

from photo_metadata_agent.domain.schemas import Tags
from photo_metadata_agent.ingestion.local import atomic_write

X = "adobe:ns:meta/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC = "http://purl.org/dc/elements/1.1/"
for prefix, uri in (("x", X), ("rdf", RDF), ("dc", DC)):
    ET.register_namespace(prefix, uri)


def xmp_bytes(tags: Tags) -> bytes:
    root = ET.Element(f"{{{X}}}xmpmeta")
    rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
    description = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
    bag = ET.SubElement(ET.SubElement(description, f"{{{DC}}}subject"), f"{{{RDF}}}Bag")
    for keyword in tags.keywords():
        ET.SubElement(bag, f"{{{RDF}}}li").text = keyword
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_sidecar(job: dict, data_dir: Path) -> Path:
    if not job["reviews"]:
        raise ValueError("Review the tags before exporting metadata.")
    target = data_dir / "exports" / f"{job['asset_id']}.{job['id']}.xmp"
    atomic_write(target, xmp_bytes(Tags.model_validate(job["effective_tags"])))
    return target
