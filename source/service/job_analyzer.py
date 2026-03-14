from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import json 
from utils.llm_prompt import (
    create_job_page_analysis_prompt,
    get_job_ats_determination
)
from openai import OpenAI
from utils.logging import setup_logger

# Configure logging
logger = setup_logger(__name__)


# =============================================================================
# Assuming these are imported from your existing modules:
# from chrome_manager import ChromeCDPManager, ChromeConfig
# from dom_extractor import DOMContentExtractor, ExtractedContent
# from web_searcher import WebSearcher, SearchEngine, SearchResult
# from utils.llm_prompt import create_job_page_analysis_prompt, create_job_page_analysis_prompt_rag
# =============================================================================


# =============================================================================
# Job Page Analyzer
# =============================================================================


class AnalysisPromptType(Enum):
    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"


@dataclass
class AnalysisResult:
    response: dict[str, Any]
    success: bool
    error: Optional[str] = None
    token_usage: int = 0

class JobPageAnalyzer:
    def __init__(self, api_key: str, model: str = "gpt-4.1-nano"):
        self._client = OpenAI(api_key=api_key)
        self._model = model
        logger.debug(
            "JobPageAnalyzer initialized",
            extra={"model": self._model},
        )

    async def analyze(
        self,
        url: str | None,
        content: str,
        prompt_type: AnalysisPromptType = AnalysisPromptType.UNSTRUCTURED,
        json_resonse: bool = True,
        main_domain: str | None = None
    ) -> AnalysisResult:
        logger.info(
            "Starting page analysis",
            extra={
                "url": url,
                "content_length": len(content),
                "prompt_type": prompt_type.value if hasattr(prompt_type, 'value') else str(prompt_type),
                "json_response": json_resonse,
            },
        ) 

        prompt = (
            get_job_ats_determination(content, url, main_domain)
            if prompt_type == AnalysisPromptType.STRUCTURED
            else create_job_page_analysis_prompt(url, content)
        )
        logger.debug(
            "Prompt created",
            extra={
                "prompt_type": prompt_type.value if hasattr(prompt_type, 'value') else str(prompt_type),
                "prompt_length": len(prompt),
            },
        )

        error = ""
        for i in range(2):
            logger.debug(
                "Attempting API call",
                extra={"attempt": i + 1, "max_attempts": 2},
            )
            try:
                response = self._client.responses.create(
                    model=self._model,
                    input=prompt,
                )
         
                # Print token usage
                logger.info("Input tokens",
                            extra={"token": response.usage.input_tokens, "url": url,
                "content_length": len(content)})
                logger.info("Output tokens",
                            extra={"token": response.usage.output_tokens, "url": url,
                "content_length": len(content)})
                logger.info("Total tokens",
                            extra={"token": response.usage.total_tokens, "url": url,
                "content_length": len(content)})

                output = response.output_text
                logger.debug(
                    "API response received",
                    extra={"output_length": len(output) if output else 0},
                )
                
                if json_resonse:
                    output = json.loads(output)
                    logger.debug("JSON parsing successful")
                logger.info(
                    "Page analysis completed successfully",
                    extra={"url": url, "attempt": i + 1, "llm_reasoning": output.get("confidence_reason"), "page_category": output.get('page_category')},
                )
                return AnalysisResult(
                    response=output,
                    success=True,
                    token_usage=response.usage.total_tokens
                )
            except Exception as e:
                logger.warning(
                    "Analysis API call failed",
                    extra={
                        "attempt": i + 1,
                        "max_attempts": 2,
                        "error": str(e),
                        "url": url,
                    },
                )
                error = e
                continue

        logger.error(
            "Page analysis failed after all retries",
            extra={
                "url": url,
                "error": str(error),
                "attempts": 2,
            },
        )
        return AnalysisResult(
            response={},
            success=False,
            error=str(error),
        )
    
    async def analyze_data(
        self,
        prompt: str,
        json_response: bool = True,
    ) -> AnalysisResult:
        error = ""
        for i in range(2):
            try:
                response = self._client.responses.create(
                    model=self._model,
                    input=prompt,
                )
                logger.info("Total tokens", extra={"token": response.usage.total_tokens})
                output = response.output_text

                if json_response:
                    output = json.loads(output)
                    logger.debug("JSON parsing successful")

                return AnalysisResult(
                    response=output,
                    success=True,
                    token_usage=response.usage.total_tokens
                )
            except Exception as e:
                logger.warning(
                    "analyze_data API call failed",
                    extra={"attempt": i + 1, "error": str(e)},
                )
                error = e
                continue

        return AnalysisResult(
            response={},
            success=False,
            error=str(error),
        )