from PIL import Image
import pypdfium2

PDF_DPI = 150


def pdf_to_images(data: bytes) -> list[Image.Image]:
    """Convert every page of a PDF to a PIL Image at PDF_DPI resolution."""
    doc = pypdfium2.PdfDocument(data)
    scale = PDF_DPI / 72  # pdfium renders at 72 dpi by default
    images = []
    for i, page in enumerate(doc):
        bitmap = page.render(scale=scale, rotation=0)
        images.append(bitmap.to_pil())
        print(f"[PDF] Converted page {i + 1}/{len(doc)}")
    return images
