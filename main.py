from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import requests
import json
from datetime import datetime, timedelta, timezone
import logging
from dotenv import load_dotenv
import asyncio
import sys
import os

# Add the parent directory to the path to import from agents
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# FreshService API configuration
API_KEY = os.getenv("FRESHSERVICE_API_KEY")
DOMAIN = os.getenv("FRESHSERVICE_DOMAIN")
BASE_URL = f"https://{DOMAIN}/api/v2/tickets"
auth = (API_KEY, "X")

# Status mapping
STATUS_MAP = {
    2: "Open",
    3: "Pending",
    4: "Resolved",
    5: "Closed"
}

# Import the alert resolution agent
from agents.alert_resolution_agent import run_alert_resolution

# Create FastAPI app
app = FastAPI(title="Cloud Ticket Resolution System API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class Ticket(BaseModel):
    id: int
    subject: str
    description: Optional[str] = None
    status: int
    priority: Optional[int] = None
    created_at: str
    updated_at: str
    requester_id: Optional[int] = None
    responder_id: Optional[int] = None
    group_id: Optional[int] = None
    
class TicketUpdate(BaseModel):
    status: Optional[int] = None
    priority: Optional[int] = None
    responder_id: Optional[int] = None
    group_id: Optional[int] = None
    note: Optional[str] = None

class AgentAction(BaseModel):
    ticket_id: int
    action: str  # "close", "update", "escalate"
    details: Optional[Dict[str, Any]] = None

# Helper functions
def to_ist(dt_utc):
    ist_offset = timedelta(hours=5, minutes=30)
    return dt_utc + ist_offset

async def fetch_tickets(hours_ago=2, per_page=50):
    """Fetch tickets updated since the specified hours ago"""
    try:
        time_ago = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        updated_since = time_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        url = f"{BASE_URL}?updated_since={updated_since}&per_page={per_page}"
        response = requests.get(url, auth=auth)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch tickets: {response.status_code} - {response.text}")
            return []
            
        data = response.json()
        tickets = data.get("tickets", [])
        logger.info(f"Fetched {len(tickets)} tickets")
        return tickets
    except Exception as e:
        logger.error(f"Error fetching tickets: {str(e)}")
        return []

async def update_ticket(ticket_id: int, update_data: dict):
    """Update a ticket in FreshService"""
    try:
        url = f"{BASE_URL}/{ticket_id}"
        response = requests.put(
            url, 
            auth=auth, 
            headers={"Content-Type": "application/json"}, 
            data=json.dumps(update_data)
        )
        
        if response.status_code == 200:
            logger.info(f"Successfully updated ticket #{ticket_id}")
            return True
        else:
            logger.error(f"Failed to update ticket #{ticket_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error updating ticket #{ticket_id}: {str(e)}")
        return False

async def add_note_to_ticket(ticket_id: int, note_content: str, is_private: bool = True):
    """Add a note to a ticket"""
    try:
        url = f"{BASE_URL}/{ticket_id}/notes"
        payload = {
            "body": note_content,
            "private": is_private
        }
        
        response = requests.post(
            url,
            auth=auth,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        
        if response.status_code == 201:
            logger.info(f"Successfully added note to ticket #{ticket_id}")
            return True
        else:
            logger.error(f"Failed to add note to ticket #{ticket_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error adding note to ticket #{ticket_id}: {str(e)}")
        return False

# API Endpoints
@app.get("/")
async def root():
    return {"message": "Cloud Ticket Resolution System API"}

@app.get("/tickets/", response_model=List[Dict[str, Any]])
async def get_tickets(hours: int = 24):
    """Get tickets updated in the last specified hours"""
    tickets = await fetch_tickets(hours_ago=hours)
    return tickets

@app.get("/tickets/{ticket_id}", response_model=Dict[str, Any])
async def get_ticket(ticket_id: int):
    """Get a specific ticket by ID"""
    try:
        url = f"{BASE_URL}/{ticket_id}"
        response = requests.get(url, auth=auth)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Failed to fetch ticket: {response.text}")
            
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching ticket: {str(e)}")

@app.put("/tickets/{ticket_id}", response_model=Dict[str, Any])
async def update_ticket_endpoint(ticket_id: int, ticket_update: TicketUpdate):
    """Update a ticket"""
    update_data = ticket_update.dict(exclude_unset=True)
    
    # Add note if provided
    note = None
    if "note" in update_data:
        note = update_data.pop("note")
    
    # Update ticket
    success = await update_ticket(ticket_id, update_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update ticket")
    
    # Add note if provided
    if note:
        note_success = await add_note_to_ticket(ticket_id, note)
        if not note_success:
            logger.warning(f"Ticket #{ticket_id} was updated but failed to add note")
    
    # Get updated ticket
    updated_ticket = await get_ticket(ticket_id)
    return updated_ticket

@app.post("/agent/action", response_model=Dict[str, Any])
async def agent_action(action: AgentAction, background_tasks: BackgroundTasks):
    """Process an agent action on a ticket"""
    try:
        ticket_id = action.ticket_id
        
        if action.action == "close":
            # Close the ticket
            update_data = {"status": 5}  # 5 = Closed
            success = await update_ticket(ticket_id, update_data)
            
            if success and action.details and "note" in action.details:
                await add_note_to_ticket(ticket_id, action.details["note"])
                
            return {"success": success, "message": f"Ticket #{ticket_id} closed successfully"}
            
        elif action.action == "update":
            # Update the ticket
            if not action.details:
                raise HTTPException(status_code=400, detail="Details required for update action")
                
            update_data = {}
            if "status" in action.details:
                update_data["status"] = action.details["status"]
            if "priority" in action.details:
                update_data["priority"] = action.details["priority"]
            
            success = await update_ticket(ticket_id, update_data)
            
            if success and "note" in action.details:
                await add_note_to_ticket(ticket_id, action.details["note"])
                
            return {"success": success, "message": f"Ticket #{ticket_id} updated successfully"}
            
        elif action.action == "escalate":
            # Escalate the ticket
            if not action.details or "group_id" not in action.details:
                raise HTTPException(status_code=400, detail="Group ID required for escalation")
                
            update_data = {
                "group_id": action.details["group_id"],
                "status": 2  # Set to Open
            }
            
            success = await update_ticket(ticket_id, update_data)
            
            if success and "note" in action.details:
                await add_note_to_ticket(
                    ticket_id, 
                    f"Ticket escalated to group {action.details['group_id']}. {action.details['note']}"
                )
                
            return {"success": success, "message": f"Ticket #{ticket_id} escalated successfully"}
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")
            
    except Exception as e:
        logger.error(f"Error processing agent action: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing agent action: {str(e)}")

@app.post("/alerts/resolve", response_model=Dict[str, Any])
async def resolve_alert_pairs(hours: int = Query(24, description="Number of hours to look back for tickets")):
    """Run the alert resolution agent to automatically close paired alert tickets"""
    try:
        # Run in a background task to avoid blocking
        def run_resolution():
            return run_alert_resolution(hours)
        
        # Execute the alert resolution process
        summary = run_resolution()
        
        return {
            "success": True,
            "message": f"Alert resolution process completed successfully",
            "summary": summary
        }
    except Exception as e:
        logger.error(f"Error running alert resolution: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error running alert resolution: {str(e)}")

@app.get("/alerts/summary", response_model=Dict[str, Any])
async def get_alert_resolution_summary(hours: int = Query(24, description="Number of hours to look back for tickets")):
    """Get a summary of alert pairs without taking action"""
    try:
        # Import necessary functions
        from agents.alert_resolution_agent import fetch_recent_tickets, categorize_tickets, match_alert_pairs, subjects_match, AlertResolutionState
        
        # Create initial state
        initial_state = AlertResolutionState({
            "hours": hours,
            "tickets": [],
            "firing_alerts": [],
            "resolved_alerts": [],
            "matched_pairs": [],
            "manual_review_tickets": []
        })
        
        # Run the first three steps of the process
        state = fetch_recent_tickets(initial_state)
        state = categorize_tickets(state)
        state = match_alert_pairs(state)
        
        # Return the summary
        return {
            "total_tickets": len(state["tickets"]),
            "firing_alerts": len(state["firing_alerts"]),
            "resolved_alerts": len(state["resolved_alerts"]),
            "matched_pairs": len(state["matched_pairs"]),
            "manual_review_tickets": len(state["manual_review_tickets"]),
            "pairs_for_auto_close": [
                {
                    "firing_id": pair["firing_id"],
                    "firing_subject": pair["firing_subject"],
                    "resolved_id": pair["resolved_id"],
                    "resolved_subject": pair["resolved_subject"],
                    "time_diff_minutes": pair["time_diff_minutes"]
                } for pair in state["matched_pairs"]
            ],
            "pairs_for_manual_review": [
                {
                    "firing_id": pair["firing_id"],
                    "firing_subject": pair["firing_subject"],
                    "resolved_id": pair["resolved_id"],
                    "resolved_subject": pair["resolved_subject"],
                    "time_diff_minutes": pair["time_diff_minutes"]
                } for pair in state["manual_review_tickets"]
            ]
        }
    except Exception as e:
        logger.error(f"Error getting alert resolution summary: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting alert resolution summary: {str(e)}")

@app.post("/alerts/resolve", response_model=Dict[str, Any])
async def resolve_alert_tickets(hours: int = Query(24, description="Number of hours to look back for tickets")):
    """Run the alert resolution agent to auto-close matched alert pairs"""
    try:
        # Import necessary functions
        from agents.alert_resolution_agent import run_alert_resolution, subjects_match
        
        logger.info(f"Starting alert resolution for the last {hours} hours")
        
        # Run the alert resolution agent with explicit try/except
        try:
            result = run_alert_resolution(hours=hours)
            logger.info(f"Alert resolution completed successfully, processing results")
        except Exception as agent_error:
            logger.error(f"Error in alert resolution agent: {str(agent_error)}")
            # Create a minimal valid result structure
            result = {
                "tickets": [],
                "firing_alerts": [],
                "resolved_alerts": [],
                "matched_pairs": [],
                "closed_tickets": [],
                "manual_review_tickets": [],
                "summary": {"error": str(agent_error)}
            }
        
        # Validate result structure and log available keys
        if not isinstance(result, dict):
            logger.error(f"Expected dict result, got {type(result)}")
            result = {}
        else:
            logger.info(f"Result keys: {list(result.keys())}")
        
        # Create a new result dictionary with guaranteed keys
        safe_result = {
            "tickets": [],
            "firing_alerts": [],
            "resolved_alerts": [],
            "matched_pairs": [],
            "closed_tickets": [],
            "manual_review_tickets": [],
            "summary": {}
        }
        
        # Copy values from result to safe_result, with explicit error handling
        for key in safe_result.keys():
            try:
                if key in result and result[key] is not None:
                    safe_result[key] = result[key]
            except Exception as key_error:
                logger.error(f"Error accessing key '{key}' in result: {str(key_error)}")
        
        # Safely access result keys with defaults
        tickets = safe_result.get("tickets", [])
        firing_alerts = safe_result.get("firing_alerts", [])
        resolved_alerts = safe_result.get("resolved_alerts", [])
        matched_pairs = safe_result.get("matched_pairs", [])
        closed_tickets = safe_result.get("closed_tickets", [])
        manual_review = safe_result.get("manual_review_tickets", [])
        summary = safe_result.get("summary", {})
        
        logger.info(f"Result stats: {len(tickets)} tickets, {len(matched_pairs)} matched pairs, {len(closed_tickets)} closed")
        
        # Check for errors in the summary
        has_error = isinstance(summary, dict) and "error" in summary
        if has_error:
            logger.warning(f"Alert resolution completed with error: {summary.get('error')}")
        
        # Return the result with safe access
        return {
            "success": not has_error,
            "error": summary.get("error", "") if has_error else "",
            "total_tickets": len(tickets),
            "firing_alerts": len(firing_alerts),
            "resolved_alerts": len(resolved_alerts),
            "matched_pairs": len(matched_pairs),
            "closed_tickets": len(closed_tickets),
            "manual_review_tickets": len(manual_review),
            "email_sent": safe_result.get("email_sent", False),
            "summary": summary,
            "pairs_closed": [
                {
                    "firing_id": pair.get("firing_id", ""),
                    "firing_subject": pair.get("firing_subject", ""),
                    "resolved_id": pair.get("resolved_id", ""),
                    "resolved_subject": pair.get("resolved_subject", ""),
                    "time_diff_minutes": pair.get("time_diff_minutes", 0)
                } for pair in closed_tickets if isinstance(pair, dict)
            ],
            "pairs_for_manual_review": [
                {
                    "firing_id": pair.get("firing_id", ""),
                    "firing_subject": pair.get("firing_subject", ""),
                    "resolved_id": pair.get("resolved_id", ""),
                    "resolved_subject": pair.get("resolved_subject", ""),
                    "time_diff_minutes": pair.get("time_diff_minutes", 0)
                } for pair in manual_review if isinstance(pair, dict)
            ]
        }
    except Exception as e:
        logger.error(f"Error resolving alert tickets: {str(e)}")
        # Return a more informative error response
        return {
            "success": False,
            "error": str(e),
            "total_tickets": 0,
            "firing_alerts": 0,
            "resolved_alerts": 0,
            "matched_pairs": 0,
            "closed_tickets": 0,
            "manual_review_tickets": 0,
            "email_sent": False,
            "summary": {"error": str(e)},
            "pairs_closed": [],
            "pairs_for_manual_review": []
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
