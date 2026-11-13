from pathlib import Path
from app.ocr import validate,extract_text,parse
import pytest
from fastapi import HTTPException

@pytest.mark.parametrize('filename',['synthetic-receipt.png','synthetic-text.pdf','synthetic-scanned.pdf'])
def test_real_extraction(filename):
    data=Path('samples',filename).read_bytes(); mime=validate(data)
    result,flags=parse(extract_text(data,mime))
    assert result['total']=='20.00' and result['currency']=='USD'
    assert len(result['items'])==3
    assert [i['category'] for i in result['items']]==['Groceries','Household','Personal Care']

def test_missing_currency_flagged():
    result,flags=parse('SHOP\nMilk 4.50\nTotal 4.50')
    assert not result['currency'] and any('Currency' in f for f in flags)

def test_pdf_page_limit_and_encryption(monkeypatch):
    import io
    from pypdf import PdfReader,PdfWriter
    from app.config import settings
    source=PdfReader('samples/synthetic-text.pdf')
    writer=PdfWriter();writer.add_page(source.pages[0]);writer.add_page(source.pages[0])
    data=io.BytesIO();writer.write(data)
    monkeypatch.setattr(settings,'max_pages',1)
    with pytest.raises(HTTPException): validate(data.getvalue())
    writer=PdfWriter();writer.add_page(source.pages[0]);writer.encrypt('synthetic-password')
    encrypted=io.BytesIO();writer.write(encrypted)
    with pytest.raises(HTTPException): validate(encrypted.getvalue())

def test_size_limit(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'max_bytes',100)
    with pytest.raises(HTTPException): validate(Path('samples/synthetic-receipt.png').read_bytes())
