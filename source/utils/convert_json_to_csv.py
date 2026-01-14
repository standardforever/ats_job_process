import json
import csv
from pathlib import Path
from typing import List, Dict, Any
import io


def read_all_jobs_from_files(output_dir: str = "job_outputs", task_id: str = None) -> List[Dict]:
    """
    Read all job records from JSON files.
    
    Args:
        output_dir: Directory containing job files
        task_id: Optional task_id to filter records
    
    Returns:
        List of all job records
    """
    output_path = Path(output_dir)
    
    if not output_path.exists():
        return []
    
    # Determine file pattern based on task_id
    if task_id:
        pattern = f"jobs_{task_id}_*.json"
    else:
        pattern = "jobs_*.json"
    
    all_files = sorted(output_path.glob(pattern))
    all_jobs = []
    for file_path in all_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                records = json.load(f)
                
                # Filter by task_id if provided
                if task_id:
                    records = [r for r in records if r.get("_task_id") == task_id]
                all_jobs.extend(records)
                
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading {file_path}: {e}")
            continue
    
    return all_jobs


def flatten_ats_result(ats_result: dict) -> dict:
    """Flatten ATS check result into CSV-friendly format"""
    flattened = {}
    
    if not ats_result:
        return flattened
    
    # Basic ATS info
    flattened['ats_status'] = ats_result.get('status', '')
    flattened['ats_is_ats'] = ats_result.get('is_ats', '')
    flattened['ats_provider'] = ats_result.get('ats_provider', '')
    flattened['ats_confidence'] = ats_result.get('confidence', '')
    flattened['ats_application_type'] = ats_result.get('application_type', '')
    flattened['ats_reasoning'] = ats_result.get('reasoning', '')
    flattened['ats_detection_method'] = ats_result.get('detection_method', '')
    flattened['ats_token_usage'] = ats_result.get('token_usage', 0)
    
    # Indicators
    indicators = ats_result.get('indicators_found', [])
    flattened['ats_indicators'] = ', '.join(indicators) if indicators else ''
    
    return flattened


def flatten_job_record(job_record: dict) -> List[dict]:
    """
    Flatten a job record into CSV rows.
    Handles both successful scrapes and all error cases.
    
    Returns list of dicts (one or more CSV rows).
    """
    rows = []
    
    # Base metadata (always present)
    base_data = {
        'task_id': job_record.get('_task_id', ''),
        'saved_at': job_record.get('_saved_at', ''),
        'domain': job_record.get('domain', ''),
        'success': job_record.get('success', False),
        'message': job_record.get('message', ''),
        'total_duration_seconds': job_record.get('total_duration_seconds', 0),
        'total_token_usage': job_record.get('total_token_usage', 0),
    }
    
    # Summary stats (may not exist for old error records)
    summary = job_record.get('summary', {})
    base_data.update({
        'urls_checked': summary.get('urls_checked', 0),
        'jobs_found': summary.get('jobs_found', 0),
        'successful_scrapes': summary.get('successful_scrapes', 0),
        'failed_scrapes': summary.get('failed_scrapes', 0),
        'linkedin_indeed_redirects': summary.get('linkedin_indeed_redirects', 0),
        'ats_jobs_found': summary.get('ats_jobs_found', 0),
    })
    
    # Error details (for failed domain access, etc.)
    error_details = job_record.get('error_details')
    if error_details:
        base_data['error_type'] = error_details.get('error_type', '')
        base_data['error'] = error_details.get('error', '')
        base_data['error_status'] = error_details.get('status', '')
        base_data['redirected'] = error_details.get('redirected', False)
        base_data['cancelled'] = error_details.get('cancelled', False)
    
    # Check for legacy error format (old structure without standardization)
    if 'error' in job_record and not error_details:
        base_data['error'] = job_record.get('error', '')
        base_data['error_status'] = job_record.get('status', '')
    
    # Process scrape results
    scrape_results = job_record.get('scrape_results', [])
    
    # If no scrape results, return one row with base data (error case)
    if not scrape_results:
        return [base_data]
    
    # Process each scrape result
    for scrape_result in scrape_results:
        row = base_data.copy()
        
        # Scrape info
        row['scraped_url'] = scrape_result.get('url', '')
        row['scrape_status'] = scrape_result.get('status', '')
        row['scrape_duration_seconds'] = scrape_result.get('scrape_duration_seconds', 0)
        row['result_type'] = scrape_result.get('result_type', '')
        row['scrape_error'] = scrape_result.get('error', '')
        
        # Jobs found
        jobs = scrape_result.get('jobs', {})
        row['job_count'] = jobs.get('count', 0)
        row['job_urls'] = ', '.join(jobs.get('job_urls', []))
        
        # Scraping details
        details = scrape_result.get('scraping_details', {})
        row['scrape_tokens'] = details.get('total_tokens', 0)
        row['scrape_llm_iterations'] = details.get('llm_iterations', 0)
        row['scrape_message'] = details.get('message', '')
        row['visited_urls'] = ', '.join(details.get('visited_urls', []))
        
        # ATS check info
        ats_check = scrape_result.get('ats_check')
        if ats_check:
            row['ats_duration_seconds'] = ats_check.get('duration_seconds', 0)
            row['ats_total_tokens'] = ats_check.get('total_tokens', 0)
            row['ats_jobs_processed'] = ats_check.get('jobs_processed', 0)
            
            # Get first ATS result
            ats_results = ats_check.get('results', [])
            if ats_results:
                first_ats = ats_results[0]
                row['ats_status'] = first_ats.get('status', '')
                row['ats_is_ats'] = first_ats.get('is_ats', '')
                row['ats_provider'] = first_ats.get('ats_provider', '')
                row['ats_confidence'] = first_ats.get('confidence', '')
                row['ats_application_type'] = first_ats.get('application_type', '')
                row['ats_reasoning'] = first_ats.get('reasoning', '')
                row['ats_job_url'] = first_ats.get('job_url', '')
        
        rows.append(row)
    
    return rows




def generate_csv_from_jobs(jobs: List[Dict]) -> str:
    """
    Generate CSV from job records.
    Handles both new standardized format and legacy error formats.
    """
    if not jobs:
        return ""
    
    # Flatten all records
    all_rows = []
    for job in jobs:
        try:
            rows = flatten_job_record(job)
            all_rows.extend(rows)
        except Exception as e:
            # Fallback for completely unexpected formats
            print(f"Warning: Could not flatten record for domain {job.get('domain', 'unknown')}: {e}")
            
            # Create minimal row with whatever we can extract
            all_rows.append({
                'task_id': job.get('_task_id', ''),
                'saved_at': job.get('_saved_at', ''),
                'domain': job.get('domain', ''),
                'success': job.get('success', False),
                'message': job.get('message', ''),
                'error': str(job.get('error', '')),
                'raw_record': str(job)[:500]  # First 500 chars of raw record
            })
    
    if not all_rows:
        return ""
    
    # Get all unique field names
    all_fields = set()
    for row in all_rows:
        all_fields.update(row.keys())
    
    # Sort fields for consistent column order
    fieldnames = sorted(all_fields)
    
    # Generate CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    
    writer.writeheader()
    writer.writerows(all_rows)
    
    return output.getvalue()


def export_task_to_csv(task_id: str, output_dir: str = "job_outputs") -> str:
    """
    Export all jobs for a specific task to CSV.
    
    Args:
        task_id: The task ID to export
        output_dir: Directory containing job files
    
    Returns:
        CSV content as string
    """
    jobs = read_all_jobs_from_files(output_dir=output_dir, task_id=task_id)
    return generate_csv_from_jobs(jobs)