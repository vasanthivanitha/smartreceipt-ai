"""Conservative receipt heuristics: every extraction must be reviewed by a person."""
import io, re
from decimal import Decimal, InvalidOperation
from PIL import Image, ImageOps
from pypdf import PdfReader
import pypdfium2 as pdfium
import pytesseract
from fastapi import HTTPException
from .config import settings

Image.MAX_IMAGE_PIXELS = 25_000_000
CATEGORIES = ['Groceries','Household','Personal Care','Electronics','Uncategorized']
def category(name):
    name=name.lower()
    for words, result in [(['milk','bread','apple','egg','banana','coffee'],'Groceries'),
        (['towel','detergent','soap dish','cleaner'],'Household'),
        (['shampoo','toothpaste','deodorant'],'Personal Care'),(['cable','charger','battery'],'Electronics')]:
        if any(w in name for w in words): return result
    return 'Uncategorized'
def validate(data):
    if not data or len(data)>settings.max_bytes: raise HTTPException(400,'File is empty or exceeds the upload size limit.')
    if data.startswith(b'%PDF-'):
        try:
            pdf=PdfReader(io.BytesIO(data), strict=True)
            if pdf.is_encrypted or not 1<=len(pdf.pages)<=settings.max_pages: raise ValueError()
            # Force page parsing before accepting.
            for page in pdf.pages: _ = page.mediabox
        except Exception: raise HTTPException(400,'Invalid, encrypted, or oversized PDF.')
        return 'application/pdf'
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('JPEG','PNG') or im.width*im.height>25_000_000: raise ValueError()
            mime={'JPEG':'image/jpeg','PNG':'image/png'}[im.format]; im.verify()
        return mime
    except Exception: raise HTTPException(400,'Upload a valid JPG, PNG, or PDF.')
def image_text(image):
    return pytesseract.image_to_string(ImageOps.exif_transpose(image).convert('RGB'), timeout=40)
def extract_text(data,mime):
    if mime!='application/pdf':
        with Image.open(io.BytesIO(data)) as im: return image_text(im)
    reader=PdfReader(io.BytesIO(data)); doc=None; texts=[]
    try:
        for i,page in enumerate(reader.pages):
            text=page.extract_text() or ''
            if len(text.strip())<30:
                if doc is None: doc=pdfium.PdfDocument(data)
                p=doc[i]
                if p.get_width()*p.get_height()*4>25_000_000: raise ValueError('Page too large')
                bitmap=p.render(scale=2)
                try: text=image_text(bitmap.to_pil())
                finally: bitmap.close(); p.close()
            texts.append(text)
    finally:
        if doc: doc.close()
    return '\n'.join(texts)

def parse(text):
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    result={'store':lines[0][:150] if lines else '', 'purchase_date':'', 'currency':'',
        'subtotal':None,'tax':None,'discount':None,'total':None,'items':[]}
    flags=['Review all extracted fields and item categories before confirming.']
    match=re.search(r'\b(20\d{2})[-/](\d{2})[-/](\d{2})\b',text)
    if match: result['purchase_date']='-'.join(match.groups())
    else:
        match=re.search(r'\b(\d{2})/(\d{2})/(20\d{2})\b',text)
        if match: result['purchase_date']=f'{match[3]}-{match[1]}-{match[2]}'
    currencies=[c for c in ['USD','EUR','GBP','CAD','AUD','JPY','CHF'] if re.search(r'\b'+c+r'\b',text)]
    if len(currencies)==1: result['currency']=currencies[0]
    else: flags.append('Currency is missing or ambiguous; select it explicitly.')
    for line in lines[1:]:
        amount=re.search(r'(-?\d+[.,]\d{2})\s*$',line)
        if not amount: continue
        value=str(Decimal(amount[1].replace(',','.')))
        label=line[:amount.start()].strip(' .$€£:')
        low=label.lower()
        field=None
        if re.search(r'\b(subtotal|sub total)\b',low): field='subtotal'
        elif re.search(r'\btax\b',low): field='tax'
        elif re.search(r'\b(discount|savings|coupon)\b',low): field='discount'; value=str(abs(Decimal(value)))
        elif re.search(r'\b(total|amount due|balance due)\b',low): field='total'
        elif re.search(r'\b(cash|change|visa|mastercard|payment|tender)\b',low): continue
        if field:
            if result[field] is not None: flags.append(f'Multiple {field} values detected.')
            result[field]=value
        elif label:
            qty=re.search(r'\s+(\d+(?:\.\d+)?)\s*(?:x|@)\s*(\d+\.\d{2})$',label,re.I)
            result['items'].append({'name':label[:qty.start()].strip() if qty else label[:200],
                'quantity':qty[1] if qty else '', 'unit_price':qty[2] if qty else '',
                'line_total':value, 'category':category(label)})
    for key in ['store','purchase_date','subtotal','tax','discount','total']:
        if result[key] in (None,''): flags.append(f'{key.replace("_"," ").title()} needs review.')
    return result,flags
