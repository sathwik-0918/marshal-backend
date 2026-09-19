import fitz


def extract_text(pdf_bytes: bytes, filename: str = "document.pdf") -> str:
    """
    PyMuPDF text-layer extraction only — covers typed/exported PDFs,
    which is the realistic case for organizer policies and venue docs.
    Scanned/image PDFs need an OCR tier (Tesseract) on top of this,
    deliberately not pulled in this pass — real added system
    dependency (a non-Python binary) for a case that likely isn't the
    common one here. Straightforward to add later if it comes up.
    """
    text_parts = []
    try:
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in pdf:
            text_parts.append(page.get_text("text"))
        pdf.close()
    except Exception as e:
        print(f"[knowledge] Failed to extract {filename}: {e}")
        return ""
    return "\n\n".join(text_parts)