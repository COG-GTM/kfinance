from fastmcp import FastMCP


class KfinanceMcp(FastMCP):
    """FastMCP subclass with some kfinance specific adaptations.

    Tool arguments are validated twice: once by the low-level mcp sdk against the
    advertised json schema, and once by the pydantic args_schema in
    KfinanceTool.arun_without_langchain. The sdk validation is stricter than pydantic and
    rejects the stringified integers that some models emit for integer fields, so the
    advertised schemas widen those fields instead of turning the sdk validation off. See
    `accept_stringified_numbers` in local_mcp.
    """
