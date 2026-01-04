import os
import json
import uuid
import time
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import requests
from pymongo import MongoClient
from bson import json_util
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
ALLOWED_RESUME_EXTENSIONS = {'pdf', 'docx', 'png', 'jpg', 'jpeg'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# DB Setup
client = MongoClient(os.getenv('MONGODB_URI'))
db = client[os.getenv('DATABASE_NAME', 'recruitment_db')]
jobs_collection = db['jobs']
candidates_collection = db['candidates']

# Open Router Configuration
llm_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

#Helper function

def mongo_to_json(data):
    """Converts MongoDB BSON (ObjectIds, Dates) to standard JSON."""
    return json.loads(json_util.dumps(data))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS

#Text extraction
def extract_text_from_resume(file_path):
    """
    Extract text from resume using LLMWhisperer API v2.
    Uses binary upload as per official documentation.
    """
    api_key = os.getenv('LLMWHISPERER_API_KEY')
    base_url = "https://llmwhisperer-api.us-central.unstract.com/api/v2"
    
    if not api_key:
        return None, "LLMWHISPERER_API_KEY is missing in .env"
    headers = {'unstract-key': api_key}
    
    try:
        # Step 1: Upload process
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        filename = os.path.basename(file_path)
        
        # Parameters - use v2 according to documentations
        #REFER https://docs.unstract.com/llmwhisperer/llm_whisperer/apis/llm_whisperer_text_extraction_api/
        params = {
            'mode': 'high_quality',  # Best for resumes with OCR support
            'output_mode': 'layout_preserving',  # Optimized for LLM consumption
            'page_seperator': '<<<'  # Page separator
        }
        
        print(f"Uploading {filename} to LLMWhisperer...")
        print(f"   File size: {len(file_data)} bytes")
        
        # Send as binary data
        response = requests.post(
            f"{base_url}/whisper",
            headers=headers,
            params=params,
            data=file_data,  # Binary content,  not multipart (PDF)
            timeout=30
        )
        
        print(f"Response Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        # Check for HTML error responses
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            return None, f"API returned HTML error. Status: {response.status_code}. Check your API key."
        
        # Error code handling
        if response.status_code == 401:
            return None, "Authentication failed. Check your LLMWHISPERER_API_KEY in .env file."
        elif response.status_code == 402:
            return None, "Payment required. Your LLMWhisperer quota may be exhausted."
        elif response.status_code == 415:
            return None, f"Unsupported media type. The API doesn't accept this file format."
        elif response.status_code not in [200, 202]: #Documentations says 202 
            try:
                error_data = response.json()
                error_msg = error_data.get('message', response.text[:200])
            except:
                error_msg = response.text[:200]
            return None, f"Upload failed ({response.status_code}): {error_msg}"

        # Parse JSON response
        try:
            res_data = response.json()
        except json.JSONDecodeError as e:
            return None, f"Failed to parse API response as JSON: {str(e)}"
        
        whisper_hash = res_data.get("whisper_hash")
        
        if not whisper_hash:
            return None, f"No whisper_hash in response. Got: {res_data}"

        print(f"Got whisper_hash: {whisper_hash}")
        
        # Step 2: Processing
        #REFER https://docs.unstract.com/llmwhisperer/llm_whisperer/apis/llm_whisperer_text_extraction_status_api/
        print("⏳ Waiting for processing...")
        time.sleep(3)  
        
        max_attempts = 40  # 40 attempts x 3 seconds = 2 minutes max
        for attempt in range(max_attempts):
            try:
                status_resp = requests.get(
                    f"{base_url}/whisper-status",
                    headers=headers,
                    params={'whisper_hash': whisper_hash},
                    timeout=10
                )
                
                if status_resp.status_code != 200:
                    print(f"Status check returned {status_resp.status_code}")
                    time.sleep(3)
                    continue
                
                # Verify JSON response
                if "application/json" not in status_resp.headers.get("Content-Type", ""):
                    return None, "Status API returned non-JSON response."

                status_data = status_resp.json()
                status = status_data.get("status")
                
                print(f" Attempt {attempt + 1}/{max_attempts} - Status: {status}")
                
                if status == "processed":
                    print("Document processed successfully!")
                    
                    # Step 3: RETRIEVAL (REFER ) 
                    # -https://docs.unstract.com/llmwhisperer/llm_whisperer/apis/llm_whisperer_text_extraction_retrieve_api/
                    retr_resp = requests.get(
                        f"{base_url}/whisper-retrieve",
                        headers=headers,
                        params={'whisper_hash': whisper_hash},
                        timeout=30
                    )
                    
                    if retr_resp.status_code != 200:
                        try:
                            error_data = retr_resp.json()
                            error_msg = error_data.get('message', retr_resp.text[:200])
                        except:
                            error_msg = retr_resp.text[:200]
                        return None, f"Retrieve failed ({retr_resp.status_code}): {error_msg}"
                    
                    try:
                        retrieve_data = retr_resp.json()
                        # Extract text from the result_text field
                        extracted_text = retrieve_data.get("result_text", "").strip()
                        
                        if not extracted_text:
                            # Fallback: sometimes it's in extraction dict
                            extraction = retrieve_data.get("extraction", {})
                            extracted_text = extraction.get("result_text", "").strip()
                        
                    except json.JSONDecodeError:
                        # Fallback: sometimes returns plain text
                        extracted_text = retr_resp.text.strip()
                    
                    if not extracted_text:
                        return None, "Extraction returned empty text."
                    
                    print(f"Extracted {len(extracted_text)} characters")
                    return extracted_text, None
                
                elif status == "failed" or status == "error":
                    error_msg = status_data.get('message', 'Unknown error')
                    return None, f"Processing failed: {error_msg}"
                
                elif status in ["processing", "accepted", "uploaded"]:
                    # Still processing
                    time.sleep(3)
                    continue
                else:
                    # Unknown status
                    print(f"⚠️ Unknown status: {status}")
                    time.sleep(3)
                    continue
                    
            except requests.exceptions.Timeout:
                print(f"Status check timeout (attempt {attempt + 1})")
                time.sleep(3)
                continue
            except Exception as e:
                print(f"Status check error: {str(e)}")
                time.sleep(3)
                continue
        
        return None, f"Timeout: Processing exceeded {max_attempts * 3} seconds."

    except requests.exceptions.Timeout:
        return None, "Network timeout. Please try again."
    except requests.exceptions.RequestException as e:
        return None, f"Network error: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"

# MISTRAL (OPEN ROUTER ANALYSIS)

def analyze_resume_with_ai(resume_text, job_data):
    #PROMPT
    """Analyzes resume text against job requirements using AI."""
    prompt = f"""
    Analyze this RESUME against the JOB DETAILS. Return ONLY valid JSON.
    Do not include markdown blocks, code fences, or conversational text.

    JOB TITLE: {job_data.get('title', 'Not specified')}
    REQUIRED SKILLS: {job_data.get('required_skills', 'Not specified')}
    EXPERIENCE REQUIRED: {job_data.get('experience_years', 'Not specified')} years

    RESUME TEXT:
    {resume_text[:3000]}

    Return JSON with this exact schema:
    {{
      "match_score": <number 0-100>,
      "recommendation": "<Strong Match|Good Match|Moderate Match|Weak Match>",
      "key_strengths": ["strength1", "strength2"],
      "missing_skills": ["skill1", "skill2"],
      "skills_found": ["skill1", "skill2"],
      "experience_summary": "brief summary",
      "education": "education details",
      "estimated_experience_years": <number>,
      "reasoning": "explanation of the match score"
    }}
    """
    try:
        completion = llm_client.chat.completions.create(
            model="mistralai/devstral-2512:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        raw_content = completion.choices[0].message.content.strip()
        
        # Remove markdown code blocks (checked output after POSTMAN API)
        raw_content = re.sub(r'```json\s*|\s*```', '', raw_content)
        
        # Extract JSON object
        json_match = re.search(r'(\{.*\})', raw_content, re.DOTALL)
        
        if json_match:
            clean_json = json_match.group(1)
            result = json.loads(clean_json)
            print(f"AI Analysis: Match Score = {result.get('match_score', 0)}")
            return result
        
        # Fallback parsing
        return json.loads(raw_content)
        
    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return {
            "match_score": 0,
            "recommendation": "Analysis Failed",
            "reasoning": f"AI failed to analyze: {str(e)}",
            "key_strengths": [],
            "missing_skills": [],
            "skills_found": [],
            "experience_summary": "Analysis error",
            "education": "Unknown",
            "estimated_experience_years": 0
        }

# FLASK Routing for 3 web pages

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manager')
def manager():
    return render_template('manager.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

# API routes

@app.route('/api/jobs', methods=['GET', 'POST'])
def handle_jobs():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            return jsonify({"error": "No file provided"}), 400
        
        try:
            jobs = json.loads(file.read())
            if not isinstance(jobs, list):
                jobs = [jobs]
            
            inserted_count = 0
            for j in jobs:
                # Ensure both id and job_id fields exist
                if 'job_id' not in j and 'id' in j:
                    j['job_id'] = j['id']
                elif 'job_id' not in j:
                    j['job_id'] = str(uuid.uuid4())
                
                j['id'] = j['job_id']  # Mirror for consistency
                j['uploaded_at'] = datetime.now().isoformat()
                
                jobs_collection.update_one(
                    {"job_id": j['job_id']},
                    {"$set": j},
                    upsert=True
                )
                inserted_count += 1
            
            return jsonify({
                "success": True,
                "message": f"Successfully uploaded {inserted_count} job(s)"
            })
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON file"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    # GET request
    jobs = list(jobs_collection.find())
    return jsonify({"jobs": mongo_to_json(jobs)})

# End point for admin page
@app.route('/api/upload-jobs', methods=['POST'])
def upload_jobs():
    return handle_jobs()

@app.route('/api/upload-resume', methods=['POST'])
def upload_resume():
    file = request.files.get('file')
    job_id = request.form.get('job_id')
    
    print(f"Resume upload: job_id={job_id}, file={file.filename if file else 'None'}")
    
    if not file or not job_id:
        return jsonify({"error": "Missing file or job_id"}), 400

    # Find job
    job = jobs_collection.find_one({"job_id": job_id})
    if not job:
        print(f"Job not found: {job_id}")
        return jsonify({"error": f"Job not found: {job_id}"}), 404

    # Save file
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4()}_{filename}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(file_path)
    print(f"Saved: {file_path}")

    # Extract text
    text, error = extract_text_from_resume(file_path)
    
    candidate_id = str(uuid.uuid4())
    
    if error:
        print(f"Extraction failed: {error}")
        candidate_data = {
            "candidate_id": candidate_id,
            "id": candidate_id,
            "job_id": job_id,
            "job_title": job.get('title', 'Unknown'),
            "filename": filename,
            "match_score": 0,
            "recommendation": "Parsing Failed",
            "reasoning": error,
            "status": "error",
            "uploaded_at": datetime.now().isoformat(),
            "analysis": f"Failed to extract text: {error}"
        }
    else:
        print(f"Extracted {len(text)} chars. Running AI analysis...")
        analysis = analyze_resume_with_ai(text, job)
        
        # Format for display
        analysis_text = f"""Match Score: {analysis.get('match_score', 0)}/100
Recommendation: {analysis.get('recommendation', 'Unknown')}

Key Strengths:
{chr(10).join('• ' + s for s in analysis.get('key_strengths', []))}

Missing Skills:
{chr(10).join('• ' + s for s in analysis.get('missing_skills', []))}

Skills Found: {', '.join(analysis.get('skills_found', []))}

Experience: {analysis.get('experience_summary', 'N/A')}
Education: {analysis.get('education', 'N/A')}
Years of Experience: {analysis.get('estimated_experience_years', 0)}

Reasoning: {analysis.get('reasoning', 'N/A')}"""
        
        candidate_data = {
            "candidate_id": candidate_id,
            "id": candidate_id,
            "job_id": job_id,
            "job_title": job.get('title'),
            "filename": filename,
            "resume_text": text[:1000],
            "status": "success",
            "uploaded_at": datetime.now().isoformat(),
            "analysis": analysis_text,
            **analysis
        }

    candidates_collection.insert_one(candidate_data)
    print(f"Saved candidate: {candidate_id}")
    
    return jsonify({
        "success": True,
        "message": "Resume processed successfully!",
        "candidate": mongo_to_json(candidate_data)
    })

@app.route('/api/candidates', methods=['GET'])
def get_candidates():
    job_id = request.args.get('job_id')
    query = {"job_id": job_id} if job_id else {}
    results = list(candidates_collection.find(query).sort("match_score", -1))
    return jsonify({"candidates": mongo_to_json(results)})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        total_jobs = jobs_collection.count_documents({})
        total_candidates = candidates_collection.count_documents({})
        
        # Jobs with at least one candidate
        pipeline = [
            {"$group": {"_id": "$job_id"}},
            {"$count": "count"}
        ]
        result = list(candidates_collection.aggregate(pipeline))
        jobs_with_candidates = result[0]['count'] if result else 0
        
        return jsonify({
            "total_jobs": total_jobs,
            "total_candidates": total_candidates,
            "jobs_with_candidates": jobs_with_candidates
        })
    except Exception as e:
        return jsonify({
            "total_jobs": 0,
            "total_candidates": 0,
            "jobs_with_candidates": 0
        }), 500

@app.route('/api/health')
def health():
    try:
        db_status = "connected" if client.server_info() else "disconnected"
    except:
        db_status = "disconnected"
    
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "database": db_status,
        "llmwhisperer_key": "Set" if os.getenv('LLMWHISPERER_API_KEY') else "Missing",
        "openrouter_key": "Set" if os.getenv('OPENROUTER_API_KEY') else "Missing"
    })

if __name__ == '__main__':
    print("AI Recruitment System Starting...")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print(f"LLMWhisperer: {'Configured' if os.getenv('LLMWHISPERER_API_KEY') else 'Missing'}")
    print(f"OpenRouter: {'Configured' if os.getenv('OPENROUTER_API_KEY') else 'Missing'}")
    print(f"MongoDB: {'Configured' if os.getenv('MONGODB_URI') else 'Missing'}")
    print("="*50)
    app.run(debug=True, port=5000)