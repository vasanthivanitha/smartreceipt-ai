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
    for words, result in [(['milk','bread','apple','egg','banana','coffee','onion','drumstick','cilantro','wintermelon','beans','leaves','okra','brinjal','chilli'],'Groceries'),
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
    image = ImageOps.exif_transpose(image).convert('L')
    # Small phone photos lose punctuation and thin characters at native size.
    # Bound enlargement so decoded images stay within the existing pixel limit.
    scale = min(3, 1600 / image.width, (25_000_000 / (image.width * image.height)) ** .5)
    if scale > 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    return pytesseract.image_to_string(ImageOps.autocontrast(image), timeout=40)
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
    # POS receipts often print name + line total, then quantity + unit price.
    money = r'-?\d+[.,]\d{2}'
    detail = re.compile(r'^\s*(\d+(?:[.,]\d+)?)\s*(?:lb|lbs|kg|g|pc|pcs)?\s*(?:@|x)\s*[$€£]?\s*(' + money + r')(?:\s*/\s*(?:lb|lbs|kg|g|pc|pcs))?\s*$', re.I)
    summary_started = False
    pending_field = None
    pending_name = None
    for line in lines[1:]:
        qty = detail.fullmatch(line)
        if qty:
            if result['items'] and not summary_started:
                result['items'][-1].update(quantity=qty[1].replace(',', '.'), unit_price=qty[2].replace(',', '.'))
            continue
        # Unit-price fragments must never become purchased items.
        if re.search(r'/\s*(lb|lbs|kg|g|pc|pcs)\s*$', line, re.I):
            continue
        amount = re.search(r'[$€£]?\s*(' + money + r')\s*$', line)
        label = line[:amount.start()].strip(' .$€£:') if amount else line.strip(' .$€£:')
        low = label.lower()
        field = None
        if re.search(r'\b(subtotal|sub total)\b', low): field = 'subtotal'
        elif re.search(r'\btax\b', low): field = 'tax'
        elif re.search(r'\b(discount|savings|coupon)\b', low): field = 'discount'
        elif re.search(r'\b(total|amount due|balance due)\b', low): field = 'total'
        # Header columns are not a grand total.
        if re.search(r'unit price|total price|weight/pc|item count', low):
            pending_name = None
            continue
        if field:
            summary_started = True
            pending_name = None
            if not amount:
                pending_field = field
                continue
        elif re.search(r'\b(cash|change|visa|mastercard|payment|tender|check|return policy)\b', low):
            pending_name = None
            pending_field = None
            continue
        if not amount:
            # Only retain a standalone product name if it has a POS product code.
            # This avoids treating addresses, cashier information or policy as items.
            pending_name = label if not summary_started and re.search(r'-\d+\*?$', label) else None
            continue
        value = str(Decimal(amount[1].replace(',', '.')))
        if not label and pending_field:
            field, pending_field = pending_field, None
        if field:
            pending_field = None
            if field == 'discount': value = str(abs(Decimal(value)))
            if result[field] is not None: flags.append(f'Multiple {field} values detected.')
            result[field] = value
            continue
        if summary_started:
            continue
        label = label or pending_name or ''
        pending_name = None
        if not label or not re.search(r'[a-zA-Z]', label):
            continue
        inline_qty = re.search(r'\s+(\d+(?:\.\d+)?)\s*(?:x|@)\s*[$€£]?(\d+\.\d{2})$', label, re.I)
        name = label[:inline_qty.start()].strip() if inline_qty else label
        name = re.sub(r'-\d+\*?$', '', name).strip()
        result['items'].append({'name': name[:200],
            'quantity': inline_qty[1] if inline_qty else '',
            'unit_price': inline_qty[2] if inline_qty else '',
            'line_total': value, 'category': category(name)})
    if result['items'] and result['subtotal'] is not None:
        if sum(Decimal(item['line_total']) for item in result['items']) != Decimal(result['subtotal']):
            flags.append('Extracted item totals do not match subtotal; check missing or misread items.')
    for key in ['store','purchase_date','subtotal','tax','discount','total']:
        if result[key] in (None,''): flags.append(f'{key.replace("_"," ").title()} needs review.')
    return result,flags
