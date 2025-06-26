import os
import re
import json
import requests
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, TypedDict
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# Load environment variables from .env file
try:
    # Try to load from the backend directory first (where .env is located)
    backend_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend', '.env')
    if os.path.exists(backend_env_path):
        print(f"Loading environment variables from {backend_env_path}")
        load_dotenv(backend_env_path)
    else:
        # Fallback to default dotenv behavior
        print("No .env file found in backend directory, using default dotenv behavior")
        load_dotenv()
    print("Environment variables loaded successfully")
except Exception as e:
    print(f"Error loading environment variables: {str(e)}")
    # Continue execution even if env loading fails

# FreshService API configuration
API_KEY = os.getenv("FRESHSERVICE_API_KEY", "")
DOMAIN = os.getenv("FRESHSERVICE_DOMAIN", "example.freshservice.com")

# Validate API configuration
if not API_KEY or not DOMAIN or DOMAIN == "example.freshservice.com":
    print(f"Warning: FreshService API not properly configured. API_KEY={API_KEY}, DOMAIN={DOMAIN}")

# Construct the base URL only if domain is valid
BASE_URL = f"https://{DOMAIN}/api/v2/tickets" if DOMAIN else ""
auth = (API_KEY, "X") if API_KEY else None

# Email configuration
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "cloc@example.com")

# Status mapping
STATUS_MAP = {
    2: "Open",
    3: "Pending",
    4: "Resolved",
    5: "Closed"
}

class AlertResolutionState(TypedDict):
    """State for the alert resolution agent"""
    tickets: List[Dict[str, Any]]
    firing_alerts: List[Dict[str, Any]]
    resolved_alerts: List[Dict[str, Any]]
    matched_pairs: List[Dict[str, Any]]
    closed_tickets: List[Dict[str, Any]]
    manual_review_tickets: List[Dict[str, Any]]
    summary: Dict[str, Any]

def clean_subject(subject: str) -> str:
    """Clean and normalize the subject for better matching"""
    if not subject:
        return ""
    
    # Convert to lowercase
    subject = subject.lower().strip()
    
    # Remove firing/resolved prefixes for comparison
    subject = re.sub(r"^\[firing:\d+\]\s*", "", subject)
    subject = re.sub(r"^\[resolved\]\s*", "", subject)
    subject = re.sub(r"^resolved:\s*", "", subject)
    
    # Remove alert prefixes
    subject = re.sub(r"^alert-critical:\s*", "", subject)
    subject = re.sub(r"^alert-warning:\s*", "", subject)
    subject = re.sub(r"^crit alert:\s*", "", subject)
    
    return subject.strip()

def is_firing_alert(subject: str) -> bool:
    """Check if the subject indicates a firing alert"""
    if not subject:
        return False
    
    subject_lower = subject.lower()
    return (
        "[firing" in subject_lower or 
        "alert-critical" in subject_lower or 
        "alert-warning" in subject_lower or
        "crit alert" in subject_lower
    )

def is_resolved_alert(subject: str) -> bool:
    """Check if the subject indicates a resolved alert"""
    if not subject:
        return False
    
    subject_lower = subject.lower()
    return (
        "[resolved]" in subject_lower or 
        "resolved:" in subject_lower
    )

