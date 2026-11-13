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


def test_split_pos_receipt():
    # Transcribed from the supplied photo; does not assert photo OCR accuracy.
    products = [
        ('SKS RED ONION 5LB-03383601038*', '11.98', '2 pc', '5.99/pc'),
        ('DRUMSTICK-111*', '5.79', '0.58 lb', '9.99/lb'),
        ('CILANTRO-36*', '1.00', '2 pc', '0.50/pc'),
        ('WINTERMELON-51*', '2.35', '1.18 lb', '1.99/lb'),
        ('French Beans-12*', '4.10', '1.37 lb', '2.99/lb'),
        ('DRUM STICK LEAVES-10*', '3.28', '0.41 lb', '7.99/lb'),
        ('CURRY LEAVES-58*', '0.99', '1 pc', '0.99/pc'),
        ('CURRY LEAVES-58*', '0.99', '1 pc', '0.99/pc'),
        ('Indian okra/Bhindi-76*', '3.71', '1.24 lb', '2.99/lb'),
        ('MINT LEAVES-59*', '0.99', '1 pc', '0.99/pc'),
        ('PEARL ONION LOOSE-96*', '6.46', '1.62 lb', '3.99/lb'),
        ('Indian Egg Plant/Round Brinjal-64*', '2.21', '0.74 lb', '2.99/lb'),
        ('THAI CHILLI-24*', '1.56', '0.39 lb', '3.99/lb'),
        ('CHINESE OKARA/BIRAKAYA-18*', '5.20', '1.74 lb', '2.99/lb'),
    ]
    text = "Sam's indian Grocery\n305 New Byhalia Rd, #103\nDATE/TIME: 11/12/2026 5:49:26 PM\nItem Count: 16\nWeight/pc Unit Price Total Price\n"
    text += '\n'.join(f'{name} ${amount}\n{qty} @ ${price}' for name, amount, qty, price in products)
    text += '\nSubtotal $50.61\nTax 1 $3.42\nGRAND TOTAL $54.03\nCheck $54.03\nRETURN POLICY'
    result, flags = parse(text)
    assert result['store'] == "Sam's indian Grocery"
    assert result['purchase_date'] == '2026-11-12'
    assert (result['subtotal'], result['tax'], result['total']) == ('50.61', '3.42', '54.03')
    assert len(result['items']) == 14
    assert result['items'][0] == dict(name='SKS RED ONION 5LB', quantity='2', unit_price='5.99', line_total='11.98', category='Groceries')
    assert result['items'][1]['quantity'] == '0.58'
    assert result['items'][1]['unit_price'] == '9.99'
    assert len([i for i in result['items'] if i['name'] == 'CURRY LEAVES']) == 2
    assert not any('do not match' in f for f in flags)
    assert not result['currency']  # A dollar symbol alone is ambiguous.


def test_split_product_and_summary_amounts():
    result, _ = parse('SHOP\nONION-123*\n$2.00\n2 pc @ $1.00/pc\nSubtotal\n$2.00\nTax 1\n$0.10\nGRAND TOTAL\n$2.10\nCheck\n$2.10')
    assert len(result['items']) == 1
    assert result['items'][0]['name'] == 'ONION'
    assert result['items'][0]['quantity'] == '2'
    assert (result['subtotal'], result['tax'], result['total']) == ('2.00', '0.10', '2.10')


def test_inline_quantity_and_item_mismatch():
    result, flags = parse('SHOP\nMilk 2 @ 3.00 $6.00\nSubtotal 7.00\nTax 0.00\nTotal 7.00')
    assert result['items'][0]['quantity'] == '2'
    assert result['items'][0]['unit_price'] == '3.00'
    assert any('do not match' in f for f in flags)
