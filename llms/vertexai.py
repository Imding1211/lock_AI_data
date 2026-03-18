"""Vertex AI (Gemini) LLM provider."""

import json
import os
import sys
from typing import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types


def get_vertexai_llm(model_name: str, **kwargs) -> Callable:
    """Return a generate_json closure backed by Vertex AI Gemini.

    Parameters
    ----------
    model_name : str
        Model identifier, e.g. ``"gemini-2.5-flash"``.
    **kwargs
        Extra generation parameters (``temperature``, etc.).
    """
    load_dotenv()

    project = os.getenv("VERTEX_PROJECT_ID")
    location = os.getenv("VERTEX_LOCATION")
    if not project or not location:
        sys.exit("VERTEX_PROJECT_ID / VERTEX_LOCATION not found in environment")

    client = genai.Client(vertexai=True, project=project, location=location)
    temperature = kwargs.get("temperature", 0.3)

    def generate_json(prompt: str, system_prompt: str, schema: dict) -> dict:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )
        return json.loads(response.text)

    return generate_json


def get_vertexai_vision_llm(model_name: str, **kwargs) -> Callable:
    """Return a generate_from_video closure backed by Vertex AI Gemini.

    Parameters
    ----------
    model_name : str
        Model identifier, e.g. ``"gemini-3-flash-preview"``.
    **kwargs
        Extra generation parameters (``temperature``, etc.).

    Returns
    -------
    Callable
        ``generate_from_video(video_path, system_prompt) -> str``
    """
    load_dotenv()

    project = os.getenv("VERTEX_PROJECT_ID")
    location = os.getenv("VERTEX_LOCATION")
    if not project or not location:
        sys.exit("VERTEX_PROJECT_ID / VERTEX_LOCATION not found in environment")

    client = genai.Client(vertexai=True, project=project, location=location)
    temperature = kwargs.get("temperature", 0.1)

    def generate_from_video(video_path: str, system_prompt: str) -> str:
        video_bytes = open(video_path, "rb").read()
        video_part = types.Part.from_bytes(
            data=video_bytes,
            mime_type="video/mp4",
        )

        response = client.models.generate_content(
            model=model_name,
            contents=[video_part, system_prompt],
            config=types.GenerateContentConfig(
                temperature=temperature,
            ),
        )
        return response.text

    return generate_from_video
