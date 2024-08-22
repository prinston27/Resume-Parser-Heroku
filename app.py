import openai
import oracledb
import requests
import os
import json
import magic
from PyPDF2 import PdfReader
import docx2txt
import chardet
from flask import Flask, request, jsonify

app = Flask(__name__)

# Setting up OpenAI API Key from environment variable
api_key = os.getenv("OPENAI_API_KEY")
openai.api_key = api_key

# PDF.co API key [Using this to format the JSON response from ChatGPT API]
pdfco_api_key = os.getenv("PDFCO_API_KEY")

# OracleDB Connection details from environment variables
oracle_user = os.getenv("ORACLE_USER")
oracle_password = os.getenv("ORACLE_PASSWORD")
oracle_host = os.getenv("ORACLE_HOST")
oracle_port = os.getenv("ORACLE_PORT", 1521)
oracle_service_name = os.getenv("ORACLE_SERVICE_NAME")

dsn_tns = oracledb.makedsn(oracle_host, oracle_port, service_name=oracle_service_name)

# Establish OracleDB connection
try:
    connection = oracledb.connect(user=oracle_user, password=oracle_password, dsn=dsn_tns)
    print("Connected to Oracle DB successfully")
except oracledb.DatabaseError as e:
    print(f"Database connection failed: {str(e)}")
    connection = None

# Function to fetch a specific HTML template by ID from PDF.co
def fetch_html_template_by_id(api_key, template_id):
    url = f"https://api.pdf.co/v1/templates/html/{template_id}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching template by ID: {e}")
        return None

# Function to format document using PDF.co
def format_document_with_pdfco(api_key, json_data, template):
    url = "https://api.pdf.co/v1/pdf/convert/from/html"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key
    }
    
    json_string = json.dumps(json_data)
    
    payload = {
        "html": template['body'],
        "templateData": json_string,
        "outputFormat": "pdf"
    }
    try:
        print(f"Sending payload: {json.dumps(payload, indent=4)}")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        
        response_json = response.json()
        print(f"Response JSON: {response_json}")

        pdf_url = response_json['url']
        pdf_response = requests.get(pdf_url)
        pdf_response.raise_for_status()

        return pdf_response.content
    except requests.exceptions.RequestException as e:
        print(f"Error formatting document: {e}")
        return None

# Function to process CV with ChatGPT
def process_cv_with_chatgpt(cv_text):
    prompt = (
    """Rewrite the attached resume in the following strict JSON format. Do not deviate from the structure provided:
    ...[truncated for brevity]...
    """
    )

    headers = {
        'Authorization': f'Bearer {openai.api_key}',
        'Content-Type': 'application/json'
    }

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "user", "content": f"{prompt}\n\n{cv_text}"}
        ],
        "max_tokens": 2000
    }

    response = requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=data)

    if response.status_code == 200:
        raw_response = response.json()["choices"][0]["message"]["content"]
        print(f"Raw Response: {raw_response}")

        try:
            cleaned_response = json.loads(raw_response)
            return cleaned_response
        except json.JSONDecodeError:
            print("JSON decoding failed: Raw response may be incomplete or malformed.")
            return None
    else:
        print(f"Request failed with status code {response.status_code}: {response.text}")
        return None

# Function to extract text from different file types
def extract_text_from_file(file_path, file_extension):
    text = ""
    try:
        if file_extension == ".pdf":
            with open(file_path, "rb") as file:
                reader = PdfReader(file)
                for page in reader.pages:
                    text += page.extract_text()
        elif file_extension == ".docx":
            text = docx2txt.process(file_path)
        elif file_extension in [".txt", ".csv"]:
            with open(file_path, "rb") as file:
                result = chardet.detect(file.read())
                encoding = result['encoding']
            with open(file_path, "r", encoding=encoding) as file:
                text = file.read()
        else:
            print(f"Unsupported file type: {file_extension}")
    except Exception as e:
        print(f"Failed to extract text from {file_path}: {e}")
    
    return text

# Flask route to process the CVs
@app.route('/process-cv', methods=['POST'])
def process_cv():
    try:
        # Get the uploaded file from the request
        uploaded_file = request.files['file']
        filename = uploaded_file.filename
        
        # Determine file extension
        mime = magic.Magic(mime=True)
        detected_mime_type = mime.from_buffer(uploaded_file.read(1024))
        uploaded_file.seek(0)  # Reset file pointer after reading for mime type detection

        if detected_mime_type == "application/pdf":
            file_extension = ".pdf"
        elif detected_mime_type == "application/msword":
            file_extension = ".doc"
        elif detected_mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            file_extension = ".docx"
        elif detected_mime_type == "text/plain":
            file_extension = ".txt"
        else:
            file_extension = ".bin"  # Defaults to .bin for unknown types

        # Save the uploaded file temporarily
        file_path = os.path.join(os.path.expanduser("~"), "Downloads", f"temp_uploaded_cv{file_extension}")
        uploaded_file.save(file_path)
        
        # Extract text from the file
        cv_text = extract_text_from_file(file_path, file_extension)

        if not cv_text.strip():
            return jsonify({"error": "No text could be extracted from the uploaded file."}), 400

        # Process the CV with ChatGPT
        processed_cv_json = process_cv_with_chatgpt(cv_text)
        if not processed_cv_json:
            return jsonify({"error": "Failed to process the CV with ChatGPT."}), 500

        # Fetch template and format the document
        template_id = 2993
        template = fetch_html_template_by_id(pdfco_api_key, template_id)
        if not template:
            return jsonify({"error": "Failed to fetch HTML template."}), 500

        formatted_doc = format_document_with_pdfco(pdfco_api_key, processed_cv_json, template)
        if not formatted_doc:
            return jsonify({"error": "Failed to format the document."}), 500

        # Save the formatted document
        formatted_doc_path = os.path.join(os.path.expanduser("~"), "Downloads", f"Formatted_CV{file_extension}")
        with open(formatted_doc_path, "wb") as file:
            file.write(formatted_doc)
        
        return jsonify({"message": f"Formatted CV saved successfully as '{formatted_doc_path}'."}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)

