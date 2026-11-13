"""Made-up invoice PDFs for trying the import with: `scripts/dev.sh sample-invoices`.

Written into "Sample invoices" in the dev stack's invoice folder. Every vendor, number and amount
here is invented. The names are deliberately mixed (tidy, a scanner's, a download's) because that is
what the import has to cope with; the text inside is what it reads. Standard library only.
"""
import sys
from pathlib import Path

FOLDER = "Sample invoices"
# (path below the folder, the lines printed on the page)
INVOICES = [
    ("261024 - Tyres - Example Tyres.pdf", [
        "Example Tyres Pty Ltd", "TAX INVOICE 10482", "Invoice Date: 24/10/2026", "",
        "4 x 235/45R18 tyres fitted and balanced", "Odometer 45,120 km", "", "Total   $1,234.50"]),
    ("scan-0007.pdf", [
        "Sample Alignment Centre Pty Ltd", "Tax Invoice 7731", "Invoice Date: 02/09/2026", "",
        "Wheel alignment, four wheels", "Odometer: 43,870 km", "", "Total   $129.00"]),
    ("Insurance/Certificate of insurance.pdf", [
        "Sample Mutual Insurance Limited", "Certificate of Insurance", "Date of issue: 05/01/2026", "",
        "Comprehensive car insurance, 12 months", "", "Total premium   $1,480.40"]),
    ("Registration/renewal-notice.pdf", [
        "Department of Example Transport", "Registration renewal notice", "Invoice Date: 14/03/2026", "",
        "Vehicle registration renewal", "", "Total due   6 months $412.35   12 months $798.60"]),
    ("download (3).pdf", [
        "Example Auto Parts Pty Ltd", "Tax Invoice A-20991", "Invoice Date: 18/07/2026", "",
        "Cabin air filter x 1", "", "Total   $64.95"]),
    ("Maintenance/260611 Tyre rotation - Example Tyres.pdf", [
        "Example Tyres Pty Ltd", "TAX INVOICE 9877", "Invoice Date: 11/06/2026", "",
        "Tyre rotation and pressure check", "Odometer 41,205 km", "", "Total   $45.00"]),
    ("brochure.pdf", ["Example Tyres: summer catalogue", "Nothing to pay. Not an invoice."]),
]


def pdf(lines):
    """A one-page PDF with real text in it, so the app's text extraction has something to read."""
    def esc(text):
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    body = "BT /F1 12 Tf 72 740 Td 18 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    stream = body.encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def main(root):
    top = Path(root) / FOLDER
    for name, lines in INVOICES:
        target = top / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pdf(lines))
    print(f"dev: {len(INVOICES)} sample invoices in {top}")


if __name__ == "__main__":
    main(sys.argv[1])