def subjects_match(firing_subject: str, resolved_subject: str) -> bool:
    """Check if a firing alert subject matches a resolved alert subject
    
    This function compares the cleaned subjects of firing and resolved alerts
    to determine if they refer to the same underlying issue.
    
    Business Rules:
    1. Match tickets when they refer to the same underlying issue
    2. One will be a firing/alert type and the other will be resolved
    3. The core component/server/service name should match
    4. Handle variations in wording (offline/online, lost/restored, etc.)
    """
    if not firing_subject or not resolved_subject:
        return False
        
    # Simple exact match after cleaning
    if firing_subject == resolved_subject:
        return True
    
    # Extract the core components from each subject
    # First, remove any remaining alert indicators
    firing_clean = re.sub(r"alert|warning|critical|error", "", firing_subject).strip()
    resolved_clean = re.sub(r"alert|warning|critical|error", "", resolved_subject).strip()
    
    # Check if the core parts match
    if firing_clean == resolved_clean:
        return True
    
    # Handle common opposite status pairs
    status_pairs = [
        ("offline", "online"),
        ("down", "up"),
        ("lost", "restored"),
        ("unavailable", "available"),
        ("unavailable", "back online"),  # Added for the specific test case
        ("failure", "recovered"),
        ("failed", "succeeded"),
        ("high", "normal"),
        ("critical", "normal"),
        ("warning", "normal"),
        ("problem", "resolved")
    ]
    
    # Replace status words with a placeholder to improve matching
    normalized_firing = firing_clean
    normalized_resolved = resolved_clean
    
    for status1, status2 in status_pairs:
        normalized_firing = re.sub(r'\b' + status1 + r'\b', "STATUS", normalized_firing)
        normalized_firing = re.sub(r'\b' + status2 + r'\b', "STATUS", normalized_firing)
        normalized_resolved = re.sub(r'\b' + status1 + r'\b', "STATUS", normalized_resolved)
        normalized_resolved = re.sub(r'\b' + status2 + r'\b', "STATUS", normalized_resolved)
    
    # Check if normalized versions match
    if normalized_firing == normalized_resolved:
        return True
    
    # Extract key identifiers (like server names, IPs, service names)
    # These often follow patterns like server-01, app-server, R72_Pen_4, etc.
    identifiers_firing = re.findall(r'\b([a-zA-Z0-9_-]+(?:[0-9._-]+[a-zA-Z0-9_-]*)?)\b', firing_clean)
    identifiers_resolved = re.findall(r'\b([a-zA-Z0-9_-]+(?:[0-9._-]+[a-zA-Z0-9_-]*)?)\b', resolved_clean)
    
    # Look for matching identifiers that are likely to be server/service names
    # (typically containing numbers, underscores, or hyphens)
    for id_firing in identifiers_firing:
        if len(id_firing) >= 4 and (re.search(r'[0-9_-]', id_firing)):
            for id_resolved in identifiers_resolved:
                if id_firing == id_resolved:
                    return True
    
    # Special case for "service unavailable" and "service back online"
    if "service" in firing_clean and "service" in resolved_clean:
        if ("unavailable" in firing_clean and "back online" in resolved_clean) or \
           ("back online" in firing_clean and "unavailable" in resolved_clean):
            return True
    
    # Check for significant word overlap (at least 70% of words match)
    words_firing = set(w for w in re.findall(r'\b\w+\b', firing_clean) if len(w) > 3)
    words_resolved = set(w for w in re.findall(r'\b\w+\b', resolved_clean) if len(w) > 3)
    
    if words_firing and words_resolved:
        common_words = words_firing.intersection(words_resolved)
        overlap_percentage = len(common_words) / min(len(words_firing), len(words_resolved))
        if overlap_percentage >= 0.7:
            return True
    
    # Check if one is a substring of the other (with at least 70% overlap)
    if len(firing_clean) > len(resolved_clean):
        longer, shorter = firing_clean, resolved_clean
    else:
        longer, shorter = resolved_clean, firing_clean
        
    if shorter in longer:
        overlap_percentage = len(shorter) / len(longer)
        if overlap_percentage >= 0.7:
            return True
    
    # NEW: Check if the subject contains the same key entity with different status
    # This is especially important for [FIRING:1] and [RESOLVED] ticket pairs
    # Extract the main entity name (e.g., "disk usage warning on server-01")
    entity_pattern = r'\b([a-zA-Z0-9_\s-]+(?:\s+on\s+[a-zA-Z0-9_-]+)?)\b'
    firing_entities = re.findall(entity_pattern, firing_clean)
    resolved_entities = re.findall(entity_pattern, resolved_clean)
    
    for f_entity in firing_entities:
        if len(f_entity) > 5:  # Only consider substantial entities
            for r_entity in resolved_entities:
                if f_entity == r_entity or (f_entity in r_entity or r_entity in f_entity):
                    return True
    
    # If we get here, no match was found
    return False

def parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string from FreshService API"""
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        # Fallback if the format is different
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

def fetch_tickets(hours: int = 24) -> List[Dict[str, Any]]:
    """Fetch tickets from FreshService API"""
    # Check if API configuration is valid
    if not API_KEY or not DOMAIN or not BASE_URL:
        print("Cannot fetch tickets: FreshService API not properly configured")
        print(f"API_KEY: {'Configured' if API_KEY else 'Missing'}")
        print(f"DOMAIN: {DOMAIN if DOMAIN else 'Missing'}")
        print(f"BASE_URL: {BASE_URL if BASE_URL else 'Missing'}")
        return []
        
    try:
        # Calculate time N hours ago
        time_ago = datetime.now() - timedelta(hours=hours)
        updated_since = time_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Make API request
        url = f"{BASE_URL}?updated_since={updated_since}&per_page=100"
        print(f"Fetching tickets from: {url}")
        
        response = requests.get(url, auth=auth, timeout=30)  # Add timeout
        
        if response.status_code != 200:
            print(f"Error fetching tickets: {response.status_code} - {response.text}")
            return []
        
        # Parse response
        data = response.json()
        tickets = data.get("tickets", [])
        print(f"Fetched {len(tickets)} tickets from the last {hours} hours")
        return tickets
    except requests.exceptions.Timeout:
        print(f"Timeout error fetching tickets from {BASE_URL}")
        return []
    except requests.exceptions.ConnectionError as ce:
        print(f"Connection error fetching tickets: {str(ce)}")
        return []
    except Exception as e:
        print(f"Exception fetching tickets: {str(e)}")
        traceback.print_exc()  # Print full traceback for debugging
        return []

def close_ticket(ticket_id: int, note: str = None) -> bool:
    """Close a ticket in FreshService"""
    # Check if API configuration is valid
    if not API_KEY or not DOMAIN or not BASE_URL:
        print(f"Cannot close ticket #{ticket_id}: FreshService API not properly configured")
        print(f"API_KEY: {'Configured' if API_KEY else 'Missing'}")
        print(f"DOMAIN: {DOMAIN if DOMAIN else 'Missing'}")
        print(f"BASE_URL: {BASE_URL if BASE_URL else 'Missing'}")
        return False
        
    try:
        # Step 1: Close the ticket with minimal payload
        url = f"{BASE_URL}/{ticket_id}"
        print(f"Attempting to close ticket #{ticket_id} at URL: {url}")
        
        # Create minimal payload with just the status change
        # This avoids issues with invalid fields
        payload = {
            "status": 5  # 5 = Closed
        }
        
        print(f"Sending payload to close ticket: {json.dumps(payload)}")
        
        # Make API request to update ticket status
        response = requests.put(
            url, 
            auth=auth, 
            headers={"Content-Type": "application/json"}, 
            data=json.dumps(payload),
            timeout=30
        )
        
        # Check response for ticket closure
        if response.status_code == 200:
            print(f"Successfully closed ticket #{ticket_id}")
            
            # Step 2: Add note separately if provided
            if note:
                try:
                    notes_url = f"{BASE_URL}/{ticket_id}/notes"
                    notes_payload = {
                        "body": note,
                        "private": False
                    }
                    
                    print(f"Adding note to ticket #{ticket_id} via: {notes_url}")
                    notes_response = requests.post(
                        notes_url,
                        auth=auth,
                        headers={"Content-Type": "application/json"},
                        data=json.dumps(notes_payload),
                        timeout=30
                    )
                    
                    if notes_response.status_code == 201:
                        print(f"Successfully added note to ticket #{ticket_id}")
                    else:
                        print(f"Failed to add note to ticket #{ticket_id}: {notes_response.status_code}")
                        print(f"Note response: {notes_response.text}")
                        # Note failure doesn't affect overall success of closing the ticket
                except Exception as note_error:
                    print(f"Error adding note to ticket #{ticket_id}: {str(note_error)}")
                    # Note failure doesn't affect overall success of closing the ticket
            
            return True
        else:
            print(f"Failed to close ticket #{ticket_id}: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except requests.exceptions.Timeout:
        print(f"Timeout error closing ticket #{ticket_id}")
        return False
    except requests.exceptions.ConnectionError as ce:
        print(f"Connection error closing ticket #{ticket_id}: {str(ce)}")
        return False
    except Exception as e:
        print(f"Error closing ticket #{ticket_id}: {str(e)}")
        traceback.print_exc()  # Print full traceback for debugging
        return False

def send_email_notification(subject: str, body: str) -> bool:
    """Send email notification for manual review"""
    if not EMAIL_USER or not EMAIL_PASSWORD:
        print("Email credentials not configured. Skipping email notification.")
        return False
    
    if not EMAIL_TO:
        print("Email recipient not configured. Skipping email notification.")
        return False
    
    try:
        print(f"Preparing to send email notification to {EMAIL_TO}")
        
        # Create message
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        
        # Attach body
        msg.attach(MIMEText(body, "html"))
        
        # Send email with timeout
        print(f"Connecting to SMTP server {EMAIL_HOST}:{EMAIL_PORT}")
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=30)
        
        try:
            # Start TLS for security
            print("Starting TLS connection")
            server.starttls()
            
            # Login to server
            print(f"Logging in as {EMAIL_USER}")
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            
            # Send email
            print("Sending email message")
            server.send_message(msg)
            
            print(f"✓ Email notification successfully sent to {EMAIL_TO}")
            return True
        except Exception as e:
            print(f"Error during SMTP operation: {str(e)}")
            return False
        finally:
            # Always quit the server connection
            print("Closing SMTP connection")
            server.quit()
    except Exception as e:
        print(f"Failed to send email notification: {str(e)}")
        traceback.print_exc()  # Print full traceback for debugging
        return False

# Agent nodes
def fetch_recent_tickets(state: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch recent tickets from FreshService API"""
    try:
        # Get hours parameter from state or use default
        hours = state.get("hours", 24)
        print(f"Fetching tickets from the last {hours} hours")
        
        # Fetch tickets
        tickets = fetch_tickets(hours)
        print(f"Successfully fetched {len(tickets)} tickets")
        
        # Create a new state dictionary and update it with tickets
        state["tickets"] = tickets
        
        # Return the updated state
        return state
    except Exception as e:
        print(f"Error in fetch_recent_tickets: {str(e)}")
        # Create a new state with empty tickets list
        state["tickets"] = []
        return state

