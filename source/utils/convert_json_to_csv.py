import json
import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlunparse
import io


# ---------------------------------------------------------------------------
# Tiered Career URL Matching
# ---------------------------------------------------------------------------

TIER_1_SEGMENTS = {
    "jobs", "job", "job-opportunities", "jobs-opportunities", "opportunities",
    "work-with-us", "work-withus", "work-for-us", "careers-at",
    "current-vacancies", "vacancies", "vacancies-list", "open-roles",
    "available-roles", "positions", "join-our-team",
}

TIER_2_SEGMENTS = {
    "join-us", "joinus", "be-part-of-our-team", "become-a-member",
    "careers", "recruitment", "hiring", "work-with", "employment",
}

TIER_3_KEYWORDS = {"career", "job", "vacancy", "role", "recruitment"}


def normalise_url(url: str) -> str:
    """Normalise a URL: lowercase, remove trailing slash, strip params/fragments."""
    url = url.lower().strip()
    parsed = urlparse(url)
    # Remove query params and fragments
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return clean


def get_final_segment(url: str) -> str:
    """Extract the final non-empty path segment of a URL."""
    path = urlparse(url).path
    segments = [s for s in path.split("/") if s]
    return segments[-1] if segments else ""


def get_path_depth(url: str) -> int:
    """Return the depth (number of path segments) of a URL."""
    path = urlparse(url).path
    return len([s for s in path.split("/") if s])


def _is_noise_url(url: str) -> bool:
    """Return True for URLs that are likely not career pages."""
    noise_patterns = ["/blog/", "/news/", "/press/", "/events/", "/admissions/", "/students/"]
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in noise_patterns)


