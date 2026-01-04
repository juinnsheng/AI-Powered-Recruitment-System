import os
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# MongoDB Configuration
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
DATABASE_NAME = os.getenv('DATABASE_NAME', 'recruitment_db')

# Global database connection
db = None
client = None

def init_db():
    """Initialize MongoDB connection"""
    global db, client
    try:
        mongodb_uri = MONGODB_URI
        
        # Validate MongoDB URI
        if not mongodb_uri or mongodb_uri == 'mongodb://localhost:27017/':
            print("Warning: Using default localhost MongoDB")
            print("For production, use MongoDB Atlas. See MONGODB_SETUP.md")
        
        if 'your_username' in mongodb_uri or 'your_password' in mongodb_uri:
            print("Error: Please update MONGODB_URI in .env file")
            print("Replace 'your_username' and 'your_password' with actual values")
            return None
        
        print(f"Connecting to MongoDB...")
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        
        # Test connection immediately
        client.admin.command('ping')
        print(f"✓ Connected to MongoDB successfully")
        
        db = client[DATABASE_NAME]
        
        # Create indexes for better performance
        db.jobs.create_index("job_id", unique=True)
        db.candidates.create_index("candidate_id", unique=True)
        db.candidates.create_index("job_id")
        print(f"✓ Database '{DATABASE_NAME}' initialized")
        
        return db
    except Exception as e:
        print(f"Database connection error: {e}")
        print("\nTroubleshooting steps:")
        print("1. Check MONGODB_URI in .env file")
        print("2. Verify username and password are correct")
        print("3. Check if IP is whitelisted in MongoDB Atlas")
        print("4. See MONGODB_SETUP.md for detailed instructions")
        client = None
        db = None
        return None

def test_connection():
    """Test if MongoDB connection is working"""
    global client
    try:
        if client is None:
            print("MongoDB client not initialized. Call init_db() first.")
            return False
        client.admin.command('ping')
        return True
    except Exception as e:
        print(f"MongoDB connection test failed: {e}")
        return False

# ==================== JOB OPERATIONS ====================

def save_job(job_data):
    """
    Save a single job to the database
    
    Args:
        job_data (dict): Job information
        
    Returns:
        dict: Saved job with _id
    """
    try:
        # Add metadata
        job_data['created_at'] = datetime.utcnow()
        job_data['updated_at'] = datetime.utcnow()
        job_data['status'] = 'active'
        
        # Generate job_id if not exists
        if 'job_id' not in job_data:
            last_job = db.jobs.find_one(sort=[('job_id', -1)])
            job_data['job_id'] = (last_job['job_id'] + 1) if last_job else 1
        
        result = db.jobs.insert_one(job_data)
        job_data['_id'] = str(result.inserted_id)
        
        return job_data
    except Exception as e:
        print(f"Error saving job: {e}")
        return None

def save_jobs_bulk(jobs_list):
    """
    Save multiple jobs to database
    
    Args:
        jobs_list (list): List of job dictionaries
        
    Returns:
        list: Saved jobs with IDs
    """
    saved_jobs = []
    for job in jobs_list:
        saved_job = save_job(job)
        if saved_job:
            saved_jobs.append(saved_job)
    return saved_jobs

def get_all_jobs():
    """
    Get all active jobs from database
    
    Returns:
        list: List of all jobs
    """
    try:
        jobs = list(db.jobs.find({'status': 'active'}).sort('created_at', -1))
        # Convert ObjectId to string
        for job in jobs:
            job['_id'] = str(job['_id'])
        return jobs
    except Exception as e:
        print(f"Error fetching jobs: {e}")
        return []

def get_job_by_id(job_id):
    """
    Get a specific job by job_id
    
    Args:
        job_id (int): Job ID
        
    Returns:
        dict: Job information or None
    """
    try:
        job = db.jobs.find_one({'job_id': int(job_id)})
        if job:
            job['_id'] = str(job['_id'])
        return job
    except Exception as e:
        print(f"Error fetching job: {e}")
        return None

def delete_job(job_id):
    """
    Soft delete a job (mark as inactive)
    
    Args:
        job_id (int): Job ID
        
    Returns:
        bool: Success status
    """
    try:
        result = db.jobs.update_one(
            {'job_id': int(job_id)},
            {'$set': {'status': 'inactive', 'updated_at': datetime.utcnow()}}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"Error deleting job: {e}")
        return False

# ==================== CANDIDATE OPERATIONS ====================

