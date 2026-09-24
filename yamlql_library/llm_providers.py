from abc import ABC, abstractmethod
import os
import openai
import re
import warnings

# Suppress the google.generativeai deprecation warning
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

# Lazy import for Google Genai to avoid import-time warnings
_genai_module = None
_GENAI_AVAILABLE = False
_USING_LEGACY_GENAI = False

def _get_genai():
    """Lazy import Google Genai with fallback to legacy API."""
    global _genai_module, _GENAI_AVAILABLE, _USING_LEGACY_GENAI
    
    if _genai_module is not None:
        return _genai_module
    
    try:
        from google import genai
        _genai_module = genai
        _GENAI_AVAILABLE = True
        _USING_LEGACY_GENAI = False
    except ImportError:
        try:
            import google.generativeai as genai
            _genai_module = genai
            _GENAI_AVAILABLE = True
            _USING_LEGACY_GENAI = True
        except ImportError:
            _GENAI_AVAILABLE = False
            _USING_LEGACY_GENAI = False
    
    return _genai_module

# --- Base Provider ---

class LlmProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    def get_sql_query(self, schema: str, question: str) -> str:
        """
        Generates a SQL query from a natural language question and a database schema.

        Args:
            schema: A string representation of the database schema.
            question: The user's natural language question.

        Returns:
            A SQL query string.
        """
        pass

    def _sanitize_sql(self, sql_string: str) -> str:
        """Removes markdown code fences and other artifacts from the LLM's output."""
        # Remove markdown code block fences (e.g., ```sql ... ``` or ``` ... ```)
        match = re.search(r"```(?:\w*\n)?(.*?)```", sql_string, re.DOTALL)
        if match:
            return match.group(1).strip()
        return sql_string.strip()

    def _build_prompt(self, schema: str, question: str) -> str:
        """Constructs the standard prompt for the LLM."""
        return f"""
You are an expert DuckDB SQL assistant. Based on the following database schema, please write a single SQL query to answer the user's question.

Your response must contain ONLY the SQL query. Do not include any explanations, introductory text, or markdown code fences.

Here is the schema:
{schema}

User Question: {question}
"""

# --- Concrete Providers ---

class OpenAiProvider(LlmProvider):
    """Provider for OpenAI models like GPT-4."""
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = openai.OpenAI(api_key=self.api_key)

    def get_sql_query(self, schema: str, question: str) -> str:
        prompt = self._build_prompt(schema, question)
        response = self.client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw_sql = response.choices[0].message.content.strip()
        return self._sanitize_sql(raw_sql)

class GeminiProvider(LlmProvider):
    """Provider for Google's Gemini models."""
    def __init__(self):
        genai = _get_genai()
        
        if genai is None:
            raise ValueError(
                "Google Genai package not installed. "
                "Install with: pip install google-genai"
            )
        
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        
        # Try new google.genai API first, fall back to legacy if needed
        if not _USING_LEGACY_GENAI:
            try:
                # New google.genai API
                self.client = genai.Client(api_key=self.api_key)
                self.use_legacy = False
            except (AttributeError, TypeError):
                # Fall back to legacy if new API structure is different
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel('gemini-2.0-flash')
                self.use_legacy = True
        else:
            # Legacy google.generativeai API
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            self.use_legacy = True

    def get_sql_query(self, schema: str, question: str) -> str:
        prompt = self._build_prompt(schema, question)
        
        if self.use_legacy:
            # Legacy API
            response = self.model.generate_content(prompt)
            raw_sql = response.text.strip()
        else:
            # New API
            response = self.client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            raw_sql = response.text.strip()
        
        return self._sanitize_sql(raw_sql)

# --- Factory Function ---

def get_llm_provider(provider_name: str) -> LlmProvider:
    """
    Factory function to get an instance of the specified LLM provider.
    """
    if not provider_name:
        raise ValueError("YAMLQL_LLM_PROVIDER environment variable not set.")
        
    provider_name = provider_name.lower()
    if provider_name == "openai":
        return OpenAiProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "ollama":
        raise NotImplementedError("Ollama provider is not yet implemented.")
    else:
        raise ValueError(f"Unsupported LLM provider: {provider_name}. Supported providers are: OpenAI, Gemini, Ollama.") 