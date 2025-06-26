import os
from typing import Dict, List, Any, Annotated, TypedDict, Literal
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END
import requests
import json

# Load environment variables
load_dotenv()

# FreshService API configuration
API_KEY = os.getenv("FRESHSERVICE_API_KEY")
DOMAIN = os.getenv("FRESHSERVICE_DOMAIN")
BASE_URL = f"https://{DOMAIN}/api/v2/tickets"
auth = (API_KEY, "X")

# Define the state
class AgentState(TypedDict):
    ticket: Dict[str, Any]
    analysis: Dict[str, Any]
    action: Dict[str, Any]
    history: List[Dict[str, Any]]
    final_response: Dict[str, Any]

# Define the nodes for our agent graph
def analyzer(state: AgentState) -> AgentState:
    """Analyze the ticket and determine what action to take"""
    ticket = state["ticket"]
    
    # Create the LLM
    llm = ChatGroq(model=os.getenv("LLM_MODEL", "llama3-70b-8192"), temperature=0)
    
    # Create the prompt
    system_prompt = """You are an expert cloud support analyst. Your job is to analyze cloud support tickets and determine the best course of action.
    
    Based on the ticket information provided, analyze:
    1. The severity and priority of the issue
    2. What category the issue falls into (e.g., server down, performance issue, access problem)
    3. Whether this is a known issue with a standard resolution
    4. Whether this needs escalation to a specialized team
    
    Provide your analysis in a JSON format with the following fields:
    - severity: A number from 1-5, where 5 is most severe
    - category: The category of the issue
    - is_known_issue: true/false
    - needs_escalation: true/false
    - recommended_action: "resolve", "update", "escalate", or "investigate"
    - reasoning: Your reasoning for the recommended action
    """
    
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"""
        Ticket ID: {ticket.get('id')}
        Subject: {ticket.get('subject')}
        Description: {ticket.get('description', 'No description provided')}
        Status: {ticket.get('status')}
        Priority: {ticket.get('priority')}
        Created At: {ticket.get('created_at')}
        Updated At: {ticket.get('updated_at')}
        """)
    ])
    
    # Get the analysis
    response = llm.invoke(prompt)
    
    # Parse the response
    try:
        analysis = json.loads(response.content)
    except:
        # Fallback if JSON parsing fails
        analysis = {
            "severity": ticket.get("priority", 3),
            "category": "unknown",
            "is_known_issue": False,
            "needs_escalation": False,
            "recommended_action": "investigate",
            "reasoning": "Failed to parse analysis response"
        }
    
    # Update the state
    return {**state, "analysis": analysis}

def action_planner(state: AgentState) -> AgentState:
    """Plan the action to take based on the analysis"""
    ticket = state["ticket"]
    analysis = state["analysis"]
    
    # Create the LLM
    llm = ChatGroq(model=os.getenv("LLM_MODEL", "llama3-70b-8192"), temperature=0)
    
    # Create the prompt
    system_prompt = """You are an expert cloud support resolution system. Your job is to determine the specific action to take on a ticket based on analysis.
    
    For each action type, provide the necessary details:
    
    1. If resolving:
       - Provide a resolution note explaining what was done
       - Set the appropriate status (4 for Resolved, 5 for Closed)
    
    2. If updating:
       - Provide an update note for the customer
       - Set the appropriate status (2 for Open, 3 for Pending)
       - Update the priority if needed
    
    3. If escalating:
       - Provide an escalation note explaining why
       - Specify which group to escalate to (use group_id: 1 for Cloud Infrastructure, 2 for Security, 3 for Database)
    
    4. If investigating:
       - Provide a note on what additional information is needed
       - Keep the ticket in its current state
    
    Provide your action plan in a JSON format with the following fields:
    - action_type: "resolve", "update", "escalate", or "investigate"
    - status: The new status code (if changing)
    - priority: The new priority (if changing)
    - note: The note to add to the ticket
    - group_id: The group to escalate to (if escalating)
    """
    
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"""
        Ticket ID: {ticket.get('id')}
        Subject: {ticket.get('subject')}
        Description: {ticket.get('description', 'No description provided')}
        Current Status: {ticket.get('status')}
        Current Priority: {ticket.get('priority')}
        
        Analysis:
        Severity: {analysis.get('severity')}
        Category: {analysis.get('category')}
        Is Known Issue: {analysis.get('is_known_issue')}
        Needs Escalation: {analysis.get('needs_escalation')}
        Recommended Action: {analysis.get('recommended_action')}
        Reasoning: {analysis.get('reasoning')}
        """)
    ])
    
    # Get the action plan
    response = llm.invoke(prompt)
    
    # Parse the response
    try:
        action = json.loads(response.content)
    except:
        # Fallback if JSON parsing fails
        action = {
            "action_type": "investigate",
            "note": "Additional investigation is needed for this ticket.",
            "status": ticket.get("status")  # Keep current status
        }
    
    # Update the state
    return {**state, "action": action}