def save_candidate(candidate_data):
    """
    Save candidate information to database
    
    Args:
        candidate_data (dict): Candidate information including:
            - filename: Resume filename
            - job_id: Applied job ID
            - resume_text: Extracted resume text
            - resume_summary: AI-generated summary
            - skills_extracted: List of skills
            - experience_years: Years of experience
            - education: Education details
            - ai_analysis: Complete AI analysis
            - match_score: Matching score (0-100)
            
    Returns:
        dict: Saved candidate with _id
    """
    try:
        # Generate candidate_id
        last_candidate = db.candidates.find_one(sort=[('candidate_id', -1)])
        candidate_data['candidate_id'] = (last_candidate['candidate_id'] + 1) if last_candidate else 1
        
        # Add metadata
        candidate_data['applied_at'] = datetime.utcnow()
        candidate_data['status'] = 'pending'  # pending, reviewed, shortlisted, rejected
        
        result = db.candidates.insert_one(candidate_data)
        candidate_data['_id'] = str(result.inserted_id)
        
        # Update job with candidate count
        db.jobs.update_one(
            {'job_id': candidate_data['job_id']},
            {'$inc': {'candidate_count': 1}}
        )
        
        return candidate_data
    except Exception as e:
        print(f"Error saving candidate: {e}")
        return None

def get_all_candidates(job_id=None):
    """
    Get all candidates, optionally filtered by job_id
    
    Args:
        job_id (int, optional): Filter by job ID
        
    Returns:
        list: List of candidates
    """
    try:
        query = {}
        if job_id:
            query['job_id'] = int(job_id)
        
        candidates = list(db.candidates.find(query).sort('applied_at', -1))
        
        # Convert ObjectId to string and add job title
        for candidate in candidates:
            candidate['_id'] = str(candidate['_id'])
            job = get_job_by_id(candidate['job_id'])
            candidate['job_title'] = job['title'] if job else 'Unknown'
        
        return candidates
    except Exception as e:
        print(f"Error fetching candidates: {e}")
        return []

def get_candidate_by_id(candidate_id):
    """
    Get a specific candidate by candidate_id
    
    Args:
        candidate_id (int): Candidate ID
        
    Returns:
        dict: Candidate information or None
    """
    try:
        candidate = db.candidates.find_one({'candidate_id': int(candidate_id)})
        if candidate:
            candidate['_id'] = str(candidate['_id'])
            job = get_job_by_id(candidate['job_id'])
            candidate['job_title'] = job['title'] if job else 'Unknown'
        return candidate
    except Exception as e:
        print(f"Error fetching candidate: {e}")
        return None

def get_top_candidates(job_id, limit=5):
    """
    Get top N candidates for a job sorted by match score
    
    Args:
        job_id (int): Job ID
        limit (int): Number of top candidates to return
        
    Returns:
        list: Top candidates
    """
    try:
        candidates = list(db.candidates.find(
            {'job_id': int(job_id)}
        ).sort('match_score', -1).limit(limit))
        
        for candidate in candidates:
            candidate['_id'] = str(candidate['_id'])
        
        return candidates
    except Exception as e:
        print(f"Error fetching top candidates: {e}")
        return []

def update_candidate_status(candidate_id, status):
    """
    Update candidate application status
    
    Args:
        candidate_id (int): Candidate ID
        status (str): New status (pending, reviewed, shortlisted, rejected)
        
    Returns:
        bool: Success status
    """
    try:
        result = db.candidates.update_one(
            {'candidate_id': int(candidate_id)},
            {'$set': {'status': status, 'updated_at': datetime.utcnow()}}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"Error updating candidate status: {e}")
        return False

# ==================== STATISTICS ====================

def get_statistics():
    """
    Get system statistics
    
    Returns:
        dict: Statistics including job count, candidate count, etc.
    """
    try:
        stats = {
            'total_jobs': db.jobs.count_documents({'status': 'active'}),
            'total_candidates': db.candidates.count_documents({}),
            'pending_reviews': db.candidates.count_documents({'status': 'pending'}),
            'shortlisted': db.candidates.count_documents({'status': 'shortlisted'}),
            'jobs_with_candidates': len(db.candidates.distinct('job_id'))
        }
        return stats
    except Exception as e:
        print(f"Error fetching statistics: {e}")
        return {
            'total_jobs': 0,
            'total_candidates': 0,
            'pending_reviews': 0,
            'shortlisted': 0,
            'jobs_with_candidates': 0
        }

# ==================== UTILITY FUNCTIONS ====================

def clear_all_data():
    """
    Clear all data from database (use with caution!)
    
    Returns:
        bool: Success status
    """
    try:
        db.jobs.delete_many({})
        db.candidates.delete_many({})
        return True
    except Exception as e:
        print(f"Error clearing data: {e}")
        return False

def get_jobs_with_candidate_count():
    """
    Get all jobs with their candidate counts
    
    Returns:
        list: Jobs with candidate_count field
    """
    try:
        pipeline = [
            {'$match': {'status': 'active'}},
            {
                '$lookup': {
                    'from': 'candidates',
                    'localField': 'job_id',
                    'foreignField': 'job_id',
                    'as': 'candidates'
                }
            },
            {
                '$addFields': {
                    'candidate_count': {'$size': '$candidates'}
                }
            },
            {'$project': {'candidates': 0}}
        ]
        
        jobs = list(db.jobs.aggregate(pipeline))
        for job in jobs:
            job['_id'] = str(job['_id'])
        
        return jobs
    except Exception as e:
        print(f"Error fetching jobs with counts: {e}")
        return []