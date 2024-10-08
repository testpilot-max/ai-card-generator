from flask import Flask, request, jsonify, render_template, send_file, after_this_request
import fal_client
import os
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image
import requests
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import uuid
from reportlab.lib.utils import ImageReader
import time
import threading
import re

app = Flask(__name__)

# Load environment variables from .env file
load_dotenv()

# Now you can access your environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
FAL_KEY = os.getenv('FAL_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY)
fal_client = fal_client

# PDF buffer storage with timestamps and card details
pdf_buffers = {}

# Buffer lifetime in seconds (e.g., 1 hour)
BUFFER_LIFETIME = 3600

def cleanup_old_buffers():
    while True:
        current_time = time.time()
        to_delete = []
        for filename, (buffer_data, timestamp, _) in pdf_buffers.items():
            if current_time - timestamp > BUFFER_LIFETIME:
                to_delete.append(filename)
        for filename in to_delete:
            del pdf_buffers[filename]
        time.sleep(300)  # Check every 5 minutes

# Start the cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_buffers, daemon=True)
cleanup_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_card():
    data = request.json
    prompt = data.get('prompt')
    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400

    try:
        # OpenAI part
        messages = [
            {"role": "system", "content": """Analyze the following user prompt for greeting card generation and provide content suggestions. Use these categories and their associated keywords:

[Categories and keywords as previously listed]

For the user prompt, please:
1. Determine the category of the greeting card from the options above.
2. Identify the specific occasion or sentiment based on the keywords.
3. Extract any names or specific recipients mentioned.
4. Suggest a short, appropriate text for the front page of the card, following these guidelines:
   - The text should be 1-5 words long
   - It should be a common greeting or wish associated with the occasion
   - Do not include any specific names in this text
   - For general occasions, use a universal greeting
   - For specific holidays, use a traditional or popular greeting
5. Generate a brief, heartfelt message for the inside of the card, following these guidelines:
   - Keep it between 10-20 words
   - Include the recipient's name if provided
   - Make it personal and appropriate for the occasion
   - Express warm wishes or sentiments relevant to the category and occasion
   - Don't add [Your Name] at the end
6. Provide your analysis and suggestions in this format:
   Category: [Category]
   Occasion/Sentiment: [Occasion/Sentiment]
   Recipient(s): [Name(s) or 'None specified']
   Front Page Text: [Suggested text for the front page]
   Inside Message: [Suggested message for inside the card]
"""},
            {"role": "user", "content": prompt}
        ]

        completion = openai_client.chat.completions.create(
            model="gpt-4",
            messages=messages
        )

        openai_output = completion.choices[0].message.content

        # Parse OpenAI output
        lines = openai_output.split('\n')
        parsed_output = {}
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                parsed_output[key.strip()] = value.strip()

        # Generate front page
        front_result = fal_client.submit(
            "fal-ai/flux-pro",
            arguments={"prompt": f"A greeting card design with '{parsed_output['Front Page Text']}' as the main text. The design should be festive and appropriate for {parsed_output['Occasion/Sentiment']}. Include decorative elements and a border typical of greeting cards.","image_size": "portrait_4_3"}
        ).get()
        
        # Generate inside page
        inside_result = fal_client.submit(
            "fal-ai/flux-pro",
            arguments={"prompt": f"An inside page design for a greeting card. Include a decorative border or background suitable for {parsed_output['Occasion/Sentiment']}. Leave ample space in the center for the message: '{parsed_output['Inside Message']}'. The text should be clearly visible and nicely integrated into the design.","image_size": "portrait_4_3"}
        ).get()

        # Download images
        front_image_data = BytesIO(requests.get(front_result['images'][0]['url']).content)
        back_image_data = BytesIO(requests.get(inside_result['images'][0]['url']).content)
        front_img_reader = ImageReader(front_image_data)
        back_img_reader = ImageReader(back_image_data)

        # Create PDF in a buffer
        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=letter)
        c.drawImage(front_img_reader, 0, 0, width=letter[0], height=letter[1])
        c.showPage()
        c.drawImage(back_img_reader, 0, 0, width=letter[0], height=letter[1])
        c.save()

        # Store the buffer's content, not the buffer itself
        pdf_data = pdf_buffer.getvalue()

        # Generate a unique filename
        pdf_filename = f"greeting_card_{uuid.uuid4()}.pdf"

        # Store buffer data with timestamp and card details
        pdf_buffers[pdf_filename] = (pdf_data, time.time(), parsed_output)

        return jsonify({
            "front_image_url": front_result['images'][0]['url'],
            "inside_image_url": inside_result['images'][0]['url'],
            "pdf_url": f"/download_pdf/{pdf_filename}",
            "card_details": parsed_output
        })

    except Exception as e:
        app.logger.error(f"An error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download_pdf/<filename>')
def download_pdf(filename):
    if filename not in pdf_buffers:
        return jsonify({'error': 'PDF not found'}), 404
    
    pdf_data, _, card_details = pdf_buffers[filename]
    
    # Create a new BytesIO object from the stored data
    pdf_buffer = BytesIO(pdf_data)
    
    # Get the occasion from card details
    occasion = card_details.get('Occasion/Sentiment', 'greeting')
    
    # Clean the occasion string to make it suitable for a filename
    clean_occasion = re.sub(r'[^\w\-_\. ]', '_', occasion)
    clean_occasion = clean_occasion.replace(' ', '_').lower()
    
    # Create the download filename
    download_filename = f"greeting_card_{clean_occasion}.pdf"
    
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=download_filename
    )

if __name__ == '__main__':
    app.run(debug=True)