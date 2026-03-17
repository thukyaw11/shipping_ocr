import pytesseract
from PIL import Image
import ollama
import json
import os
import sys
import threading
import time
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from typing import List, Optional, Dict, Union
from google import genai
from tenacity import retry, stop_after_attempt, wait_exponential

# --- CONFIGURATION ---
# If you are on Windows, uncomment the line below and point it to your tesseract.exe
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Gemini API Configuration
const_api_key = "AIzaSyCpc_TZgWzQ2HtVZ67F9_c4AK1-RGc0Wto" # The execution environment provides the key at runtime
GEMINI_MODEL = "gemini-2.0-flash" 

# --- DATA MODELS ---

# Global config to prevent additionalProperties in Pydantic schema generation
model_config = ConfigDict(extra='forbid')

class DocumentInfo(BaseModel):
    model_config = model_config
    awb_number: Optional[str] = None
    airline_prefix: Optional[str] = None
    serial_number: Optional[str] = None
    document_status: Optional[str] = None
    copy_name: Optional[str] = None

class EntityInfo(BaseModel):
    model_config = model_config
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    account_number: Optional[str] = None

class IssuingAgent(BaseModel):
    model_config = model_config
    name: Optional[str] = None
    city: Optional[str] = None
    iata_code: Optional[str] = None

class Parties(BaseModel):
    model_config = model_config
    shipper: EntityInfo
    consignee: EntityInfo
    issuing_agent: IssuingAgent

class RoutingStep(BaseModel):
    model_config = model_config
    to: Optional[str] = None
    by_carrier: Optional[str] = None
    flight_number: Optional[str] = None
    date: Optional[str] = None

class RoutingAndDestination(BaseModel):
    model_config = model_config
    departure_airport: Optional[str] = None
    destination_airport: Optional[str] = None
    routing: List[RoutingStep]

class Declaration(BaseModel):
    model_config = model_config
    currency: Optional[str] = None
    charge_code: Optional[str] = None
    weight_valuation_charge: Optional[str] = None
    other_charges: Optional[str] = None
    declared_value_for_carriage: Optional[str] = None
    declared_value_for_customs: Optional[str] = None

class CargoDetails(BaseModel):
    model_config = model_config
    pieces: Optional[int] = None
    gross_weight: Optional[float] = None
    weight_unit: Optional[str] = None
    rate_class: Optional[str] = None
    chargeable_weight: Optional[float] = None
    rate_charge: Optional[Union[float, str]] = None
    total_weight_charge: Optional[Union[float, str]] = None
    nature_and_quantity_of_goods: Optional[str] = None
    total_volume_mc: Optional[float] = None
    dimensions: List[str]

class HandlingInformation(BaseModel):
    model_config = model_config
    special_notes: Optional[str] = None
    instruction: Optional[str] = None
    eap: Optional[str] = None
    rcar: Optional[str] = None

class TotalPrepaidSummary(BaseModel):
    model_config = model_config
    weight_charge: Optional[Union[float, str]] = None
    total_other_charges_due_agent: Optional[Union[float, str]] = None
    grand_total: Optional[Union[float, str]] = None

class AccountingAndCharges(BaseModel):
    model_config = model_config
    freight_prepaid: List[str]
    other_charges_breakdown: Dict[str, Optional[float]]
    total_prepaid_summary: TotalPrepaidSummary

class Execution(BaseModel):
    model_config = model_config
    shipper_signature_authority: Optional[str] = None
    execution_date: Optional[str] = None
    execution_place: Optional[str] = None
    carrier_signature_code: Optional[str] = None

class ExtractedInfo(BaseModel):
    model_config = model_config
    document_info: DocumentInfo
    parties: Parties
    routing_and_destination: RoutingAndDestination
    declaration: Declaration
    cargo_details: CargoDetails
    handling_information: HandlingInformation
    accounting_and_charges: AccountingAndCharges
    execution: Execution

# --- UI LOGIC ---

class LoadingSpinner:
    def __init__(self, message="Processing"):
        self.message = message
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._animate)

    def _animate(self):
        chars = ["-", "\\", "|", "/"]
        idx = 0
        while not self.stop_event.is_set():
            sys.stdout.write(f"\r{chars[idx % len(chars)]} {self.message}...")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.1)
        sys.stdout.write("\r" + " " * (len(self.message) + 25) + "\r")
        sys.stdout.flush()

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join()