def categorize_tickets(state: Dict[str, Any]) -> Dict[str, Any]:
    """Categorize tickets into firing and resolved alerts"""
    try:
        # Access tickets using the property or fallback to direct access
        tickets = state.get("tickets", [])
        if not tickets:
            print("No tickets found to categorize")
        
        firing_alerts = []
        resolved_alerts = []
        
        for ticket in tickets:
            subject = ticket.get("subject", "").strip()
            if not subject:
                continue
            
            # Add clean subject for matching
            ticket["clean_subject"] = clean_subject(subject)
            
            # Categorize based on subject
            if is_firing_alert(subject):
                firing_alerts.append(ticket)
            elif is_resolved_alert(subject):
                resolved_alerts.append(ticket)
        
        print(f"Categorized {len(firing_alerts)} firing alerts and {len(resolved_alerts)} resolved alerts")
        
        # Create a new state dictionary and update it
        state["firing_alerts"] = firing_alerts
        state["resolved_alerts"] = resolved_alerts
        
        # Return the updated state
        return state
    except Exception as e:
        print(f"Error in categorize_tickets: {str(e)}")
        # Return state with empty categorized lists
        state["firing_alerts"] = []
        state["resolved_alerts"] = []
        return state

def match_alert_pairs(state: Dict[str, Any]) -> Dict[str, Any]:
    """Match firing alerts with their corresponding resolved alerts
    
    Business Rules:
    1. Match tickets based on subject similarity
    2. Auto-close pairs with time gap <= 5 minutes
    3. Send for manual review if time gap > 5 minutes
    """
    try:
        firing_alerts = state.get("firing_alerts", [])
        resolved_alerts = state.get("resolved_alerts", [])
        matched_pairs = []
        manual_review_tickets = []
        
        print(f"Attempting to match {len(firing_alerts)} firing alerts with {len(resolved_alerts)} resolved alerts")
        
        # Debug: Print sample of firing and resolved alerts
        print("\nDEBUG: Sample of Firing Alerts:")
        for i, alert in enumerate(firing_alerts[:3]):
            print(f"  {i+1}. ID: {alert.get('id')}, Subject: {alert.get('subject')}, Clean: {alert.get('clean_subject', '')}, Created: {alert.get('created_at')}")
        
        print("\nDEBUG: Sample of Resolved Alerts:")
        for i, alert in enumerate(resolved_alerts[:3]):
            print(f"  {i+1}. ID: {alert.get('id')}, Subject: {alert.get('subject')}, Clean: {alert.get('clean_subject', '')}, Created: {alert.get('created_at')}")
        
        for firing in firing_alerts:
            firing_subject = firing.get("clean_subject", "")
            firing_id = firing.get("id")
            firing_created = firing.get("created_at")
            
            if not firing_subject or not firing_id or not firing_created:
                print(f"Skipping firing alert with missing data: {firing.get('id', 'unknown')}")
                continue
            
            try:
                firing_time = datetime.fromisoformat(firing_created.replace("Z", "+00:00"))
            except Exception as e:
                print(f"Error parsing firing time for ticket #{firing_id}: {str(e)}")
                continue
            
            for resolved in resolved_alerts:
                resolved_subject = resolved.get("clean_subject", "")
                resolved_id = resolved.get("id")
                resolved_created = resolved.get("created_at")
                
                if not resolved_subject or not resolved_id or not resolved_created:
                    continue
                
                # Check if subjects match
                match_result = subjects_match(firing_subject, resolved_subject)
                if not match_result:
                    # Debug: Print subject comparison for first few non-matches
                    if firing_id % 10 == 0 and resolved_id % 10 == 0:  # Only print some samples to avoid log flooding
                        print(f"\nDEBUG: No match between:\n  Firing #{firing_id}: '{firing_subject}' (from '{firing.get('subject', '')}')\n  Resolved #{resolved_id}: '{resolved_subject}' (from '{resolved.get('subject', '')}')")
                    continue
                
                # Calculate time difference
                try:
                    resolved_time = datetime.fromisoformat(resolved_created.replace("Z", "+00:00"))
                    
                    # Calculate absolute time difference regardless of which came first
                    time_diff = resolved_time - firing_time
                    time_diff_minutes = time_diff.total_seconds() / 60
                    abs_time_diff_minutes = abs(time_diff_minutes)
                    
                    # Print match found
                    print(f"\nMATCH FOUND: Firing #{firing_id} with Resolved #{resolved_id}")
                    print(f"  Firing: '{firing.get('subject', '')}'")
                    print(f"  Resolved: '{resolved.get('subject', '')}'")  
                    print(f"  Time diff: {time_diff_minutes:.2f} minutes (absolute: {abs_time_diff_minutes:.2f})")
                    
                    # Create a deep copy of the ticket objects to avoid reference issues
                    firing_copy = firing.copy()
                    resolved_copy = resolved.copy()
                    
                    # Create pair object with complete ticket information
                    pair = {
                        "firing": firing_copy,
                        "resolved": resolved_copy,
                        "time_diff_minutes": time_diff_minutes,
                        "abs_time_diff_minutes": abs_time_diff_minutes,
                        "firing_id": firing_id,
                        "resolved_id": resolved_id,
                        "firing_subject": firing.get("subject", ""),
                        "resolved_subject": resolved.get("subject", "")
                    }
                    
                    # Check if absolute time difference is within auto-close threshold (5 minutes)
                    # This handles cases where the timestamps might be in a different order
                    if abs_time_diff_minutes <= 5:
                        print(f"  Result: Auto-close pair (within 5 minute threshold)")
                        matched_pairs.append(pair)
                    else:
                        print(f"  Result: Manual review pair (exceeds 5 minute threshold)")
                        manual_review_tickets.append(pair)
                except Exception as e:
                    print(f"Error calculating time difference: {str(e)}")
                    continue
        
        print(f"Found {len(matched_pairs)} pairs for auto-close and {len(manual_review_tickets)} pairs for manual review")
        
        # Update state with matched pairs
        state["matched_pairs"] = matched_pairs
        state["manual_review_tickets"] = manual_review_tickets
        
        return state
    except Exception as e:
        print(f"Error in match_alert_pairs: {str(e)}")
        traceback.print_exc()
        # Return state with empty matched pairs
        state["matched_pairs"] = []
        state["manual_review_tickets"] = []
        return state

