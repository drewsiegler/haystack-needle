from needle.pdftext import pdf_pages
from pdfs import make_pdf


def test_reads_each_page():
    pages = pdf_pages(make_pdf(["Staff Report", "Flock Safety lease\nsecond line"]))
    assert len(pages) == 2
    assert "Staff Report" in pages[0]
    assert "Flock Safety lease" in pages[1]


def test_max_pages():
    assert len(pdf_pages(make_pdf(["a", "b", "c"]), max_pages=2)) == 2


def test_not_a_pdf_gives_no_pages():
    assert pdf_pages(b"<html>Not found</html>") == []
    assert pdf_pages(b"%PDF-1.4 truncated") == []