def execute_action(state: AgentState) -> AgentState:
    """Execute the planned action on the ticket"""
    ticket = state["ticket"]
    action = state["action"]
    
    ticket_id = ticket.get("id")
    action_type = action.get("action_type")
    
    # Prepare the API request based on action type
    if action_type == "resolve":
        # Resolve the ticket
        update_data = {
            "status": action.get("status", 4)  # Default to Resolved (4)
        }
        
        # Make the API request
        url = f"{BASE_URL}/{ticket_id}"
        response = requests.put(
            url, 
            auth=auth, 
            headers={"Content-Type": "application/json"}, 
            data=json.dumps(update_data)
        )
        
        # Add resolution note
        if "note" in action and action["note"]:
            note_url = f"{BASE_URL}/{ticket_id}/notes"
            note_data = {
                "body": action["note"],
                "private": False
            }
            note_response = requests.post(
                note_url,
                auth=auth,
                headers={"Content-Type": "application/json"},
                data=json.dumps(note_data)
            )
    
    elif action_type == "update":
        # Update the ticket
        update_data = {}
        if "status" in action:
            update_data["status"] = action["status"]
        if "priority" in action:
            update_data["priority"] = action["priority"]
        
        # Make the API request if there are updates
        if update_data:
            url = f"{BASE_URL}/{ticket_id}"
            response = requests.put(
                url, 
                auth=auth, 
                headers={"Content-Type": "application/json"}, 
                data=json.dumps(update_data)
            )
        
        # Add update note
        if "note" in action and action["note"]:
            note_url = f"{BASE_URL}/{ticket_id}/notes"
            note_data = {
                "body": action["note"],
                "private": False
            }
            note_response = requests.post(
                note_url,
                auth=auth,
                headers={"Content-Type": "application/json"},
                data=json.dumps(note_data)
            )
    
    elif action_type == "escalate":
        # Escalate the ticket
        update_data = {}
        if "group_id" in action:
            update_data["group_id"] = action["group_id"]
        if "status" in action:
            update_data["status"] = action["status"]
        
        # Make the API request
        url = f"{BASE_URL}/{ticket_id}"
        response = requests.put(
            url, 
            auth=auth, 
            headers={"Content-Type": "application/json"}, 
            data=json.dumps(update_data)
        )
        
        # Add escalation note
        if "note" in action and action["note"]:
            note_url = f"{BASE_URL}/{ticket_id}/notes"
            note_data = {
                "body": action["note"],
                "private": False
            }
            note_response = requests.post(
                note_url,
                auth=auth,
                headers={"Content-Type": "application/json"},
                data=json.dumps(note_data)
            )
    
    elif action_type == "investigate":
        # Just add a note for investigation
        if "note" in action and action["note"]:
            note_url = f"{BASE_URL}/{ticket_id}/notes"
            note_data = {
                "body": action["note"],
                "private": True  # Internal note
            }
            note_response = requests.post(
                note_url,
                auth=auth,
                headers={"Content-Type": "application/json"},
                data=json.dumps(note_data)
            )
    
    # Create a summary of what was done
    final_response = {
        "ticket_id": ticket_id,
        "action_taken": action_type,
        "details": action,
        "success": True,  # Assuming success for simplicity
        "timestamp": ticket.get("updated_at")
    }
    
    # Update history
    history = state.get("history", [])
    history_entry = {
        "timestamp": ticket.get("updated_at"),
        "action": action_type,
        "details": action
    }
    history.append(history_entry)
    
    # Update the state
    return {**state, "final_response": final_response, "history": history}

# Create the graph
def create_agent_graph():
    """Create the agent graph for ticket resolution"""
    # Initialize the graph
    graph = StateGraph(AgentState)
    
    # Add the nodes
    graph.add_node("analyzer", analyzer)
    graph.add_node("action_planner", action_planner)
    graph.add_node("execute_action", execute_action)
    
    # Add the edges
    graph.add_edge("analyzer", "action_planner")
    graph.add_edge("action_planner", "execute_action")
    graph.add_edge("execute_action", END)
    
    # Set the entry point
    graph.set_entry_point("analyzer")
    
    # Compile the graph
    return graph.compile()

def process_ticket(ticket_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single ticket through the agent graph"""
    # Create the initial state
    initial_state = {
        "ticket": ticket_data,
        "analysis": {},
        "action": {},
        "history": [],
        "final_response": {}
    }
    
    # Create the graph
    graph = create_agent_graph()
    
    # Run the graph
    result = graph.invoke(initial_state)
    
    # Return the final response
    return result["final_response"]

if __name__ == "__main__":
    # Example ticket for testing
    test_ticket = {
        "id": 12345,
        "subject": "Server CPU usage at 95%",
        "description": "Our production server has been experiencing high CPU usage for the last hour.",
        "status": 2,  # Open
        "priority": 3,  # Medium
        "created_at": "2025-06-16T12:00:00Z",
        "updated_at": "2025-06-16T12:05:00Z"
    }
    
    # Process the ticket
    result = process_ticket(test_ticket)
    print(json.dumps(result, indent=2))