def process_matched_pairs(state: Dict[str, Any]) -> Dict[str, Any]:
    """Process matched alert pairs for auto-closing"""
    try:
        # Access matched pairs using the property or fallback to direct access
        matched_pairs = state.get("matched_pairs", [])
        closed_tickets = []
        
        print(f"Processing {len(matched_pairs)} matched pairs for auto-closing")
        
        for pair in matched_pairs:
            try:
                firing_id = pair.get("firing_id")
                resolved_id = pair.get("resolved_id")
                time_diff = pair.get("time_diff_minutes", 0)
                
                if not firing_id or not resolved_id:
                    print(f"Missing ticket IDs in pair: {pair}")
                    continue
                
                print(f"Processing pair: Firing #{firing_id} and Resolved #{resolved_id} with time diff {time_diff:.2f} minutes")
                
                # Create closure notes
                firing_note = f"Auto-closed by Cloud Ticket Resolution System. Matched with ticket #{resolved_id} as an alert-resolution pair that resolved within 5 minutes."
                resolved_note = f"Auto-closed by Cloud Ticket Resolution System. Matched with ticket #{firing_id} as an alert-resolution pair that resolved within 5 minutes."
                
                # Close firing alert
                print(f"Attempting to close firing alert ticket #{firing_id}...")
                firing_closed = close_ticket(firing_id, firing_note)
                
                # Close resolved alert
                print(f"Attempting to close resolved alert ticket #{resolved_id}...")
                resolved_closed = close_ticket(resolved_id, resolved_note)
                
                if firing_closed and resolved_closed:
                    print(f"✓ Successfully closed alert pair: #{firing_id} and #{resolved_id}")
                    closed_tickets.append(pair)
                else:
                    print(f"✗ Failed to close one or both tickets in pair: #{firing_id} and #{resolved_id}")
            except Exception as e:
                print(f"Error processing pair: {str(e)}")
        
        print(f"Successfully closed {len(closed_tickets)} alert pairs out of {len(matched_pairs)} matched pairs")
        
        # Create a new state dictionary and update it
        state["closed_tickets"] = closed_tickets
        
        # Return the updated state
        return state
    except Exception as e:
        print(f"Error in process_matched_pairs: {str(e)}")
        # Return state with empty closed tickets
        state["closed_tickets"] = []
        return state

