"""Generate only synthetic receipt fixtures; no real customer data."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
ROOT=Path(__file__).parent
TEXT='''GREENFIELD MARKET
SYNTHETIC RECEIPT - NOT A REAL PURCHASE
2026-11-01
Currency USD
Milk 2 x 2.25                 4.50
Paper towels 1 x 6.00         6.00
Shampoo 1 x 8.00              8.00
Subtotal                    18.50
Tax                          1.50
Discount                     0.00
Grand Total                 20.00
Thank you!
'''
def generate():
    image=Image.new('RGB',(1200,1400),'white'); draw=ImageDraw.Draw(image)
    path='/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
    try: font=ImageFont.truetype(path,33)
    except OSError: font=ImageFont.load_default(size=33)
    draw.multiline_text((65,90),TEXT,fill='black',font=font,spacing=35)
    image.save(ROOT/'synthetic-receipt.png'); image.save(ROOT/'synthetic-scanned.pdf','PDF')
    # Minimal valid PDF with embedded text, testing the non-OCR path.
    lines=['BT /F1 12 Tf 40 780 Td 20 TL']
    for i,line in enumerate(TEXT.splitlines()):
        if i: lines.append('T*')
        escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        lines.append(f'({escaped}) Tj')
    lines.append('ET'); stream='\n'.join(lines).encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream']
    output=bytearray(b'%PDF-1.4\n'); offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(output)); output.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=len(output); output.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    (ROOT/'synthetic-text.pdf').write_bytes(output)
if __name__=='__main__': generate()