# --- GEMINI CLIENT WRAPPER ---

def get_clean_schema_for_gemini(model):
    """Recursively removes additionalProperties from JSON schema."""
    schema = model.model_json_schema()
    
    def strip_unsupported(obj, defs=None):
        if defs is None:
            defs = schema.get('$defs', {})
            
        if isinstance(obj, dict):
            if '$ref' in obj:
                ref_key = obj['$ref'].split('/')[-1]
                return strip_unsupported(defs[ref_key], defs)
            
            new_obj = {}
            for k, v in obj.items():
                if k in ["additionalProperties", "title", "description", "$defs"]:
                    continue
                new_obj[k] = strip_unsupported(v, defs)
            return new_obj
        elif isinstance(obj, list):
            return [strip_unsupported(i, defs) for i in obj]
        return obj

    return strip_unsupported(schema)

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    reraise=True
)
def call_gemini_sdk(prompt: str):
    """Calls Gemini using the official google-genai SDK with a cleaned schema."""
    client = genai.Client(api_key=const_api_key)
    
    # Gemini requires a schema without 'additionalProperties' or '$ref'
    clean_schema = get_clean_schema_for_gemini(ExtractedInfo)
    
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': clean_schema,
            'system_instruction': 'You are a specialized logistics data extractor. Extract data from OCR text into valid JSON following the provided schema.'
        }
    )
    return response.text

# --- MAIN PROCESSING LOGIC ---

def process_file_to_json(file_path: str, engine: str):
    output_dir = "outputs"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if not os.path.exists(file_path):
        print(f"ERROR: File not found at {file_path}")
        return

    try:
        all_text = ""
        is_pdf = file_path.lower().endswith(".pdf")
        
        spinner = LoadingSpinner(f"Processing {os.path.basename(file_path)}")
        spinner.start()

        if is_pdf:
            from pdf2image import convert_from_path
            pages = convert_from_path(file_path)
            for i, page in enumerate(pages):
                page_text = pytesseract.image_to_string(page)
                all_text += f"\n--- Page {i+1} ---\n{page_text}"
        else:
            img = Image.open(file_path)
            all_text = pytesseract.image_to_string(img)
        
        spinner.stop()

        print("\n--- RAW PYTESSERACT TEXT ---")
        print(all_text)
        print("----------------------------\n")

        if not all_text.strip():
            print("WARNING: No text detected in the file.")
            all_text = "[No text detected]"

        spinner = LoadingSpinner(f"AI extracting data using {engine.upper()}")
        spinner.start()
        
        raw_json_response = ""
        if engine.lower() == "ollama":
            system_prompt = (
                'You are a specialized logistics data extractor for Air Waybills (AWB). '
                'Extract all details accurately into the requested detailed JSON format. '
                'IMPORTANT: If specific numeric fields (like rate_charge or grand_total) '
                'contain "AS ARRANGED" in the text, provide that string instead of a number. '
                'Specifically for "freight_prepaid", extract all numbers (like 20250703317) '
                'into a clean array of strings.'
            )
            user_prompt = f"Extract all detailed logistics and account data from this OCR text:\n\n{all_text}"
            response = ollama.chat(
                model='llama3',
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                format=ExtractedInfo.model_json_schema(),
            )
            raw_json_response = response.message.content
        else:
            user_prompt = f"Extract detailed logistics data from this OCR text:\n\n{all_text}"
            raw_json_response = call_gemini_sdk(user_prompt)

        spinner.stop()

        json_content = json.loads(raw_json_response)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.basename(file_path).rsplit('.', 1)[0]
        output_filename = f"{base_name}_{timestamp}.json"
        output_path = os.path.join(output_dir, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_content, f, indent=4)

        print(f"DONE: Data saved to: {output_path}")
        return json_content

    except Exception as e:
        if 'spinner' in locals(): spinner.stop()
        print(f"ERROR: An error occurred: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python ocr_to_json.py <path_to_image_or_pdf> <ollama|gemini>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    engine_choice = sys.argv[2]
    
    if engine_choice.lower() not in ["ollama", "gemini"]:
        print("Error: Engine must be either 'ollama' or 'gemini'")
        sys.exit(1)

    process_file_to_json(file_path, engine_choice)