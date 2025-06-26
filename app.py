import streamlit as st
import pandas as pd
import datetime
import json
import requests
import pandas as pd
import json
from datetime import datetime, timedelta
import time
import os
import sys

# Add the parent directory to the path to import from agents
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure page
st.set_page_config(
    page_title="Cloud Ticket Resolution System",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #4285F4;
        margin-bottom: 1rem;
    }
    .sub-header {
        font-size: 1.5rem;
        color: #5F6368;
        margin-bottom: 1rem;
    }
    .card {
        padding: 1.5rem;
        border-radius: 0.5rem;
        background-color: #F8F9FA;
        margin-bottom: 1rem;
        border-left: 4px solid #4285F4;
    }
    .status-open {
        color: #EA4335;
        font-weight: bold;
    }
    .status-pending {
        color: #FBBC05;
        font-weight: bold;
    }
    .status-resolved {
        color: #34A853;
        font-weight: bold;
    }
    .status-closed {
        color: #5F6368;
        font-weight: bold;
    }
    .priority-1 {
        background-color: #EA4335;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
    }
    .priority-2 {
        background-color: #FBBC05;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
    }
    .priority-3 {
        background-color: #4285F4;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
    }
    .priority-4 {
        background-color: #34A853;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'tickets' not in st.session_state:
    st.session_state.tickets = []
if 'selected_ticket' not in st.session_state:
    st.session_state.selected_ticket = None
if 'processing_history' not in st.session_state:
    st.session_state.processing_history = {}
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = False
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = datetime.now()

# API endpoint
API_ENDPOINT = "http://localhost:8000"

# Status and priority mappings
STATUS_MAP = {
    2: "Open",
    3: "Pending",
    4: "Resolved",
    5: "Closed"
}

PRIORITY_MAP = {
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Urgent"
}

# Functions
def fetch_tickets(hours=24):
    """Fetch tickets from the API"""
    try:
        response = requests.get(f"{API_ENDPOINT}/tickets/?hours={hours}")
        if response.status_code == 200:
            st.session_state.tickets = response.json()
            st.session_state.last_refresh = datetime.now()
            return True
        else:
            st.error(f"Failed to fetch tickets: {response.status_code}")
            return False
    except Exception as e:
        st.error(f"Error connecting to API: {str(e)}")
        return False

def get_ticket_details(ticket_id):
    """Get details of a specific ticket"""
    try:
        response = requests.get(f"{API_ENDPOINT}/tickets/{ticket_id}")
        if response.status_code == 200:
            # Parse the JSON response
            data = response.json()
            
            # If the API returns a nested structure with 'ticket' key
            if isinstance(data, dict) and 'ticket' in data:
                ticket_data = data['ticket']
            else:
                ticket_data = data
            
            # Validate that we have a dictionary
            if not isinstance(ticket_data, dict):
                st.error(f"Invalid ticket data format: {type(ticket_data)}")
                return {}
                
            # Return the ticket data with default values for critical fields
            return {
                'id': ticket_data.get('id', ticket_id),
                'subject': ticket_data.get('subject', f'Ticket #{ticket_id}'),
                'status': ticket_data.get('status'),
                'priority': ticket_data.get('priority'),
                'created_at': ticket_data.get('created_at'),
                'updated_at': ticket_data.get('updated_at'),
                'description': ticket_data.get('description', ''),
                'requester_id': ticket_data.get('requester_id'),
                'responder_id': ticket_data.get('responder_id'),
                'group_id': ticket_data.get('group_id')
            }
        else:
            st.error(f"Failed to fetch ticket details: {response.status_code}")
            return {}
    except Exception as e:
        st.error(f"Error connecting to API: {str(e)}")
        return {}

def process_ticket_with_agent(ticket_id):
    """Process a ticket with the agent"""
    try:
        # First get the ticket details
        ticket = get_ticket_details(ticket_id)
        if not ticket:
            return False
        
        # Then send to the agent for processing
        response = requests.post(
            f"{API_ENDPOINT}/agent/action",
            json={
                "ticket_id": ticket_id,
                "action": "analyze",  # This will trigger the full agent workflow
                "details": {}
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            # Store the processing history
            if ticket_id not in st.session_state.processing_history:
                st.session_state.processing_history[ticket_id] = []
            
            st.session_state.processing_history[ticket_id].append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": result.get("action_taken", "unknown"),
                "details": result.get("details", {})
            })
            
            # Refresh the ticket list
            fetch_tickets()
            return True
        else:
            st.error(f"Failed to process ticket: {response.status_code}")
            return False
    except Exception as e:
        st.error(f"Error processing ticket: {str(e)}")
        return False

def format_datetime(dt_str):
    """Format a datetime string for display"""
    if not dt_str:
        return "Unknown"
        
    try:
        # Try different date formats
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",  # Standard ISO format with Z
            "%Y-%m-%dT%H:%M:%S.%fZ", # ISO format with microseconds and Z
            "%Y-%m-%dT%H:%M:%S",   # ISO format without timezone
            "%Y-%m-%dT%H:%M:%S.%f", # ISO format with microseconds
            "%Y-%m-%d %H:%M:%S"    # Simple format
        ]
        
        # Replace Z with +00:00 for better parsing
        if isinstance(dt_str, str) and dt_str.endswith('Z'):
            dt_str = dt_str.replace('Z', '+00:00')
            
        # Try parsing with different formats
        for fmt in formats:
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
                
        # If we have a string that looks like a timestamp, try direct conversion
        if isinstance(dt_str, (int, float)) or (isinstance(dt_str, str) and dt_str.isdigit()):
            try:
                dt = datetime.fromtimestamp(float(dt_str))
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass
                
        # If all parsing attempts fail, return the original string
        return str(dt_str)
    except Exception as e:
        print(f"Error formatting datetime: {e}")
        return str(dt_str)

