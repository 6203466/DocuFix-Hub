from flask import Flask, request, send_file, render_template
from PIL import Image, ImageColor
from rembg import remove
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
from pdf2docx import Converter
import io
import os
import tempfile

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB Max Upload Size

@app.route('/')
def index():
    return render_template('index.html')

# --- Helper Function ---
def convert_to_pixels(val, unit, dpi=300):
    val = float(val)
    if unit == 'cm': return int((val / 2.54) * dpi)
    elif unit == 'inch': return int(val * dpi)
    return int(val) 

# --- 1. IMAGE RESIZER & PAN PRESETS ---
@app.route('/api/resize', methods=['POST'])
def resize_image():
    if 'image' not in request.files: return "No image", 400
    file, unit = request.files['image'], request.form.get('unit', 'px')
    preset = request.form.get('preset')
    
    if preset == 'pan_photo': width, height = 213, 213
    elif preset == 'pan_sig': width, height = 400, 200
    else:
        width = convert_to_pixels(request.form.get('width', 800), unit)
        height = convert_to_pixels(request.form.get('height', 600), unit)
    
    try:
        img = Image.open(file.stream)
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        img_io = io.BytesIO()
        format_to_save = 'JPEG' if preset else (img.format if img.format else 'JPEG')
        if format_to_save == 'JPEG' and img.mode in ('RGBA', 'P'): img = img.convert('RGB')
        img.save(img_io, format=format_to_save, quality=90, dpi=(300, 300))
        img_io.seek(0)
        return send_file(img_io, mimetype=f'image/{format_to_save.lower()}', as_attachment=True, download_name=f"resized_{file.filename}")
    except Exception as e: return str(e), 500

# --- 2. BACKGROUND REPLACEMENT ---
@app.route('/api/remove-bg', methods=['POST'])
def remove_background():
    if 'image' not in request.files: return "No image", 400
    file, bg_color = request.files['image'], request.form.get('bg_color')
    bg_image_file = request.files.get('bg_image')
    
    try:
        img = Image.open(io.BytesIO(remove(file.read()))).convert("RGBA")
        if bg_image_file and bg_image_file.filename != '':
            bg_img = Image.open(bg_image_file.stream).convert("RGBA").resize(img.size, Image.Resampling.LANCZOS)
            bg_img.paste(img, (0, 0), img)
            img, ext, mime = bg_img.convert("RGB"), 'JPEG', 'image/jpeg'
        elif bg_color and bg_color != '#00000000': 
            try:
                bg = Image.new("RGBA", img.size, ImageColor.getrgb(bg_color))
                bg.paste(img, (0, 0), img)
                img, ext, mime = bg.convert("RGB"), 'JPEG', 'image/jpeg'
            except ValueError: ext, mime = 'PNG', 'image/png'
        else: ext, mime = 'PNG', 'image/png'

        img_io = io.BytesIO()
        img.save(img_io, format=ext)
        img_io.seek(0)
        return send_file(img_io, mimetype=mime, as_attachment=True, download_name=f"processed_{file.filename}")
    except Exception as e: return str(e), 500

# --- 3. TARGET KB MODIFIER (INCREASE/DECREASE) ---
@app.route('/api/modify-image-size', methods=['POST'])
def modify_image_size():
    if 'image' not in request.files: return "No image", 400
    file = request.files['image']
    target_bytes = float(request.form.get('target_kb', 50)) * 1024
    
    try:
        img = Image.open(file.stream)
        if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
        img_io = io.BytesIO()
        img.save(img_io, format='JPEG', quality=95)
        
        if img_io.tell() > target_bytes: # Compress
            quality = 95
            while img_io.tell() > target_bytes and quality > 10:
                quality -= 5
                img_io = io.BytesIO()
                img.save(img_io, format='JPEG', quality=quality)
            if img_io.tell() > target_bytes: # Scale down if still too big
                scale = 0.9
                while img_io.tell() > target_bytes and scale > 0.3:
                    resized = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
                    img_io = io.BytesIO()
                    resized.save(img_io, format='JPEG', quality=20)
                    scale -= 0.1
        elif img_io.tell() < target_bytes: # Inflate
            img_io = io.BytesIO()
            img.save(img_io, format='JPEG', quality=100, subsampling=0)
            scale = 1.1
            while img_io.tell() < target_bytes and scale < 3.0:
                resized = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
                img_io = io.BytesIO()
                resized.save(img_io, format='JPEG', quality=100, subsampling=0)
                scale += 0.2

        img_io.seek(0)
        return send_file(img_io, mimetype='image/jpeg', as_attachment=True, download_name=f"modified.jpg")
    except Exception as e: return str(e), 500

# --- 4. DOCUMENT CONVERSIONS ---
@app.route('/api/jpg-to-pdf', methods=['POST'])
def jpg_to_pdf():
    files = request.files.getlist('images')
    if not files: return "No images", 400
    try:
        images = [Image.open(f.stream).convert('RGB') for f in files]
        pdf_io = io.BytesIO()
        images[0].save(pdf_io, format='PDF', save_all=True, append_images=images[1:])
        pdf_io.seek(0)
        return send_file(pdf_io, mimetype='application/pdf', as_attachment=True, download_name="combined.pdf")
    except Exception as e: return str(e), 500

@app.route('/api/pdf-to-doc', methods=['POST'])
def pdf_to_doc():
    if 'pdf' not in request.files: return "No PDF", 400
    pdf_file = request.files['pdf']
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        pdf_file.save(temp_pdf.name)
        temp_pdf_path = temp_pdf.name
    temp_docx_path = temp_pdf_path.replace('.pdf', '.docx')
    
    try:
        cv = Converter(temp_pdf_path)
        cv.convert(temp_docx_path)
        cv.close()
        return_data = io.BytesIO()
        with open(temp_docx_path, 'rb') as fo: return_data.write(fo.read())
        return_data.seek(0)
        os.remove(temp_pdf_path)
        os.remove(temp_docx_path)
        return send_file(return_data, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name="converted.docx")
    except Exception as e: return str(e), 500

@app.route('/api/merge-pdf', methods=['POST'])
def merge_pdfs():
    files = request.files.getlist('pdfs')
    if len(files) < 2: return "Need 2+ PDFs", 400
    try:
        merger = PdfMerger()
        for pdf in files: merger.append(pdf)
        pdf_io = io.BytesIO()
        merger.write(pdf_io)
        merger.close()
        pdf_io.seek(0)
        return send_file(pdf_io, mimetype='application/pdf', as_attachment=True, download_name="merged.pdf")
    except Exception as e: return str(e), 500

@app.route('/api/compress-pdf', methods=['POST'])
def compress_pdf():
    if 'pdf' not in request.files: return "No PDF", 400
    try:
        reader = PdfReader(request.files['pdf'])
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        pdf_io = io.BytesIO()
        writer.write(pdf_io)
        pdf_io.seek(0)
        return send_file(pdf_io, mimetype='application/pdf', as_attachment=True, download_name="compressed.pdf")
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    # Bind to all interfaces and use port 5001 to avoid conflicts
    app.run(host='0.0.0.0', port=5001, debug=True)