def _best_match(candidates: List[str]) -> Optional[str]:
    """
    From a list of candidate URLs apply tie-break rules:
    - Prefer shortest URL path
    - Prefer top-level path (fewer segments)
    - Prefer plural over singular  (jobs > job)
    - Avoid paths containing blog/news
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Filter noise URLs if alternatives exist
    non_noise = [u for u in candidates if not _is_noise_url(u)]
    if non_noise:
        candidates = non_noise

    # Sort: fewer segments first, then shorter URL
    candidates.sort(key=lambda u: (get_path_depth(u), len(u)))

    # Prefer plural if the top two differ only by pluralisation
    if len(candidates) >= 2:
        seg0 = get_final_segment(candidates[0])
        seg1 = get_final_segment(candidates[1])
        # Only swap when segments are genuinely different
        if seg0 != seg1 and (seg1 == seg0 + "s" or ("-" in seg0 and seg1 == seg0.replace("-", "s"))):
            return candidates[1]

    return candidates[0]


def find_career_url_by_tiers(candidate_urls: List[str]) -> Optional[str]:
    """
    Apply Tier 1 → Tier 2 → Tier 3 matching to find the best career URL.
    Returns the matched URL or None.
    """
    normalised = [normalise_url(u) for u in candidate_urls if u]
    unique = list(dict.fromkeys(normalised))  # deduplicate preserving order

    # ---------- Tier 1 ----------
    tier1_matches = [u for u in unique if get_final_segment(u) in TIER_1_SEGMENTS]
    if tier1_matches:
        return _best_match(tier1_matches)

    # ---------- Tier 2 ----------
    tier2_matches = [u for u in unique if get_final_segment(u) in TIER_2_SEGMENTS]
    if tier2_matches:
        # Extra guard for "join-us" – skip if it looks like customer acquisition
        safe = []
        for u in tier2_matches:
            seg = get_final_segment(u)
            if seg in {"join-us", "joinus"}:
                # Heuristic: safe if path contains hiring-context parent folders
                path = urlparse(u).path.lower()
                if any(kw in path for kw in ["about", "company", "team", "work"]):
                    safe.append(u)
                # If no parent folder hint, still include but de-prioritise
                else:
                    safe.append(u)
            else:
                safe.append(u)
        if safe:
            return _best_match(safe)

    # ---------- Tier 3 ----------
    tier3_matches = []
    for u in unique:
        path = urlparse(u).path.lower()
        seg = get_final_segment(u)
        for kw in TIER_3_KEYWORDS:
            if kw in seg or kw in path:
                tier3_matches.append(u)
                break

    if tier3_matches:
        # Prefer keyword at end of path
        end_matches = [u for u in tier3_matches if any(
            get_final_segment(u).startswith(kw) or get_final_segment(u).endswith(kw)
            for kw in TIER_3_KEYWORDS
        )]
        pool = end_matches if end_matches else tier3_matches
        # Avoid blog posts unless nothing else
        non_blog = [u for u in pool if "blog" not in u]
        return _best_match(non_blog if non_blog else pool)

    return None


def extract_career_url(job_record: dict) -> Optional[str]:
    """
    Determine the career_url for a job record:
    - Jobs found  → use filter_url from ats_detection
    - No jobs found → apply tiered matching across all visited/scraped URLs
    """
    summary = job_record.get("summary", {})
    jobs_found = summary.get("jobs_found", 0)

    # ── Jobs were found: use the filter_url directly ──────────────────────
    if jobs_found > 0:
        ats_detection = job_record.get("ats_detection") or {}
        filter_url = ats_detection.get("filter_url")
        if filter_url:
            return normalise_url(filter_url)

        # Fallback: first job_url from ats_true/false lists
        breakdown = summary.get("ats_breakdown", {})
        for key in ("ats_true_jobs", "ats_false_jobs", "ats_uncertain_jobs"):
            jobs_list = breakdown.get(key, [])
            if jobs_list:
                url = jobs_list[0].get("filter_url") or jobs_list[0].get("job_url")
                if url:
                    return normalise_url(url)

    # ── No jobs found: collect all candidate URLs and apply tiered logic ──
    candidate_urls = []

    # From scrape_results visited URLs
    for scrape in job_record.get("scrape_results", []):
        visited = scrape.get("scraping_details", {}).get("visited_urls", [])
        candidate_urls.extend(visited)
        # Also include the top-level scrape URL
        top_url = scrape.get("url")
        if top_url:
            candidate_urls.append(top_url)

    # From summary job_filtered (URLs that were evaluated)
    candidate_urls.extend(summary.get("job_filtered", []))

    return find_career_url_by_tiers(candidate_urls)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_all_jobs_from_files(output_dir: str = "job_outputs", task_id: str = None) -> List[Dict]:
    """Read all job records from JSON files."""
    output_path = Path(output_dir)
    if not output_path.exists():
        return []

    pattern = f"jobs_{task_id}_*.json" if task_id else "jobs_*.json"
    all_files = sorted(output_path.glob(pattern))
    all_jobs = []

    for file_path in all_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                records = json.load(f)
                all_jobs.extend(records)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading {file_path}: {e}")
            continue

    return all_jobs


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------

def flatten_ats_result(ats_result: dict) -> dict:
    """Flatten ATS check result into CSV-friendly format."""
    if not ats_result:
        return {}

    indicators = ats_result.get("indicators_found", [])
    return {
        "ats_status": ats_result.get("status", ""),
        "ats_is_ats": ats_result.get("is_ats", ""),
        "ats_provider": ats_result.get("ats_provider", ""),
        "ats_confidence": ats_result.get("confidence", ""),
        "ats_application_type": ats_result.get("application_type", ""),
        "ats_reasoning": ats_result.get("reasoning", ""),
        "ats_detection_method": ats_result.get("detection_method", ""),
        "ats_token_usage": ats_result.get("token_usage", 0),
        "ats_indicators": ", ".join(indicators) if indicators else "",
    }


def flatten_job_record(job_record: dict) -> List[dict]:
    """
    Flatten a job record into CSV rows (one row per job record).
    Includes career_url derived from tiered URL matching or filter_url.
    """
    summary = job_record.get("summary", {})
    ats_detection = job_record.get("ats_detection") or {}

    # Resolve career_url using tiered logic
    career_url = extract_career_url(job_record)

    row = {
        "task_id": job_record.get("_task_id", ""),
        "saved_at": job_record.get("_saved_at", ""),
        "domain": job_record.get("domain", ""),
        "message": job_record.get("message", ""),
        "total_duration_seconds": job_record.get("total_duration_seconds", 0),
        "total_token_usage": job_record.get("total_token_usage", 0),
        "run_status": job_record.get("run_status", ""),
        # Summary
        "jobs_found": summary.get("jobs_found", 0),
        "linkedin_indeed_redirects": summary.get("linkedin_indeed_redirects", 0),
        # ATS detection
        "ats_status": ats_detection.get("ats_status"),
        "job_url": ats_detection.get("job_url"),
        "filter_url": ats_detection.get("filter_url"),
        "ats_provider": ats_detection.get("ats_provider"),
        "confidence": ats_detection.get("confidence"),
        "reasoning": ats_detection.get("reasoning"),
        "detection_method": ats_detection.get("detection_method"),
        # ── NEW ──
        "career_url": career_url,
    }

    return [row]


# ---------------------------------------------------------------------------
# CSV Generation
# ---------------------------------------------------------------------------

# Column order — career_url placed right after filter_url for readability
COLUMN_ORDER = [
    "task_id", "saved_at", "domain", "run_status",
    "jobs_found", "linkedin_indeed_redirects",
    "ats_status", "ats_provider", "confidence", "detection_method", "reasoning",
    "job_url", "filter_url", "career_url",
    "message", "total_duration_seconds", "total_token_usage",
]


def generate_csv_from_jobs(jobs: List[Dict]) -> str:
    """Generate CSV string from job records."""
    if not jobs:
        return ""

    all_rows = []
    for job in jobs:
        try:
            rows = flatten_job_record(job)
            all_rows.extend(rows)
        except Exception as e:
            print(f"Warning: Could not flatten record for domain {job.get('domain', 'unknown')}: {e}")
            all_rows.append({
                "task_id": job.get("_task_id", ""),
                "saved_at": job.get("_saved_at", ""),
                "domain": job.get("domain", ""),
                "run_status": "",
                "message": job.get("message", ""),
                "career_url": None,
            })

    if not all_rows:
        return ""

    # Build fieldnames: predefined order first, then any extra fields alphabetically
    extra_fields = sorted(
        f for f in set().union(*[r.keys() for r in all_rows]) if f not in COLUMN_ORDER
    )
    fieldnames = COLUMN_ORDER + extra_fields

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_rows)

    return output.getvalue()


def export_task_to_csv(task_id: str, output_dir: str = "job_outputs") -> str:
    """Export all jobs for a specific task to CSV string."""
    jobs = read_all_jobs_from_files(output_dir=output_dir, task_id=task_id)
    return generate_csv_from_jobs(jobs)