def fetch_data(api_url, endpoint, params=None):
    """Fetch data from the API"""
    import requests
    
    try:
        url = f"{api_url}{endpoint}"
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Error fetching data: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        st.error(f"Error connecting to API: {str(e)}")
        return None

def post_data(api_url, endpoint, data=None, params=None):
    """Post data to the API"""
    import requests
    
    try:
        url = f"{api_url}{endpoint}"
        response = requests.post(url, json=data, params=params)
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            st.error(f"Error posting data: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        st.error(f"Error connecting to API: {str(e)}")
        return None

def display_ticket_list(api_url):
    """Display the list of tickets"""
    # Fetch tickets from API
    tickets_data = fetch_data(api_url, "/tickets/")
    
    if not tickets_data:
        st.warning("No tickets found or error fetching tickets")
        return
    
    # Convert to DataFrame for display
    tickets = tickets_data.get("tickets", [])
    if not tickets:
        st.info("No tickets available")
        return
    
    df = pd.DataFrame(tickets)
    
    # Display as a table
    st.dataframe(df)

def display_alert_resolution(api_url):
    """Display the alert resolution section"""
    st.subheader("Alert Resolution")
    
    col1, col2 = st.columns(2)
    
    with col1:
        hours = st.slider("Hours to look back", min_value=1, max_value=72, value=24)
    
    with col2:
        if st.button("Get Alert Pairs Summary"):
            with st.spinner("Analyzing tickets for alert pairs..."):
                summary = fetch_data(api_url, f"/alerts/summary", {"hours": hours})
                
                if summary:
                    st.session_state.alert_summary = summary
    
    # Display summary if available
    if hasattr(st.session_state, 'alert_summary') and st.session_state.alert_summary:
        summary = st.session_state.alert_summary
        
        # Create metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Tickets", summary.get("total_tickets", 0))
        col2.metric("Firing Alerts", summary.get("firing_alerts", 0))
        col3.metric("Resolved Alerts", summary.get("resolved_alerts", 0))
        col4.metric("Matched Pairs", summary.get("matched_pairs", 0))
        
        # Display auto-close pairs
        st.subheader("Pairs for Auto-Close")
        auto_close_pairs = summary.get("pairs_for_auto_close", [])
        
        if auto_close_pairs:
            # Convert to DataFrame
            df_auto = pd.DataFrame(auto_close_pairs)
            st.dataframe(df_auto)
            
            if st.button("Execute Auto-Close"):
                try:
                    with st.spinner("Closing matched alert pairs..."):
                        result = post_data(api_url, "/alerts/resolve", params={"hours": hours})
                        
                        if result and result.get("success"):
                            st.success("Successfully closed matched alert pairs!")
                            st.json(result.get("summary", {}))
                            
                            # Clear the alert summary to force a refresh
                            st.session_state.alert_summary = None
                            st.rerun()
                        else:
                            error_msg = result.get("error", "Unknown error") if result else "No response from server"
                            st.error(f"Failed to close alert pairs: {error_msg}")
                except Exception as e:
                    st.error(f"Error during auto-close: {str(e)}")
                    st.info("Please try refreshing the page and try again.")
                    import traceback
                    st.code(traceback.format_exc(), language="python")
        else:
            st.info("No pairs found for auto-closing")
        
        # Display manual review pairs
        st.subheader("Pairs for Manual Review")
        manual_review_pairs = summary.get("pairs_for_manual_review", [])
        
        if manual_review_pairs:
            # Convert to DataFrame
            df_manual = pd.DataFrame(manual_review_pairs)
            st.dataframe(df_manual)
            
            if st.button("Send for Manual Review"):
                with st.spinner("Sending pairs for manual review..."):
                    # This would trigger the email sending functionality
                    st.info("Email notification feature will be implemented soon")
        else:
            st.info("No pairs found for manual review")

# Sidebar
with st.sidebar:
    st.markdown("<h1 class='main-header'>☁️ Cloud Ticket System</h1>", unsafe_allow_html=True)
    
    # Refresh options
    st.subheader("Refresh Options")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh Now"):
            with st.spinner("Refreshing tickets..."):
                fetch_tickets()
    with col2:
        st.session_state.auto_refresh = st.checkbox("Auto Refresh", value=st.session_state.auto_refresh)
    
    hours_options = [1, 2, 4, 8, 12, 24, 48, 72]
    hours = st.selectbox("Fetch tickets from last:", hours_options, format_func=lambda x: f"{x} hours")
    
    # Filters
    st.subheader("Filters")
    status_filter = st.multiselect(
        "Status:",
        options=list(STATUS_MAP.values()),
        default=list(STATUS_MAP.values())
    )
    
    priority_filter = st.multiselect(
        "Priority:",
        options=list(PRIORITY_MAP.values()),
        default=list(PRIORITY_MAP.values())
    )
    
    # Auto-refresh logic
    if st.session_state.auto_refresh:
        time_since_refresh = (datetime.now() - st.session_state.last_refresh).total_seconds()
        if time_since_refresh > 60:  # Refresh every minute
            fetch_tickets(hours)
            st.session_state.last_refresh = datetime.now()
    
    # Display last refresh time
    st.caption(f"Last refreshed: {st.session_state.last_refresh.strftime('%Y-%m-%d %H:%M:%S')}")

# Main content
st.markdown("<h1 class='main-header'>Cloud Ticket Resolution Dashboard</h1>", unsafe_allow_html=True)

# Initialize tabs
tab1, tab2, tab3, tab4 = st.tabs(["📋 Tickets", "🔄 Alert Resolution", "📊 Analytics", "⚙️ Settings"])

with tab1:
    # Check if we have tickets
    if not st.session_state.tickets:
        st.info("No tickets found. Click 'Refresh Now' to fetch tickets.")
        if st.button("Fetch Tickets"):
            with st.spinner("Fetching tickets..."):
                fetch_tickets(hours)
    else:
        # Convert tickets to DataFrame for easier filtering
        df = pd.DataFrame(st.session_state.tickets)
        
        # Apply filters
        if 'status' in df.columns:
            df['status_text'] = df['status'].apply(lambda x: STATUS_MAP.get(x, str(x)))
            filtered_df = df[df['status_text'].isin(status_filter)]
        else:
            filtered_df = df
            
        if 'priority' in df.columns and len(filtered_df) > 0:
            filtered_df['priority_text'] = filtered_df['priority'].apply(lambda x: PRIORITY_MAP.get(x, str(x)))
            filtered_df = filtered_df[filtered_df['priority_text'].isin(priority_filter)]
        
        # Display ticket count
        st.markdown(f"<p class='sub-header'>Showing {len(filtered_df)} tickets</p>", unsafe_allow_html=True)
        
        # Display tickets
        for _, ticket in filtered_df.iterrows():
            col1, col2 = st.columns([3, 1])
            
            with col1:
                # Ticket card
                st.markdown(f"""
                <div class='card'>
                    <h3>#{ticket['id']} - {ticket['subject']}</h3>
                    <p>
                        <span class='status-{STATUS_MAP.get(ticket['status'], "").lower()}'>{STATUS_MAP.get(ticket['status'], ticket['status'])}</span> | 
                        <span class='priority-{ticket['priority']}'>{PRIORITY_MAP.get(ticket['priority'], ticket['priority'])}</span>
                    </p>
                    <p>Created: {format_datetime(ticket['created_at'])}</p>
                    <p>Updated: {format_datetime(ticket['updated_at'])}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                # Action buttons
                if st.button("View Details", key=f"view_{ticket['id']}"):
                    st.session_state.selected_ticket = ticket['id']
                
                if st.button("Process with Agent", key=f"process_{ticket['id']}"):
                    with st.spinner(f"Processing ticket #{ticket['id']}..."):
                        success = process_ticket_with_agent(ticket['id'])
                        if success:
                            st.success(f"Ticket #{ticket['id']} processed successfully!")
        
        # Ticket details modal
        if st.session_state.selected_ticket:
            ticket_id = st.session_state.selected_ticket
            ticket_details = get_ticket_details(ticket_id)
            
            if ticket_details:
                with st.expander(f"Ticket #{ticket_id} Details", expanded=True):
                    # Safely access ticket properties with defaults
                    subject = ticket_details.get('subject', f'Ticket #{ticket_id}')
                    status = ticket_details.get('status')
                    priority = ticket_details.get('priority')
                    created_at = ticket_details.get('created_at')
                    
                    st.markdown(f"## {subject}")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        status_display = STATUS_MAP.get(status, status) if status is not None else 'Unknown'
                        st.markdown(f"**Status:** {status_display}")
                    with col2:
                        priority_display = PRIORITY_MAP.get(priority, priority) if priority is not None else 'Unknown'
                        st.markdown(f"**Priority:** {priority_display}")
                    with col3:
                        created_display = format_datetime(created_at) if created_at else 'Unknown'
                        st.markdown(f"**Created:** {created_display}")
                    
                    st.markdown("### Description")
                    st.markdown(ticket_details.get('description', 'No description provided'))
                    
                    # Processing history
                    if ticket_id in st.session_state.processing_history:
                        st.markdown("### Processing History")
                        for entry in st.session_state.processing_history[ticket_id]:
                            st.markdown(f"**{entry['timestamp']}** - Action: {entry['action']}")
                            st.json(entry['details'])
                    
                    # Actions
                    st.markdown("### Actions")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if st.button("Process with Agent", key=f"process_detail_{ticket_id}"):
                            with st.spinner(f"Processing ticket #{ticket_id}..."):
                                success = process_ticket_with_agent(ticket_id)
                                if success:
                                    st.success(f"Ticket #{ticket_id} processed successfully!")
                    with col2:
                        if st.button("Close", key=f"close_{ticket_id}"):
                            st.session_state.selected_ticket = None
                            st.experimental_rerun()

with tab2:
    st.markdown("<h2 class='sub-header'>Alert Resolution</h2>", unsafe_allow_html=True)
    
    # Alert resolution section
    col1, col2 = st.columns(2)
    
    with col1:
        hours = st.slider("Hours to look back", min_value=1, max_value=72, value=24)
    
    with col2:
        if st.button("Get Alert Pairs Summary"):
            with st.spinner("Analyzing tickets for alert pairs..."):
                api_url = st.session_state.get("api_url", "http://localhost:8000")
                summary = fetch_data(api_url, f"/alerts/summary", {"hours": hours})
                
                if summary:
                    st.session_state.alert_summary = summary
    
    # Display summary if available
    if "alert_summary" in st.session_state and st.session_state.alert_summary:
        summary = st.session_state.alert_summary
        
        # Create metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Tickets", summary.get("total_tickets", 0))
        col2.metric("Firing Alerts", summary.get("firing_alerts", 0))
        col3.metric("Resolved Alerts", summary.get("resolved_alerts", 0))
        col4.metric("Matched Pairs", summary.get("matched_pairs", 0))
        
        # Display auto-close pairs
        st.subheader("Pairs for Auto-Close")
        auto_close_pairs = summary.get("pairs_for_auto_close", [])
        
        if auto_close_pairs:
            # Convert to DataFrame
            df_auto = pd.DataFrame(auto_close_pairs)
            st.dataframe(df_auto)
            
            if st.button("Execute Auto-Close"):
                with st.spinner("Closing matched alert pairs..."):
                    api_url = st.session_state.get("api_url", "http://localhost:8000")
                    result = post_data(api_url, "/alerts/resolve", params={"hours": hours})
                    
                    if result and result.get("success"):
                        st.success("Successfully closed matched alert pairs!")
                        st.json(result.get("summary", {}))
        else:
            st.info("No pairs found for auto-closing")
        
        # Display manual review pairs
        st.subheader("Pairs for Manual Review")
        manual_review_pairs = summary.get("pairs_for_manual_review", [])
        
        if manual_review_pairs:
            # Convert to DataFrame
            df_manual = pd.DataFrame(manual_review_pairs)
            st.dataframe(df_manual)
            
            if st.button("Send for Manual Review"):
                with st.spinner("Sending pairs for manual review..."):
                    # This would trigger the email sending functionality
                    st.info("Email notification feature will be implemented soon")
        else:
            st.info("No pairs found for manual review")

with tab3:
    st.markdown("<h2 class='sub-header'>Ticket Analytics</h2>", unsafe_allow_html=True)
    
    if st.session_state.tickets:
        df = pd.DataFrame(st.session_state.tickets)
        
        # Add text columns
        if 'status' in df.columns:
            df['status_text'] = df['status'].apply(lambda x: STATUS_MAP.get(x, str(x)))
        if 'priority' in df.columns:
            df['priority_text'] = df['priority'].apply(lambda x: PRIORITY_MAP.get(x, str(x)))
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Status distribution
            if 'status_text' in df.columns:
                st.subheader("Tickets by Status")
                status_counts = df['status_text'].value_counts()
                st.bar_chart(status_counts)
        
        with col2:
            # Priority distribution
            if 'priority_text' in df.columns:
                st.subheader("Tickets by Priority")
                priority_counts = df['priority_text'].value_counts()
                st.bar_chart(priority_counts)
        
        # Tickets over time
        if 'created_at' in df.columns:
            st.subheader("Tickets Created Over Time")
            df['created_date'] = pd.to_datetime(df['created_at']).dt.date
            date_counts = df['created_date'].value_counts().sort_index()
            st.line_chart(date_counts)
    else:
        st.info("No ticket data available for analytics.")

with tab3:
    st.markdown("<h2 class='sub-header'>System Settings</h2>", unsafe_allow_html=True)
    
    st.subheader("API Configuration")
    api_endpoint = st.text_input("API Endpoint", value=API_ENDPOINT)
    
    if st.button("Save Settings"):
        API_ENDPOINT = api_endpoint
        st.success("Settings saved successfully!")
    
    st.subheader("Agent Configuration")
    st.markdown("""
    The Cloud Ticket Resolution System uses an intelligent agent powered by LangGraph to:
    
    1. Analyze incoming tickets
    2. Determine the appropriate action based on ticket content
    3. Execute actions such as resolving, updating, or escalating tickets
    4. Learn from past resolutions to improve future handling
    """)
    
    # Test connection
    if st.button("Test API Connection"):
        try:
            response = requests.get(f"{API_ENDPOINT}/")
            if response.status_code == 200:
                st.success("API connection successful!")
            else:
                st.error(f"API connection failed: {response.status_code}")
        except Exception as e:
            st.error(f"Error connecting to API: {str(e)}")

# Footer
st.markdown("---")
st.markdown("© 2025 CTE Cloud Ticket Resolution System | Powered by LangGraph Agents & FastAPI")