def handle_manual_review(state: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tickets that need manual review"""
    try:
        # Access manual review tickets using the property or fallback to direct access
        manual_review_tickets = state.get("manual_review_tickets", [])
        
        print(f"Handling {len(manual_review_tickets)} tickets that need manual review")
        
        if not manual_review_tickets:
            print("No tickets require manual review")
            return state
        
        # Create email content
        subject = f"Alert Resolution: {len(manual_review_tickets)} Ticket Pairs Need Manual Review"
        
        body = f"""
        <html>
        <head>
            <style>
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
            </style>
        </head>
        <body>
            <h2>Alert Resolution: Tickets Requiring Manual Review</h2>
            <p>The following alert-resolution pairs were identified but have a time difference greater than 5 minutes:</p>
            
            <table>
                <tr>
                    <th>Firing Ticket</th>
                    <th>Firing Subject</th>
                    <th>Firing Created</th>
                    <th>Resolved Ticket</th>
                    <th>Resolved Subject</th>
                    <th>Resolved Created</th>
                    <th>Time Diff (min)</th>
                </tr>
        """
        
        for pair in manual_review_tickets:
            try:
                firing_created = datetime.fromisoformat(pair.get("firing_created", "").replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
                resolved_created = datetime.fromisoformat(pair.get("resolved_created", "").replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
                
                body += f"""
                <tr>
                    <td>{pair.get("firing_id", "N/A")}</td>
                    <td>{pair.get("firing_subject", "N/A")}</td>
                    <td>{firing_created}</td>
                    <td>{pair.get("resolved_id", "N/A")}</td>
                    <td>{pair.get("resolved_subject", "N/A")}</td>
                    <td>{resolved_created}</td>
                    <td>{pair.get("time_diff_minutes", 0):.1f}</td>
                </tr>
                """
            except Exception as e:
                print(f"Error formatting ticket pair for email: {str(e)}")
        
        body += """
            </table>
            
            <p>Please review these tickets manually to determine if they should be closed.</p>
            
            <p>This is an automated message from the Cloud Ticket Resolution System.</p>
        </body>
        </html>
        """
        
        print("Sending email notification for manual review tickets")
        # Send email notification
        email_sent = send_email_notification(subject, body)
        
        if email_sent:
            print("✓ Email notification sent successfully")
        else:
            print("✗ Failed to send email notification")
        
        # Create a new state dictionary and update it
        state["email_sent"] = email_sent
        
        # Return the updated state
        return state
    except Exception as e:
        print(f"Error in handle_manual_review: {str(e)}")
        # Return state with email_sent set to False
        state["email_sent"] = False
        return state

def generate_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a summary of the alert resolution process"""
    try:
        # Safely access state values with defaults
        tickets = state.get("tickets", [])
        firing_alerts = state.get("firing_alerts", [])
        resolved_alerts = state.get("resolved_alerts", [])
        matched_pairs = state.get("matched_pairs", [])
        closed_tickets = state.get("closed_tickets", [])
        manual_review_tickets = state.get("manual_review_tickets", [])
        
        summary = {
            "total_tickets": len(tickets),
            "firing_alerts": len(firing_alerts),
            "resolved_alerts": len(resolved_alerts),
            "matched_pairs": len(matched_pairs),
            "closed_tickets": len(closed_tickets),
            "manual_review_tickets": len(manual_review_tickets),
            "email_sent": state.get("email_sent", False),
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Alert Resolution Summary: {json.dumps(summary, indent=2)}")
        
        # Create a new state dictionary and update it
        state["summary"] = summary
        
        # Return the updated state
        return state
    except Exception as e:
        print(f"Error in generate_summary: {str(e)}")
        # Return minimal valid summary
        state["summary"] = {
            "total_tickets": 0,
            "firing_alerts": 0,
            "resolved_alerts": 0,
            "matched_pairs": 0,
            "closed_tickets": 0,
            "manual_review_tickets": 0,
            "email_sent": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
        return state

def create_alert_resolution_graph() -> StateGraph:
    """Create the alert resolution agent graph"""
    # Initialize the graph
    graph = StateGraph(dict)
    
    # Add nodes
    graph.add_node("fetch_recent_tickets", fetch_recent_tickets)
    graph.add_node("categorize_tickets", categorize_tickets)
    graph.add_node("match_alert_pairs", match_alert_pairs)
    graph.add_node("process_matched_pairs", process_matched_pairs)
    graph.add_node("handle_manual_review", handle_manual_review)
    graph.add_node("generate_summary", generate_summary)
    
    # Add edges
    graph.add_edge("fetch_recent_tickets", "categorize_tickets")
    graph.add_edge("categorize_tickets", "match_alert_pairs")
    graph.add_edge("match_alert_pairs", "process_matched_pairs")
    graph.add_edge("process_matched_pairs", "handle_manual_review")
    graph.add_edge("handle_manual_review", "generate_summary")
    graph.add_edge("generate_summary", END)
    
    # Set entry point
    graph.set_entry_point("fetch_recent_tickets")
    print("inside alert resolution graph before compiling")
    
    # Compile the graph
    return graph.compile()

def run_alert_resolution(hours: int = 24) -> Dict[str, Any]:
    """Run the alert resolution process"""
    # Create initial state with default values for all required keys
    initial_state = {
        "hours": hours,
        "tickets": [],
        "firing_alerts": [],
        "resolved_alerts": [],
        "matched_pairs": [],
        "closed_tickets": [],
        "manual_review_tickets": [],
        "summary": {}
    }
    
    print(f"Initial state created with hours={hours}")
    
    try:
        # Fetch tickets directly to avoid potential issues with the graph
        tickets = fetch_tickets(hours)
        print(f"Fetched {len(tickets)} tickets from the last {hours} hours")
        
        # Update initial state with fetched tickets
        initial_state["tickets"] = tickets
        
        # Categorize tickets
        try:
            state = categorize_tickets(initial_state)
            print(f"Categorized {len(state['firing_alerts'])} firing alerts and {len(state['resolved_alerts'])} resolved alerts")
        except Exception as e:
            print(f"Error in categorize_tickets: {str(e)}")
            initial_state["summary"] = {"error": f"Error categorizing tickets: {str(e)}"}
            return initial_state
        
        # Match alert pairs
        try:
            state = match_alert_pairs(state)
            print(f"Found {len(state['matched_pairs'])} matched pairs and {len(state['manual_review_tickets'])} for manual review")
        except Exception as e:
            print(f"Error in match_alert_pairs: {str(e)}")
            state["summary"] = {"error": f"Error matching alert pairs: {str(e)}"}
            return state
        
        # Process matched pairs (auto-close)
        try:
            state = process_matched_pairs(state)
            print(f"Processed {len(state['closed_tickets'])} tickets for auto-close")
        except Exception as e:
            print(f"Error in process_matched_pairs: {str(e)}")
            state["summary"] = {"error": f"Error processing matched pairs: {str(e)}"}
            return state
        
        # Generate summary
        try:
            state = generate_summary(state)
            print("Generated summary successfully")
        except Exception as e:
            print(f"Error in generate_summary: {str(e)}")
            state["summary"] = {"error": f"Error generating summary: {str(e)}"}
        
        # Create a clean result dictionary with all required keys
        result = {
            "tickets": state.get("tickets", []),
            "firing_alerts": state.get("firing_alerts", []),
            "resolved_alerts": state.get("resolved_alerts", []),
            "matched_pairs": state.get("matched_pairs", []),
            "closed_tickets": state.get("closed_tickets", []),
            "manual_review_tickets": state.get("manual_review_tickets", []),
            "summary": state.get("summary", {})
        }
        
        # Log the final state for debugging
        print(f"Final result: {len(result['tickets'])} tickets, {len(result['matched_pairs'])} matched pairs, {len(result['closed_tickets'])} closed")
        
        return result
    except Exception as e:
        print(f"Error in run_alert_resolution: {str(e)}")
        import traceback
        traceback.print_exc()
        # Return the initial state with error information
        initial_state["summary"] = {"error": str(e)}
        return initial_state

if __name__ == "__main__":
    # Run the alert resolution process for the last 24 hours
    summary = run_alert_resolution(24)
    print(f"Alert Resolution Summary: {json.dumps(summary, indent=2)}")
