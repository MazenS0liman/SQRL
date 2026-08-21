#!/usr/bin/python
"""
API Models Module
=================

Overview
--------

This module provides Pydantic data models for agent-related API requests and responses.
It defines models for conversation messages, agent requests and agent responses.

Models
------

- :class:`ConversationMessage`: Represents a single message in conversation history
- :class:`AgentRequest`: Represents a agent API request
- :class:`AgentResponse`: Represents a agent API response

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from enum import Enum
from datetime import datetime
from importlib import metadata
from pydantic import BaseModel, Field, AnyUrl, model_validator
from typing import List, Optional, Dict, Any, Literal

# Schemas
from squirrel.schemas.file import FileProcessingIssue, FileType

# ——————————————————————————————————————————————————————————————
# Agentic API Models

# --- Conversation Message Model ---
class ConversationMessage(BaseModel):
    """
    Conversation Message Model
    --------------------------
    
    **Description:**
    
        Represents a single message exchange in a conversation, tracking the speaker
        role (user or assistant), message content, and timestamp.

    """
    role: Literal['user', 'assistant'] = Field(..., description="The role of the message sender (user or assistant)")
    content: str = Field(..., description="The content of the message")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="The timestamp when the message was created")

# --- LLM Tokens Model ---
class LLMTokens(BaseModel):
    """
    LLM Tokens Model
    ----------------
    
    **Description:**
    
        Represents the token usage details for a chat interaction, including the number
        of tokens used in the prompt, completion, and total.
    
    """
    #: Number of tokens used in the prompt
    promptTokens: int = Field(..., description="Number of tokens used in the prompt")
    
    #: Number of tokens used in the completion
    completionTokens: int = Field(..., description="Number of tokens used in the completion")
    
    #: Total number of tokens used (prompt + completion)
    totalTokens: int = Field(..., description="Total number of tokens used (prompt + completion)")

# --- Agent Request Model ---
class AgentRequest(BaseModel):
    """
    Chat Request Model
    ------------------
    
    **Description:**
    
        Represents the structure of a chat API request, including the user's query,
        conversation history, and optional parameters for response generation.

    """
    #: The user's query or message to the assistant
    query: str = Field(
        min_length=0,
        max_length=5000,
        description="The user's query or message to the assistant"
    )

    #: Unqiue project identifier
    projectId: str = Field(..., description="Unique identifier for the project")

    #: Unique conversation identifier
    conversationId: str = Field(
        pattern=r'^[a-zA-Z0-9_-]+$',
        description="Unique identifier for the conversation (alphanumeric, underscores, hyphens)"
    )

    #: Optional request body content
    body: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional request body content for additional context or parameters"
    )

    #: Block Storage URLs of files to be processed
    fileUrls: Optional[List[str]] = Field(
        default=[],
        min_items=0,
        description="List of Block Storage URLs of files to be processed"
    )

    #: Optional recent conversation history for context (up to 10 messages)
    conversationHistory: Optional[List[ConversationMessage]] = Field(
        default=[],
        max_items=10,
        description="Optional recent conversation history for context (up to 10 messages)"
    )

    #: Optional additional parameters as metadata
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional additional parameters as metadata for the request"
    )
    
    #: Request identifier
    requestId: str = Field(
        default=None,
        description="Unique identifier for the request (for tracking and correlation)"
    )
    
    @model_validator(mode='before')
    @classmethod
    def convert_empty_strings_to_none(cls, data: Any) -> Any:
        """Convert empty strings to None for optional URL fields before validation"""
        if isinstance(data, dict):
            # Handle sourceDocumentUrl
            if 'sourceDocumentUrl' in data and data['sourceDocumentUrl'] == '':
                data['sourceDocumentUrl'] = None
            # Handle generatedId
            if 'generatedId' in data and data['generatedId'] == '':
                data['generatedId'] = None
        return data

# --- Agent Response Model ---
class AgentResponse(BaseModel):
    """
    Agent Response Data Model
    -------------------------
    
    **Description:**
    
        Represents the data structure of a chat API response, including the assistant's
        reply, any generated files, and optional metadata.

    """
    #: The assistant's reply to the user's query
    reply: str = Field(..., description="The assistant's reply to the user's query")
    
    #: The action taken by the agent
    action: Literal["chat", "explore", "build"] = Field(..., description="The action taken by the orchestator agent.")

    #: Unqiue project identifier
    projectId: str = Field(..., description="Unique identifier for the project")

    #: Unique conversation identifier
    conversationId: str = Field(..., description="Unique identifier for the conversation") 
    
    #: Block Storage URLs of files
    fileUrls: Optional[List[str]] = Field(
        default=[],
        min_items=0,
        description="List of Block Storage URLs of files."
    )

    #: Optional response body content
    body: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional response body content for additional context or parameters"
    )
    
    #: Timestamp when the response was generated
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="The timestamp when the response was generated")

    #: Time taken to generate the response in seconds
    responseTime: float = Field(..., description="Time taken to generate the response in seconds")

    #: LLM token usage details for the response
    llmTokens: LLMTokens = Field(..., description="LLM token usage details for the response")

    #: Optional list of files that could not be processed, including details about the issues encountered
    fileProcessingIssues: Optional[List[FileProcessingIssue]] = Field(
        default=None,
        description="Optional list of files that could not be processed, including details about the issues encountered"
    )

    #: Optional metadata related to the response
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata related to the response for additional context"
    )

# ——————————————————————————————————————————————————————————————
# Workspace API Models


class ModelMetric(BaseModel):
    model_key: str
    metric_name: str
    mean: float
    std: Optional[float] = None










