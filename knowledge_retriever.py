import os
import json
from typing import List, Dict, Any, Optional
from langchain_groq import GroqEmbeddings
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class KnowledgeRetriever:
    """
    A class to retrieve relevant knowledge from past tickets to help resolve current tickets.
    """
    
    def __init__(self, knowledge_base_dir: str):
        """
        Initialize the knowledge retriever.
        
        Args:
            knowledge_base_dir: Directory containing the knowledge base files
        """
        self.knowledge_base_dir = knowledge_base_dir
        self.embeddings = GroqEmbeddings(model=os.getenv("LLM_MODEL", "llama3-70b-8192"))
        self.vector_store = None
        self.initialize_vector_store()
    
    def initialize_vector_store(self):
        """Initialize the vector store from the knowledge base files"""
        # Check if the knowledge base directory exists
        if not os.path.exists(self.knowledge_base_dir):
            os.makedirs(self.knowledge_base_dir)
            # Create an empty vector store
            self.vector_store = FAISS.from_texts(
                ["Placeholder document"], self.embeddings
            )
            return
        
        # Check if there's a persisted vector store
        vector_store_path = os.path.join(self.knowledge_base_dir, "vector_store")
        if os.path.exists(vector_store_path):
            # Load the existing vector store
            self.vector_store = FAISS.load_local(vector_store_path, self.embeddings)
        else:
            # Create a new vector store from the knowledge base files
            documents = self._load_documents()
            if documents:
                self.vector_store = FAISS.from_documents(documents, self.embeddings)
                # Save the vector store
                self.vector_store.save_local(vector_store_path)
            else:
                # Create an empty vector store
                self.vector_store = FAISS.from_texts(
                    ["Placeholder document"], self.embeddings
                )
    
    def _load_documents(self) -> List[Document]:
        """Load documents from the knowledge base files"""
        documents = []
        
        # Look for JSON files in the knowledge base directory
        for filename in os.listdir(self.knowledge_base_dir):
            if filename.endswith(".json") and filename != "vector_store.json":
                file_path = os.path.join(self.knowledge_base_dir, filename)
                try:
                    with open(file_path, "r") as f:
                        tickets = json.load(f)
                        
                    # Convert each ticket to a document
                    for ticket in tickets:
                        # Create the document content
                        content = f"""
                        Ticket ID: {ticket.get('id')}
                        Subject: {ticket.get('subject')}
                        Description: {ticket.get('description', 'No description provided')}
                        Status: {ticket.get('status')}
                        Priority: {ticket.get('priority')}
                        Resolution: {ticket.get('resolution', 'No resolution provided')}
                        """
                        
                        # Create metadata
                        metadata = {
                            "ticket_id": ticket.get('id'),
                            "subject": ticket.get('subject'),
                            "status": ticket.get('status'),
                            "priority": ticket.get('priority'),
                            "category": ticket.get('category', 'unknown')
                        }
                        
                        # Create the document
                        doc = Document(page_content=content, metadata=metadata)
                        documents.append(doc)
                        
                except Exception as e:
                    print(f"Error loading knowledge base file {filename}: {str(e)}")
        
        return documents
    
    def add_ticket_to_knowledge_base(self, ticket: Dict[str, Any], resolution: Optional[str] = None):
        """
        Add a resolved ticket to the knowledge base.
        
        Args:
            ticket: The ticket data
            resolution: The resolution of the ticket
        """
        # Add resolution to the ticket if provided
        if resolution:
            ticket["resolution"] = resolution
        
        # Create the document content
        content = f"""
        Ticket ID: {ticket.get('id')}
        Subject: {ticket.get('subject')}
        Description: {ticket.get('description', 'No description provided')}
        Status: {ticket.get('status')}
        Priority: {ticket.get('priority')}
        Resolution: {ticket.get('resolution', 'No resolution provided')}
        """
        
        # Create metadata
        metadata = {
            "ticket_id": ticket.get('id'),
            "subject": ticket.get('subject'),
            "status": ticket.get('status'),
            "priority": ticket.get('priority'),
            "category": ticket.get('category', 'unknown')
        }
        
        # Create the document
        doc = Document(page_content=content, metadata=metadata)
        
        # Add the document to the vector store
        if self.vector_store:
            self.vector_store.add_documents([doc])
            
            # Save the updated vector store
            vector_store_path = os.path.join(self.knowledge_base_dir, "vector_store")
            self.vector_store.save_local(vector_store_path)
        
        # Also save to a JSON file for backup
        self._save_ticket_to_json(ticket)
    
    def _save_ticket_to_json(self, ticket: Dict[str, Any]):
        """Save a ticket to a JSON file"""
        # Determine the category for filing
        category = ticket.get('category', 'general')
        
        # Create the filename
        filename = f"{category}_tickets.json"
        file_path = os.path.join(self.knowledge_base_dir, filename)
        
        # Load existing tickets or create a new list
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    tickets = json.load(f)
            except:
                tickets = []
        else:
            tickets = []
        
        # Add the new ticket
        tickets.append(ticket)
        
        # Save the updated list
        with open(file_path, "w") as f:
            json.dump(tickets, f, indent=2)
    
    def retrieve_similar_tickets(self, query: str, k: int = 3) -> List[Document]:
        """
        Retrieve similar tickets from the knowledge base.
        
        Args:
            query: The query to search for
            k: The number of similar tickets to retrieve
            
        Returns:
            A list of similar tickets
        """
        if not self.vector_store:
            return []
        
        # Search for similar tickets
        docs = self.vector_store.similarity_search(query, k=k)
        return docs
    
    def get_resolution_suggestion(self, ticket: Dict[str, Any]) -> str:
        """
        Get a resolution suggestion for a ticket based on similar past tickets.
        
        Args:
            ticket: The ticket data
            
        Returns:
            A suggested resolution
        """
        # Create a query from the ticket
        query = f"""
        Subject: {ticket.get('subject')}
        Description: {ticket.get('description', 'No description provided')}
        """
        
        # Retrieve similar tickets
        similar_tickets = self.retrieve_similar_tickets(query)
        
        if not similar_tickets:
            return "No similar tickets found in the knowledge base."
        
        # Create a context from the similar tickets
        context = "\n\n".join([doc.page_content for doc in similar_tickets])
        
        # Create an LLM to generate a resolution suggestion
        llm = ChatGroq(model=os.getenv("LLM_MODEL", "llama3-70b-8192"), temperature=0)
        
        # Create a prompt
        prompt = ChatPromptTemplate.from_template(
            """You are an expert cloud support analyst. Based on the current ticket and similar past tickets,
            suggest a resolution for the current ticket.
            
            Current Ticket:
            Subject: {subject}
            Description: {description}
            
            Similar Past Tickets:
            {context}
            
            Provide a concise resolution suggestion that could help resolve the current ticket.
            Include specific steps if applicable.
            """
        )
        
        # Create the chain
        chain = prompt | llm | StrOutputParser()
        
        # Generate the resolution suggestion
        resolution = chain.invoke({
            "subject": ticket.get('subject'),
            "description": ticket.get('description', 'No description provided'),
            "context": context
        })
        
        return resolution

if __name__ == "__main__":
    # Example usage
    knowledge_base_dir = "../knowledge_base"
    retriever = KnowledgeRetriever(knowledge_base_dir)
    
    # Example ticket
    ticket = {
        "id": 12345,
        "subject": "Server CPU usage at 95%",
        "description": "Our production server has been experiencing high CPU usage for the last hour.",
        "status": 2,  # Open
        "priority": 3,  # Medium
        "category": "performance"
    }
    
    # Get a resolution suggestion
    suggestion = retriever.get_resolution_suggestion(ticket)
    print(suggestion)
    
    # Add the ticket to the knowledge base with a resolution
    retriever.add_ticket_to_knowledge_base(
        ticket, 
        "Identified a runaway process consuming excessive CPU. Restarted the process and implemented monitoring."
    )
